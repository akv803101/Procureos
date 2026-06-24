"""Goal REF codes (Fix 06).

The REF embedded in every RFQ must be EXACTLY 8 chars [A-Z0-9] so the inbound
router's `REF:([A-Z0-9]{8})` regex matches. A prefix of the goal id fails this
for short ids (e.g. in-memory 'goal-1' -> 'goal1', 5 chars), so we hash instead:
deterministic, always 8 chars, and collision-resistant across goals.
"""
from __future__ import annotations

import hashlib


def ref_code(goal_id: str) -> str:
    return hashlib.sha1(goal_id.encode()).hexdigest()[:8].upper()
