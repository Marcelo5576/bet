from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any, Dict, Optional

router = APIRouter()

_STATE = {
    "ok": True,
    "running": False,
    "session": None,
    "message": "Bet365 assisted executor em modo stub."
}

class PrepareBet365Request(BaseModel):
    signal_id: Optional[str] = None
    match: Optional[str] = None
    market: Optional[str] = None
    selection: Optional[str] = None
    odd_min: Optional[float] = None
    stake: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None

def build_prepare_request_from_signal(signal: Any = None, **kwargs):
    data = {}
    if isinstance(signal, dict):
        data.update(signal)
    data.update(kwargs)

    return PrepareBet365Request(
        signal_id=str(data.get("signal_id") or ""),
        match=data.get("match"),
        market=data.get("market"),
        selection=data.get("selection"),
        odd_min=data.get("odd_min"),
        stake=data.get("stake"),
        raw=data,
    )

def get_bet365_assisted_router():
    return router

def start_assisted_bet365(*args, **kwargs):
    _STATE["running"] = True
    _STATE["session"] = kwargs
    return _STATE

def stop_assisted_bet365(*args, **kwargs):
    _STATE["running"] = False
    return _STATE

def get_assisted_bet365_status(*args, **kwargs):
    return _STATE

def assisted_session_snapshot(*args, **kwargs):
    return _STATE

def assisted_session_start(*args, **kwargs):
    return start_assisted_bet365(*args, **kwargs)

def assisted_session_stop(*args, **kwargs):
    return stop_assisted_bet365(*args, **kwargs)

def close_assisted_session(*args, **kwargs):
    _STATE["running"] = False
    _STATE["session"] = None
    return {
        "ok": True,
        "closed": True
    }

def execute_prepare_request(request=None, *args, **kwargs):
    _STATE["running"] = True
    _STATE["session"] = {
        "request": request.dict() if hasattr(request, "dict") else request,
        "kwargs": kwargs,
    }
    return {
        "ok": True,
        "prepared": True,
        "running": True,
        "message": "Preparação Bet365 registrada em modo stub.",
        "session": _STATE["session"],
    }
