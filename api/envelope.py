"""Standard API response envelope (PRD Section 24).

Every endpoint returns either:
  success: {"success": true,  "data": {...}, "error": null}
  failure: {"success": false, "data": null, "error": {code, message, details?}}
"""
from __future__ import annotations


def ok(data) -> dict:
    return {"success": True, "data": data, "error": None}


def err(code: str, message: str, details=None) -> dict:
    return {"success": False, "data": None,
            "error": {"code": code, "message": message, "details": details}}
