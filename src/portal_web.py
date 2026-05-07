from __future__ import annotations

from datetime import datetime, timezone
import html
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from src.config import Settings, load_settings
from src.integrations.supabase import SupabaseSink
from src.intelligence.risk import red_stop_status
from src.portal import (
    DEFAULT_AI_SUPPORT_SKILLS,
    PortalStore,
    issue_session_token,
    read_session_token,
    send_password_reset_email,
    support_agent_reply,
)
from src.storage import StateStore
from src.usage_metrics import UsagePricing, UsageTracker

router = APIRouter()
SESSION_COOKIE = "bs_session"
_STORE_CACHE: dict[str, PortalStore] = {}
_BOOTSTRAP_DONE: set[str] = set()
_RATE_LIMIT: dict[str, list[float]] = {}
_AI_SKILLS_SYNC_DONE: set[str] = set()
PLAN_FEATURES = {
    "starter": [
        "Scanner quantitativo + Telegram",
        "Histórico de decisões",
        "Dashboard responsiva",
    ],
    "pro": [
        "Tudo do Starter",
        "IA com memória operacional",
        "Suporte prioritário",
    ],
    "team": [
        "Tudo do Pro",
        "Multi-operadores",
        "Gestão comercial/admin completa",
    ],
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupPayload(BaseModel):
    name: str
    email: str
    password: str
    plan: str = "starter"


class LoginPayload(BaseModel):
    email: str
    password: str


class ForgotPayload(BaseModel):
    email: str


class ResetPayload(BaseModel):
    token: str
    password: str


class SupportPayload(BaseModel):
    message: str


class FantasyPayload(BaseModel):
    description: str
    budget: float = 100.0


class CheckoutPayload(BaseModel):
    plan: str | None = None


class AdminActionPayload(BaseModel):
    user_id: int
    action: str
    plan: str | None = None
    monthly_price_brl: float | None = None
    reason: str | None = None
    cycle_days: int | None = None


class PreferencesPayload(BaseModel):
    scan_enabled: bool | None = None
    idle_scan_seconds: int | None = None
    active_scan_seconds: int | None = None
    telegram_enabled: bool | None = None
    telegram_chat_id: str | None = None


class ProfilePayload(BaseModel):
    name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    new_password: str | None = None


class PlanPricePayload(BaseModel):
    plan: str
    monthly_price_brl: float


class AdminTelegramApprovedPayload(BaseModel):
    enabled: bool | None = None
    chat_id: str | None = None


def _settings() -> Settings:
    return load_settings()


def _portal_store(settings: Settings) -> PortalStore:
    key = settings.portal_db_file
    store = _STORE_CACHE.get(key)
    if store is None:
        store = PortalStore(key)
        _STORE_CACHE[key] = store
    if key not in _BOOTSTRAP_DONE:
        store.ensure_admin(settings.admin_email, settings.admin_name, settings.admin_password)
        store.seed_ai_skills(DEFAULT_AI_SUPPORT_SKILLS)
        _BOOTSTRAP_DONE.add(key)
    return store


def _safe_plan(plan: str | None) -> str:
    value = (plan or "starter").strip().lower()
    if value not in {"starter", "pro", "team"}:
        return "starter"
    return value


def _clean_telegram_chat_ids(value: str | None) -> str:
    raw = str(value or "").replace(";", ",")
    if not raw.strip():
        return ""
    ids: list[str] = []
    for item in raw.split(","):
        clean = item.strip()
        if not clean:
            continue
        if not re.fullmatch(r"-?\d{5,24}", clean):
            raise HTTPException(status_code=400, detail="Chat ID Telegram invalido.")
        ids.append(clean)
    return ",".join(dict.fromkeys(ids))


def _pricing_defaults(settings: Settings) -> dict[str, float]:
    return {
        "starter": float(settings.plan_starter_price_brl),
        "pro": float(settings.plan_pro_price_brl),
        "team": float(settings.plan_team_price_brl),
    }


def _plan_catalog(settings: Settings, store: PortalStore | None = None) -> dict[str, dict[str, Any]]:
    pricing = _pricing_defaults(settings)
    if store:
        pricing = store.pricing_map(pricing)
    return {
        "starter": {
            "label": "Starter",
            "price": float(pricing["starter"]),
            "features": PLAN_FEATURES["starter"],
        },
        "pro": {
            "label": "Pro",
            "price": float(pricing["pro"]),
            "features": PLAN_FEATURES["pro"],
        },
        "team": {
            "label": "Team",
            "price": float(pricing["team"]),
            "features": PLAN_FEATURES["team"],
        },
    }


def _cookie_secure(request: Request, settings: Settings) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded_proto == "https"


def _set_session_cookie(response: JSONResponse, request: Request, settings: Settings, user_id: int) -> None:
    token = issue_session_token(user_id, settings.portal_session_secret, settings.portal_session_hours)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=_cookie_secure(request, settings),
        samesite="lax",
        max_age=max(3600, settings.portal_session_hours * 3600),
        path="/",
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _origin_from_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_origins(request: Request, settings: Settings) -> set[str]:
    allowed: set[str] = set()
    base = _origin_from_url(str(request.base_url))
    if base:
        allowed.add(base)
    configured = _origin_from_url(settings.website_url)
    if configured:
        allowed.add(configured)
    host = (request.headers.get("host") or "").strip()
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    if host:
        scheme = forwarded_proto or request.url.scheme
        allowed.add(f"{scheme.lower()}://{host.lower()}")
    if forwarded_proto and forwarded_host:
        allowed.add(f"{forwarded_proto}://{forwarded_host}")
    return allowed


def _request_origin(request: Request) -> str | None:
    origin = _origin_from_url(request.headers.get("origin"))
    if origin:
        return origin
    return _origin_from_url(request.headers.get("referer"))


def _assert_same_origin(request: Request, settings: Settings) -> None:
    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        raise HTTPException(status_code=403, detail="Requisicao bloqueada por politica de origem.")
    origin = _request_origin(request)
    if not origin:
        xrw = (request.headers.get("x-requested-with") or "").strip().lower()
        if xrw == "xmlhttprequest" and fetch_site in {"", "same-origin", "same-site", "none"}:
            return
        raise HTTPException(status_code=403, detail="Cabecalho de origem ausente.")
    if origin not in _allowed_origins(request, settings):
        raise HTTPException(status_code=403, detail="Origem invalida para esta operacao.")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return "unknown"


def _rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    now = time.time()
    ip = _client_ip(request)
    bucket_key = f"{ip}:{key}"
    bucket = [moment for moment in _RATE_LIMIT.get(bucket_key, []) if now - moment <= window_seconds]
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde e tente novamente.")
    bucket.append(now)
    _RATE_LIMIT[bucket_key] = bucket


def _telegram_status(
    settings: Settings,
    prefs: dict[str, Any] | None,
    state_obj,
) -> dict[str, Any]:
    prefs = prefs or {}
    scan_enabled = bool(int(prefs.get("scan_enabled") or 0))
    notifications_enabled = bool(int(prefs.get("telegram_enabled") or 0))
    chat_id = str(prefs.get("telegram_chat_id") or "").strip()
    token_present = bool(settings.telegram_bot_token)
    blockers: list[str] = []
    if not token_present:
        blockers.append("Telegram desligado neste ambiente: falta TELEGRAM_BOT_TOKEN no .env.")
    if not chat_id:
        blockers.append("Falta vincular o Chat ID. Abra o bot e use /chatid.")
    if chat_id and not notifications_enabled:
        blockers.append("As notificações do Telegram estão desmarcadas neste usuário.")
    if not scan_enabled:
        blockers.append("O scanner deste usuário está pausado, então nenhum alerta será enviado.")
    ready = token_present and bool(chat_id) and notifications_enabled and scan_enabled
    summary = (
        "Telegram pronto para este usuário."
        if ready
        else blockers[0] if blockers else "Telegram ainda precisa de configuração."
    )
    return {
        "ready": ready,
        "severity": "ok" if ready else "warning",
        "summary": summary,
        "blockers": blockers,
        "token_present": token_present,
        "chat_id_present": bool(chat_id),
        "notifications_enabled": notifications_enabled,
        "scan_enabled": scan_enabled,
        "state_chat_ids": len(getattr(state_obj, "chat_ids", []) or []),
    }


def _telegram_status_html(status: dict[str, Any]) -> str:
    css = "notice ok" if status.get("ready") else "notice muted"
    blockers = status.get("blockers") or []
    extra = f"<div class='muted'>{_esc(' | '.join(blockers))}</div>" if blockers else ""
    return (
        f"<div id='telegram-status' class='{css}'>"
        f"<strong>Telegram:</strong> {_esc(status.get('summary') or '-')}{extra}"
        "</div>"
    )


def _ai_memory_status(settings: Settings, store: PortalStore) -> dict[str, Any]:
    store.seed_ai_skills(DEFAULT_AI_SUPPORT_SKILLS)
    local_skills = store.list_ai_skills(limit=200)
    supabase_enabled = bool(settings.supabase_url and settings.supabase_service_role_key)
    summary = (
        f"{len(local_skills)} skills curtas ativas. Supabase {'ligado' if supabase_enabled else 'desligado'} neste ambiente."
    )
    return {
        "skills_total": len(local_skills),
        "supabase_enabled": supabase_enabled,
        "summary": summary,
    }


def _ai_memory_status_html(status: dict[str, Any]) -> str:
    css = "notice ok" if status.get("supabase_enabled") else "notice muted"
    return (
        f"<div id='ai-memory-status' class='{css}'>"
        f"<strong>Memoria IA:</strong> {_esc(status.get('summary') or '-')}"
        "</div>"
    )


async def _ai_support_skills(settings: Settings) -> list[dict[str, Any]]:
    store = _portal_store(settings)
    store.seed_ai_skills(DEFAULT_AI_SUPPORT_SKILLS)
    local_skills = store.list_ai_skills()
    sink = SupabaseSink.from_settings(settings)
    if not sink.enabled:
        return local_skills or DEFAULT_AI_SUPPORT_SKILLS
    sync_key = settings.supabase_url or "default"
    if sync_key not in _AI_SKILLS_SYNC_DONE:
        await sink.sync_ai_skills(local_skills or DEFAULT_AI_SUPPORT_SKILLS)
        _AI_SKILLS_SYNC_DONE.add(sync_key)
    skills = await sink.fetch_ai_skills()
    return skills or local_skills or DEFAULT_AI_SUPPORT_SKILLS


def _session_user(request: Request, settings: Settings, store: PortalStore) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = read_session_token(token, settings.portal_session_secret)
    if not user_id:
        raise HTTPException(status_code=401, detail="Sessao expirada.")
    user = store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Usuario invalido.")
    return user


def _require_user(request: Request) -> dict[str, Any]:
    settings = _settings()
    store = _portal_store(settings)
    return _session_user(request, settings, store)


def _optional_user(request: Request) -> dict[str, Any] | None:
    settings = _settings()
    store = _portal_store(settings)
    try:
        return _session_user(request, settings, store)
    except HTTPException:
        return None


def _require_admin(user: dict[str, Any] = Depends(_require_user)) -> dict[str, Any]:
    if not int(user.get("is_admin") or 0):
        raise HTTPException(status_code=403, detail="Acesso admin obrigatorio.")
    return user


def _trial_left_days(user: dict[str, Any]) -> int:
    trial_ends = user.get("trial_ends_at")
    if not trial_ends:
        return 0
    try:
        dt = datetime.fromisoformat(str(trial_ends).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds() // 86400) + (1 if delta.total_seconds() > 0 else 0))
    except ValueError:
        return 0


def _fmt_money(value: Any) -> str:
    try:
        amount = float(value)
        if amount.is_integer():
            return f"R$ {int(amount)}"
        return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "R$ 0"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _mask_secret(value: str | None) -> dict[str, Any]:
    raw = (value or "").strip()
    if not raw:
        return {"configured": False, "preview": "nao configurado", "length": 0}
    if len(raw) <= 8:
        preview = "***"
    else:
        preview = f"{raw[:4]}...{raw[-4:]}"
    return {"configured": True, "preview": preview, "length": len(raw)}


_FANTASY_POSITION_ALIASES = {
    "GOL": "GOL",
    "GK": "GOL",
    "GOLEIRO": "GOL",
    "ZAG": "ZAG",
    "DEF": "ZAG",
    "ZAGUEIRO": "ZAG",
    "LAT": "LAT",
    "LATERAL": "LAT",
    "MEI": "MEI",
    "MID": "MEI",
    "MEIA": "MEI",
    "ATA": "ATA",
    "FWD": "ATA",
    "ATACANTE": "ATA",
    "TEC": "TEC",
    "TÉC": "TEC",
    "TECNICO": "TEC",
    "TÉCNICO": "TEC",
}
_FANTASY_REQUIREMENTS = {"GOL": 1, "ZAG": 2, "LAT": 2, "MEI": 3, "ATA": 3}


def _fantasy_position(value: str) -> str | None:
    token = re.sub(r"[^A-Za-zÀ-ÿ]", "", value or "").upper()
    return _FANTASY_POSITION_ALIASES.get(token)


def _extract_number(value: str, default: float) -> float:
    match = re.search(r"(\d+(?:[,.]\d+)?)", value or "")
    if not match:
        return default
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return default


def _fantasy_parse_players(description: str) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for raw_line in (description or "").splitlines():
        line = raw_line.strip()
        if not line or len(line) < 4:
            continue
        parts = [part.strip() for part in re.split(r"[;|,]", line) if part.strip()]
        pos = None
        pos_index = -1
        for idx, part in enumerate(parts):
            pos = _fantasy_position(part)
            if pos:
                pos_index = idx
                break
        if not pos:
            match = re.search(r"\b(GOL|GK|GOLEIRO|ZAG|DEF|ZAGUEIRO|LAT|LATERAL|MEI|MID|MEIA|ATA|FWD|ATACANTE|TEC|TÉC|TECNICO|TÉCNICO)\b", line, re.I)
            if match:
                pos = _fantasy_position(match.group(1))
        if not pos:
            continue
        name = parts[0] if pos_index != 0 and parts else re.sub(r"\b" + re.escape(parts[pos_index]) + r"\b", "", line, count=1).strip(" -:,")
        team = parts[2] if len(parts) > 2 and pos_index != 2 else ""
        price = 10.0
        projection = 0.0
        risk = 45.0
        for part in parts[1:]:
            lower = part.lower()
            if any(key in lower for key in ("preço", "preco", "valor", "custo", "cartoleta", "cart")):
                price = _extract_number(part, price)
            elif any(key in lower for key in ("proj", "media", "média", "pontos", "pts")):
                projection = _extract_number(part, projection)
            elif "risco" in lower:
                risk = _extract_number(part, risk)
            elif not team and not re.search(r"\d", part) and not _fantasy_position(part):
                team = part[:40]
        if not projection:
            numeric = [_extract_number(part, 0) for part in parts[1:] if re.search(r"\d", part)]
            if numeric:
                projection = max(numeric)
        if not projection:
            projection = {"GOL": 5.8, "ZAG": 5.2, "LAT": 5.6, "MEI": 6.4, "ATA": 7.0, "TEC": 4.5}.get(pos, 5.0)
        name = re.sub(r"\b(GOL|GK|GOLEIRO|ZAG|DEF|ZAGUEIRO|LAT|LATERAL|MEI|MID|MEIA|ATA|FWD|ATACANTE|TEC|TÉC|TECNICO|TÉCNICO)\b", "", name, flags=re.I).strip(" -:,")
        if len(name) < 2:
            name = f"{pos} {len(players) + 1}"
        players.append(
            {
                "name": name[:80],
                "position": pos,
                "team": team[:40] or "informado",
                "price": round(max(1.0, price), 2),
                "projection": round(max(0.5, projection), 2),
                "risk": round(max(0, min(100, risk)), 1),
            }
        )
    return players[:80]


def _fantasy_placeholders(description: str) -> list[dict[str, Any]]:
    teams = []
    match = re.search(r"([A-Za-zÀ-ÿ0-9 .-]{3,})\s+x\s+([A-Za-zÀ-ÿ0-9 .-]{3,})", description or "", re.I)
    if match:
        teams = [match.group(1).strip()[:24], match.group(2).strip()[:24]]
    if len(teams) < 2:
        teams = ["Favorito", "Equilíbrio"]
    template = [
        ("GOL", "Goleiro seguro", teams[0], 7.4, 6.2),
        ("ZAG", "Zagueiro bola parada", teams[0], 7.0, 5.8),
        ("ZAG", "Zagueiro desarme", teams[1], 6.6, 5.4),
        ("LAT", "Lateral ofensivo", teams[0], 7.8, 6.4),
        ("LAT", "Lateral cruzamentos", teams[1], 7.1, 5.9),
        ("MEI", "Meia criador", teams[0], 9.0, 7.4),
        ("MEI", "Meia finalizador", teams[0], 8.4, 6.9),
        ("MEI", "Meia regular", teams[1], 7.5, 6.0),
        ("ATA", "Atacante referência", teams[0], 10.5, 8.2),
        ("ATA", "Atacante velocidade", teams[0], 9.2, 7.1),
        ("ATA", "Atacante contragolpe", teams[1], 8.2, 6.5),
    ]
    return [
        {"position": pos, "name": name, "team": team, "price": price, "projection": proj, "risk": 42.0}
        for pos, name, team, price, proj in template
    ]


def _fantasy_build_lineup(description: str, budget: float) -> dict[str, Any]:
    budget = max(40.0, min(300.0, float(budget or 100)))
    parsed = _fantasy_parse_players(description)
    players = parsed or _fantasy_placeholders(description)
    for item in players:
        price = float(item.get("price") or 1)
        projection = float(item.get("projection") or 0)
        risk = float(item.get("risk") or 45)
        item["score"] = round((projection / max(price, 1)) * 10 + projection - (risk / 100), 3)
    chosen: list[dict[str, Any]] = []
    remaining = budget
    for pos, needed in _FANTASY_REQUIREMENTS.items():
        candidates = sorted(
            [item for item in players if item["position"] == pos and item not in chosen],
            key=lambda item: (item["score"], item["projection"]),
            reverse=True,
        )
        for candidate in candidates[:needed]:
            chosen.append(candidate)
            remaining -= float(candidate["price"])
    if len(chosen) < sum(_FANTASY_REQUIREMENTS.values()):
        fallback = [item for item in _fantasy_placeholders(description) if item["position"] not in {"TEC"}]
        for item in fallback:
            if len([p for p in chosen if p["position"] == item["position"]]) < _FANTASY_REQUIREMENTS.get(item["position"], 0):
                item["score"] = round((item["projection"] / item["price"]) * 10 + item["projection"], 3)
                chosen.append(item)
    total = round(sum(float(item["price"]) for item in chosen), 2)
    if total > budget:
        scale = max(0.5, budget / total)
        for item in chosen:
            item["price"] = round(max(1.0, float(item["price"]) * scale), 2)
        total = round(sum(float(item["price"]) for item in chosen), 2)
        if total > budget and chosen:
            chosen[-1]["price"] = round(max(1.0, float(chosen[-1]["price"]) - (total - budget)), 2)
            total = round(sum(float(item["price"]) for item in chosen), 2)
    projection = round(sum(float(item["projection"]) for item in chosen), 2)
    parsed_note = "Usei o pool informado." if parsed else "Não encontrei jogadores estruturados; gerei perfis ideais pela descrição."
    return {
        "ok": True,
        "budget": budget,
        "total_price": total,
        "remaining": round(budget - total, 2),
        "projection": projection,
        "formation": "1-2-2-3-3",
        "note": parsed_note,
        "lineup": chosen[:11],
        "tips": [
            "Priorizei projeção por preço e risco menor.",
            "Revise titulares, desfalques e mando antes de escalar.",
            "Não use a sugestão como promessa de lucro.",
        ],
    }


def _size_label(size_bytes: int) -> str:
    amount = float(max(0, int(size_bytes)))
    units = ["B", "KB", "MB", "GB"]
    idx = 0
    while amount >= 1024 and idx < len(units) - 1:
        amount /= 1024
        idx += 1
    return f"{amount:.1f} {units[idx]}"


def _usage_tracker(settings: Settings) -> UsageTracker:
    return UsageTracker(settings.usage_metrics_db_file)


def _usage_pricing(settings: Settings) -> UsagePricing:
    return UsagePricing(
        gemini_input_cost_per_1m_brl=settings.gemini_input_cost_per_1m_brl,
        gemini_output_cost_per_1m_brl=settings.gemini_output_cost_per_1m_brl,
        api_football_cost_per_request_brl=settings.api_football_cost_per_request_brl,
        football_data_org_cost_per_request_brl=settings.football_data_org_cost_per_request_brl,
        odds_api_io_cost_per_request_brl=settings.odds_api_io_cost_per_request_brl,
        espn_cost_per_request_brl=settings.espn_cost_per_request_brl,
        supabase_cost_per_request_brl=settings.supabase_cost_per_request_brl,
        stripe_cost_per_request_brl=settings.stripe_cost_per_request_brl,
        mercadopago_cost_per_request_brl=settings.mercadopago_cost_per_request_brl,
    )


def _usage_service_label(service: str) -> str:
    labels = {
        "gemini": "Gemini IA",
        "api_football": "API-Football",
        "football_data_org": "football-data.org",
        "odds_api_io": "Odds-API.io",
        "espn": "ESPN Scoreboard",
        "supabase": "Supabase",
        "stripe": "Stripe",
        "mercadopago": "Mercado Pago",
    }
    return labels.get(service, service.replace("_", " ").title())


def _build_stamp() -> str:
    stamp = datetime.fromtimestamp(Path(__file__).stat().st_mtime, timezone.utc)
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M")


def _initials(value: str) -> str:
    parts = [part for part in str(value or "").strip().split() if part]
    if not parts:
        return "U"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _sanitize_avatar_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "/assets/", "data:image/")):
        return raw[:600]
    raise HTTPException(status_code=400, detail="URL da foto invalida. Use https://, /assets/ ou data:image/.")


def _validate_email(value: str) -> str:
    clean = value.strip().lower()
    if not EMAIL_RE.match(clean):
        raise HTTPException(status_code=400, detail="Email invalido.")
    return clean


def _page_shell(
    title: str,
    body_html: str,
    extra_script: str = "",
    description: str | None = None,
    canonical_path: str | None = None,
) -> str:
    settings = _settings()
    desc = description or (
        "APEXGOL AI é uma central quantitativa de inteligência esportiva com scanner, odds, "
        "backtesting, risco, Telegram e explicabilidade IA."
    )
    site_url = (settings.website_url or "").rstrip("/")
    canonical_url = f"{site_url}{canonical_path or ''}" if site_url else canonical_path or "/"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{_esc(desc)}">
  <link rel="canonical" href="{_esc(canonical_url)}">
  <meta property="og:title" content="{_esc(title)}">
  <meta property="og:description" content="{_esc(desc)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{_esc(canonical_url)}">
  <meta property="og:image" content="{_esc(site_url)}/assets/logo-apexgol-mark.svg">
  <meta name="keywords" content="inteligência esportiva, scanner quantitativo, IA esportiva, odds, análise estatística, backtesting, Telegram">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{_esc(title)}">
  <meta name="twitter:description" content="{_esc(desc)}">
  <link rel="icon" href="/assets/logo-apexgol-mark.svg" type="image/svg+xml">
  <title>{_esc(title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg:#0b0e11; --panel:#11161d; --line:#2a3441; --txt:#ecf1f7; --muted:#9eacbf;
      --gold:#f5c842; --green:#00c278; --red:#ff5f66; --blue:#5a95ff;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--txt); }}
    a {{ color:#8bc4ff; text-decoration:none; }}
    .top {{ position:sticky; top:0; z-index:2; border-bottom:1px solid #1d2530; background:rgba(8,11,16,.92); backdrop-filter:blur(14px); }}
    .topin {{ max-width:1180px; margin:0 auto; display:flex; align-items:center; justify-content:space-between; padding:14px 16px; gap:16px; }}
    .brand {{ align-items:center; display:flex; font-weight:900; gap:8px; letter-spacing:0; color:var(--gold); font-size:19px; }}
    .brand::before {{
      background:url('/assets/logo-apexgol-mark.svg') center/cover no-repeat;
      border:1px solid #2d3b50;
      border-radius:6px;
      content:"";
      display:inline-block;
      height:20px;
      width:20px;
    }}
    .nav {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .nav-scroll {{ max-width:100%; overflow-x:auto; padding-bottom:2px; scrollbar-width:none; }}
    .nav-scroll::-webkit-scrollbar {{ display:none; }}
    .btn {{
      align-items:center;
      border:1px solid #344254;
      background:linear-gradient(180deg, #182435, #111824);
      color:var(--txt);
      border-radius:8px;
      display:inline-flex;
      justify-content:center;
      padding:10px 14px;
      font-weight:800;
      letter-spacing:0;
      line-height:1.1;
      min-height:42px;
      text-align:center;
      white-space:nowrap;
      cursor:pointer;
      transition:transform .16s ease, box-shadow .2s ease, border-color .2s ease;
      box-shadow:0 6px 14px rgba(4,10,20,.32);
    }}
    .btn:hover {{ transform:translateY(-1px); border-color:#4e6682; box-shadow:0 10px 18px rgba(4,10,20,.44); }}
    .btn:focus-visible {{ outline:2px solid #7fc1ff; outline-offset:2px; }}
    .btn.primary {{
      background:linear-gradient(180deg, #f7d866, #d5ad38);
      color:#111;
      border-color:#cfa52f;
      box-shadow:0 8px 18px rgba(212,168,52,.34);
    }}
    .btn.primary:hover {{ border-color:#f0cf5f; box-shadow:0 12px 22px rgba(212,168,52,.46); }}
    .btn.green {{ background:#10211c; border-color:#1f5d48; color:#8ff0c7; }}
    .btn.red {{ background:#2a1416; border-color:#673036; color:#ffb8bc; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:16px; }}
    .hero {{
      min-height:86vh;
      display:flex;
      align-items:center;
      padding:14vh 0 7vh;
      position:relative;
      overflow:hidden;
      background:
        linear-gradient(90deg, rgba(5,8,12,.98) 0%, rgba(8,12,17,.86) 44%, rgba(6,9,14,.72) 100%),
        linear-gradient(180deg, rgba(0,194,120,.12), rgba(245,200,66,.08)),
        url('https://images.unsplash.com/photo-1522778119026-d647f0596c20?auto=format&fit=crop&w=1800&q=82') center/cover no-repeat;
      border-bottom:1px solid #202a37;
    }}
    .hero::before {{
      content:"";
      position:absolute;
      inset:0;
      background-image:
        linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
      background-size:28px 28px;
      mask-image:linear-gradient(180deg, rgba(0,0,0,.75), transparent 78%);
      pointer-events:none;
    }}
    .hero-grid {{ position:relative; display:grid; grid-template-columns:minmax(0,1.04fr) minmax(360px,.96fr); gap:34px; align-items:center; }}
    .hero-copy {{ min-width:0; }}
    .hero h1 {{ margin:18px 0 14px; font-size:clamp(42px,7vw,84px); line-height:.94; max-width:880px; text-wrap:balance; }}
    .hero p {{ margin:0; color:#d3ddeb; font-size:clamp(16px,2.2vw,22px); max-width:720px; }}
    .hero-actions {{ margin-top:18px; display:flex; gap:10px; flex-wrap:wrap; }}
    .status-pill {{
      align-items:center; border:1px solid #26384d; border-radius:999px; background:rgba(11,18,26,.72);
      color:#aebdd0; display:inline-flex; font-size:11px; font-weight:900; gap:8px; letter-spacing:1px; padding:7px 11px; text-transform:uppercase;
    }}
    .status-dot {{ background:var(--green); border-radius:999px; box-shadow:0 0 12px rgba(0,194,120,.9); height:7px; width:7px; }}
    .hero-word {{ color:var(--green); }}
    .hero-word.gold {{ color:var(--gold); }}
    .hud {{
      background:rgba(11,16,23,.78);
      border:1px solid rgba(96,125,157,.42);
      border-radius:8px;
      box-shadow:0 28px 70px rgba(0,0,0,.46);
      min-height:420px;
      padding:18px;
      position:relative;
    }}
    .hud::after {{ content:""; position:absolute; inset:8px; border:1px solid rgba(255,255,255,.035); border-radius:6px; pointer-events:none; }}
    .hud-head {{ align-items:center; display:flex; justify-content:space-between; gap:12px; margin-bottom:18px; }}
    .hud-label {{ color:#748297; font-size:11px; font-weight:900; letter-spacing:1.6px; text-transform:uppercase; }}
    .market-card {{ border:1px solid #26384d; border-radius:8px; background:rgba(10,16,24,.82); padding:12px; margin-bottom:10px; }}
    .market-line {{ align-items:center; display:flex; justify-content:space-between; gap:10px; }}
    .metric-bar {{ background:#232b35; border-radius:999px; height:7px; overflow:hidden; margin-top:9px; }}
    .metric-bar span {{ background:linear-gradient(90deg, var(--green), var(--gold)); display:block; height:100%; }}
    .signal-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:14px; }}
    .signal-mini {{ border:1px solid #26384d; border-radius:8px; background:#0b121a; padding:11px; }}
    .signal-mini strong {{ display:block; font-size:24px; line-height:1.1; }}
    .callout {{ border-left:3px solid var(--green); background:rgba(0,194,120,.1); margin-top:12px; padding:10px 12px; font-size:13px; font-weight:800; }}
    .ticker {{ border-top:1px solid #202a37; border-bottom:1px solid #202a37; overflow:hidden; background:#070a0e; }}
    .ticker-track {{ animation:ticker 28s linear infinite; display:flex; gap:28px; min-width:max-content; padding:12px 0; }}
    .ticker-track span {{ color:#a9b8ca; font-size:12px; font-weight:900; letter-spacing:1px; text-transform:uppercase; }}
    @keyframes ticker {{ from {{ transform:translateX(0); }} to {{ transform:translateX(-50%); }} }}
    .section {{ margin-top:22px; }}
    .section.big {{ margin-top:78px; }}
    .title {{ margin:0 0 10px; font-size:18px; }}
    .display-title {{ font-size:clamp(30px,4.8vw,56px); line-height:1; margin:0 0 12px; text-align:center; text-wrap:balance; }}
    .accent-red {{ color:var(--red); }}
    .accent-gold {{ color:var(--gold); }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .grid {{ display:grid; gap:10px; }}
    .g3 {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .g2 {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    .card {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:14px; }}
    .feature-card {{ min-height:190px; padding:20px; }}
    .feature-card h3 {{ font-size:26px; margin:14px 0 10px; }}
    .hero-stack {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-top:18px;
    }}
    .stack-pill {{
      border:1px solid #28415b;
      border-radius:999px;
      background:rgba(9,15,22,.72);
      color:#d3e6fb;
      font-size:12px;
      font-weight:900;
      letter-spacing:.4px;
      padding:8px 12px;
    }}
    .hud-actions {{ display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); margin-top:14px; }}
    .hud-actions .signal-mini strong {{ font-size:18px; }}
    .story-grid {{ display:grid; gap:12px; grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .story-step {{
      border:1px solid #26384d;
      border-radius:8px;
      background:linear-gradient(180deg, rgba(15,22,32,.96), rgba(8,12,18,.96));
      min-height:220px;
      padding:20px;
      position:relative;
      overflow:hidden;
    }}
    .story-step::after {{
      content:"";
      position:absolute;
      inset:auto -30px -30px auto;
      width:120px;
      height:120px;
      background:radial-gradient(circle, rgba(0,194,120,.14), transparent 70%);
      pointer-events:none;
    }}
    .story-step h3 {{ font-size:28px; margin:12px 0 8px; text-wrap:balance; }}
    .step-index {{
      color:#7fb6ff;
      font-size:12px;
      font-weight:900;
      letter-spacing:1px;
      text-transform:uppercase;
    }}
    .module-grid {{ display:grid; gap:12px; grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .module-card {{
      border:1px solid #2a3441;
      border-radius:8px;
      background:linear-gradient(180deg, rgba(15,22,32,.98), rgba(8,12,18,.98));
      display:grid;
      gap:12px;
      min-height:220px;
      padding:18px;
    }}
    .module-head {{ align-items:flex-start; display:flex; justify-content:space-between; gap:12px; }}
    .module-card h3 {{ font-size:24px; line-height:1.02; margin:0; text-wrap:balance; }}
    .module-list {{ display:grid; gap:8px; margin:0; padding:0; list-style:none; }}
    .module-list li {{
      border:1px solid #26384d;
      border-radius:8px;
      background:#0b121a;
      color:#d2deeb;
      font-size:13px;
      font-weight:700;
      padding:10px 12px;
    }}
    .module-kpi-row {{ display:grid; gap:10px; grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .module-kpi {{
      border:1px solid #26384d;
      border-radius:8px;
      background:#0b121a;
      padding:12px;
    }}
    .module-kpi strong {{ display:block; font-size:24px; line-height:1; margin-top:6px; }}
    .module-banner {{
      border:1px solid #2a3441;
      border-radius:8px;
      background:
        linear-gradient(120deg, rgba(0,194,120,.12), rgba(90,149,255,.08) 48%, rgba(245,200,66,.08)),
        #0d1117;
      display:grid;
      gap:18px;
      grid-template-columns:minmax(0,.95fr) minmax(320px,1fr);
      padding:22px;
    }}
    .module-banner h3 {{ font-size:34px; line-height:1; margin:10px 0; text-wrap:balance; }}
    .module-shell {{
      border:1px solid #26384d;
      border-radius:8px;
      background:#0b121a;
      overflow:hidden;
    }}
    .module-shell-top {{
      align-items:center;
      border-bottom:1px solid #1d2734;
      display:flex;
      gap:8px;
      justify-content:space-between;
      padding:12px 14px;
    }}
    .module-shell-grid {{
      display:grid;
      gap:10px;
      grid-template-columns:1.1fr .9fr;
      padding:14px;
    }}
    .proof-wall {{
      display:grid;
      gap:16px;
      grid-template-columns:1.08fr .92fr;
    }}
    .preview-card {{
      border:1px solid #2a3441;
      border-radius:8px;
      background:linear-gradient(180deg, rgba(15,22,32,.98), rgba(8,12,18,.98));
      overflow:hidden;
    }}
    .preview-top {{
      align-items:center;
      border-bottom:1px solid #1d2734;
      display:flex;
      gap:10px;
      justify-content:space-between;
      padding:12px 14px;
    }}
    .window-dots {{ display:flex; gap:6px; }}
    .window-dots span {{
      width:9px;
      height:9px;
      border-radius:999px;
      background:#334559;
      display:block;
    }}
    .window-dots span:first-child {{ background:#ff6b6f; }}
    .window-dots span:nth-child(2) {{ background:#f5c842; }}
    .window-dots span:nth-child(3) {{ background:#00c278; }}
    .preview-body {{ padding:14px; display:grid; gap:12px; }}
    .preview-grid {{
      display:grid;
      gap:10px;
      grid-template-columns:1.08fr .92fr;
      align-items:start;
    }}
    .preview-panel {{
      border:1px solid #243446;
      border-radius:8px;
      background:#0b121a;
      padding:12px;
    }}
    .preview-list {{ display:grid; gap:8px; }}
    .preview-item {{
      border:1px solid #243446;
      border-radius:8px;
      background:#0f1823;
      display:grid;
      gap:8px;
      grid-template-columns:minmax(0,1fr) auto;
      padding:10px;
    }}
    .preview-item strong {{ font-size:16px; line-height:1.08; }}
    .odd-row {{ display:grid; gap:8px; grid-template-columns:repeat(3,minmax(0,1fr)); margin-top:8px; }}
    .odd-pill {{
      border:1px solid #2b425d;
      border-radius:8px;
      background:#111a25;
      color:#e8eef6;
      font-size:13px;
      font-weight:900;
      padding:9px;
      text-align:center;
    }}
    .signal-strip {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .signal-strip span {{
      border:1px solid #2b425d;
      border-radius:999px;
      background:#0f1823;
      color:#cfe1f4;
      font-size:12px;
      font-weight:800;
      padding:7px 10px;
    }}
    .mini-proof-grid {{ display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); }}
    .mini-proof {{
      border:1px solid #243446;
      border-radius:8px;
      background:#0b121a;
      min-height:148px;
      padding:12px;
    }}
    .mini-proof h4 {{ font-size:19px; margin:8px 0; }}
    .bar-rail {{ background:#1c2733; border-radius:999px; height:8px; overflow:hidden; margin-top:8px; }}
    .bar-rail span {{ display:block; height:100%; background:linear-gradient(90deg, var(--green), var(--gold)); }}
    .shell-list {{ display:grid; gap:8px; }}
    .shell-line {{
      align-items:center;
      border:1px solid #243446;
      border-radius:8px;
      display:grid;
      gap:10px;
      grid-template-columns:minmax(0,1fr) auto auto;
      padding:10px;
    }}
    .shell-metrics {{ display:grid; gap:8px; }}
    .shell-metric {{
      border:1px solid #243446;
      border-radius:8px;
      background:#0f1823;
      padding:12px;
    }}
    .shell-metric strong {{ display:block; font-size:28px; line-height:1; margin-top:8px; }}
    .cta-panel {{
      border:1px solid #2a3441;
      border-radius:8px;
      background:linear-gradient(180deg, rgba(16,25,36,.98), rgba(9,14,21,.98));
      display:grid;
      gap:18px;
      grid-template-columns:minmax(0,.95fr) minmax(300px,.85fr);
      padding:24px;
    }}
    .cta-panel h3 {{ font-size:36px; line-height:1; margin:8px 0; text-wrap:balance; }}
    .cta-proof {{ display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); }}
    .cta-proof .mini strong {{ display:block; font-size:26px; margin-top:8px; }}
    .feature-tag {{ border:1px solid #2f445d; border-radius:999px; color:#a7d9ff; display:inline-flex; font-size:11px; font-weight:900; letter-spacing:1px; padding:5px 9px; text-transform:uppercase; }}
    .plan-intel {{
      border:1px solid #2a3441;
      border-radius:8px;
      background:linear-gradient(180deg, rgba(16,25,36,.96), rgba(9,14,21,.96));
      display:grid;
      gap:18px;
      grid-template-columns:minmax(0,.82fr) minmax(360px,1fr);
      margin-bottom:18px;
      padding:24px;
    }}
    .plan-intel h3 {{ font-size:30px; line-height:1.05; margin:10px 0; text-wrap:balance; }}
    .plan-scope-grid {{ display:grid; gap:10px; grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .plan-scope {{
      border:1px solid #26384d;
      border-radius:8px;
      background:#0b121a;
      display:grid;
      gap:8px;
      min-height:156px;
      padding:14px;
    }}
    .plan-scope strong {{ font-size:18px; }}
    .plan-note {{
      border-left:3px solid var(--gold);
      color:#c8d4e3;
      font-size:13px;
      font-weight:800;
      line-height:1.45;
      margin-top:12px;
      padding:8px 0 8px 12px;
    }}
    .opportunity {{
      background:radial-gradient(circle at 72% 24%, rgba(0,194,120,.18), transparent 32%), #0d1117;
      border:1px solid #2a3441;
      border-radius:8px;
      display:grid;
      gap:18px;
      grid-template-columns:minmax(0,.9fr) minmax(320px,1fr);
      padding:26px;
    }}
    .op-panel {{ border:1px solid #304155; border-radius:8px; background:rgba(8,13,20,.86); padding:18px; }}
    .op-row {{ align-items:center; border-bottom:1px solid #202a37; display:flex; justify-content:space-between; gap:14px; padding:12px 0; }}
    .op-row:last-child {{ border-bottom:0; }}
    .price-row {{ align-items:end; display:flex; gap:8px; }}
    .price-row strong {{ font-size:38px; line-height:1; }}
    .mini {{ border:1px solid var(--line); border-radius:8px; background:#0d141e; padding:12px; }}
    .kpi {{ font-size:29px; font-weight:900; }}
    .fantasy-board {{ display:grid; gap:12px; grid-template-columns:360px minmax(0,1fr); align-items:start; }}
    .fantasy-list {{ display:grid; gap:8px; }}
    .fantasy-player {{
      align-items:center; border:1px solid #26384d; border-radius:8px; background:#0c141e;
      display:grid; gap:8px; grid-template-columns:54px minmax(0,1fr) 74px; padding:10px;
    }}
    .pos-pill {{ border:1px solid #405875; border-radius:999px; color:#b9dcff; font-size:12px; font-weight:900; padding:5px 7px; text-align:center; }}
    .player-name {{ font-weight:900; overflow-wrap:anywhere; }}
    .player-meta {{ color:var(--muted); font-size:12px; }}
    .score-box {{ text-align:right; }}
    .fantasy-opportunities {{ display:grid; gap:10px; max-height:420px; overflow:auto; padding-right:4px; }}
    .fantasy-opportunity {{ border:1px solid #26384d; border-radius:8px; background:#0c141e; display:grid; gap:5px; padding:10px; }}
    .profile-row {{ align-items:flex-start; display:flex; gap:14px; }}
    .avatar {{
      align-items:center;
      background:linear-gradient(180deg,#1f3249,#15253a);
      border:1px solid #3b5674;
      border-radius:999px;
      color:#dcecff;
      display:flex;
      font-size:20px;
      font-weight:900;
      height:64px;
      justify-content:center;
      overflow:hidden;
      width:64px;
    }}
    .avatar img {{ display:block; height:100%; object-fit:cover; width:100%; }}
    .profile-fields {{ flex:1; min-width:0; }}
    label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    input, select, textarea {{
      width:100%; background:#0d131b; color:var(--txt); border:1px solid #2e3a4a; border-radius:8px;
      padding:10px; font:inherit;
    }}
    textarea {{ min-height:100px; resize:vertical; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ padding:8px; border-bottom:1px solid #22303f; text-align:left; vertical-align:top; }}
    th {{ color:#bbcbde; font-weight:700; background:#111a24; }}
    .good {{ color:var(--green); }}
    .warn {{ color:var(--gold); }}
    .bad {{ color:var(--red); }}
    .notice {{ min-height:20px; margin-top:8px; font-size:13px; }}
    .ai-fab {{
      position:fixed; right:18px; bottom:18px; z-index:40;
      width:56px; height:56px; border-radius:999px; border:1px solid #35506d;
      background:radial-gradient(circle at 30% 30%, #6ec5ff, #16406f);
      color:#fff; font-weight:900; box-shadow:0 8px 30px rgba(10,60,110,.5);
      cursor:pointer;
    }}
    .ai-fab::after {{
      content:''; position:absolute; inset:-4px; border-radius:999px; border:1px solid rgba(110,197,255,.35);
      animation:pulse 2.2s infinite;
    }}
    @keyframes pulse {{ 0%{{transform:scale(1);opacity:1;}} 100%{{transform:scale(1.22);opacity:0;}} }}
    .ai-panel {{
      position:fixed; right:18px; bottom:86px; width:min(380px, calc(100vw - 26px));
      background:#0f1723; border:1px solid #2c4360; border-radius:8px; padding:12px; z-index:39; display:none;
      box-shadow:0 18px 40px rgba(0,0,0,.35);
    }}
    .ai-panel.open {{ display:block; }}
    .ai-panel textarea {{ min-height:76px; }}
    @media (max-width:960px) {{
      .g3, .g2 {{ grid-template-columns:1fr; }}
      .fantasy-board {{ grid-template-columns:1fr; }}
      .hero {{ padding:15vh 0 7vh; min-height:78vh; background-position:58% center; }}
      .hero-grid {{ grid-template-columns:1fr; }}
      .hero h1 {{ font-size:clamp(28px,9vw,42px); max-width:100%; }}
      .hero p {{ font-size:16px; }}
      .hud {{ min-height:auto; }}
      .opportunity {{ grid-template-columns:1fr; padding:18px; }}
      .plan-intel {{ grid-template-columns:1fr; padding:18px; }}
      .plan-scope-grid {{ grid-template-columns:1fr; }}
      .story-grid, .module-grid, .module-kpi-row, .cta-proof, .mini-proof-grid {{ grid-template-columns:1fr; }}
      .module-banner, .cta-panel {{ grid-template-columns:1fr; padding:18px; }}
      .module-shell-grid, .preview-grid, .proof-wall {{ grid-template-columns:1fr; }}
      .preview-item, .shell-line {{ grid-template-columns:1fr; }}
      .module-head, .preview-top {{ align-items:flex-start; flex-direction:column; }}
      .hud-actions {{ grid-template-columns:1fr; }}
      .topin {{ align-items:flex-start; flex-direction:column; }}
    }}
    @media (max-width:520px) {{
      .topin {{ gap:10px; padding:12px 16px; }}
      .nav {{ flex-wrap:nowrap; min-width:max-content; }}
      .nav .btn {{ padding:9px 12px; font-size:14px; }}
      .nav .desktop-only {{ display:none; }}
      .hero {{ min-height:76vh; padding-top:18vh; }}
      .hero-actions {{ display:grid; grid-template-columns:1fr; }}
      .hero-actions .btn {{ width:100%; min-width:0; padding-inline:10px; white-space:normal; }}
      .signal-grid {{ grid-template-columns:1fr; }}
      .odd-row {{ grid-template-columns:1fr; }}
      .op-row {{ align-items:flex-start; flex-direction:column; }}
      .window-dots {{ align-self:flex-start; }}
      .price-row strong {{ font-size:31px; }}
      .ai-fab {{ right:14px; top:116px; bottom:auto; width:48px; height:48px; }}
      .ai-panel {{ right:13px; bottom:76px; max-height:70vh; overflow:auto; }}
    }}
  </style>
</head>
<body>
{body_html}
<button class="ai-fab" type="button" onclick="toggleAiHelp()">IA</button>
<section id="ai-float" class="ai-panel" aria-live="polite">
  <h3 class="title" style="margin-top:0">Assistente ApexGol</h3>
  <p class="muted">Dúvidas rápidas sobre scanner, plano, login, Telegram e configuração.</p>
  <textarea id="ai-float-input" placeholder="Ex: como conecto meu Telegram?"></textarea>
  <button class="btn primary" type="button" onclick="askAiFloat()">Perguntar</button>
  <div id="ai-float-note" class="notice muted"></div>
</section>
{extra_script}
<script>
function toggleAiHelp() {{
  const panel = document.getElementById('ai-float');
  if (!panel) return;
  panel.classList.toggle('open');
}}
async function askAiFloat() {{
  const input = document.getElementById('ai-float-input');
  const note = document.getElementById('ai-float-note');
  if (!input || !note) return;
  const message = input.value.trim();
  if (!message) {{ note.textContent = 'Digite uma pergunta.'; return; }}
  try {{
    note.textContent = 'Processando...';
    const res = await fetch('/api/support-chat', {{
      method:'POST',
      headers:{{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}},
      body:JSON.stringify({{message}})
    }});
    const data = await res.json();
    if (!res.ok) {{
      note.textContent = data.detail || 'Não consegui responder agora.';
      return;
    }}
    note.textContent = data.answer || 'Resposta enviada.';
  }} catch (error) {{
    note.textContent = 'Não consegui conectar ao assistente agora.';
  }}
}}
</script>
</body>
</html>"""


def _auth_js(kind: str) -> str:
    return f"""<script>
async function submitAuth(event) {{
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form).entries());
  const notice = document.getElementById('notice');
  notice.textContent = 'Processando...';
  try {{
    const response = await fetch('{kind}', {{
      method:'POST',
      headers:{{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}},
      body:JSON.stringify(data)
    }});
    const payload = await response.json();
    if (!response.ok) {{
      notice.textContent = payload.detail || payload.message || 'Falha na operação.';
      return;
    }}
    notice.textContent = payload.message || 'Sucesso.';
    window.location.href = payload.redirect || '/app';
  }} catch (error) {{
    notice.textContent = 'Não consegui conectar ao servidor agora.';
  }}
}}
</script>"""


@router.get("/", response_class=HTMLResponse)
def landing() -> str:
    settings = _settings()
    store = _portal_store(settings)
    plans = _plan_catalog(settings, store)
    plan_html = []
    for key in ("starter", "pro", "team"):
        plan = plans[key]
        features = "".join(f"<li>{_esc(feature)}</li>" for feature in plan["features"])
        card_class = "card" if key != "pro" else "card"
        badge = "<div class='feature-tag'>Mais usado</div>" if key == "pro" else ""
        plan_html.append(
            f"<div class='{card_class}'>"
            f"{badge}"
            f"<h3>{_esc(plan['label'])}</h3>"
            f"<div class='kpi'>{_fmt_money(plan['price'])}/mês</div>"
            f"<p class='muted'>7 dias de teste grátis. Cancele quando quiser.</p>"
            f"<ul>{features}</ul>"
            f"<a class='btn primary' href='/signup?plan={key}'>Começar { _esc(plan['label']) }</a>"
            "</div>"
        )
    body = f"""
<header class='top'>
  <div class='topin'>
    <div class='brand'>APEXGOL AI</div>
    <nav class='nav nav-scroll'>
      <a class='btn desktop-only' href='#top'>Início</a>
      <a class='btn desktop-only' href='#plataforma'>Como funciona</a>
      <a class='btn desktop-only' href='#scanner-ia'>Scanner IA</a>
      <a class='btn desktop-only' href='#cerebro-ia'>Cérebro IA</a>
      <a class='btn desktop-only' href='#telegram-analyst'>Telegram Analyst</a>
      <a class='btn desktop-only' href='#planos'>Planos</a>
      <a class='btn' href='/login'>Entrar</a>
    </nav>
  </div>
</header>
<section class='hero' id='top'>
  <div class='wrap hero-grid'>
    <div class='hero-copy'>
      <div class='status-pill'><span class='status-dot'></span>Scanner ao vivo + research skill + aprendizado contínuo</div>
      <h1>APEXGOL AI</h1>
      <h2 class='title'>Central Quantitativa de Inteligência Esportiva</h2>
      <p>Scanner, IA, odds, backtesting, risco e Telegram em uma plataforma feita para apoiar decisões esportivas com método, dados reais e rastreabilidade.</p>
      <div class='hero-stack'>
        <span class='stack-pill'>Scanner ao Vivo</span>
        <span class='stack-pill'>Cérebro IA</span>
        <span class='stack-pill'>Odds e risco</span>
        <span class='stack-pill'>Backtesting</span>
        <span class='stack-pill'>Telegram Analyst</span>
      </div>
      <div class='hero-actions'>
        <a class='btn primary' href='/signup'>Testar grátis por 7 dias</a>
        <a class='btn' href='#scanner-ia'>Ver Scanner em ação</a>
      </div>
    </div>
    <aside class='hud' aria-label='Painel de indicadores ApexGol'>
      <div class='hud-head'>
        <div>
          <div class='hud-label'>APEXGOL QUANT</div>
          <strong>Operação viva + pesquisa auditável</strong>
        </div>
        <div class='status-pill'><span class='status-dot'></span>modo análise</div>
      </div>
      <div class='market-card'>
        <div class='market-line'><span class='muted'>Jogos em leitura</span><strong>08</strong></div>
        <div class='metric-bar'><span style='width:87%'></span></div>
      </div>
      <div class='market-card'>
        <div class='market-line'><span class='muted'>Research Skill</span><strong class='good'>Ativo</strong></div>
        <div class='metric-bar'><span style='width:74%'></span></div>
      </div>
      <div class='market-card'>
        <div class='market-line'><span class='muted'>Governança</span><strong class='warn'>aprovação obrigatória</strong></div>
        <div class='metric-bar'><span style='width:32%'></span></div>
      </div>
      <div class='signal-grid'>
        <div class='signal-mini'><span class='muted'>Backtests</span><strong>Avançados</strong></div>
        <div class='signal-mini'><span class='muted'>IA</span><strong>Explicável</strong></div>
        <div class='signal-mini'><span class='muted'>Memória</span><strong>Histórica</strong></div>
      </div>
      <div class='hud-actions'>
        <div class='signal-mini'><span class='muted'>Decisão clara</span><strong>Entrar / Aguardar / Monitorar</strong></div>
        <div class='signal-mini'><span class='muted'>Risk engine</span><strong>Kelly fracionado</strong></div>
      </div>
      <div class='callout'>Toda hipótese passa por score estatístico, odd justa, EV, risco e revisão posterior. Nada de aposta real automatizada.</div>
    </aside>
  </div>
</section>
<section class='ticker' aria-label='Indicadores do sistema'>
  <div class='ticker-track'>
    <span>Scanner ao Vivo</span><span>Cérebro IA</span><span>Odds e risco</span><span>Backtesting</span><span>Telegram Analyst</span><span>APIs reais</span>
    <span>Scanner ao Vivo</span><span>Cérebro IA</span><span>Odds e risco</span><span>Backtesting</span><span>Telegram Analyst</span><span>APIs reais</span>
  </div>
</section>
<main class='wrap'>
  <section id='plataforma' class='section big module-banner'>
    <div>
      <div class='feature-tag'>Nova camada de produto</div>
      <h3>Da leitura ao vivo ao laboratório quantitativo, sem trocar de sistema.</h3>
      <p class='muted'>Em vez de um painel isolado, o ApexGol agora reúne operação live, memória IA, análise histórica, simulação e aprovação humana em um fluxo só. O operador ganha clareza. O gestor ganha rastreabilidade. A IA ganha contexto real para evoluir.</p>
      <div class='hero-actions'>
        <a class='btn primary' href='/signup'>Testar grátis por 7 dias</a>
        <a class='btn' href='/app/football-analysis'>Explorar analysis</a>
      </div>
    </div>
    <div class='module-shell' aria-label='Preview do ecossistema ApexGol'>
      <div class='module-shell-top'>
        <strong>APEXGOL AI · Quant workspace</strong>
        <div class='status-pill'><span class='status-dot'></span>camadas integradas</div>
      </div>
      <div class='module-shell-grid'>
        <div class='shell-list'>
          <div class='shell-line'><strong>Scanner ao Vivo</strong><span class='feature-tag'>Ao vivo</span><span class='good'>Odds + leitura</span></div>
          <div class='shell-line'><strong>Análise estatística</strong><span class='feature-tag'>Pré-jogo</span><span class='warn'>Poisson + EV</span></div>
          <div class='shell-line'><strong>Backtesting</strong><span class='feature-tag'>Histórico</span><span class='good'>ROI / drawdown</span></div>
          <div class='shell-line'><strong>Cérebro IA</strong><span class='feature-tag'>Memória</span><span>qualidade de dados</span></div>
        </div>
        <div class='shell-metrics'>
          <div class='shell-metric'><span class='muted'>Mercados vivos</span><strong>Gols · Escanteios · Handicap</strong></div>
          <div class='shell-metric'><span class='muted'>Aprendizado</span><strong>revisão pós-jogo + memória longa</strong></div>
        </div>
      </div>
    </div>
  </section>
  <section id='indicadores' class='section big opportunity'>
    <div>
      <div class='feature-tag'>Simulador de oportunidade</div>
      <h2 class='display-title' style='text-align:left'>Simule o impacto de operar sem critério</h2>
      <p class='muted'>A página fala direto com quem opera: o problema não é só acertar, é saber quando não entrar, quando proteger e quando deixar a IA aprender com o histórico.</p>
    </div>
    <div class='op-panel'>
      <div class='feature-tag'>Exemplo simulado</div>
      <div class='op-row'><span class='muted'>Banca operacional</span><div class='price-row'><strong>R$ 1.000</strong></div></div>
      <div class='op-row'><span class='muted'>Entradas ruins evitadas/mês</span><div class='price-row'><strong>12</strong><span class='muted'>alertas</span></div></div>
      <div class='op-row'><span class='muted'>Exposição reduzida</span><div class='price-row'><strong class='accent-gold'>R$ 240</strong></div></div>
      <div class='op-row'><span class='muted'>Impacto da disciplina IA</span><strong class='good'>menos tilt, mais processo</strong></div>
    </div>
  </section>
  <section id='planos' class='section big'>
    <h2 class='display-title'>Seu método não foi feito para depender de <span class='accent-red'>achismo</span>.</h2>
    <p class='muted' style='text-align:center;max-width:760px;margin:0 auto'>A IA organiza sinais, contexto, risco e saída. Você decide com mais clareza e registra tudo para a próxima leitura ficar melhor.</p>
  </section>
  <section class='section big'>
    <div class='story-grid'>
      <article class='story-step'>
        <div class='step-index'>Etapa 01</div>
        <h3>A IA filtra o ruído antes do jogo.</h3>
        <p class='muted'>Watchlist pré-jogo, score mínimo, odd válida, dados de forma, Poisson, EV e risco. O sistema já elimina o que não merece atenção.</p>
      </article>
      <article class='story-step'>
        <div class='step-index'>Etapa 02</div>
        <h3>Quando entra ao vivo, a leitura fica operacional.</h3>
        <p class='muted'>Pressão, escanteios, gols, cartões, momentum, odds e explicação aparecem em uma mesa pronta para decidir com calma.</p>
      </article>
      <article class='story-step'>
        <div class='step-index'>Etapa 03</div>
        <h3>Depois do jogo, a IA aprende com o que realmente aconteceu.</h3>
        <p class='muted'>Backtesting, revisão pós-jogo, memória longa, drift, governança e estratégia em rascunho mantêm o sistema evoluindo sem improviso.</p>
      </article>
    </div>
  </section>
  <section id='scanner-ia' class='section big grid g3'>
    <div class='card feature-card' id='cerebro-ia'>
      <div class='feature-tag'>IA integrada</div>
      <h3>Scanner que prioriza jogo vivo</h3>
      <p class='muted'>Acompanha placar, minuto, pressão, mercado, confiança e risco para destacar onde existe leitura operacional.</p>
    </div>
    <div class='card feature-card'>
      <div class='feature-tag'>Gestão real</div>
      <h3>Entrada, proteção e saída</h3>
      <p class='muted'>Simula dinâmica do jogo, sugere saída por green, perda de edge ou preservação da banca e grava tudo no Supabase.</p>
    </div>
    <div class='card feature-card'>
      <div class='feature-tag'>Cérebro IA</div>
      <h3>Aprendizado visível e auditável</h3>
      <p class='muted'>Mostra maturidade, fontes ativas, sinais, backtests, bloqueios de risco e recomendações sem inventar métrica.</p>
    </div>
    <div class='card feature-card'>
      <div class='feature-tag'>Telegram</div>
      <h3>Analista no canal do operador</h3>
      <p class='muted'>Receba avisos e resumos sem ficar preso ao painel, mantendo o histórico de sinais e decisões rastreável.</p>
    </div>
    <div class='card feature-card'>
      <div class='feature-tag'>Memória IA</div>
      <h3>Aprendizado por resultado</h3>
      <p class='muted'>Cada resultado alimenta leitura futura por mercado, score, risco, confiança e comportamento da banca.</p>
    </div>
    <div class='card feature-card'>
      <div class='feature-tag'>Operação</div>
      <h3>Painel para repetir processo</h3>
      <p class='muted'>Scanner, mercado, ao vivo, entradas, histórico, rankings e comercial no mesmo ambiente.</p>
    </div>
  </section>
  <section id='research' class='section big'>
    <h2 class='display-title'>Research Skill e inteligência quantitativa esportiva.</h2>
    <p class='muted' style='text-align:center;max-width:820px;margin:0 auto 18px'>A camada nova não substitui o que já funciona. Ela amplia histórico, simulações, governança, memória e explicabilidade sobre o ApexGol atual.</p>
    <div class='module-grid'>
      <article class='module-card'>
        <div class='module-head'>
          <div>
            <div class='feature-tag'>Pesquisa</div>
            <h3>Análise estatística</h3>
          </div>
          <span class='status-pill'><span class='status-dot'></span>pré-jogo</span>
        </div>
        <p class='muted'>Média de gols, força ofensiva, força defensiva, casa/fora, Poisson, odd justa, EV e stake sugerida em modo paper.</p>
        <ul class='module-list'>
          <li>Poisson 0x0 até 6x6</li>
          <li>Probabilidade casa / empate / fora</li>
          <li>Over/Under e BTTS</li>
        </ul>
      </article>
      <article class='module-card'>
        <div class='module-head'>
          <div>
            <div class='feature-tag'>Simulação</div>
            <h3>Backtesting avançado</h3>
          </div>
          <span class='status-pill'><span class='status-dot'></span>risk first</span>
        </div>
        <p class='muted'>Antes de confiar em uma regra, o sistema simula série histórica, curva de banca, drawdown e risco de ruína.</p>
        <div class='module-kpi-row'>
          <div class='module-kpi'><span class='muted'>ROI</span><strong>histórico</strong></div>
          <div class='module-kpi'><span class='muted'>Drawdown</span><strong>máximo</strong></div>
          <div class='module-kpi'><span class='muted'>Ruína</span><strong>simulada</strong></div>
        </div>
      </article>
      <article class='module-card'>
        <div class='module-head'>
          <div>
            <div class='feature-tag'>Orquestração</div>
            <h3>Governança + explicabilidade</h3>
          </div>
          <span class='status-pill'><span class='status-dot'></span>supervisão humana</span>
        </div>
        <p class='muted'>Camadas especializadas avaliam contexto, risco, valor e qualidade de dados. Mudanças importantes ficam em rascunho até aprovação.</p>
        <ul class='module-list'>
          <li>Consensus engine com trust score</li>
          <li>Detecção de drift e anomalia</li>
          <li>Rollback e change history</li>
        </ul>
      </article>
    </div>
  </section>
  <section class='section big'>
    <h2 class='display-title'>Veja o ApexGol AI em operação</h2>
    <p class='muted' style='text-align:center;max-width:760px;margin:0 auto 18px'>Cards visuais baseados nos módulos reais do sistema: Scanner ao Vivo, Cérebro IA, Backtesting e Telegram Analyst.</p>
    <div class='proof-wall'>
      <article class='preview-card'>
        <div class='preview-top'>
          <div style='display:flex;align-items:center;gap:10px'>
            <div class='window-dots'><span></span><span></span><span></span></div>
            <strong>Scanner ao Vivo</strong>
          </div>
          <div class='status-pill'><span class='status-dot'></span>scanner vivo</div>
        </div>
        <div class='preview-body'>
          <div class='signal-strip'>
            <span>Ao vivo 03</span>
            <span>Pré 12</span>
            <span>Próxima leitura 20s</span>
            <span>Somente ao vivo</span>
          </div>
          <div class='preview-grid'>
            <div class='preview-panel'>
              <div class='preview-list'>
                <div class='preview-item'>
                  <div>
                    <strong>São Paulo x Bahia</strong>
                    <div class='muted'>21' · 1 x 0 · confiança 76%</div>
                    <div class='odd-row'>
                      <div class='odd-pill'>Casa 1.35</div>
                      <div class='odd-pill'>Empate 4.90</div>
                      <div class='odd-pill'>Fora 8.00</div>
                    </div>
                  </div>
                  <span class='feature-tag'>Gols</span>
                </div>
                <div class='preview-item'>
                  <div>
                    <strong>Flamengo x Vasco</strong>
                    <div class='muted'>22' · monitorar agora · pressão 79 x 64</div>
                    <div class='odd-row'>
                      <div class='odd-pill'>Over 3.5</div>
                      <div class='odd-pill'>Odd 2.26</div>
                      <div class='odd-pill'>Handicap</div>
                    </div>
                  </div>
                  <span class='feature-tag'>Live</span>
                </div>
              </div>
            </div>
            <div class='preview-panel'>
              <div class='feature-tag'>Painel lateral</div>
              <h3 style='font-size:28px;margin:12px 0 8px'>Leitura por mercado</h3>
              <p class='muted'>Pressão, corrida para o gol, leitura IA, cantos, odds e consenso sem sair da mesa operacional.</p>
              <div class='bar-rail'><span style='width:68%'></span></div>
              <div class='signal-strip' style='margin-top:10px'>
                <span>Gols</span>
                <span>Escanteios</span>
                <span>Handicap</span>
                <span>Cartões</span>
              </div>
            </div>
          </div>
        </div>
      </article>
      <div class='mini-proof-grid'>
        <article class='mini-proof'>
          <div class='feature-tag'>Backtesting</div>
          <h4>ROI, drawdown e curva de banca</h4>
          <p class='muted'>Antes de confiar numa regra, o sistema mede desempenho histórico, estabilidade e risco por mercado.</p>
          <p class='muted'>Exemplo simulado no modo paper.</p>
          <div class='bar-rail'><span style='width:74%'></span></div>
        </article>
        <article class='mini-proof' id='telegram-analyst'>
          <div class='feature-tag'>Cérebro IA</div>
          <h4>Maturidade e qualidade dos dados</h4>
          <p class='muted'>A tela mostra fontes ativas, sinais, backtests, bloqueios e recomendações sem inventar métricas.</p>
          <div class='bar-rail'><span style='width:61%'></span></div>
        </article>
        <article class='mini-proof'>
          <div class='feature-tag'>Telegram Analyst</div>
          <h4>Resumo operacional direto</h4>
          <p class='muted'>O operador recebe decisão, motivo, risco e checklist sem expor chaves ou automatizar apostas.</p>
          <div class='bar-rail'><span style='width:53%'></span></div>
        </article>
        <article class='mini-proof'>
          <div class='feature-tag'>Governança</div>
          <h4>Mudanças com aprovação humana</h4>
          <p class='muted'>Estratégias novas entram como rascunho, ficam logadas e só podem ser ativadas com aprovação.</p>
          <div class='bar-rail'><span style='width:88%'></span></div>
        </article>
      </div>
    </div>
  </section>
  <section class='section big'>
    <h2 class='display-title'>Escolha seu plano de operação</h2>
    <p class='muted' style='text-align:center;margin:0 auto 18px;max-width:680px'>Comece testando. Evolua quando precisar de mais memória, prioridade e estrutura comercial.</p>
    <p class='muted' style='text-align:center;margin:-8px auto 18px;max-width:760px'><strong>O ApexGol AI é uma ferramenta estatística de apoio. Não garante lucro e não realiza apostas automáticas.</strong></p>
    <div class='plan-intel'>
      <div>
        <div class='feature-tag'>O que o cliente compra</div>
        <h3>IA por contexto, plano e histórico de operação.</h3>
        <p class='muted'>Cada cliente recebe uma experiência própria dentro do mesmo motor: login, Telegram, preferências, banca, histórico de decisões e simulações ficam ligados à conta dele. Assim a IA responde com base no que aquele operador usa e registra.</p>
        <div class='plan-note'>No Pro, o cliente tem uma camada lógica própria. Não é preciso criar um servidor separado por pessoa: o isolamento acontece por usuário, memória e regras do plano.</div>
      </div>
      <div class='plan-scope-grid' aria-label='Resumo dos planos e inteligência artificial'>
        <div class='plan-scope'>
          <div class='feature-tag'>Starter</div>
          <strong>Leitura essencial</strong>
          <p class='muted'>Scanner, Telegram, histórico básico e respostas de suporte para operar com processo.</p>
        </div>
        <div class='plan-scope'>
          <div class='feature-tag'>Pro</div>
          <strong>IA com memória</strong>
          <p class='muted'>Contexto do cliente, simulações no Supabase, preferências de banca e aprendizado por resultado.</p>
        </div>
        <div class='plan-scope'>
          <div class='feature-tag'>Team</div>
          <strong>Operação em equipe</strong>
          <p class='muted'>Multioperador, visão admin, carteira comercial e IA acompanhando grupos de operação.</p>
        </div>
      </div>
    </div>
    <div class='grid g3'>{''.join(plan_html)}</div>
  </section>
  <section class='section big cta-panel'>
    <div>
      <div class='feature-tag'>Pronto para crescer</div>
      <h3>Comece no scanner e evolua até um laboratório próprio de decisão.</h3>
      <p class='muted'>Você não precisa esperar a estrutura perfeita para começar. O ApexGol já nasce com scanner, memória, Telegram, backtesting e painéis operacionais. Quando quiser subir o nível, a camada de research e governança já está na mesma base.</p>
      <p class='muted'><strong>O ApexGol AI é uma ferramenta estatística de apoio. Não garante lucro e não realiza apostas automáticas.</strong></p>
      <div class='hero-actions'>
        <a class='btn primary' href='/signup'>Testar grátis por 7 dias</a>
        <a class='btn' href='/login'>Entrar no sistema</a>
      </div>
    </div>
    <div class='cta-proof'>
      <div class='mini'><span class='muted'>Ao vivo</span><strong>Scanner</strong><div class='muted'>watchlist, odds, filtros e leitura por mercado</div></div>
      <div class='mini'><span class='muted'>Pesquisa</span><strong>Research Skill</strong><div class='muted'>Poisson, EV, Kelly, histórico e revisão</div></div>
      <div class='mini'><span class='muted'>Simulação</span><strong>Backtesting avançado</strong><div class='muted'>curva de banca, drawdown e cenários</div></div>
      <div class='mini'><span class='muted'>Controle</span><strong>Governança</strong><div class='muted'>aprovação humana, logs e rollback</div></div>
    </div>
  </section>
  <section class='section big card'>
    <h3 class='title'>Aviso importante</h3>
    <p class='muted'>O {_esc(settings.product_name)} é uma ferramenta estatística de apoio. Não garante lucro e não realiza apostas automáticas. As sugestões de entrada são parâmetros de análise e não promessa de resultado. A decisão final é sempre do usuário, que assume integralmente a responsabilidade pelas operações realizadas.</p>
  </section>
</main>
"""
    return _page_shell(
        "APEXGOL AI | Central Quantitativa de Inteligência Esportiva",
        body,
        description="APEXGOL AI une scanner quantitativo, IA esportiva, odds, análise estatística, backtesting, risco e Telegram para apoiar decisões esportivas.",
        canonical_path="/",
    )


@router.get("/robots.txt")
def robots_txt() -> HTMLResponse:
    settings = _settings()
    base = (settings.website_url or "").rstrip("/")
    content = "User-agent: *\nAllow: /\n"
    if base:
        content += f"Sitemap: {base}/sitemap.xml\n"
    return HTMLResponse(content, media_type="text/plain")


@router.get("/favicon.ico")
def favicon() -> RedirectResponse:
    return RedirectResponse("/assets/logo-apexgol-mark.svg", status_code=status.HTTP_308_PERMANENT_REDIRECT)


@router.get("/sitemap.xml")
def sitemap_xml() -> HTMLResponse:
    settings = _settings()
    base = (settings.website_url or "").rstrip("/")
    urls = ["", "/signup", "/login"]
    items = "".join(
        f"<url><loc>{_esc(base + (path or '/'))}</loc><changefreq>weekly</changefreq><priority>{'1.0' if path == '' else '0.7'}</priority></url>"
        for path in urls
        if base
    )
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>"""
    return HTMLResponse(content, media_type="application/xml")


@router.get("/signup", response_class=HTMLResponse)
def signup_page(plan: str | None = None) -> str:
    settings = _settings()
    selected = _safe_plan(plan)
    body = f"""
<header class='top'><div class='topin'><div class='brand'>Cadastro</div><nav class='nav nav-scroll'><a class='btn' href='/'>Início</a><a class='btn' href='/login'>Login</a></nav></div></header>
<main class='wrap'>
  <div class='card' style='max-width:520px;margin:24px auto;'>
    <h2 class='title'>Crie sua conta (teste grátis por 7 dias)</h2>
    <form onsubmit='submitAuth(event)'>
      <label>Nome</label><input name='name' required maxlength='120' />
      <label>Email</label><input name='email' type='email' required />
      <label>Senha (mínimo 8 caracteres)</label><input name='password' type='password' minlength='8' required />
      <label>Plano inicial</label>
      <select name='plan'>
        <option value='starter' {'selected' if selected == 'starter' else ''}>Starter</option>
        <option value='pro' {'selected' if selected == 'pro' else ''}>Pro</option>
        <option value='team' {'selected' if selected == 'team' else ''}>Team</option>
      </select>
      <button class='btn primary' type='submit'>Criar conta</button>
      <div id='notice' class='notice muted'></div>
    </form>
  </div>
</main>
"""
    return _page_shell(f"Cadastro | {settings.product_name}", body, _auth_js("/api/auth/signup"), canonical_path="/signup")


@router.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    settings = _settings()
    body = """
<header class='top'><div class='topin'><div class='brand'>Login</div><nav class='nav nav-scroll'><a class='btn' href='/'>Início</a><a class='btn' href='/signup'>Cadastro</a></nav></div></header>
<main class='wrap'>
  <div class='card' style='max-width:520px;margin:24px auto;'>
    <h2 class='title'>Entrar na plataforma</h2>
    <form onsubmit='submitAuth(event)'>
      <label>Email</label><input name='email' type='email' required />
      <label>Senha</label><input name='password' type='password' required />
      <button class='btn primary' type='submit'>Entrar</button>
      <div id='notice' class='notice muted'></div>
    </form>
    <p class='muted'><a href='/forgot-password'>Esqueci minha senha</a></p>
  </div>
</main>
"""
    return _page_shell(f"Login | {settings.product_name}", body, _auth_js("/api/auth/login"), canonical_path="/login")


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_page() -> str:
    settings = _settings()
    body = """
<header class='top'><div class='topin'><div class='brand'>Recuperar senha</div><nav class='nav'><a class='btn' href='/login'>Voltar</a></nav></div></header>
<main class='wrap'>
  <div class='card' style='max-width:520px;margin:24px auto;'>
    <h2 class='title'>Redefinicao por email</h2>
    <form onsubmit='submitAuth(event)'>
      <label>Email da conta</label><input name='email' type='email' required />
      <button class='btn primary' type='submit'>Enviar link</button>
      <div id='notice' class='notice muted'></div>
    </form>
  </div>
</main>
"""
    return _page_shell(f"Recuperar senha | {settings.product_name}", body, _auth_js("/api/auth/forgot-password"))


@router.get("/reset-password", response_class=HTMLResponse)
def reset_page(token: str | None = None) -> str:
    settings = _settings()
    token_value = token or ""
    body = f"""
<header class='top'><div class='topin'><div class='brand'>Nova senha</div><nav class='nav'><a class='btn' href='/login'>Login</a></nav></div></header>
<main class='wrap'>
  <div class='card' style='max-width:520px;margin:24px auto;'>
    <h2 class='title'>Definir nova senha</h2>
    <form onsubmit='submitAuth(event)'>
      <input type='hidden' name='token' value='{_esc(token_value)}' />
      <label>Token recebido no email</label><input name='token_visible' value='{_esc(token_value)}' oninput="document.querySelector('input[name=token]').value=this.value" />
      <label>Nova senha (minimo 8)</label><input name='password' type='password' minlength='8' required />
      <button class='btn primary' type='submit'>Atualizar senha</button>
      <div id='notice' class='notice muted'></div>
    </form>
  </div>
</main>
"""
    return _page_shell(f"Redefinir senha | {settings.product_name}", body, _auth_js("/api/auth/reset-password"))


@router.get("/app", response_class=HTMLResponse)
def app_portal(request: Request, user: dict[str, Any] = Depends(_require_user)) -> str:
    settings = _settings()
    store = _portal_store(settings)
    plans = _plan_catalog(settings, store)
    store.seed_ai_skills(DEFAULT_AI_SUPPORT_SKILLS)
    prefs = store.get_preferences(int(user["id"]))
    state_obj = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    telegram_status = _telegram_status(settings, prefs, state_obj)
    ai_memory_status = _ai_memory_status(settings, store)
    logs = store.list_support_logs(int(user["id"]), limit=20)
    payments = store.list_payments(int(user["id"]), limit=10)
    chat_rows = "".join(
        f"<tr><td>{_esc(row['created_at'])[:16]}</td><td>{_esc(row['role'])}</td><td>{_esc(row['message'])}</td></tr>"
        for row in logs
    ) or "<tr><td colspan='3'>Sem conversas ainda.</td></tr>"
    payment_rows = "".join(
        f"<tr><td>{_esc(row['created_at'])[:16]}</td><td>{_esc(row['gateway'])}</td><td>{_fmt_money(row['amount_brl'])}</td><td>{_esc(row['status'])}</td></tr>"
        for row in payments
    ) or "<tr><td colspan='4'>Nenhum pagamento registrado.</td></tr>"
    trial_days = _trial_left_days(user)
    admin_link = "<a class='btn' href='/admin/users'>Painel Admin</a>" if int(user.get("is_admin") or 0) else ""
    avatar_url = str(user.get("avatar_url") or "").strip()
    avatar_label = _initials(str(user.get("name") or "Usuario"))
    avatar_block = (
        f"<img src='{_esc(avatar_url)}' alt='Foto do usuario' id='profile-avatar-img' />"
        if avatar_url
        else f"<span id='profile-avatar-fallback'>{_esc(avatar_label)}</span>"
    )
    # Product focus: legacy auxiliary routes remain available internally,
    # but they are hidden from primary navigation to keep SaaS UX focused.
    body = f"""
<header class='top'><div class='topin'><div class='brand'>Area do Cliente</div><nav class='nav nav-scroll'><a class='btn' href='/dashboard'>Dashboard</a><a class='btn' href='/dashboard#scanner'>Scanner</a><a class='btn' href='/cerebro-ia'>Cérebro IA</a><a class='btn' href='/app/backtesting-lab'>Backtesting</a><a class='btn' href='#telegram'>Telegram</a><a class='btn' href='#perfil'>Configurações</a>{admin_link}<button class='btn red' onclick='logoutNow()'>Sair</button></nav></div></header>
<main class='wrap'>
  <section class='grid g3'>
    <div class='card'><div class='muted'>Conta</div><div class='kpi'>{_esc(user.get('name'))}</div><div class='muted'>{_esc(user.get('email'))}</div></div>
    <div class='card'><div class='muted'>Plano atual</div><div class='kpi'>{_esc(str(user.get('plan', '-')).upper())}</div><div class='muted'>Status: {_esc(user.get('status'))}</div></div>
    <div class='card'><div class='muted'>Teste gratis restante</div><div class='kpi'>{trial_days} dias</div><div class='muted'>Proxima cobranca: {_esc(user.get('next_due_at') or '-')}</div></div>
  </section>
  <section class='section card' id='perfil'>
    <h3 class='title'>Meu perfil</h3>
    <div class='profile-row'>
      <div class='avatar' id='profile-avatar'>{avatar_block}</div>
      <div class='profile-fields'>
        <label>Nome</label><input id='profile-name' value='{_esc(user.get("name") or "")}' maxlength='120' />
        <label>Email</label><input id='profile-email' type='email' value='{_esc(user.get("email") or "")}' />
        <label>URL da foto (opcional)</label><input id='profile-avatar-url' value='{_esc(avatar_url)}' placeholder='https://...' />
        <label>Nova senha (opcional, minimo 8)</label><input id='profile-password' type='password' minlength='8' />
        <button class='btn primary' onclick='saveProfile()'>Atualizar perfil</button>
        <div id='profile-note' class='notice muted'></div>
      </div>
    </div>
  </section>
  <section class='section grid g2' id='telegram'>
    <div class='card'>
      <h3 class='title'>Pagamento e assinatura</h3>
      <label>Trocar plano</label>
      <select id='plan'>
        <option value='starter'>Starter ({_fmt_money(plans["starter"]["price"])})</option>
        <option value='pro'>Pro ({_fmt_money(plans["pro"]["price"])})</option>
        <option value='team'>Team ({_fmt_money(plans["team"]["price"])})</option>
      </select>
      <button class='btn primary' onclick='createCheckout()'>Gerar checkout</button>
      <div id='billing-note' class='notice muted'></div>
      <table><thead><tr><th>Data</th><th>Gateway</th><th>Valor</th><th>Status</th></tr></thead><tbody>{payment_rows}</tbody></table>
    </div>
    <div class='card'>
      <h3 class='title'>Agente de suporte</h3>
      <p class='muted'>Resolvo login, scanner, importacao, cobranca e duvidas basicas da plataforma.</p>
      <textarea id='support-input' placeholder='Escreva sua duvida'></textarea>
      <button class='btn green' onclick='sendSupport()'>Perguntar ao agente</button>
      <div id='support-note' class='notice muted'></div>
      <table><thead><tr><th>Data</th><th>Role</th><th>Mensagem</th></tr></thead><tbody id='support-log'>{chat_rows}</tbody></table>
    </div>
  </section>
  <section class='section grid g2'>
    <div class='card'>
      <h3 class='title'>Preferencias de scanner/notificacao</h3>
      <label><input id='pref-scan-enabled' type='checkbox' {'checked' if int(prefs.get("scan_enabled") or 0) else ''} /> Scanner ativo para meu usuario</label>
      <label>Scanner sem jogo ativo (segundos)</label>
      <input id='pref-idle' type='number' min='30' max='1800' value='{int(prefs.get("idle_scan_seconds") or 60)}' />
      <label>Scanner com jogo selecionado (segundos)</label>
      <input id='pref-active' type='number' min='60' max='1800' value='{int(prefs.get("active_scan_seconds") or 120)}' />
      <label>Chat ID Telegram (para notificacoes)</label>
      <input id='pref-chatid' value='{_esc(prefs.get("telegram_chat_id") or "")}' placeholder='Ex: 123456789' />
      <label><input id='pref-enabled' type='checkbox' {'checked' if int(prefs.get("telegram_enabled") or 0) else ''} /> Quero notificacoes no Telegram</label>
      <button class='btn primary' onclick='savePrefs()'>Salvar preferencias</button>
      <div id='prefs-note' class='notice muted'></div>
      {_telegram_status_html(telegram_status)}
      {_ai_memory_status_html(ai_memory_status)}
      <p class='muted'>Guia rapido: abra o bot, use <strong>/chatid</strong>, copie o numero e cole aqui.</p>
      <p class='muted'>Se desativar o scanner, voce pausa alertas para seu usuario sem afetar os demais.</p>
    </div>
    <div class='card'>
      <h3 class='title'>Radar de futebol</h3>
      <p class='muted'>Brasil primeiro, depois mundo. Mercados principais: gols, escanteios, 1x2 e handicap asiatica. A IA prioriza leitura de pressao, minuto, odds e contexto do placar.</p>
      <p class='muted'>Se houver sequencia de red acima do limite, o sistema nao bloqueia: ele alerta disciplina para reduzir risco e revisar estrategia.</p>
    </div>
  </section>
</main>
"""
    script = """<script>
async function logoutNow() {
  await fetch('/api/auth/logout', {method:'POST', headers:{'X-Requested-With':'XMLHttpRequest'}});
  window.location.href = '/';
}
function refreshAvatarPreview(url, name) {
  const box = document.getElementById('profile-avatar');
  if (!box) return;
  const clean = (url || '').trim();
  if (clean) {
    box.innerHTML = `<img src="${clean}" alt="Foto do usuario" id="profile-avatar-img" />`;
    return;
  }
  const words = (name || '').trim().split(/\\s+/).filter(Boolean);
  const initials = words.length > 1 ? (words[0][0] + words[words.length - 1][0]).toUpperCase() : ((words[0] || 'U')[0] || 'U').toUpperCase();
  box.innerHTML = `<span id="profile-avatar-fallback">${initials}</span>`;
}
async function saveProfile() {
  const note = document.getElementById('profile-note');
  const payload = {
    name: document.getElementById('profile-name').value.trim(),
    email: document.getElementById('profile-email').value.trim(),
    avatar_url: document.getElementById('profile-avatar-url').value.trim(),
    new_password: document.getElementById('profile-password').value
  };
  if (!payload.name || !payload.email) {
    note.textContent = 'Nome e email sao obrigatorios.';
    return;
  }
  note.textContent = 'Atualizando perfil...';
  const res = await fetch('/api/user/profile', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || 'Falha ao atualizar perfil.'; return; }
  note.textContent = 'Perfil atualizado com sucesso.';
  document.getElementById('profile-password').value = '';
  refreshAvatarPreview(data.user.avatar_url || '', data.user.name || payload.name);
}
async function createCheckout() {
  const note = document.getElementById('billing-note');
  note.textContent = 'Gerando checkout...';
  const plan = document.getElementById('plan').value;
  const res = await fetch('/api/billing/checkout', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify({plan})
  });
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || data.message || 'Falha ao criar checkout.'; return; }
  note.innerHTML = `Checkout criado (${data.gateway}). <a href="${data.url}" target="_blank" rel="noopener">Abrir pagamento</a>`;
}
async function sendSupport() {
  const note = document.getElementById('support-note');
  const text = document.getElementById('support-input').value.trim();
  if (!text) { note.textContent = 'Digite uma pergunta primeiro.'; return; }
  note.textContent = 'Analisando...';
  const res = await fetch('/api/support-chat', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify({message:text})
  });
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || 'Falha no suporte.'; return; }
  note.textContent = 'Resposta gerada.';
  document.getElementById('support-input').value = '';
  const log = document.getElementById('support-log');
  log.innerHTML = data.logs_html;
}
async function savePrefs() {
  const note = document.getElementById('prefs-note');
  note.textContent = 'Salvando...';
  const payload = {
    scan_enabled: document.getElementById('pref-scan-enabled').checked,
    idle_scan_seconds: Number(document.getElementById('pref-idle').value || 60),
    active_scan_seconds: Number(document.getElementById('pref-active').value || 120),
    telegram_chat_id: document.getElementById('pref-chatid').value.trim(),
    telegram_enabled: document.getElementById('pref-enabled').checked
  };
  const res = await fetch('/api/user/preferences', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || 'Falha ao salvar.'; return; }
  note.textContent = 'Preferencias salvas com sucesso.';
  renderTelegramStatus(data.telegram_status);
  renderAiMemoryStatus(data.ai_memory_status);
}
function renderTelegramStatus(status) {
  const box = document.getElementById('telegram-status');
  if (!box || !status) return;
  const blockers = Array.isArray(status.blockers) ? status.blockers.filter(Boolean) : [];
  box.className = status.ready ? 'notice ok' : 'notice muted';
  const extra = blockers.length ? `<div class="muted">${blockers.join(' | ')}</div>` : '';
  box.innerHTML = `<strong>Telegram:</strong> ${status.summary || '-'}${extra}`;
}
function renderAiMemoryStatus(status) {
  const box = document.getElementById('ai-memory-status');
  if (!box || !status) return;
  box.className = status.supabase_enabled ? 'notice ok' : 'notice muted';
  box.innerHTML = `<strong>Memoria IA:</strong> ${status.summary || '-'}`;
}
</script>"""
    return _page_shell("Portal do Cliente", body, script)


@router.get("/fantasy-ia", include_in_schema=False)
def fantasy_ia_page(user: dict[str, Any] = Depends(_require_user)) -> RedirectResponse:
    # Legacy visual module hidden to keep the ApexGol AI SaaS identity focused.
    return RedirectResponse("/dashboard", status_code=303)

@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(_: dict[str, Any] = Depends(_require_admin)) -> str:
    settings = _settings()
    body = """
<header class='top'><div class='topin'><div class='brand'>Admin APEXGOL AI</div><nav class='nav'><a class='btn' href='/app'>Area cliente</a><a class='btn' href='/dashboard'>Dashboard</a></nav></div></header>
<main class='wrap'>
  <section class='card'>
    <h2 class='title'>Clientes, cobranca e cancelamento</h2>
    <div class='muted'>Acoes: cobrar ciclo, gerar checkout, mudar plano, cancelar e reativar.</div>
    <label style='display:inline-flex;gap:8px;align-items:center;margin-top:8px;'>
      <input id='show-canceled' type='checkbox' />
      Mostrar clientes cancelados
    </label>
    <div id='admin-note' class='notice muted'></div>
    <div style='overflow:auto'><table><thead><tr><th>ID</th><th>Cliente</th><th>Plano</th><th>Status</th><th>Mensalidade</th><th>Trial</th><th>Vencimento</th><th>Acoes</th></tr></thead><tbody id='admin-rows'><tr><td colspan='8'>Carregando...</td></tr></tbody></table></div>
  </section>
  <section class='section card'>
    <h2 class='title'>Configuracoes SaaS</h2>
    <div id='sys-note' class='notice muted'></div>
    <div class='grid g3'>
      <div class='mini'><div class='muted'>Gateway</div><div id='sys-gateway' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>Trial (dias)</div><div id='sys-trial' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>Scanner padrao</div><div id='sys-scan' class='kpi'>-</div></div>
    </div>
    <div class='section'>
      <label>Plano para editar</label>
      <select id='price-plan'><option value='starter'>Starter</option><option value='pro'>Pro</option><option value='team'>Team</option></select>
      <label>Novo preco (R$)</label>
      <input id='price-value' type='number' step='0.01' min='1' value='97' />
      <button class='btn primary' onclick='savePlanPrice()'>Salvar preco global do plano</button>
    </div>
  </section>
  <section class='section card'>
    <h2 class='title'>Telegram de entradas aprovadas</h2>
    <p class='muted'>Canal administrativo para receber somente sinais com decisão final aprovada. Aguardar, monitorar, sem dados e não entrar ficam bloqueados.</p>
    <div id='approved-telegram-note' class='notice muted'></div>
    <div class='grid g2'>
      <div>
        <label>Chat ID ou grupo Telegram</label>
        <input id='approved-telegram-chat-id' type='text' placeholder='Ex: -1001234567890 ou 123456789' />
      </div>
      <div>
        <label>Status</label>
        <label style='display:inline-flex;gap:8px;align-items:center;min-height:42px;'>
          <input id='approved-telegram-enabled' type='checkbox' />
          Enviar apenas entradas aprovadas
        </label>
      </div>
    </div>
    <div style='display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;'>
      <button class='btn primary' onclick='saveApprovedTelegramConfig()'>Salvar Telegram de sinais</button>
      <a class='btn' href='/app#telegram'>Ver configuração do cliente</a>
    </div>
  </section>
  <section class='section card'>
    <h2 class='title'>Governanca tecnica do SaaS</h2>
    <div id='health-note' class='notice muted'></div>
    <div class='grid g3'>
      <div class='mini'><div class='muted'>Portal DB</div><div id='health-db' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>State JSON</div><div id='health-state' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>Scanner</div><div id='health-scan-mode' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>Usuarios ativos</div><div id='health-users' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>Pagamentos</div><div id='health-payments' class='kpi'>-</div></div>
      <div class='mini'><div class='muted'>Telegram vinculados</div><div id='health-telegram' class='kpi'>-</div></div>
    </div>
    <div class='section'>
      <h3 class='title'>Tokens e integracoes</h3>
      <div style='overflow:auto'>
        <table>
          <thead><tr><th>Servico</th><th>Status</th><th>Token/Config</th><th>Detalhe</th></tr></thead>
          <tbody id='health-integrations'><tr><td colspan='4'>Carregando...</td></tr></tbody>
        </table>
      </div>
    </div>
    <div class='section'>
      <h3 class='title'>Consumo IA e APIs</h3>
      <div class='grid g3'>
        <div class='mini'><div class='muted'>Requisicoes hoje</div><div id='usage-req-today' class='kpi'>-</div></div>
        <div class='mini'><div class='muted'>Tokens IA hoje</div><div id='usage-tokens-today' class='kpi'>-</div></div>
        <div class='mini'><div class='muted'>Custo estimado hoje</div><div id='usage-cost-today' class='kpi'>-</div></div>
        <div class='mini'><div class='muted'>Requisicoes total</div><div id='usage-req-total' class='kpi'>-</div></div>
        <div class='mini'><div class='muted'>Tokens IA total</div><div id='usage-tokens-total' class='kpi'>-</div></div>
        <div class='mini'><div class='muted'>Custo estimado total</div><div id='usage-cost-total' class='kpi'>-</div></div>
      </div>
      <div class='muted' id='usage-note' style='margin-top:8px;'>Carregando consumo...</div>
      <div style='overflow:auto; margin-top:10px;'>
        <table>
          <thead><tr><th>Servico</th><th>Categoria</th><th>Requests</th><th>Tokens in/out</th><th>Custo</th><th>Ultima atividade</th><th>Operacoes</th><th>Erro recente</th></tr></thead>
          <tbody id='usage-rows'><tr><td colspan='8'>Carregando...</td></tr></tbody>
        </table>
      </div>
    </div>
  </section>
</main>
"""
    script = """<script>
async function loadUsers() {
  const includeCanceled = Boolean(document.getElementById('show-canceled')?.checked);
  const res = await fetch(`/api/admin/users?include_canceled=${includeCanceled ? '1' : '0'}`, {cache:'no-store'});
  const data = await res.json();
  if (!res.ok) { document.getElementById('admin-note').textContent = data.detail || 'Falha ao carregar usuarios.'; return; }
  const rawUsers = Array.isArray(data.users) ? data.users : [];
  const users = includeCanceled
    ? rawUsers
    : rawUsers.filter(user => ['active', 'trial'].includes(String(user.status || '').trim().toLowerCase()));
  const rows = users.map(user => {
    const isAdmin = Number(user.is_admin || 0) === 1;
    const trial = user.trial_ends_at ? user.trial_ends_at.slice(0,16) : '-';
    const actions = isAdmin
      ? '<span class="muted">Conta admin protegida</span>'
      : `<button class="btn" onclick="runAction(${user.id}, 'charge')">Cobrar</button>
        <button class="btn" onclick="runAction(${user.id}, 'checkout')">Checkout</button>
        <button class="btn" onclick="setPlanAndPrice(${user.id}, '${user.plan}', ${Number(user.monthly_price_brl || 0).toFixed(2)})">Plano/Preco</button>
        <button class="btn red" onclick="runAction(${user.id}, 'cancel')">Cancelar</button>
        <button class="btn green" onclick="runAction(${user.id}, 'activate')">Reativar</button>`;
    return `<tr>
      <td>${user.id}</td>
      <td><strong>${user.name}</strong><br><span class="muted">${user.email}</span></td>
      <td>${user.plan}</td>
      <td>${user.status}</td>
      <td>R$ ${Number(user.monthly_price_brl || 0).toFixed(2)}</td>
      <td>${trial}</td>
      <td>${user.next_due_at ? user.next_due_at.slice(0,16) : '-'}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('');
  document.getElementById('admin-rows').innerHTML = rows || '<tr><td colspan="8">Sem clientes.</td></tr>';
  const hiddenCanceled = Math.max(Number(data.hidden_canceled || 0), Math.max(0, rawUsers.length - users.length));
  const currentNote = document.getElementById('admin-note').textContent || '';
  if (!includeCanceled && hiddenCanceled > 0 && !currentNote) {
    document.getElementById('admin-note').textContent = `${hiddenCanceled} cliente(s) cancelado(s) ocultos da lista.`;
  }
}
async function runAction(userId, action) {
  const note = document.getElementById('admin-note');
  note.textContent = 'Processando...';
  const payload = {user_id:userId, action};
  if (action === 'cancel') {
    payload.reason = prompt('Motivo do cancelamento:', 'Solicitacao do cliente') || 'Solicitacao do cliente';
  }
  const res = await fetch('/api/admin/user-action', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || 'Falha na acao.'; return; }
  if (data.checkout_url) note.innerHTML = `Checkout gerado: <a href="${data.checkout_url}" target="_blank" rel="noopener">abrir</a>`;
  else note.textContent = data.message || 'Acao concluida.';
  await loadUsers();
}
async function setPlanAndPrice(userId, currentPlan, currentPrice) {
  const plan = (prompt('Plano (starter/pro/team):', currentPlan) || currentPlan).toLowerCase();
  const price = Number(prompt('Mensalidade em R$:', String(currentPrice)).replace(',', '.'));
  if (!Number.isFinite(price) || price <= 0) return;
  const res = await fetch('/api/admin/user-action', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify({user_id:userId, action:'set_plan', plan, monthly_price_brl:price})
  });
  const data = await res.json();
  const note = document.getElementById('admin-note');
  if (!res.ok) { note.textContent = data.detail || 'Falha ao atualizar plano/preco.'; return; }
  note.textContent = data.message || 'Plano atualizado.';
  await loadUsers();
}
async function loadSystemConfig() {
  const res = await fetch('/api/admin/system-config', {cache:'no-store'});
  const data = await res.json();
  if (!res.ok) { document.getElementById('sys-note').textContent = data.detail || 'Falha ao carregar config.'; return; }
  document.getElementById('sys-gateway').textContent = (data.payment_gateway || '-').toUpperCase();
  document.getElementById('sys-trial').textContent = data.trial_days;
  document.getElementById('sys-scan').textContent = `${data.default_idle_scan_seconds}s / ${data.default_active_scan_seconds}s`;
  const plan = document.getElementById('price-plan').value;
  const current = (((data.pricing || {})[plan] || {}).price) || 97;
  document.getElementById('price-value').value = Number(current).toFixed(2);
}
async function loadApprovedTelegramConfig() {
  const note = document.getElementById('approved-telegram-note');
  const res = await fetch('/api/admin/telegram-approved-signals', {cache:'no-store'});
  const data = await res.json();
  if (!res.ok) {
    note.textContent = data.detail || 'Falha ao carregar Telegram de entradas aprovadas.';
    return;
  }
  document.getElementById('approved-telegram-chat-id').value = data.chat_id || '';
  document.getElementById('approved-telegram-enabled').checked = Boolean(data.enabled);
  note.textContent = data.enabled
    ? 'Ativo: o bot enviará somente sinais com Entrada aprovada para este chat.'
    : 'Inativo: nenhum envio administrativo de entradas aprovadas.';
}
async function saveApprovedTelegramConfig() {
  const note = document.getElementById('approved-telegram-note');
  note.textContent = 'Salvando configuração...';
  const payload = {
    chat_id: document.getElementById('approved-telegram-chat-id').value.trim(),
    enabled: document.getElementById('approved-telegram-enabled').checked
  };
  const res = await fetch('/api/admin/telegram-approved-signals', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) {
    note.textContent = data.detail || 'Falha ao salvar Telegram de entradas aprovadas.';
    return;
  }
  note.textContent = data.message || 'Configuração salva.';
  await loadApprovedTelegramConfig();
  await loadSystemHealth();
}
async function savePlanPrice() {
  const note = document.getElementById('sys-note');
  note.textContent = 'Salvando preco...';
  const plan = document.getElementById('price-plan').value;
  const monthly_price_brl = Number(document.getElementById('price-value').value || 0);
  const res = await fetch('/api/admin/plan-price', {
    method:'POST',
    headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
    body:JSON.stringify({plan, monthly_price_brl})
  });
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || 'Falha ao salvar preco.'; return; }
  note.textContent = data.message || 'Preco atualizado.';
  await loadUsers();
  await loadSystemConfig();
}
function tokenRow(name, item, detail) {
  const status = item && item.configured ? 'configurado' : 'faltando';
  const preview = item ? item.preview : 'nao configurado';
  const detailText = detail || '-';
  return `<tr><td>${name}</td><td>${status}</td><td>${preview}</td><td>${detailText}</td></tr>`;
}
function fmtMoneyBr(value) {
  const num = Number(value || 0);
  return num.toLocaleString('pt-BR', {style:'currency', currency:'BRL'});
}
function fmtNumber(value) {
  return Number(value || 0).toLocaleString('pt-BR');
}
function fmtDateTime(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString('pt-BR');
  } catch (e) {
    return value;
  }
}
async function loadSystemHealth() {
  const note = document.getElementById('health-note');
  const res = await fetch('/api/admin/system-health', {cache:'no-store'});
  const data = await res.json();
  if (!res.ok) { note.textContent = data.detail || 'Falha ao carregar monitoramento tecnico.'; return; }
  note.textContent = `Atualizado: ${data.generated_at}`;
  const db = data.database || {};
  const state = data.state || {};
  const scanner = data.scanner || {};
  document.getElementById('health-db').textContent = db.db_size_human || '-';
  document.getElementById('health-state').textContent = state.state_size_human || '-';
  document.getElementById('health-scan-mode').textContent = scanner.mode_label || '-';
  document.getElementById('health-users').textContent = `${db.users_active || 0}/${db.users_total || 0}`;
  document.getElementById('health-payments').textContent = `${db.payments_paid || 0}/${db.payments_total || 0}`;
  document.getElementById('health-telegram').textContent = `${db.telegram_linked || 0}`;
  const integrations = data.integrations || {};
  const rows = [
    tokenRow('Telegram Bot', integrations.telegram_bot, `chat ids: ${state.chat_ids || 0}`),
    tokenRow('Telegram entradas aprovadas', integrations.telegram_approved_signals, integrations.telegram_approved_signals && integrations.telegram_approved_signals.enabled ? 'somente Entrada aprovada' : 'inativo'),
    tokenRow('Gemini', integrations.gemini_key, integrations.gemini_model || '-'),
    tokenRow('Odds-API.io', integrations.odds_api_io_key, integrations.odds_api_io_bookmakers || 'bookmaker nao configurado'),
    tokenRow('Supabase', integrations.supabase_key, integrations.supabase_url || 'URL nao configurada'),
    tokenRow('Stripe', integrations.stripe_key, integrations.payment_gateway || '-'),
    tokenRow('Mercado Pago', integrations.mercadopago_key, integrations.payment_gateway || '-'),
    tokenRow('SMTP', integrations.smtp_password, integrations.smtp_host || 'SMTP nao configurado')
  ].join('');
  document.getElementById('health-integrations').innerHTML = rows;
  const usage = data.usage || {};
  const today = usage.today || {};
  const totals = usage.totals || {};
  document.getElementById('usage-req-today').textContent = fmtNumber(today.requests || 0);
  document.getElementById('usage-tokens-today').textContent = `${fmtNumber(today.input_tokens || 0)} / ${fmtNumber(today.output_tokens || 0)}`;
  document.getElementById('usage-cost-today').textContent = fmtMoneyBr(today.estimated_cost_brl || 0);
  document.getElementById('usage-req-total').textContent = fmtNumber(totals.requests || 0);
  document.getElementById('usage-tokens-total').textContent = `${fmtNumber(totals.input_tokens || 0)} / ${fmtNumber(totals.output_tokens || 0)}`;
  document.getElementById('usage-cost-total').textContent = fmtMoneyBr(totals.estimated_cost_brl || 0);
  const pricingNote = usage.pricing_note || 'Contagem viva. Custos usam as tarifas configuradas no .env.';
  document.getElementById('usage-note').textContent = `${pricingNote} Hoje: ${fmtNumber(today.ai_requests || 0)} IA, ${fmtNumber(today.api_requests || 0)} APIs, ${fmtNumber(today.payment_requests || 0)} pagamentos.`;
  const usageRows = (usage.services || []).map(item => {
    const ops = Object.entries(item.operations || {}).map(([key, value]) => `${key}: ${value}`).join(' | ') || '-';
    const tokens = `${fmtNumber(item.input_tokens || 0)} / ${fmtNumber(item.output_tokens || 0)}`;
    return `<tr>
      <td>${item.label || item.service}</td>
      <td>${item.category || '-'}</td>
      <td>${fmtNumber(item.requests || 0)} <span class="muted">(${fmtNumber(item.error_requests || 0)} erro)</span></td>
      <td>${tokens}</td>
      <td>${fmtMoneyBr(item.estimated_cost_brl || 0)}</td>
      <td>${fmtDateTime(item.last_request_at)}</td>
      <td>${ops}</td>
      <td>${item.last_error || '-'}</td>
    </tr>`;
  }).join('');
  document.getElementById('usage-rows').innerHTML = usageRows || '<tr><td colspan="8">Sem consumo registrado ainda.</td></tr>';
}
document.getElementById('price-plan').addEventListener('change', loadSystemConfig);
const showCanceled = document.getElementById('show-canceled');
if (showCanceled) {
  showCanceled.checked = false;
  showCanceled.addEventListener('change', loadUsers);
}
loadSystemConfig();
loadApprovedTelegramConfig();
loadUsers();
loadSystemHealth();
window.setInterval(loadSystemHealth, 60 * 1000);
</script>"""
    return _page_shell(f"Admin | {settings.product_name}", body, script)


@router.post("/api/auth/signup")
def api_signup(request: Request, payload: SignupPayload) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    _rate_limit(request, "signup", limit=8, window_seconds=600)
    store = _portal_store(settings)
    email = _validate_email(payload.email)
    password = payload.password.strip()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Senha deve ter no minimo 8 caracteres.")
    plans = _plan_catalog(settings, store)
    plan = _safe_plan(payload.plan)
    user = store.create_user(
        name=payload.name,
        email=email,
        password=password,
        plan=plan,
        monthly_price_brl=float(plans[plan]["price"]),
        trial_days=settings.portal_trial_days,
    )
    response = JSONResponse({"ok": True, "message": "Conta criada com teste gratis.", "redirect": "/app"})
    _set_session_cookie(response, request, settings, int(user["id"]))
    return response


@router.post("/api/auth/login")
def api_login(request: Request, payload: LoginPayload) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    _rate_limit(request, "login", limit=12, window_seconds=900)
    store = _portal_store(settings)
    email = _validate_email(payload.email)
    user = store.authenticate(email, payload.password)
    if not user:
        # Recuperação automática de super admin caso o registro tenha sido perdido.
        if email == settings.admin_email and payload.password == settings.admin_password:
            user = store.ensure_admin(
                settings.admin_email,
                settings.admin_name,
                settings.admin_password,
            )
    if not user:
        raise HTTPException(status_code=401, detail="Email ou senha invalidos.")
    response = JSONResponse({"ok": True, "message": "Login realizado.", "redirect": "/app"})
    _set_session_cookie(response, request, settings, int(user["id"]))
    return response


@router.post("/api/auth/logout")
def api_logout(request: Request) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    response = JSONResponse({"ok": True, "message": "Sessao encerrada."})
    _clear_session_cookie(response)
    return response


@router.get("/api/auth/me")
def api_me(user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse(
        {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": user.get("name"),
            "avatar_url": user.get("avatar_url"),
            "plan": user.get("plan"),
            "status": user.get("status"),
            "is_admin": int(user.get("is_admin") or 0),
            "trial_days_left": _trial_left_days(user),
        }
    )


@router.post("/api/user/profile")
def api_user_profile_update(
    request: Request,
    payload: ProfilePayload,
    user: dict[str, Any] = Depends(_require_user),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    name = payload.name if payload.name is not None else None
    email = _validate_email(payload.email) if payload.email is not None else None
    avatar_url = _sanitize_avatar_url(payload.avatar_url) if payload.avatar_url is not None else None
    new_password = payload.new_password.strip() if payload.new_password else None
    try:
        updated = store.update_profile(
            int(user["id"]),
            name=name,
            email=email,
            avatar_url=avatar_url,
            new_password=new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return JSONResponse({"ok": True, "user": updated})


@router.get("/api/user/preferences")
def api_user_preferences(user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    settings = _settings()
    store = _portal_store(settings)
    prefs = store.get_preferences(int(user["id"]))
    state_obj = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    return JSONResponse(
        {
            "ok": True,
            "preferences": prefs,
            "telegram_status": _telegram_status(settings, prefs, state_obj),
            "ai_memory_status": _ai_memory_status(settings, store),
        }
    )


@router.post("/api/user/preferences")
def api_user_preferences_update(
    request: Request,
    payload: PreferencesPayload,
    user: dict[str, Any] = Depends(_require_user),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    prefs = store.update_preferences(
        int(user["id"]),
        scan_enabled=payload.scan_enabled,
        idle_scan_seconds=payload.idle_scan_seconds,
        active_scan_seconds=payload.active_scan_seconds,
        telegram_enabled=payload.telegram_enabled,
        telegram_chat_id=payload.telegram_chat_id,
    )
    state_obj = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    return JSONResponse(
        {
            "ok": True,
            "preferences": prefs,
            "telegram_status": _telegram_status(settings, prefs, state_obj),
            "ai_memory_status": _ai_memory_status(settings, store),
        }
    )


@router.post("/api/auth/forgot-password")
def api_forgot(request: Request, payload: ForgotPayload) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    _rate_limit(request, "forgot", limit=10, window_seconds=900)
    store = _portal_store(settings)
    email = _validate_email(payload.email)
    token = store.create_reset_token(email, ttl_minutes=45)
    if token:
        send_password_reset_email(settings, email, token)
    return JSONResponse(
        {
            "ok": True,
            "message": "Se o email existir, enviamos as instrucoes de redefinicao.",
            "redirect": "/login",
        }
    )


@router.post("/api/auth/reset-password")
def api_reset(request: Request, payload: ResetPayload) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    _rate_limit(request, "reset", limit=12, window_seconds=900)
    if len(payload.password.strip()) < 8:
        raise HTTPException(status_code=400, detail="Senha deve ter no minimo 8 caracteres.")
    store = _portal_store(settings)
    if not store.reset_password_with_token(payload.token.strip(), payload.password.strip()):
        raise HTTPException(status_code=400, detail="Token invalido ou expirado.")
    return JSONResponse({"ok": True, "message": "Senha atualizada com sucesso.", "redirect": "/login"})


@router.post("/api/support-chat")
async def api_support_chat(
    request: Request,
    payload: SupportPayload,
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    user = _optional_user(request)
    state = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    stop = red_stop_status(state.history or [], settings.daily_red_limit)
    text = payload.message.strip()
    if len(text) < 2:
        raise HTTPException(status_code=400, detail="Digite uma pergunta mais completa.")
    prefs = store.get_preferences(int(user["id"])) if user else None
    context = {
        "red_lock": stop,
        "active_signal": bool(state.active_signal),
        "skills": await _ai_support_skills(settings),
        "telegram_status": _telegram_status(settings, prefs, state),
        "ai_memory_status": _ai_memory_status(settings, store),
    }
    answer = support_agent_reply(text, context)
    logs_html = ""
    if user:
        store.log_support(int(user["id"]), "user", text)
        store.log_support(int(user["id"]), "agent", answer)
        logs = store.list_support_logs(int(user["id"]), limit=20)
        logs_html = "".join(
            f"<tr><td>{_esc(row['created_at'])[:16]}</td><td>{_esc(row['role'])}</td><td>{_esc(row['message'])}</td></tr>"
            for row in logs
        ) or "<tr><td colspan='3'>Sem conversas ainda.</td></tr>"
    return JSONResponse({"ok": True, "answer": answer, "logs_html": logs_html})


@router.post("/api/fantasy/lineup")
def api_fantasy_lineup(
    request: Request,
    payload: FantasyPayload,
    _: dict[str, Any] = Depends(_require_user),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    _rate_limit(request, "fantasy", limit=40, window_seconds=900)
    text = payload.description.strip()
    if len(text) < 12:
        raise HTTPException(status_code=400, detail="Descreva o jogo ou cole o pool de jogadores.")
    result = _fantasy_build_lineup(text[:8000], payload.budget)
    return JSONResponse(result)


@router.get("/api/admin/users")
def api_admin_users(
    include_canceled: int = 0,
    _: dict[str, Any] = Depends(_require_admin),
) -> JSONResponse:
    settings = _settings()
    store = _portal_store(settings)
    users = [user for user in store.list_users() if int(user.get("is_admin") or 0) == 0]
    if include_canceled:
        return JSONResponse({"users": users, "hidden_canceled": 0})
    filtered = [user for user in users if str(user.get("status") or "").lower() != "canceled"]
    hidden = max(0, len(users) - len(filtered))
    return JSONResponse({"users": filtered, "hidden_canceled": hidden})


@router.get("/api/admin/system-config")
def api_admin_system_config(_: dict[str, Any] = Depends(_require_admin)) -> JSONResponse:
    settings = _settings()
    store = _portal_store(settings)
    return JSONResponse(
        {
            "ok": True,
            "payment_gateway": settings.payment_gateway,
            "trial_days": settings.portal_trial_days,
            "default_idle_scan_seconds": settings.idle_scan_interval_seconds,
            "default_active_scan_seconds": settings.active_scan_interval_seconds,
            "pricing": _plan_catalog(settings, store),
            "telegram_help": "/chatid no bot para capturar o chat id e colar na area do cliente.",
        }
    )


@router.get("/api/admin/telegram-approved-signals")
def api_admin_telegram_approved_signals(_: dict[str, Any] = Depends(_require_admin)) -> JSONResponse:
    settings = _settings()
    store = _portal_store(settings)
    config = store.approved_signal_telegram_config()
    return JSONResponse(
        {
            "ok": True,
            "enabled": bool(config.get("enabled")),
            "chat_id": config.get("chat_id") or "",
            "updated_at": config.get("updated_at") or "",
            "telegram_bot_configured": bool(settings.telegram_bot_token),
            "policy": "Somente sinais com decisão final Entrada aprovada são enviados.",
        }
    )


@router.post("/api/admin/telegram-approved-signals")
def api_admin_telegram_approved_signals_update(
    request: Request,
    payload: AdminTelegramApprovedPayload,
    _: dict[str, Any] = Depends(_require_admin),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    chat_id = _clean_telegram_chat_ids(payload.chat_id)
    enabled = bool(payload.enabled)
    if enabled and not chat_id:
        raise HTTPException(status_code=400, detail="Informe o Chat ID antes de ativar.")
    store.set_system_settings(
        {
            "telegram_approved_signals_enabled": "1" if enabled else "0",
            "telegram_approved_signals_chat_id": chat_id,
            "telegram_approved_signals_updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    return JSONResponse(
        {
            "ok": True,
            "enabled": enabled,
            "chat_id": chat_id,
            "message": (
                "Telegram de entradas aprovadas ativado."
                if enabled
                else "Telegram de entradas aprovadas desativado."
            ),
        }
    )


@router.get("/api/admin/system-health")
def api_admin_system_health(_: dict[str, Any] = Depends(_require_admin)) -> JSONResponse:
    settings = _settings()
    store = _portal_store(settings)
    state_path = Path(os.getenv("STATE_FILE", "data/state.json"))
    state_obj = StateStore(str(state_path)).load()
    usage = _usage_tracker(settings).summary()
    pricing = _usage_pricing(settings)
    database = store.system_snapshot()
    state_size = state_path.stat().st_size if state_path.exists() else 0
    scanner_mode = str(state_obj.scan_preference or "brazil_first")
    mode_label_map = {
        "brazil_first": "Brasil -> Mundo",
        "world_first": "Mundo -> Brasil",
        "live_only": "Somente ao vivo",
    }
    integrations = {
        "payment_gateway": settings.payment_gateway,
        "telegram_bot": _mask_secret(settings.telegram_bot_token),
        "telegram_approved_signals": {
            "configured": bool(store.approved_signal_telegram_chat_ids()),
            "preview": store.approved_signal_telegram_config().get("chat_id") or "nao configurado",
            "enabled": bool(store.approved_signal_telegram_config().get("enabled")),
        },
        "gemini_key": _mask_secret(settings.gemini_api_key),
        "gemini_model": settings.gemini_model,
        "odds_api_io_key": _mask_secret(settings.odds_api_io_key),
        "odds_api_io_bookmakers": settings.odds_api_io_bookmakers,
        "supabase_key": _mask_secret(settings.supabase_service_role_key),
        "supabase_url": settings.supabase_url or "",
        "stripe_key": _mask_secret(settings.stripe_secret_key),
        "mercadopago_key": _mask_secret(settings.mercadopago_access_token),
        "smtp_password": _mask_secret(settings.smtp_password),
        "smtp_host": settings.smtp_host or "",
    }
    for item in usage.get("services", []):
        item["label"] = _usage_service_label(str(item.get("service") or ""))
    return JSONResponse(
        {
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database": {
                **database,
                "db_size_human": _size_label(int(database.get("db_size_bytes", 0))),
            },
            "state": {
                "state_file": state_path.as_posix(),
                "state_size_bytes": state_size,
                "state_size_human": _size_label(state_size),
                "history": len(state_obj.history or []),
                "candidate_signals": len(state_obj.candidate_signals or []),
                "last_games": len(state_obj.last_games or []),
                "chat_ids": len(state_obj.chat_ids or []),
                "last_scan_at": state_obj.last_scan_at,
                "scan_requested_at": state_obj.scan_requested_at,
            },
            "scanner": {
                "mode": scanner_mode,
                "mode_label": mode_label_map.get(scanner_mode, scanner_mode),
            },
            "integrations": integrations,
            "usage": {
                **usage,
                "pricing": pricing.as_dict(),
                "pricing_note": "Contagem em tempo real. O custo estimado depende das tarifas preenchidas no .env.",
            },
        }
    )


@router.post("/api/admin/plan-price")
def api_admin_plan_price(
    request: Request,
    payload: PlanPricePayload,
    _: dict[str, Any] = Depends(_require_admin),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    plan = _safe_plan(payload.plan)
    if float(payload.monthly_price_brl) <= 0:
        raise HTTPException(status_code=400, detail="Preco deve ser maior que zero.")
    store.update_plan_price(plan, float(payload.monthly_price_brl))
    return JSONResponse(
        {"ok": True, "message": f"Preco do plano {plan} atualizado.", "pricing": _plan_catalog(settings, store)}
    )


async def _create_checkout_for_user(
    settings: Settings,
    store: PortalStore,
    user: dict[str, Any],
    plan: str,
) -> tuple[str, str]:
    plan = _safe_plan(plan)
    price_catalog = _plan_catalog(settings, store)
    amount = float(price_catalog[plan]["price"])
    base_url = (settings.website_url or "").rstrip("/") or "http://localhost"
    usage_tracker = _usage_tracker(settings)
    if settings.payment_gateway == "stripe":
        price_map = {
            "starter": settings.stripe_price_starter,
            "pro": settings.stripe_price_pro,
            "team": settings.stripe_price_team,
        }
        price_id = price_map.get(plan)
        if not settings.stripe_secret_key or not price_id:
            raise HTTPException(status_code=400, detail="Stripe nao configurado para este plano.")
        payload = {
            "mode": "subscription",
            "success_url": f"{base_url}/app?billing=success",
            "cancel_url": f"{base_url}/app?billing=cancel",
            "customer_email": str(user.get("email")),
            "client_reference_id": str(user.get("id")),
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "subscription_data[trial_period_days]": str(max(1, int(settings.portal_trial_days))),
            "metadata[user_id]": str(user.get("id")),
            "metadata[plan]": plan,
        }
        headers = {"Authorization": f"Bearer {settings.stripe_secret_key}"}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.post(
                    "https://api.stripe.com/v1/checkout/sessions",
                    data=payload,
                    headers=headers,
                )
            except Exception as exc:
                usage_tracker.record(
                    "stripe",
                    category="payment",
                    request_count=1,
                    success=False,
                    estimated_cost_brl=settings.stripe_cost_per_request_brl,
                    operation="checkout_session",
                    error=str(exc)[:240],
                )
                raise
        usage_tracker.record(
            "stripe",
            category="payment",
            request_count=1,
            success=response.status_code < 300,
            response_bytes=len(response.content),
            estimated_cost_brl=settings.stripe_cost_per_request_brl,
            operation="checkout_session",
            error=None if response.status_code < 300 else f"http {response.status_code}",
        )
        if response.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"Falha no Stripe ({response.status_code}).")
        body = response.json()
        url = body.get("url")
        if not url:
            raise HTTPException(status_code=502, detail="Stripe nao retornou checkout URL.")
        return "stripe", str(url)

    if settings.payment_gateway == "mercadopago":
        if not settings.mercadopago_access_token:
            raise HTTPException(status_code=400, detail="Mercado Pago nao configurado.")
        preference = {
            "items": [
                {
                    "title": f"{settings.product_name} - {plan.upper()}",
                    "quantity": 1,
                    "currency_id": settings.payment_currency or "BRL",
                    "unit_price": amount,
                }
            ],
            "payer": {"email": str(user.get("email"))},
            "back_urls": {
                "success": f"{base_url}/app?billing=success",
                "failure": f"{base_url}/app?billing=failure",
                "pending": f"{base_url}/app?billing=pending",
            },
            "auto_return": "approved",
            "external_reference": f"user-{user.get('id')}",
            "metadata": {"user_id": str(user.get("id")), "plan": plan},
        }
        headers = {"Authorization": f"Bearer {settings.mercadopago_access_token}"}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.post(
                    "https://api.mercadopago.com/checkout/preferences",
                    json=preference,
                    headers=headers,
                )
            except Exception as exc:
                usage_tracker.record(
                    "mercadopago",
                    category="payment",
                    request_count=1,
                    success=False,
                    estimated_cost_brl=settings.mercadopago_cost_per_request_brl,
                    operation="checkout_preference",
                    error=str(exc)[:240],
                )
                raise
        usage_tracker.record(
            "mercadopago",
            category="payment",
            request_count=1,
            success=response.status_code < 300,
            response_bytes=len(response.content),
            estimated_cost_brl=settings.mercadopago_cost_per_request_brl,
            operation="checkout_preference",
            error=None if response.status_code < 300 else f"http {response.status_code}",
        )
        if response.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"Falha no Mercado Pago ({response.status_code}).")
        body = response.json()
        url = body.get("init_point")
        if not url:
            raise HTTPException(status_code=502, detail="Mercado Pago nao retornou checkout URL.")
        return "mercadopago", str(url)

    raise HTTPException(status_code=400, detail="Gateway de pagamento nao configurado.")


@router.post("/api/billing/checkout")
async def api_checkout(
    request: Request,
    payload: CheckoutPayload,
    user: dict[str, Any] = Depends(_require_user),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    plan = _safe_plan(payload.plan or str(user.get("plan") or "starter"))
    gateway, checkout_url = await _create_checkout_for_user(settings, store, user, plan)
    amount = _plan_catalog(settings, store)[plan]["price"]
    store.log_payment(int(user["id"]), gateway, float(amount), "checkout_created", checkout_url=checkout_url)
    updated = store.update_user(int(user["id"]), plan=plan, monthly_price_brl=float(amount))
    return JSONResponse(
        {
            "ok": True,
            "gateway": gateway,
            "url": checkout_url,
            "plan": plan,
            "trial_days": max(1, int(settings.portal_trial_days)),
            "user": updated,
        }
    )


@router.post("/api/admin/user-action")
async def api_admin_user_action(
    request: Request,
    payload: AdminActionPayload,
    _: dict[str, Any] = Depends(_require_admin),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    user = store.get_user(int(payload.user_id))
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    action = payload.action.strip().lower()
    if int(user.get("is_admin") or 0) == 1 and action in {"cancel", "activate"}:
        raise HTTPException(status_code=400, detail="Conta admin protegida contra cancelamento/reativacao.")
    if action == "charge":
        updated = store.mark_charge(int(payload.user_id), cycle_days=payload.cycle_days or 30)
        amount = payload.monthly_price_brl if payload.monthly_price_brl is not None else user.get("monthly_price_brl", 0)
        store.log_payment(
            int(payload.user_id),
            settings.payment_gateway or "manual",
            float(amount or 0),
            "paid",
            payment_id=f"manual-{int(time.time())}",
            paid_at=datetime.now(timezone.utc).isoformat(),
        )
        return JSONResponse({"ok": True, "message": "Cobranca registrada.", "user": updated})
    if action == "cancel":
        updated = store.cancel_user(int(payload.user_id), payload.reason or "Cancelado pelo admin.")
        return JSONResponse({"ok": True, "message": "Assinatura cancelada.", "user": updated})
    if action == "activate":
        updated = store.update_user(int(payload.user_id), status="active", cancel_reason="")
        return JSONResponse({"ok": True, "message": "Conta reativada.", "user": updated})
    if action == "checkout":
        gateway, checkout_url = await _create_checkout_for_user(
            settings,
            store,
            user,
            user.get("plan") or "starter",
        )
        store.log_payment(
            int(payload.user_id),
            gateway,
            float(user.get("monthly_price_brl") or 0),
            "checkout_created",
            checkout_url=checkout_url,
        )
        return JSONResponse({"ok": True, "message": "Checkout gerado.", "checkout_url": checkout_url})
    if action == "set_plan":
        plan = _safe_plan(payload.plan or user.get("plan"))
        amount = payload.monthly_price_brl
        if amount is None:
            amount = _plan_catalog(settings, store)[plan]["price"]
        updated = store.update_user(int(payload.user_id), plan=plan, monthly_price_brl=float(amount))
        return JSONResponse({"ok": True, "message": "Plano atualizado.", "user": updated})
    raise HTTPException(status_code=400, detail="Acao admin invalida.")


@router.get("/logout")
def logout_redirect() -> RedirectResponse:
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
