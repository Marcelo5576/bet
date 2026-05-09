from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import load_settings
from src.executors.bet365_assisted import execute_prepare_request
from src.models.executor_models import PrepareBet365Request, PrepareBet365Response
from src.portal import PortalStore, read_session_token

router = APIRouter(tags=["executor"])
_security = HTTPBasic(auto_error=False)


def _require_executor_access(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> dict[str, Any]:
    settings = load_settings()
    token = request.cookies.get("bs_session")
    user_id = read_session_token(token, settings.portal_session_secret)
    if user_id:
        user = PortalStore(settings.portal_db_file).get_user(int(user_id))
        if user and int(user.get("is_admin") or 0):
            return user
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais obrigatórias.",
        )
    user_ok = secrets.compare_digest(credentials.username, settings.dashboard_user)
    password_ok = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not user_ok or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas.",
        )
    return {"id": 0, "is_admin": 1, "auth": "basic"}


@router.post("/executor/bet365/prepare", response_model=PrepareBet365Response)
async def api_prepare_bet365(
    payload: PrepareBet365Request,
    _: dict[str, Any] = Depends(_require_executor_access),
) -> PrepareBet365Response:
    return await execute_prepare_request(payload)
