"""Request schemas for the approval endpoints."""
from __future__ import annotations

from pydantic import BaseModel


class ApproveRequest(BaseModel):
    option_id: str            # the chosen option (vendor_id of the ranked option)
    token: str | None = None  # magic-link token (Fix 12); required until JWT auth lands (Phase 3)


class RejectRequest(BaseModel):
    reason: str
    token: str | None = None
