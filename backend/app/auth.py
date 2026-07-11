"""The single seam where all token->user resolution lives (PLAN decision 1).

v1: one static bearer token (API_TOKEN env) maps to user 1. Real multi-user
auth, if it ever happens, replaces this one dependency — no route reads the
token directly (PLAN appendix).
"""
import hmac
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

PRIMARY_USER = {"id": 1, "name": "primary"}


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    expected = os.environ.get("API_TOKEN", "")
    if not expected or creds is None or not hmac.compare_digest(creds.credentials, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return PRIMARY_USER
