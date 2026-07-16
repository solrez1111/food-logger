"""Phase 5 — POST /api/log/estimate: photo and/or text -> confirmable candidates.

Returns candidates only; the client logs confirmed items through the normal
POST /api/log (entry_method ai_photo / ai_text). Photos are not persisted.
"""
from __future__ import annotations

import base64

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from .. import estimate as est
from ..auth import get_current_user
from ..db import get_conn

router = APIRouter(prefix="/api/log")

MAX_IMAGE_B64 = 8_000_000  # ~6 MB decoded; client downscales to ~1024px anyway


class EstimateIn(BaseModel):
    image_b64: str | None = Field(default=None, description="base64 JPEG, no data: prefix")
    text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def need_something(self):
        if not self.image_b64 and not (self.text or "").strip():
            raise ValueError("provide image_b64 and/or text")
        if self.image_b64 and len(self.image_b64) > MAX_IMAGE_B64:
            raise ValueError("image too large — downscale before upload")
        return self


@router.post("/estimate")
async def estimate_plate(
    body: EstimateIn,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if body.image_b64:
        try:
            base64.b64decode(body.image_b64[:400], validate=True)
        except Exception:
            raise HTTPException(status_code=422, detail="image_b64 is not valid base64")

    try:
        result = await est.call_claude_estimate(body.image_b64, body.text)
    except RuntimeError as e:                    # ANTHROPIC_API_KEY missing
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:                       # API/network failure -> degrade to manual
        raise HTTPException(status_code=502, detail=f"estimation failed: {e}")

    candidates = await est.assemble_candidates(conn, result["items"])
    return {
        "candidates": candidates,
        "note": result.get("note"),
        "model": result.get("model"),
        "entry_method": "ai_photo" if body.image_b64 else "ai_text",
    }
