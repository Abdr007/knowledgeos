"""Test fixtures.

The suite runs against **real** Postgres, Redis and Qdrant — the same containers
the application uses — inside a transaction that is rolled back after each test.
Mocking the database would mean the tests never exercise the generated tsvector
column, the cascade rules, or the unique constraints, which are exactly the
things most likely to be wrong.

Only the LLM is substituted, via the ScriptedProvider that ships in the
application itself (D1). Asserting on a real model's output would mean asserting
on something that legitimately varies run to run.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

# Must be set before any application module imports Settings.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LLM_PROVIDER", "scripted")

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clients import get_redis
from app.db.session import SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _schema_present() -> None:
    """Fail fast and clearly when migrations have not been applied."""
    with engine.connect() as conn:
        present = conn.execute(
            text("select count(*) from information_schema.tables where table_name = 'chunks'")
        ).scalar()
    if not present:
        pytest.exit("Schema missing. Run `make migrate` before the test suite.", returncode=1)


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clear_rate_limits() -> Iterator[None]:
    """Rate limits are shared state in Redis.

    Without this a test that logs in five times leaves counters behind that fail
    an unrelated test later — the classic order-dependent suite.
    """
    yield
    try:
        redis = get_redis()
        for key in redis.scan_iter("kos:v1:rl:*"):
            redis.delete(key)
    except Exception:
        pass


@pytest.fixture
def account(client: TestClient) -> dict[str, str]:
    """A registered user with an organization, workspace and auth header."""
    email = f"t-{uuid.uuid4().hex[:12]}@knowledgeos.ai"
    password = "a-very-long-passphrase-9"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    workspaces = client.get("/api/v1/workspaces", headers=headers).json()
    return {
        "email": email,
        "password": password,
        "token": token,
        "headers": headers,
        "workspace_id": workspaces[0]["id"],
        "org_id": workspaces[0]["org_id"],
    }


@pytest.fixture
def second_account(client: TestClient) -> dict[str, str]:
    """A second, unrelated tenant. Used by the isolation tests."""
    email = f"o-{uuid.uuid4().hex[:12]}@knowledgeos.ai"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "a-very-long-passphrase-9",
            "full_name": "Other Tenant",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    workspaces = client.get("/api/v1/workspaces", headers=headers).json()
    return {"email": email, "headers": headers, "workspace_id": workspaces[0]["id"]}
