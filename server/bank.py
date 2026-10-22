"""The bullet bank, for the person to read and prune (tailor.bank)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tailor import bank

router = APIRouter(prefix="/bullets", tags=["bullets"])


@router.get("")
def list_bullets() -> dict:
    rows = sorted(bank.load(), key=lambda b: (b.company.lower(), -(b.rating or 0)))
    return {"bullets": [b.to_dict() for b in rows]}


@router.delete("/{bullet_id}")
def delete_bullet(bullet_id: str) -> dict:
    if not bank.delete(bullet_id):
        raise HTTPException(404, "no such bullet")
    return {"deleted": bullet_id}
