#!/usr/bin/env python
"""Seed a demo account and ingest a real document.

Creates an account, uploads the project's own design document, waits for the
worker to finish, and prints credentials. Everything it loads is a real file
processed by the real pipeline — no fabricated documents and no pre-computed
vectors, because a demo that stages its own data proves nothing about whether
the pipeline works.

    make seed
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import httpx

# Defaults to the Compose-published port; override for a local dev server.
API_ORIGIN = os.environ.get("KOS_API", "http://127.0.0.1:8000")
API = f"{API_ORIGIN}/api/v1"
ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "KnowledgeOS_AI_TDD.pdf"

PASSWORD = "demo-passphrase-knowledgeos"


def main() -> int:
    if not CORPUS.is_file():
        print(f"Corpus not found: {CORPUS}", file=sys.stderr)
        return 1

    client = httpx.Client(timeout=300)

    try:
        client.get(f"{API_ORIGIN}/healthz")
    except httpx.HTTPError:
        print("Backend is not reachable. Start it with `make up` first.", file=sys.stderr)
        return 1

    email = f"demo-{uuid.uuid4().hex[:6]}@knowledgeos.ai"
    print(f"Creating account {email} …")
    response = client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Demo User",
              "organization_name": "Demo Organization"},
    )
    if response.status_code != 201:
        print(f"Registration failed: {response.status_code} {response.text}", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    workspace = client.get(f"{API}/workspaces", headers=headers).json()[0]
    print(f"Workspace: {workspace['name']} ({workspace['id']})")

    print(f"Uploading {CORPUS.name} ({CORPUS.stat().st_size // 1024} KB) …")
    with CORPUS.open("rb") as handle:
        upload = client.post(
            f"{API}/workspaces/{workspace['id']}/documents",
            headers=headers,
            files={"file": (CORPUS.name, handle, "application/pdf")},
        )
    if upload.status_code != 202:
        print(f"Upload failed: {upload.status_code} {upload.text}", file=sys.stderr)
        return 1

    document_id = upload.json()["document"]["id"]
    print("Waiting for the worker (first run downloads the embedding model) …")

    deadline = time.time() + 300
    status = "PENDING"
    while time.time() < deadline:
        document = client.get(f"{API}/documents/{document_id}", headers=headers).json()
        if document["status"] != status:
            status = document["status"]
            print(f"  → {status}")
        if status in {"READY", "FAILED"}:
            break
        time.sleep(2)

    if status != "READY":
        print(f"Ingestion did not complete: {document.get('error_message')}", file=sys.stderr)
        return 1

    print(
        f"  {document['page_count']} pages → {document['chunk_count']} chunks "
        f"→ {document['token_count']:,} tokens"
    )

    print("\nVerifying retrieval …")
    search = client.post(
        f"{API}/workspaces/{workspace['id']}/search",
        headers=headers,
        json={"query": "Why was Reciprocal Rank Fusion chosen?", "top_k": 3},
    ).json()
    print(
        f"  {len(search['hits'])} hits in {search['took_ms']}ms "
        f"({search['dense_candidates']} vector, {search['sparse_candidates']} keyword candidates)"
    )

    print("\n" + "─" * 62)
    print("  Console:  http://localhost:3000")
    print(f"  Email:    {email}")
    print(f"  Password: {PASSWORD}")
    print("─" * 62)
    print("\nTry these:")
    print('  "Why was Reciprocal Rank Fusion chosen instead of a weighted blend?"')
    print('  "How is tenant isolation enforced?"   → grounded, with citations')
    print('  "What is the share price of Emirates NBD?"  → refused, gate fires')
    return 0


if __name__ == "__main__":
    sys.exit(main())
