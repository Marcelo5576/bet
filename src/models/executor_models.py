from __future__ import annotations

from pydantic import BaseModel, Field


class PrepareBet365Request(BaseModel):
    match_name: str = Field(min_length=3, max_length=160)
    market: str = Field(min_length=2, max_length=160)
    selection: str = Field(min_length=1, max_length=160)
    min_odd: float = Field(gt=1.0)
    stake: float = Field(gt=0.0)
    signal_id: str | None = Field(default=None, max_length=80)


class PrepareBet365Response(BaseModel):
    ok: bool
    status: str
    message: str
    current_odd: float | None = None
    screenshot_path: str | None = None
    signal_id: str | None = None
