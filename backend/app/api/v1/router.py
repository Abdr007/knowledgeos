"""API v1 aggregate router.

Every v1 route is mounted here and nowhere else, so the surface of the API is one
file you can read top to bottom — rather than a set of includes scattered across
main.py that nobody can enumerate.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, documents, workspaces

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(documents.router)
