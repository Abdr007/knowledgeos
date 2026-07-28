"""Authentication, authorization and the cross-tenant boundary (§9, §13).

The isolation tests are the most important in the suite. A vector store or a
query that returns another tenant's data is a data breach, and it is the one
class of bug that no amount of prompt engineering can compensate for.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

BASE = "/api/v1"


# ── registration and login ───────────────────────────────────────────────


def test_registration_creates_org_and_workspace_atomically(client: TestClient, account):
    me = client.get(f"{BASE}/auth/me", headers=account["headers"]).json()
    assert me["user"]["email"] == account["email"]
    assert len(me["memberships"]) == 1
    assert me["memberships"][0]["role"] == "OWNER"
    assert len(client.get(f"{BASE}/workspaces", headers=account["headers"]).json()) == 1


def test_password_hash_never_leaves_the_api(client: TestClient, account):
    body = client.get(f"{BASE}/auth/me", headers=account["headers"]).text
    assert "password" not in body.lower()


def test_duplicate_email_conflicts(client: TestClient, account):
    response = client.post(
        f"{BASE}/auth/register",
        json={
            "email": account["email"],
            "password": "a-very-long-passphrase-9",
            "full_name": "Impostor",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "conflict"


def test_short_passwords_are_rejected(client: TestClient):
    response = client.post(
        f"{BASE}/auth/register",
        json={
            "email": f"s-{uuid.uuid4().hex[:8]}@knowledgeos.ai",
            "password": "short",
            "full_name": "S",
        },
    )
    assert response.status_code == 422


def test_wrong_password_and_unknown_user_are_indistinguishable(client: TestClient, account):
    wrong = client.post(
        f"{BASE}/auth/login",
        json={"email": account["email"], "password": "not-the-right-password"},
    )
    unknown = client.post(
        f"{BASE}/auth/login",
        json={"email": "nobody@knowledgeos.ai", "password": "not-the-right-password"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


# ── token handling ───────────────────────────────────────────────────────


def test_requests_without_a_token_are_rejected(client: TestClient):
    assert client.get(f"{BASE}/auth/me").status_code == 401
    assert client.get(f"{BASE}/workspaces").status_code == 401


def test_garbage_token_is_rejected(client: TestClient):
    response = client.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert response.status_code == 401


def test_refresh_token_is_httponly_and_not_in_the_body(client: TestClient):
    response = client.post(
        f"{BASE}/auth/register",
        json={
            "email": f"c-{uuid.uuid4().hex[:8]}@knowledgeos.ai",
            "password": "a-very-long-passphrase-9",
            "full_name": "Cookie",
        },
    )
    assert "kos_refresh" not in response.text
    header = response.headers.get("set-cookie", "")
    assert "kos_refresh" in header
    assert "httponly" in header.lower()


def test_refresh_rotates_and_replaying_a_spent_token_revokes_the_family(client: TestClient):
    """The property that makes a stolen refresh token a single-use theft."""
    email = f"r-{uuid.uuid4().hex[:8]}@knowledgeos.ai"
    client.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": "a-very-long-passphrase-9", "full_name": "R"},
    )
    original = client.cookies.get("kos_refresh")
    assert original

    assert client.post(f"{BASE}/auth/refresh").status_code == 200
    rotated = client.cookies.get("kos_refresh")
    assert rotated != original

    # Replay the spent token from a clean client.
    replay = TestClient(client.app)
    replay.cookies.set("kos_refresh", original)
    assert replay.post(f"{BASE}/auth/refresh").status_code == 401

    # Reuse detection killed the whole family, so the legitimate successor dies too.
    client.cookies.set("kos_refresh", rotated)
    assert client.post(f"{BASE}/auth/refresh").status_code == 401


# ── cross-tenant isolation ───────────────────────────────────────────────


def test_other_tenants_workspace_returns_404_not_403(
    client: TestClient, account, second_account
):
    """403 would confirm the resource exists — an enumeration oracle (§17)."""
    response = client.get(
        f"{BASE}/workspaces/{account['workspace_id']}", headers=second_account["headers"]
    )
    assert response.status_code == 404


def test_other_tenants_cannot_search_or_upload_into_a_foreign_workspace(
    client: TestClient, account, second_account
):
    workspace = account["workspace_id"]
    headers = second_account["headers"]
    assert (
        client.post(
            f"{BASE}/workspaces/{workspace}/search", headers=headers, json={"query": "x"}
        ).status_code
        == 404
    )
    assert (
        client.get(f"{BASE}/workspaces/{workspace}/documents", headers=headers).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/workspaces/{workspace}/conversations", headers=headers, json={}
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"{BASE}/workspaces/{workspace}/analytics/overview", headers=headers
        ).status_code
        == 404
    )


def test_nonexistent_ids_also_return_404(client: TestClient, account):
    missing = uuid.uuid4()
    assert (
        client.get(f"{BASE}/workspaces/{missing}", headers=account["headers"]).status_code
        == 404
    )
    assert (
        client.get(f"{BASE}/documents/{missing}", headers=account["headers"]).status_code == 404
    )


def test_search_in_an_empty_workspace_returns_nothing_rather_than_erroring(
    client: TestClient, account
):
    response = client.post(
        f"{BASE}/workspaces/{account['workspace_id']}/search",
        headers=account["headers"],
        json={"query": "anything at all"},
    )
    assert response.status_code == 200
    assert response.json()["hits"] == []


# ── response headers ─────────────────────────────────────────────────────


def test_security_headers_are_present(client: TestClient):
    headers = client.get("/healthz").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    # Remote images blocked: the model-output exfiltration channel (§27.2).
    assert "img-src 'self' data:" in headers["content-security-policy"]


def test_every_response_carries_a_request_id(client: TestClient):
    assert client.get("/healthz").headers.get("x-request-id")
