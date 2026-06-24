"""Request schema for goal submission."""
from __future__ import annotations

from pydantic import BaseModel


class CreateGoalRequest(BaseModel):
    raw_input: str
    company_id: str               # demo mode; Phase 3 reads this from the JWT company_id claim
    employee_id: str | None = None
    company_city: str = ""        # fallback for the intent parser (the goal text usually has the location)
