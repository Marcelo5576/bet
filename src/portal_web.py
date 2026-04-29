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
from src.intelligence.risk import red_stop_status
from src.portal import (
    PortalStore,
    issue_session_token,
    read_session_token,
    send_password_reset_email,
    support_agent_reply,
)
from src.storage import StateStore

router = APIRouter()
SESSION_COOKIE = "bs_session"
_STORE_CACHE: dict[str, PortalStore] = {}
_BOOTSTRAP_DONE: set[str] = set()
_RATE_LIMIT: dict[str, list[float]] = {}
PLAN_FEATURES = {
    "starter": [
        "Scanner ao vivo + Telegram",
        "Historico Green/Red",
        "Dashboard responsiva",
    ],
    "pro": [
        "Tudo do Starter",
        "IA com memoria operacional",
        "Suporte prioritario",
    ],
    "team": [
        "Tudo do Pro",
        "Multi-operadores",
        "Gestao comercial/admin completa",
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
        _BOOTSTRAP_DONE.add(key)
    return store


def _safe_plan(plan: str | None) -> str:
    value = (plan or "starter").strip().lower()
    if value not in {"starter", "pro", "team"}:
        return "starter"
    return value


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
        return f"R$ {float(value):.2f}"
    except (TypeError, ValueError):
        return "R$ 0.00"


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


def _size_label(size_bytes: int) -> str:
    amount = float(max(0, int(size_bytes)))
    units = ["B", "KB", "MB", "GB"]
    idx = 0
    while amount >= 1024 and idx < len(units) - 1:
        amount /= 1024
        idx += 1
    return f"{amount:.1f} {units[idx]}"


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


def _page_shell(title: str, body_html: str, extra_script: str = "") -> str:
    build_stamp = _build_stamp()
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
    .top {{ position:sticky; top:0; z-index:2; border-bottom:1px solid #1d2530; background:#0c1118; }}
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
    .btn {{
      border:1px solid #344254;
      background:linear-gradient(180deg, #182435, #111824);
      color:var(--txt);
      border-radius:10px;
      padding:10px 14px;
      font-weight:800;
      letter-spacing:0;
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
      min-height:88vh;
      display:flex;
      align-items:flex-end;
      padding:18vh 0 8vh;
      background:
        linear-gradient(180deg, rgba(8,11,16,.1), rgba(8,11,16,.82)),
        url('https://images.unsplash.com/photo-1574629810360-7efbbe195018?auto=format&fit=crop&w=1800&q=80') center/cover no-repeat;
      border-bottom:1px solid #202a37;
    }}
    .hero h1 {{ margin:0 0 10px; font-size:clamp(34px,6vw,62px); line-height:1.02; max-width:860px; }}
    .hero p {{ margin:0; color:#d3ddeb; font-size:clamp(16px,2.3vw,21px); max-width:760px; }}
    .hero-actions {{ margin-top:18px; display:flex; gap:10px; flex-wrap:wrap; }}
    .section {{ margin-top:22px; }}
    .title {{ margin:0 0 10px; font-size:18px; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .grid {{ display:grid; gap:10px; }}
    .g3 {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .g2 {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
    .card {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:14px; }}
    .mini {{ border:1px solid var(--line); border-radius:8px; background:#0d141e; padding:12px; }}
    .kpi {{ font-size:29px; font-weight:900; }}
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
      background:#0f1723; border:1px solid #2c4360; border-radius:10px; padding:12px; z-index:39; display:none;
    }}
    .ai-panel.open {{ display:block; }}
    .ai-panel textarea {{ min-height:76px; }}
    .build-badge {{
      position:fixed; left:18px; bottom:18px; z-index:38;
      border:1px solid #314156; border-radius:999px; background:rgba(10,15,24,.92);
      color:#b9c8d9; padding:8px 12px; font-size:11px; font-weight:700;
      box-shadow:0 8px 18px rgba(0,0,0,.28);
    }}
    @media (max-width:960px) {{
      .g3, .g2 {{ grid-template-columns:1fr; }}
      .hero {{ padding-top:14vh; min-height:80vh; }}
      .topin {{ align-items:flex-start; flex-direction:column; }}
    }}
  </style>
</head>
<body>
{body_html}
<div class="build-badge">Build {build_stamp}</div>
<button class="ai-fab" type="button" onclick="toggleAiHelp()">IA</button>
<section id="ai-float" class="ai-panel" aria-live="polite">
  <h3 class="title" style="margin-top:0">Assistente ApexGol</h3>
  <p class="muted">Duvidas rapidas sobre scanner, plano, login e Telegram.</p>
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
  note.textContent = 'Processando...';
  const res = await fetch('/api/support-chat', {{
    method:'POST',
    headers:{{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}},
    body:JSON.stringify({{message}})
  }});
  const data = await res.json();
  if (res.status === 401) {{
    note.textContent = 'Para usar o assistente completo, faca login em /login.';
    return;
  }}
  if (!res.ok) {{
    note.textContent = data.detail || 'Nao consegui responder agora.';
    return;
  }}
  note.textContent = data.answer || 'Resposta enviada.';
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
  const response = await fetch('{kind}', {{
    method:'POST',
    headers:{{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'}},
    body:JSON.stringify(data)
  }});
  const payload = await response.json();
  if (!response.ok) {{
    notice.textContent = payload.detail || payload.message || 'Falha na operacao.';
    return;
  }}
  notice.textContent = payload.message || 'Sucesso.';
  window.location.href = payload.redirect || '/app';
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
        plan_html.append(
            "<div class='card'>"
            f"<h3>{_esc(plan['label'])}</h3>"
            f"<div class='kpi'>{_fmt_money(plan['price'])}/mes</div>"
            f"<p class='muted'>7 dias de teste gratis.</p>"
            f"<ul>{features}</ul>"
            f"<a class='btn primary' href='/signup?plan={key}'>Comecar { _esc(plan['label']) }</a>"
            "</div>"
        )
    body = f"""
<header class='top'>
  <div class='topin'>
    <div class='brand'>{_esc(settings.product_name)}</div>
    <nav class='nav'>
      <a class='btn' href='/login'>Login</a>
      <a class='btn primary' href='/signup'>Teste 7 dias</a>
      <a class='btn' href='/dashboard'>Dashboard Operacional</a>
    </nav>
  </div>
</header>
<section class='hero'>
  <div class='wrap'>
    <h1>Objetivo claro: transformar dados ao vivo em leitura racional para apoiar sua decisao.</h1>
    <p>{_esc(settings.product_tagline)}</p>
    <div class='hero-actions'>
      <a class='btn primary' href='/signup'>Quero testar gratis</a>
      <a class='btn' href='/login'>Ja sou cliente</a>
    </div>
  </div>
</section>
<main class='wrap'>
  <section class='section grid g3'>
    <div class='card'><div class='muted'>Scanner em tempo real</div><div class='kpi'>24/7</div><div class='muted'>Cobertura global com priorizacao Brasil.</div></div>
    <div class='card'><div class='muted'>Ciclo de monitoramento</div><div class='kpi'>5m / 1m</div><div class='muted'>5 min com jogo ativo, 1 min sem selecao ativa.</div></div>
    <div class='card'><div class='muted'>Fantasy Campeao</div><div class='kpi'>Scout IA</div><div class='muted'>Leitura de sala, pool de jogadores e montagem otimizada por preco, projecao e historico.</div></div>
  </section>
  <section class='section'>
    <h2 class='title'>Planos viaveis para operacao individual e equipe</h2>
    <div class='grid g3'>{''.join(plan_html)}</div>
  </section>
  <section class='section grid g2'>
    <div class='card'>
      <h3 class='title'>Como funciona</h3>
      <p class='muted'>1) Cria conta e ativa teste de 7 dias. 2) Escolhe os jogos no scanner. 3) Registra entrada real no Telegram/site. 4) IA acompanha manter/sair e aprende com Green/Red. 5) No Fantasy Campeao, cruza preco do lobby com estatisticas globais para sugerir a melhor escalação.</p>
    </div>
    <div class='card'>
      <h3 class='title'>Suporte inteligente</h3>
      <p class='muted'>O agente de suporte resolve duvidas basicas de login, scanner, importacao e plano sem voce esperar atendimento humano para o simples.</p>
    </div>
  </section>
  <section class='section card'>
    <h3 class='title'>Aviso importante</h3>
    <p class='muted'>{_esc(settings.product_name)} e uma plataforma de apoio estatistico e educacional. As sugestoes de entrada sao parametros de analise e nao promessa de lucro. A decisao final e sempre do usuario, que assume integralmente a responsabilidade pelas operacoes realizadas.</p>
  </section>
</main>
"""
    return _page_shell(f"{settings.product_name} | Plataforma", body)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(plan: str | None = None) -> str:
    settings = _settings()
    selected = _safe_plan(plan)
    body = f"""
<header class='top'><div class='topin'><div class='brand'>Cadastro</div><nav class='nav'><a class='btn' href='/'>Inicio</a><a class='btn' href='/login'>Login</a></nav></div></header>
<main class='wrap'>
  <div class='card' style='max-width:520px;margin:24px auto;'>
    <h2 class='title'>Crie sua conta (teste gratis por 7 dias)</h2>
    <form onsubmit='submitAuth(event)'>
      <label>Nome</label><input name='name' required maxlength='120' />
      <label>Email</label><input name='email' type='email' required />
      <label>Senha (minimo 8 caracteres)</label><input name='password' type='password' minlength='8' required />
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
    return _page_shell(f"Cadastro | {settings.product_name}", body, _auth_js("/api/auth/signup"))


@router.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    settings = _settings()
    body = """
<header class='top'><div class='topin'><div class='brand'>Login</div><nav class='nav'><a class='btn' href='/'>Inicio</a><a class='btn' href='/signup'>Cadastro</a></nav></div></header>
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
    return _page_shell(f"Login | {settings.product_name}", body, _auth_js("/api/auth/login"))


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
    prefs = store.get_preferences(int(user["id"]))
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
    body = f"""
<header class='top'><div class='topin'><div class='brand'>Area do Cliente</div><nav class='nav'><a class='btn' href='/dashboard'>Dashboard Trade</a>{admin_link}<button class='btn red' onclick='logoutNow()'>Sair</button></nav></div></header>
<main class='wrap'>
  <section class='grid g3'>
    <div class='card'><div class='muted'>Conta</div><div class='kpi'>{_esc(user.get('name'))}</div><div class='muted'>{_esc(user.get('email'))}</div></div>
    <div class='card'><div class='muted'>Plano atual</div><div class='kpi'>{_esc(str(user.get('plan', '-')).upper())}</div><div class='muted'>Status: {_esc(user.get('status'))}</div></div>
    <div class='card'><div class='muted'>Teste gratis restante</div><div class='kpi'>{trial_days} dias</div><div class='muted'>Proxima cobranca: {_esc(user.get('next_due_at') or '-')}</div></div>
  </section>
  <section class='section card'>
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
  <section class='section grid g2'>
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
      <input id='pref-active' type='number' min='60' max='1800' value='{int(prefs.get("active_scan_seconds") or 300)}' />
      <label>Chat ID Telegram (para notificacoes)</label>
      <input id='pref-chatid' value='{_esc(prefs.get("telegram_chat_id") or "")}' placeholder='Ex: 123456789' />
      <label><input id='pref-enabled' type='checkbox' {'checked' if int(prefs.get("telegram_enabled") or 0) else ''} /> Quero notificacoes no Telegram</label>
      <button class='btn primary' onclick='savePrefs()'>Salvar preferencias</button>
      <div id='prefs-note' class='notice muted'></div>
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
    active_scan_seconds: Number(document.getElementById('pref-active').value || 300),
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
}
</script>"""
    return _page_shell("Portal do Cliente", body, script)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(_: dict[str, Any] = Depends(_require_admin)) -> str:
    settings = _settings()
    body = """
<header class='top'><div class='topin'><div class='brand'>Admin SaaS</div><nav class='nav'><a class='btn' href='/app'>Area cliente</a><a class='btn' href='/dashboard'>Dashboard Trade</a></nav></div></header>
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
    tokenRow('Gemini', integrations.gemini_key, integrations.gemini_model || '-'),
    tokenRow('Supabase', integrations.supabase_key, integrations.supabase_url || 'URL nao configurada'),
    tokenRow('Stripe', integrations.stripe_key, integrations.payment_gateway || '-'),
    tokenRow('Mercado Pago', integrations.mercadopago_key, integrations.payment_gateway || '-'),
    tokenRow('SMTP', integrations.smtp_password, integrations.smtp_host || 'SMTP nao configurado')
  ].join('');
  document.getElementById('health-integrations').innerHTML = rows;
}
document.getElementById('price-plan').addEventListener('change', loadSystemConfig);
const showCanceled = document.getElementById('show-canceled');
if (showCanceled) {
  showCanceled.checked = false;
  showCanceled.addEventListener('change', loadUsers);
}
loadSystemConfig();
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
    return JSONResponse({"ok": True, "preferences": prefs})


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
    return JSONResponse({"ok": True, "preferences": prefs})


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
def api_support_chat(
    request: Request,
    payload: SupportPayload,
    user: dict[str, Any] = Depends(_require_user),
) -> JSONResponse:
    settings = _settings()
    _assert_same_origin(request, settings)
    store = _portal_store(settings)
    state = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    stop = red_stop_status(state.history or [], settings.daily_red_limit)
    text = payload.message.strip()
    if len(text) < 2:
        raise HTTPException(status_code=400, detail="Digite uma pergunta mais completa.")
    context = {
        "red_lock": stop,
        "active_signal": bool(state.active_signal),
    }
    answer = support_agent_reply(text, context)
    store.log_support(int(user["id"]), "user", text)
    store.log_support(int(user["id"]), "agent", answer)
    logs = store.list_support_logs(int(user["id"]), limit=20)
    logs_html = "".join(
        f"<tr><td>{_esc(row['created_at'])[:16]}</td><td>{_esc(row['role'])}</td><td>{_esc(row['message'])}</td></tr>"
        for row in logs
    ) or "<tr><td colspan='3'>Sem conversas ainda.</td></tr>"
    return JSONResponse({"ok": True, "answer": answer, "logs_html": logs_html})


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


@router.get("/api/admin/system-health")
def api_admin_system_health(_: dict[str, Any] = Depends(_require_admin)) -> JSONResponse:
    settings = _settings()
    store = _portal_store(settings)
    state_path = Path(os.getenv("STATE_FILE", "data/state.json"))
    state_obj = StateStore(str(state_path)).load()
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
        "gemini_key": _mask_secret(settings.gemini_api_key),
        "gemini_model": settings.gemini_model,
        "supabase_key": _mask_secret(settings.supabase_service_role_key),
        "supabase_url": settings.supabase_url or "",
        "stripe_key": _mask_secret(settings.stripe_secret_key),
        "mercadopago_key": _mask_secret(settings.mercadopago_access_token),
        "smtp_password": _mask_secret(settings.smtp_password),
        "smtp_host": settings.smtp_host or "",
    }
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
            response = await client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=payload,
                headers=headers,
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
            response = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                json=preference,
                headers=headers,
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
