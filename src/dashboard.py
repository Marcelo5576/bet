from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
import base64
from datetime import datetime, timezone
import hashlib
import html
from itertools import combinations
import os
from pathlib import Path
import random
import re
import secrets
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import load_settings
from src.integrations.supabase import SupabaseSink
from src.intelligence.learning import summarize_history_with_simulation
from src.intelligence.manual_import import parse_manual_bets
from src.intelligence.paper_trading import best_paper_entry, paper_opportunities
from src.intelligence.rules import ranked_signals
from src.intelligence.source_catalog import FOOTBALL_DATA_SOURCES
from src.main import build_provider, prepare_signal, scan_games, _watch_signal_from_game
from src.portal import PortalStore, read_session_token
from src.portal_web import router as portal_router
from src.storage import StateStore

app = FastAPI(title="BetSignal Cloud Dashboard")
app.include_router(portal_router)
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
security = HTTPBasic(auto_error=False)
SESSION_COOKIE = "bs_session"


def _build_stamp() -> str:
    stamp = datetime.fromtimestamp(Path(__file__).stat().st_mtime, timezone.utc)
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M")


class ImportPayload(BaseModel):
    text: str


class HistoryValuePayload(BaseModel):
    signal_id: str
    entry_value: float | None = None
    entry_odds: float | None = None
    profit_value: float | None = None


class HistoryDeletePayload(BaseModel):
    signal_id: str


class HistoryOutcomePayload(BaseModel):
    signal_id: str
    outcome: str


class ScannerPreferencePayload(BaseModel):
    mode: str


class SimulationRunPayload(BaseModel):
    games: int = 30
    bankroll: float = 100.0
    stake_percent: float = 10.0


class FantasyLineupPayload(BaseModel):
    players_text: str
    budget: float = 120.0
    formation: str = "4-4-2"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ApexGol-Build"] = _build_stamp()
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _auth(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    settings = load_settings()
    if _valid_portal_session(request, settings):
        return
    local_hosts = {"127.0.0.1", "::1", "localhost"}
    req_host = str(request.url.hostname or "").strip().lower()
    client_host = str(request.client.host if request.client else "").strip().lower()
    if settings.test_mode and req_host in local_hosts and client_host in local_hosts:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais obrigatorias",
        )
    user_ok = secrets.compare_digest(credentials.username, settings.dashboard_user)
    password_ok = secrets.compare_digest(
        credentials.password, settings.dashboard_password
    )
    if not user_ok or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais invalidas",
        )


def _valid_portal_session(request: Request, settings) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    user_id = read_session_token(token, settings.portal_session_secret)
    if not user_id:
        return False
    store = PortalStore(settings.portal_db_file)
    user = store.get_user(int(user_id))
    return bool(user)


def _valid_basic_header(request: Request, settings) -> bool:
    header = str(request.headers.get("authorization") or "").strip()
    if not header.lower().startswith("basic "):
        return False
    raw = header[6:].strip()
    if not raw:
        return False
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    username, password = decoded.split(":", 1)
    user_ok = secrets.compare_digest(username, settings.dashboard_user)
    password_ok = secrets.compare_digest(password, settings.dashboard_password)
    return bool(user_ok and password_ok)


def _can_open_dashboard(request: Request, settings) -> bool:
    if _valid_portal_session(request, settings):
        return True
    local_hosts = {"127.0.0.1", "::1", "localhost"}
    req_host = str(request.url.hostname or "").strip().lower()
    client_host = str(request.client.host if request.client else "").strip().lower()
    if settings.test_mode and req_host in local_hosts and client_host in local_hosts:
        return True
    return _valid_basic_header(request, settings)


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/admin/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> str:
    settings = load_settings()
    if not _can_open_dashboard(request, settings):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    state = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    history = state.history or []
    visible_history = _green_red(history)
    scanner = _scanner_status(state, settings)
    stats = _stats(state, visible_history)
    rows = "\n".join(_row(item) for item in visible_history[:120])
    active_entries = _active_entries(history, state.active_signal)
    active_entry_rows = "\n".join(_active_entry_row(item) for item in active_entries)
    simulation_signals = _simulation_signals(state)
    opportunities = paper_opportunities(simulation_signals)
    simulated_session = _simulate_live_session(
        opportunities,
        total_games=30,
        bankroll_units=100.0,
        stake_percent=10.0,
    )
    simulation_history_panel = _simulation_history_panel(state.simulation_sessions or [])
    simulation_rows = "\n".join(_simulation_row(item) for item in opportunities)
    thermometer_rows = _thermometer_rows(opportunities)
    default_signal = simulation_signals[0] if simulation_signals else None
    match_stats = _match_stats_panel(default_signal, visible_history)
    best_simulation = _best_simulation(best_paper_entry(simulation_signals))
    sim_session_panel = _simulation_session_panel(simulated_session)
    simulator_updated_at = _short_datetime(state.last_scan_at)
    active = _active(state.active_signal)
    advice = _advice(visible_history)
    learning = _learning_context(state, visible_history)
    backtest = learning.get("backtest") or {}
    fast_learning = learning.get("fast_learning") or {}
    rankings = _rankings(learning)
    fast_panel = _fast_learning_panel(fast_learning)
    manual_stats = _manual_stats(visible_history)
    supabase_info = _supabase_info(settings)
    source_panel = _source_catalog_panel()
    commercial_panel = _commercial_panel(settings)
    fantasy_help = _fantasy_help_panel()
    championship_rows = _championship_rows(state.last_games or [])
    leadership_rows = _leadership_rows(state.last_games or [])
    market_tape = _market_tape(state.last_games or [])
    league_radar = _league_radar_panel(state.last_games or [])
    domains = _domains(settings.dashboard_domains)
    support = _esc(settings.support_note)
    product_name = _esc(settings.product_name)
    product_tagline = _esc(settings.product_tagline)
    build_stamp = _build_stamp()
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{product_name}</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bg: #0b0e11;
      --panel: #181a20;
      --panel-2: #1e2329;
      --line: #2b3139;
      --text: #eaecef;
      --muted: #848e9c;
      --green: #0ecb81;
      --red: #f6465d;
      --amber: #fcd535;
      --cyan: #4cc9f0;
      --blue: #5b8cff;
      --purple: #9b8cff;
      --header-bg: #111318;
      --ticker-bg: #0f1217;
      --sidebar-bg: #0f1217;
      --nav-bg: linear-gradient(180deg, #121922, #0f151d);
      --nav-hover-bg: #1e2530;
      --nav-text: #cbd8e7;
      --nav-border: #1f2937;
      --nav-hover-border: #3b4859;
      --mobile-nav-bg: #111318;
      --input-bg: #0b0e11;
      --table-head-bg: #1e2329;
      --table-row-hover: #202630;
      --fab-bg: #111b29;
      --fab-link-bg: #121c29;
      --fab-border: #4a5f78;
      --fab-link-border: #334155;
      --fab-link-text: #e6edf7;
      --title-text: #f5f6f7;
      --mini-bg: #11151c;
      --subtle-text: #b5c4d6;
      --ghost-bg: #1e2329;
      --ghost-border: #343b45;
      --ghost-text: #eaecef;
      --scan-toolbar-border: #2a3441;
      --scan-mode-bg: #111a26;
      --scan-mode-border: #334155;
      --scan-mode-text: #dbe8fb;
      --sim-kpi-bg: #11151c;
      --pulse-bg: #11151c;
      --pulse-meter-bg: #0b0e11;
      --pulse-meter-border: #2b3139;
      --match-visual-bg: radial-gradient(circle at 18% 22%, rgba(91, 140, 255, .16), transparent 34%), linear-gradient(140deg, #151a22 0%, #0e141c 100%);
      --match-visual-border: #2b3340;
      --match-avatar-bg: #111820;
      --match-avatar-border: #364455;
      --match-avatar-text: #edf3ff;
      --match-team-text: #f5f6f7;
      --match-track-bg: #0b0f15;
      --match-track-border: #2b3340;
      --stats-scroll-track: #11151c;
      --stats-scroll-thumb-border: #11151c;
      --stats-tab-text: #c7cbd1;
      --stats-tab-active-text: #fff;
      --live-card-bg: #1c1c1d;
      --live-card-border: #2e353d;
      --live-label: #f5f6f7;
      --dial-inner-bg: #181a20;
      --stat-bar-bg: #0b0e11;
      --row-click-hover: #26303d;
      --mobile-row-bg: linear-gradient(180deg, rgba(17, 29, 43, .96), rgba(10, 18, 28, .96));
      --shadow-soft: 0 10px 22px rgba(0, 0, 0, .38);
      --shadow-fab-link: 0 8px 16px rgba(0, 0, 0, .28);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: var(--header-bg);
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    .topbar {{
      align-items: center;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin: 0 auto;
      min-height: 58px;
      padding: 10px 18px;
    }}
    .topbar-actions {{
      align-items: center;
      display: flex;
      gap: 10px;
    }}
    .theme-toggle {{
      align-items: center;
      background: var(--panel-2);
      border: 1px solid #39414d;
      border-radius: 999px;
      color: var(--text);
      cursor: pointer;
      display: inline-flex;
      font-size: 12px;
      font-weight: 800;
      height: 32px;
      justify-content: center;
      margin-top: 0;
      min-width: 98px;
      padding: 0 12px;
      white-space: nowrap;
    }}
    .theme-toggle:hover {{
      border-color: var(--amber);
      color: var(--amber);
    }}
    .brand-line {{
      align-items: center;
      display: flex;
      gap: 10px;
    }}
    .brand-logo {{
      border: 1px solid #2a3649;
      border-radius: 10px;
      height: 36px;
      width: 36px;
      object-fit: cover;
    }}
    h1 {{ margin: 0; font-size: 22px; letter-spacing: 0; color: var(--amber); }}
    h2 {{ font-size: 13px; margin: 0 0 10px; text-transform: none; color: var(--title-text); }}
    strong, td, th, .metric {{ overflow-wrap: anywhere; }}
    main {{ padding: 12px; max-width: none; margin: 0; }}
    .ticker {{
      display: flex;
      gap: 18px;
      overflow-x: auto;
      padding: 7px 18px;
      border-top: 1px solid #1f242d;
      background: var(--ticker-bg);
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .ticker span {{ color: var(--green); font-weight: 700; }}
    .app-shell {{
      align-items: start;
      display: grid;
      gap: 10px;
      grid-template-columns: 214px minmax(0, 1fr);
      min-height: calc(100vh - 90px);
    }}
    .sidebar {{
      background: var(--sidebar-bg);
      border: 1px solid var(--line);
      border-radius: 10px;
      margin: 12px 0 0 12px;
      max-height: calc(100vh - 100px);
      overflow: auto;
      padding: 16px 12px;
      position: sticky;
      top: 84px;
    }}
    .mobile-nav {{
      display: none;
      gap: 8px;
      overflow-x: auto;
      padding: 10px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--mobile-nav-bg);
      position: sticky;
      top: 78px;
      z-index: 1;
    }}
    .nav-title {{ color: var(--muted); font-size: 11px; font-weight: 800; margin: 0 0 10px; text-transform: uppercase; }}
    .nav-link {{
      display: block;
      color: var(--nav-text);
      border: 1px solid var(--nav-border);
      border-radius: 8px;
      padding: 9px 10px;
      text-decoration: none;
      font-size: 13px;
      margin-bottom: 6px;
      background: var(--nav-bg);
    }}
    .nav-link:hover {{ background: var(--nav-hover-bg); border-color: var(--nav-hover-border); color: var(--amber); }}
    .mobile-nav .nav-link {{ white-space: nowrap; margin: 0; background: var(--panel); }}
    .layout {{
      display: grid;
      gap: 10px;
      grid-template-columns: 1fr;
    }}
    .layout-main {{
      display: grid;
      gap: 10px;
      grid-template-columns: minmax(0, 1fr);
    }}
    .layout-side {{
      display: flex;
      gap: 10px;
      overflow-x: auto;
      padding-bottom: 4px;
      scrollbar-color: var(--amber) #11151c;
      scrollbar-width: thin;
    }}
    .layout-side > .card {{
      flex: 0 0 min(420px, 92vw);
      margin-top: 0;
    }}
    .layout-side > .card.section {{ margin-top: 0; }}
    .wide-section {{ grid-column: 1 / -1; }}
    .history-table th:nth-child(1), .history-table td:nth-child(1) {{ width: 118px; }}
    .history-table th:nth-child(2), .history-table td:nth-child(2) {{ min-width: 190px; }}
    .history-table th:nth-child(4), .history-table td:nth-child(4) {{ min-width: 230px; }}
    .history-table th:nth-child(10), .history-table td:nth-child(10) {{ width: 148px; min-width: 148px; }}
    .grid {{ display: grid; grid-template-columns: repeat(7, minmax(120px, 1fr)); gap: 1px; margin: 0 0 8px; background: var(--line); border: 1px solid var(--line); }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 12px;
      box-shadow: none;
    }}
    .grid .card {{ border: 0; border-radius: 0; min-height: 66px; }}
    .metric {{ font-size: 22px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }}
    .metric.green, .win, .pos {{ color: var(--green); }}
    .metric.red, .loss, .neg {{ color: var(--red); }}
    .metric.amber, .void, .open, .flat {{ color: var(--amber); }}
    .muted {{ color: var(--muted); font-size: 12px; }}
    .card .muted {{ line-height: 1.35; }}
    .chip {{ border: 1px solid var(--line); border-radius: 4px; display: inline-flex; padding: 7px 10px; color: #111; background: var(--amber); font-size: 12px; font-weight: 800; gap: 6px; }}
    .chip.build-chip {{ background: var(--panel-2); color: var(--muted); }}
    .status-dot {{ width: 8px; height: 8px; border-radius: 999px; background: var(--green); margin-top: 4px; }}
    .status-dot.amber {{ background: var(--amber); }}
    .status-dot.red {{ background: var(--red); }}
    .active-title {{ font-size: 20px; font-weight: 800; margin-bottom: 8px; }}
    .active-line {{
      display: flex;
      gap: 10px;
      margin-top: 14px;
      overflow-x: auto;
      padding-bottom: 4px;
      scrollbar-color: var(--amber) #11151c;
      scrollbar-width: thin;
    }}
    .active-line .mini {{
      flex: 0 0 190px;
    }}
    .mini {{ background: var(--mini-bg); border: 1px solid var(--line); border-radius: 4px; padding: 9px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 4px; background: var(--panel); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); overflow: hidden; }}
    th, td {{ padding: 7px 8px; border-bottom: 1px solid #242a33; text-align: left; font-size: 12px; vertical-align: top; font-variant-numeric: tabular-nums; }}
    th {{ background: var(--table-head-bg); color: #aeb4bc; font-weight: 700; text-transform: none; font-size: 11px; }}
    tr:hover td {{ background: var(--table-row-hover); }}
    #simulador table {{ min-width: 1120px; }}
    #simulador table th:nth-child(1), #simulador table td:nth-child(1) {{ min-width: 240px; }}
    #simulador table th:nth-child(2), #simulador table td:nth-child(2) {{ min-width: 120px; }}
    #simulador table th:nth-child(3), #simulador table td:nth-child(3) {{ min-width: 116px; }}
    #simulador table th:nth-child(4), #simulador table td:nth-child(4) {{ width: 68px; min-width: 68px; }}
    #simulador table th:nth-child(5), #simulador table td:nth-child(5) {{ width: 78px; min-width: 78px; white-space: nowrap; }}
    #simulador table th:nth-child(6), #simulador table td:nth-child(6) {{ width: 116px; min-width: 116px; white-space: nowrap; }}
    #simulador table th:nth-child(7), #simulador table td:nth-child(7) {{ min-width: 108px; }}
    #simulador table th:nth-child(8), #simulador table td:nth-child(8) {{ min-width: 260px; }}
    .links a {{ display: inline-block; margin: 4px 10px 4px 0; color: var(--cyan); text-decoration: none; }}
    .section {{ margin-top: 12px; }}
    .subtle {{ color: var(--subtle-text); line-height: 1.45; }}
    textarea {{
      width: 100%;
      min-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--input-bg);
      color: var(--text);
      padding: 12px;
      font: inherit;
      font-size: 13px;
      line-height: 1.4;
    }}
    button {{
      border: 0;
      border-radius: 8px;
      background: var(--amber);
      color: #141414;
      cursor: pointer;
      font-weight: 800;
      margin-top: 10px;
      padding: 10px 14px;
    }}
    button:disabled {{ cursor: wait; opacity: .65; }}
    button.ghost {{
      align-items: center;
      background: var(--ghost-bg);
      border: 1px solid var(--ghost-border);
      border-radius: 4px;
      color: var(--ghost-text);
      display: inline-flex;
      font-size: 11px;
      gap: 5px;
      height: 28px;
      justify-content: center;
      margin-top: 0;
      min-width: 64px;
      padding: 0 8px;
    }}
    button.ghost:hover {{ border-color: var(--amber); color: var(--amber); }}
    button.danger {{
      background: rgba(255, 90, 104, .12);
      border-color: rgba(255, 90, 104, .36);
      color: var(--red);
    }}
    button.danger:hover {{ border-color: var(--red); color: #fff; }}
    button.success {{
      background: rgba(14, 203, 129, .12);
      border-color: rgba(14, 203, 129, .36);
      color: var(--green);
    }}
    button.success:hover {{ border-color: var(--green); color: #fff; }}
    .action-buttons {{
      align-items: center;
      display: flex;
      gap: 6px;
      justify-content: flex-end;
      white-space: nowrap;
    }}
    .action-pill {{
      align-items: center;
      border: 1px solid #3b4554;
      border-radius: 999px;
      display: inline-flex;
      font-size: 11px;
      font-weight: 800;
      height: 24px;
      justify-content: center;
      letter-spacing: .2px;
      min-width: 86px;
      padding: 0 10px;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .action-pill.enter {{ border-color: rgba(14, 203, 129, .48); color: var(--green); background: rgba(14, 203, 129, .14); }}
    .action-pill.wait {{ border-color: rgba(252, 213, 53, .45); color: var(--amber); background: rgba(252, 213, 53, .12); }}
    .action-pill.hold {{ border-color: rgba(91, 140, 255, .45); color: var(--blue); background: rgba(91, 140, 255, .12); }}
    .action-pill.exit {{ border-color: rgba(246, 70, 93, .45); color: var(--red); background: rgba(246, 70, 93, .12); }}
    .scan-toolbar {{
      align-items: center;
      border-top: 1px solid var(--scan-toolbar-border);
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
      padding-top: 12px;
    }}
    .scan-toolbar .ghost {{ height: 32px; }}
    .scan-mode {{
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .scan-mode-btn {{
      align-items: center;
      background: var(--scan-mode-bg);
      border: 1px solid var(--scan-mode-border);
      border-radius: 999px;
      color: var(--scan-mode-text);
      cursor: pointer;
      display: inline-flex;
      font-size: 11px;
      font-weight: 700;
      height: 30px;
      justify-content: center;
      margin-top: 0;
      padding: 0 12px;
      text-transform: none;
      width: auto;
    }}
    .scan-mode-btn.active {{
      background: rgba(252, 213, 53, .18);
      border-color: rgba(252, 213, 53, .52);
      color: var(--amber);
    }}
    .market-tape {{
      align-items: center;
      display: flex;
      gap: 8px;
      margin-bottom: 10px;
      overflow-x: auto;
      padding-bottom: 4px;
      white-space: nowrap;
    }}
    .tape-chip {{
      border: 1px solid #34455a;
      border-radius: 999px;
      display: inline-flex;
      font-size: 11px;
      font-weight: 700;
      gap: 6px;
      padding: 6px 10px;
    }}
    .tape-chip.up {{ color: var(--green); border-color: rgba(14, 203, 129, .45); background: rgba(14, 203, 129, .1); }}
    .tape-chip.mid {{ color: var(--amber); border-color: rgba(252, 213, 53, .4); background: rgba(252, 213, 53, .1); }}
    .tape-chip.down {{ color: var(--red); border-color: rgba(246, 70, 93, .4); background: rgba(246, 70, 93, .1); }}
    .market-board-grid {{
      display: grid;
      gap: 8px;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }}
    .notice {{ min-height: 20px; margin-top: 8px; }}
    .sim-lab-head {{
      align-items: center;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: space-between;
      margin-bottom: 10px;
    }}
    .sim-lab-actions {{
      display: inline-flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .sim-lab-grid {{
      display: flex;
      gap: 10px;
      overflow-x: auto;
      padding-bottom: 4px;
      scrollbar-color: var(--amber) #11151c;
      scrollbar-width: thin;
    }}
    .sim-lab-kpi {{
      background: var(--sim-kpi-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      flex: 0 0 172px;
      min-height: 66px;
      padding: 9px;
    }}
    .sim-lab-kpi .metric {{ font-size: 20px; }}
    .sim-lab-table-wrap {{
      border: 1px solid var(--line);
      border-radius: 4px;
      margin-top: 10px;
      max-height: 280px;
      overflow: auto;
    }}
    .sim-lab-table-wrap table {{ min-width: 920px; }}
    .sim-lab-note {{ margin-top: 8px; }}
    .sim-history-wrap {{
      border: 1px solid var(--line);
      border-radius: 4px;
      margin-top: 8px;
      max-height: 260px;
      overflow: auto;
    }}
    .sim-history-wrap table {{ min-width: 840px; }}
    .section-head {{
      align-items: center;
      display: flex;
      gap: 12px;
      justify-content: space-between;
      margin-bottom: 12px;
    }}
    .section-head h2 {{ margin: 0; }}
    #simulador {{
      min-height: 320px;
      position: relative;
      background:
        linear-gradient(rgba(43,49,57,.45) 1px, transparent 1px),
        linear-gradient(90deg, rgba(43,49,57,.45) 1px, transparent 1px),
        radial-gradient(circle at 50% 45%, rgba(252,213,53,.08), transparent 34%),
        var(--panel);
      background-size: 100% 40px, 80px 100%, auto, auto;
    }}
    #simulador::before {{
      content: "BETSIGNAL";
      position: absolute;
      inset: 120px 0 auto;
      text-align: center;
      color: rgba(132,142,156,.08);
      font-size: clamp(44px, 8vw, 92px);
      font-weight: 900;
      letter-spacing: 4px;
      pointer-events: none;
    }}
    #simulador > * {{ position: relative; }}
    .market-pulse {{
      display: grid;
      gap: 8px;
    }}
    .pulse-row {{
      align-items: center;
      background: var(--pulse-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      display: grid;
      gap: 10px;
      grid-template-columns: 34px minmax(0, 1fr) 88px 54px;
      padding: 9px;
    }}
    .pulse-rank {{
      color: var(--amber);
      font-size: 16px;
      font-weight: 900;
      text-align: center;
    }}
    .pulse-main strong {{ display: block; font-size: 13px; }}
    .pulse-main span {{ color: var(--muted); display: block; font-size: 11px; margin-top: 2px; }}
    .pulse-meter {{
      background: var(--pulse-meter-bg);
      border: 1px solid var(--pulse-meter-border);
      border-radius: 999px;
      height: 8px;
      overflow: hidden;
    }}
    .pulse-fill {{
      background: linear-gradient(90deg, var(--red), var(--amber), var(--green));
      height: 100%;
      transition: width .45s ease;
    }}
    .pulse-score {{
      font-size: 12px;
      font-weight: 800;
      text-align: right;
    }}
    .scanner-grid {{
      display: grid;
      gap: 8px;
      grid-template-columns: minmax(0, 2.2fr) minmax(360px, 1fr);
      align-items: start;
    }}
    .scanner-grid > .table-wrap {{
      max-height: calc(100vh - 142px);
      overflow: auto;
    }}
    .stats-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 4px;
      max-height: calc(100vh - 142px);
      min-height: min(320px, calc(100vh - 142px));
      overflow-y: auto;
      padding: 12px;
      position: sticky;
      scrollbar-color: var(--amber) #11151c;
      scrollbar-width: thin;
      top: 82px;
    }}
    .match-visual {{
      background: var(--match-visual-bg);
      border: 1px solid var(--match-visual-border);
      border-radius: 8px;
      margin-bottom: 10px;
      overflow: hidden;
      padding: 11px 12px 12px;
    }}
    .match-visual-head {{
      align-items: center;
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
    }}
    .match-visual-league {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }}
    .match-visual-minute {{
      background: rgba(252, 213, 53, .14);
      border: 1px solid rgba(252, 213, 53, .45);
      border-radius: 999px;
      color: var(--amber);
      font-size: 11px;
      font-weight: 800;
      padding: 2px 8px;
    }}
    .match-visual-score {{
      align-items: center;
      display: grid;
      gap: 8px;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
      margin-bottom: 10px;
    }}
    .match-team {{
      display: grid;
      gap: 4px;
      justify-items: center;
      min-width: 0;
      text-align: center;
    }}
    .match-avatar {{
      align-items: center;
      background: var(--match-avatar-bg);
      border: 1px solid var(--match-avatar-border);
      border-radius: 999px;
      color: var(--match-avatar-text);
      display: inline-flex;
      font-size: 11px;
      font-weight: 900;
      height: 30px;
      justify-content: center;
      width: 30px;
    }}
    .match-team-name {{
      color: var(--match-team-text);
      font-size: 12px;
      font-weight: 800;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      width: 100%;
    }}
    .match-goals {{
      color: var(--amber);
      font-size: 26px;
      font-weight: 900;
      letter-spacing: 0;
      min-width: 84px;
      text-align: center;
    }}
    .match-visual-bars {{
      display: grid;
      gap: 7px;
    }}
    .match-visual-row {{
      align-items: center;
      display: grid;
      gap: 8px;
      grid-template-columns: minmax(48px, auto) 1fr minmax(48px, auto);
    }}
    .match-visual-row strong {{
      font-size: 11px;
      font-weight: 800;
      text-align: center;
      white-space: nowrap;
    }}
    .match-visual-track {{
      background: var(--match-track-bg);
      border: 1px solid var(--match-track-border);
      border-radius: 999px;
      height: 8px;
      overflow: hidden;
      position: relative;
    }}
    .match-visual-track span {{
      background: linear-gradient(90deg, var(--blue), var(--cyan));
      display: block;
      height: 100%;
      width: var(--fill);
    }}
    .stats-panel::-webkit-scrollbar, .scanner-grid > .table-wrap::-webkit-scrollbar {{
      height: 10px;
      width: 10px;
    }}
    .stats-panel::-webkit-scrollbar-track, .scanner-grid > .table-wrap::-webkit-scrollbar-track {{
      background: var(--stats-scroll-track);
    }}
    .stats-panel::-webkit-scrollbar-thumb, .scanner-grid > .table-wrap::-webkit-scrollbar-thumb {{
      background: #fcd535;
      border: 2px solid var(--stats-scroll-thumb-border);
      border-radius: 999px;
    }}
    .stats-tabs {{
      border-bottom: 1px solid var(--line);
      display: flex;
      gap: 20px;
      margin: -2px -2px 12px;
      overflow-x: auto;
      padding: 0 2px;
    }}
    .stats-tab {{
      background: transparent;
      border: 0;
      color: var(--stats-tab-text);
      cursor: pointer;
      font-size: 12px;
      font-weight: 700;
      padding: 8px 0;
      white-space: nowrap;
    }}
    .stats-tab.active {{ border-bottom: 2px solid var(--amber); color: var(--stats-tab-active-text); }}
    .stats-pane {{ display: none; }}
    .stats-pane.active {{ display: block; }}
    .stats-title {{ font-size: 15px; font-weight: 900; margin-bottom: 4px; }}
    .live-card {{
      background: var(--live-card-bg);
      border: 1px solid var(--live-card-border);
      display: grid;
      gap: 12px;
      margin-top: 12px;
      padding: 14px 10px 16px;
    }}
    .live-xg {{
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: center;
      font-weight: 900;
    }}
    .live-xg span {{ color: var(--muted); font-size: 11px; font-weight: 700; }}
    .live-metrics {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .live-stat {{ text-align: center; }}
    .live-label {{ color: var(--live-label); font-size: 11px; margin-bottom: 6px; }}
    .live-dial-row {{
      align-items: center;
      display: grid;
      gap: 6px;
      grid-template-columns: minmax(28px, 1fr) 34px minmax(28px, 1fr);
    }}
    .live-num {{ font-size: 15px; font-weight: 900; }}
    .live-icons {{
      display: grid;
      gap: 5px;
      grid-template-columns: repeat(3, 1fr);
      margin-top: 9px;
      color: #f5f6f7;
      font-size: 12px;
      font-weight: 900;
    }}
    .flag, .card-dot {{ height: 8px; margin: 0 auto 4px; width: 8px; }}
    .flag {{ background: var(--red); clip-path: polygon(0 0, 100% 45%, 0 90%); }}
    .card-dot.red-card {{ background: var(--red); }}
    .card-dot.yellow-card {{ background: var(--amber); }}
    .shots-line {{ display: grid; grid-template-columns: 42px 1fr 42px; gap: 8px; align-items: center; }}
    .shots-line strong {{ font-size: 13px; text-align: center; }}
    .shots-track {{ background: #e7e9ed; height: 3px; position: relative; }}
    .shots-track span {{ background: var(--red); display: block; height: 3px; margin-left: var(--left-share); width: var(--right-share); }}
    .stats-grid {{ display: grid; gap: 10px; grid-template-columns: 1fr 1fr; margin-top: 12px; }}
    .stat-dial {{
      align-items: center;
      background: var(--mini-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      display: grid;
      gap: 8px;
      grid-template-columns: 1fr 54px 1fr;
      padding: 10px;
      text-align: center;
    }}
    .dial {{
      align-items: center;
      aspect-ratio: 1;
      background: conic-gradient(var(--red) 0 50%, #343b45 50% 100%);
      border-radius: 50%;
      display: grid;
      font-weight: 900;
      place-items: center;
      position: relative;
    }}
    .dial::after {{
      background: var(--dial-inner-bg);
      border-radius: 50%;
      content: "";
      height: 62%;
      position: absolute;
      width: 62%;
    }}
    .dial span {{ position: relative; z-index: 1; }}
    .stat-line {{
      background: var(--mini-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      margin-top: 8px;
      padding: 9px;
    }}
    .stat-bar {{ background: var(--stat-bar-bg); border-radius: 999px; height: 8px; overflow: hidden; }}
    .stat-bar span {{ background: var(--red); display: block; height: 100%; }}
    .clickable-row {{ cursor: pointer; }}
    .clickable-row:hover td {{ background: var(--row-click-hover); }}
    #ativo, #importar {{ min-height: 0; }}
    #ia table td:nth-child(2), #ia table td:nth-child(3), .history-table td:nth-child(5), .history-table td:nth-child(8) {{ text-align: right; }}
    .sparkline {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      align-items: end;
      gap: 4px;
      height: 64px;
      margin-top: 12px;
    }}
    .fab-shell {{
      position: fixed;
      right: 14px;
      bottom: 14px;
      z-index: 40;
      display: grid;
      gap: 8px;
      justify-items: end;
    }}
    .fab-main {{
      min-width: 50px;
      height: 50px;
      border-radius: 999px;
      border: 1px solid var(--fab-border);
      background: var(--fab-bg);
      color: var(--amber);
      font-weight: 900;
      box-shadow: var(--shadow-soft);
      margin-top: 0;
      padding: 0;
    }}
    .fab-links {{
      display: none;
      gap: 6px;
      justify-items: end;
    }}
    .fab-shell.open .fab-links {{
      display: grid;
    }}
    .fab-link {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--fab-link-border);
      border-radius: 10px;
      background: var(--fab-link-bg);
      color: var(--fab-link-text);
      padding: 8px 10px;
      text-decoration: none;
      font-size: 12px;
      box-shadow: var(--shadow-fab-link);
    }}
    .fab-main-text {{
      display: inline-block;
      font-size: 10px;
      line-height: 1;
      margin-top: -1px;
      text-transform: uppercase;
    }}
    .fab-link:hover {{
      color: var(--amber);
      border-color: #4d647d;
    }}
    .bar {{ background: var(--green); border-radius: 3px 3px 0 0; min-height: 8px; }}
    .bar.neg {{ background: var(--red); }}
    body[data-theme='light'] {{
      color-scheme: light;
      --bg: #f3f6fb;
      --panel: #ffffff;
      --panel-2: #edf2f9;
      --line: #d7dfeb;
      --text: #1f2937;
      --muted: #556070;
      --header-bg: #ffffff;
      --ticker-bg: #f7f9fc;
      --sidebar-bg: #f7f9fd;
      --nav-bg: linear-gradient(180deg, #ffffff, #f6f9ff);
      --nav-hover-bg: #ebf2ff;
      --input-bg: #ffffff;
      --table-head-bg: #f1f5fb;
      --table-row-hover: #f6faff;
      --fab-bg: #ffffff;
      --fab-link-bg: #ffffff;
      --fab-border: #b6c2d2;
      --fab-link-border: #c3cfdd;
      --fab-link-text: #27364a;
      --title-text: #1f2937;
      --mini-bg: #f7faff;
      --subtle-text: #4f5f73;
      --ghost-bg: #f6f9ff;
      --ghost-border: #cfd9e7;
      --ghost-text: #24354a;
      --scan-toolbar-border: #d9e2ef;
      --scan-mode-bg: #f5f8ff;
      --scan-mode-border: #c8d6ea;
      --scan-mode-text: #27405f;
      --sim-kpi-bg: #f7faff;
      --pulse-bg: #f8fbff;
      --pulse-meter-bg: #e7eef8;
      --pulse-meter-border: #d0dceb;
      --match-visual-bg: radial-gradient(circle at 18% 22%, rgba(91, 140, 255, .12), transparent 38%), linear-gradient(140deg, #f8fbff 0%, #eef4fc 100%);
      --match-visual-border: #d8e1ed;
      --match-avatar-bg: #f1f5fb;
      --match-avatar-border: #cfdaea;
      --match-avatar-text: #1b2c45;
      --match-team-text: #22364f;
      --match-track-bg: #e9f0fb;
      --match-track-border: #d5e0ee;
      --stats-scroll-track: #e6edf7;
      --stats-scroll-thumb-border: #e6edf7;
      --stats-tab-text: #5c6675;
      --stats-tab-active-text: #111827;
      --live-card-bg: #f8fbff;
      --live-card-border: #d8e1ed;
      --live-label: #30455f;
      --dial-inner-bg: #f8fbff;
      --stat-bar-bg: #e4ecf8;
      --row-click-hover: #edf4ff;
      --mobile-row-bg: linear-gradient(180deg, rgba(246, 250, 255, .98), rgba(236, 244, 253, .98));
      --nav-text: #2d3f56;
      --nav-border: #cad6e6;
      --nav-hover-border: #aebfd5;
      --mobile-nav-bg: #f7f9fc;
      --shadow-soft: 0 8px 20px rgba(12, 24, 42, .14);
      --shadow-fab-link: 0 8px 16px rgba(12, 24, 42, .12);
    }}
    body[data-theme='light'] header {{ border-bottom-color: #d9e2ef; }}
    body[data-theme='light'] .ticker {{ border-top-color: #dde5f0; }}
    body[data-theme='light'] .chip {{
      background: #f9f2ce;
      border-color: #e7d9a8;
      color: #2f2a13;
    }}
    body[data-theme='light'] .chip .status-dot {{ background: #0fa36c; }}
    body[data-theme='light'] .mini,
    body[data-theme='light'] .stat-line,
    body[data-theme='light'] .live-card,
    body[data-theme='light'] .pulse-row {{
      background: #ffffff;
    }}
    body[data-theme='light'] .live-card,
    body[data-theme='light'] .stat-line,
    body[data-theme='light'] .pulse-row,
    body[data-theme='light'] .dial,
    body[data-theme='light'] .match-visual {{
      border-color: #d8e1ed;
    }}
    body[data-theme='light'] .table-wrap {{ background: #ffffff; }}
    body[data-theme='light'] th {{ color: #506074; }}
    body[data-theme='light'] td {{ border-bottom-color: #e8eef7; }}
    body[data-theme='light'] .shots-track {{ background: #dde4f0; }}
    body[data-theme='light'] .stats-panel::-webkit-scrollbar-track,
    body[data-theme='light'] .scanner-grid > .table-wrap::-webkit-scrollbar-track {{
      background: #e6edf7;
    }}
    body[data-theme='light'] .stats-panel::-webkit-scrollbar-thumb,
    body[data-theme='light'] .scanner-grid > .table-wrap::-webkit-scrollbar-thumb {{
      border-color: #e6edf7;
    }}
    body[data-theme='light'] .stats-tab {{ color: var(--stats-tab-text); }}
    body[data-theme='light'] .stats-tab.active {{ color: var(--stats-tab-active-text); }}
    @media (max-width: 1240px) {{
      .scanner-grid {{ grid-template-columns: 1fr; }}
      .scanner-grid > .table-wrap {{ max-height: none; overflow: auto; }}
      .stats-panel {{ max-height: none; min-height: 360px; position: static; }}
    }}
    @media (max-width: 980px) {{
      .app-shell {{ grid-template-columns: 1fr; }}
      .sidebar {{ display: none; }}
      .mobile-nav {{ display: flex; }}
      .layout {{ grid-template-columns: 1fr; }}
      .layout-side > .card {{
        flex-basis: min(360px, 90vw);
      }}
      .scanner-grid {{ grid-template-columns: 1fr; }}
      .market-board-grid {{ grid-template-columns: 1fr; }}
      .scanner-grid > .table-wrap {{ max-height: none; overflow: visible; }}
      .stats-panel {{
        max-height: 72vh;
        min-height: 260px;
        position: static;
      }}
      .wide-section {{ grid-column: auto; }}
      .history-table th, .history-table td {{ width: auto; min-width: 0; }}
      .grid {{ grid-template-columns: 1fr 1fr; }}
      .live-metrics {{ grid-template-columns: 1fr; }}
      .active-line {{ gap: 8px; }}
      .table-wrap {{ border: 0; background: transparent; overflow: visible; }}
      table.responsive {{ display: block; background: transparent; border: 0; }}
      table.responsive thead {{ display: none; }}
      table.responsive tbody {{ display: grid; gap: 10px; }}
      table.responsive tr {{
        display: grid;
        gap: 7px;
        background: var(--mobile-row-bg);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
      }}
      table.responsive td {{
        border: 0;
        display: grid;
        grid-template-columns: 92px minmax(0, 1fr);
        gap: 8px;
        padding: 0;
        font-size: 13px;
        line-height: 1.35;
      }}
      table.responsive td::before {{
        content: attr(data-label);
        color: var(--muted);
        font-size: 11px;
        font-weight: 800;
        text-transform: uppercase;
      }}
      .action-buttons {{ justify-content: stretch; }}
      .action-buttons button {{ flex: 1; min-width: 0; }}
      .action-pill {{ min-width: 94px; }}
      main {{ padding: 14px 12px 28px; }}
      .topbar {{ padding: 14px 16px; }}
      h1 {{ font-size: 20px; }}
      .metric {{ font-size: 22px; }}
      th, td {{ padding: 10px 8px; font-size: 12px; }}
      .fab-shell {{
        right: 10px;
        bottom: 10px;
      }}
      .fab-link {{
        font-size: 11px;
        padding: 7px 9px;
      }}
    }}
    @media (max-width: 520px) {{
      .grid {{
        display: grid;
        gap: 8px;
        grid-auto-columns: minmax(168px, 1fr);
        grid-auto-flow: column;
        grid-template-columns: none;
        overflow-x: auto;
        padding-bottom: 4px;
      }}
      .ticker {{ padding: 8px 12px; }}
      .topbar {{ align-items: flex-start; flex-direction: column; gap: 10px; }}
      .mobile-nav {{ top: 116px; padding-inline: 12px; }}
      table.responsive td {{ grid-template-columns: 78px minmax(0, 1fr); }}
      textarea {{ min-height: 132px; }}
      button {{ width: 100%; }}
      .scan-toolbar {{ align-items: stretch; flex-direction: column; }}
      .scan-mode {{ width: 100%; }}
      .scan-mode-btn {{ flex: 1; min-width: 0; }}
    }}
  </style>
</head>
<body data-theme="dark">
  <header id="top">
    <div class="topbar">
      <div class="brand-line">
        <img class="brand-logo" src="/assets/logo-apexgol-mark.svg" alt="ApexGol AI" />
        <div>
          <h1>{product_name}</h1>
          <div class="muted">{product_tagline}</div>
        </div>
      </div>
      <div class="topbar-actions">
        <div class="chip build-chip">Build {build_stamp}</div>
        <button class="theme-toggle" id="theme-toggle" type="button" onclick="toggleTheme()" aria-pressed="false">Tema claro</button>
        <div class="chip"><span class="status-dot"></span> Sistema online</div>
      </div>
    </div>
    <div class="ticker">
      <div>GREENS <span>{stats["wins"]}</span></div>
      <div>REDS <span class="neg">{stats["losses"]}</span></div>
      <div>HIT RATE <span>{stats["hit_rate"]}%</span></div>
      <div>ROI <span class="{_value_class(stats["roi_units"])}">{stats["roi_units"]}%</span></div>
      <div>LUCRO <span class="{_value_class(stats["profit_units"])}">{stats["profit_units"]}u</span></div>
      <div>IA <span>{stats["readiness"]}</span></div>
    </div>
  </header>
  <nav class="mobile-nav">
    <a class="nav-link" href="#scanner">Scanner</a>
    <a class="nav-link" href="#mercado">Mercado</a>
    <a class="nav-link" href="#simulador">Simulador</a>
    <a class="nav-link" href="#fantasy">Fantasy</a>
    <a class="nav-link" href="#entradas">Entradas</a>
    <a class="nav-link" href="#importar">Resultados</a>
    <a class="nav-link" href="#historico">Historico</a>
    <a class="nav-link" href="#comercial">Comercial</a>
    <a class="nav-link" href="#supabase">Supabase</a>
  </nav>
  <div class="app-shell">
  <aside class="sidebar">
    <p class="nav-title">Operacao</p>
    <a class="nav-link" href="#scanner">Scanner</a>
    <a class="nav-link" href="#mercado">Mercado</a>
    <a class="nav-link" href="#simulador">Simulador</a>
    <a class="nav-link" href="#fantasy">Fantasy campeao</a>
    <a class="nav-link" href="#ativo">Jogo ativo</a>
    <a class="nav-link" href="#entradas">Entradas</a>
    <a class="nav-link" href="#importar">Importar resultados</a>
    <a class="nav-link" href="#historico">Historico</a>
    <a class="nav-link" href="#comercial">Comercial</a>
    <a class="nav-link" href="#ia">Rankings IA</a>
    <a class="nav-link" href="/api/state">API JSON</a>
  </aside>
  <main>
    <section class="grid">
      <div class="card"><div class="metric">{stats["total"]}</div><div class="muted">Sinais registrados</div></div>
      <div class="card"><div class="metric green">{stats["wins"]}</div><div class="muted">Greens</div></div>
      <div class="card"><div class="metric red">{stats["losses"]}</div><div class="muted">Reds</div></div>
      <div class="card"><div class="metric">{stats["hit_rate"]}%</div><div class="muted">Taxa de acerto</div></div>
      <div class="card"><div class="metric {_value_class(manual_stats["profit_currency"])}">R$ {manual_stats["profit_currency"]}</div><div class="muted">Resultado manual</div></div>
      <div class="card"><div class="metric">{manual_stats["total"]}</div><div class="muted">Apostas importadas</div></div>
      <div class="card"><div class="metric {_value_class(stats["profit_units"])}">{stats["profit_units"]}u</div><div class="muted">Lucro em unidades</div></div>
      <div class="card"><div class="metric {_value_class(stats["roi_units"])}">{stats["roi_units"]}%</div><div class="muted">ROI por unidade</div></div>
      <div class="card"><div class="metric amber">{stats["brier_score"]}</div><div class="muted">Brier score</div></div>
      <div class="card"><div class="metric">{stats["readiness"]}</div><div class="muted">Maturidade IA</div></div>
      <div class="card"><div class="metric">{_esc(fast_learning.get("mode", "neutro"))}</div><div class="muted">Modo rapido</div></div>
      <div class="card"><div class="metric">{_esc(fast_learning.get("momentum_score", 50))}/100</div><div class="muted">Momentum IA</div></div>
      <div class="card"><div class="metric {_value_class(backtest.get("profit_units"))}">{backtest.get("profit_units", 0)}u</div><div class="muted">Backtest lucro</div></div>
      <div class="card"><div class="metric red">{backtest.get("max_drawdown_units", 0)}u</div><div class="muted">Drawdown max</div></div>
      <div class="card"><div class="metric">{_best_name(learning.get("by_market"))}</div><div class="muted">Melhor mercado</div></div>
      <div class="card"><div class="metric">{_best_name(learning.get("by_team"))}</div><div class="muted">Melhor time</div></div>
    </section>
    <section class="card section" id="scanner">
      <h2>Scanner Mundial</h2>
      <div class="active-line">
        <div class="mini"><div class="muted">Modo</div><strong id="scan-mode-current">{scanner["mode"]}</strong></div>
        <div class="mini"><div class="muted">Perfil scan</div><strong id="scan-profile-current">{scanner["scan_profile"]}</strong></div>
        <div class="mini"><div class="muted">Ultimo ciclo</div><strong id="scan-last-current">{scanner["last_scan"]}</strong></div>
        <div class="mini"><div class="muted">Candidatos</div><strong id="scan-candidates-current">{scanner["candidates"]}</strong></div>
        <div class="mini"><div class="muted">Jogos do dia</div><strong id="scan-today-current">{scanner["today_games"]}</strong></div>
        <div class="mini"><div class="muted">Cobertura</div><strong>Brasil primeiro + ligas globais ESPN</strong></div>
        <div class="mini"><div class="muted">Seguranca</div><strong>HTTPS, Basic Auth, headers anti-frame</strong></div>
        <div class="mini"><div class="muted">Status</div><strong id="scan-status-current">{scanner["status"]}</strong></div>
      </div>
      <div class="scan-toolbar">
        <div class="scan-mode" id="scan-mode">
          <button class="scan-mode-btn" data-mode="brazil_first" type="button" onclick="setScanMode('brazil_first')">Brasil -> Mundo</button>
          <button class="scan-mode-btn" data-mode="world_first" type="button" onclick="setScanMode('world_first')">Mundo -> Brasil</button>
          <button class="scan-mode-btn" data-mode="live_only" type="button" onclick="setScanMode('live_only')">Somente ao vivo</button>
        </div>
        <button class="ghost" type="button" onclick="requestScanNow()">Scan agora</button>
        <span class="muted" id="scan-control-note">Modo atual: {scanner["scan_profile"]}</span>
      </div>
    </section>
    <section class="card section" id="mercado">
      <div class="section-head">
        <h2>Painel De Campeonatos E Liderancas</h2>
        <span id="market-board-status" class="muted">Atualizado: {simulator_updated_at}</span>
      </div>
      <div id="league-tape" class="market-tape">{market_tape}</div>
      <div class="market-board-grid">
        <div class="table-wrap"><table class="responsive">
          <thead><tr><th>Campeonato</th><th>Jogos</th><th>Gols</th><th>Lider live</th><th>Ritmo</th></tr></thead>
          <tbody id="champ-board-rows">{championship_rows}</tbody>
        </table></div>
        <div class="table-wrap"><table class="responsive">
          <thead><tr><th>Time</th><th>Liga</th><th>Placar</th><th>Min</th><th>Momentum</th></tr></thead>
          <tbody id="leader-board-rows">{leadership_rows}</tbody>
        </table></div>
      </div>
    </section>
    <section class="card section" id="supabase">
      <h2>Supabase e Fontes</h2>
      <div class="active-line">
        <div class="mini"><div class="muted">Supabase</div><strong>{supabase_info["status"]}</strong></div>
        <div class="mini"><div class="muted">Jogos</div><strong>{supabase_info["games"]}</strong></div>
        <div class="mini"><div class="muted">Sinais</div><strong>{supabase_info["signals"]}</strong></div>
        <div class="mini"><div class="muted">Memoria IA</div><strong>{supabase_info["memory"]}</strong></div>
        <div class="mini"><div class="muted">Fontes IA</div><strong>{len(FOOTBALL_DATA_SOURCES)} catalogadas</strong></div>
        <div class="mini"><div class="muted">Modo</div><strong>Simulacao, sem auto-aposta</strong></div>
      </div>
    </section>
    <section class="card section" id="simulador">
      <div class="section-head">
        <h2>Simulador Em Tempo Real</h2>
        <span id="simulator-status" class="muted">Atualizado: {simulator_updated_at} | auto 60 min</span>
      </div>
      <div id="simulator-best">{best_simulation}</div>
      <section class="card section" id="sim-lab">
        <div class="sim-lab-head">
          <h2>Laboratorio IA Ao Vivo</h2>
          <div class="sim-lab-actions">
            <button class="ghost" type="button" onclick="runSimulationSession(30)">Rodar 30 jogos</button>
            <button class="ghost" type="button" onclick="runSimulationSession(45)">Rodar 45 jogos</button>
          </div>
        </div>
        <div id="sim-session-panel">{sim_session_panel}</div>
        <section class="section" id="sim-lab-history">
          <h2>Historico Das Simulacoes IA</h2>
          <div id="sim-session-history">{simulation_history_panel}</div>
        </section>
      </section>
      <section class="card section" id="termometro">
        <div class="section-head">
          <h2>Termometro Do Jogo</h2>
          <span class="muted">ranking dinamico de entradas</span>
        </div>
        <div id="thermometer-rows" class="market-pulse">{thermometer_rows}</div>
      </section>
      <div class="scanner-grid">
        <div class="table-wrap"><table class="responsive">
          <thead><tr><th>Jogo</th><th>Mercado</th><th>Selecao</th><th>Linha</th><th>Odd</th><th>Acao</th><th>Score</th><th>Leitura</th></tr></thead>
          <tbody id="simulator-rows">{simulation_rows or '<tr><td colspan="8">Aguardando sinais do scanner.</td></tr>'}</tbody>
        </table></div>
        <aside class="stats-panel" id="match-stats">{match_stats}</aside>
      </div>
    </section>
    <div class="layout">
      <div class="layout-main">
        <section class="card" id="ativo">
          <h2>Jogo Ativo</h2>
          {active}
        </section>
        <section class="section" id="entradas">
          <h2>Entradas Em Andamento</h2>
          <div class="table-wrap"><table class="responsive">
            <thead><tr><th>Entrada</th><th>Jogo</th><th>Mercado</th><th>Valor</th><th>Odd</th><th>Acao</th></tr></thead>
            <tbody>{active_entry_rows or '<tr><td colspan="6">Nenhuma entrada em andamento.</td></tr>'}</tbody>
          </table></div>
        </section>
        <section class="card section" id="importar">
          <h2>Importar resultados</h2>
          <p class="muted">Cole texto bruto ou HTML copiado da casa de aposta. O sistema limpa scripts/metadados e importa somente linhas com valor, mercado e resultado.</p>
          <textarea id="bet365-import" placeholder="Cole aqui seu historico de resultados (texto ou HTML)"></textarea>
          <button id="import-button" type="button" onclick="importHistory()">Importar resultados</button>
          <div id="import-result" class="notice muted"></div>
        </section>
        <section class="card section">
          <h2>Curva da Banca</h2>
          {_equity_curve(backtest)}
        </section>
      </div>
      <aside class="layout-side">
        <section class="card">
          <h2>Leitura Para Proximos Jogos</h2>
          <p class="subtle">{advice}</p>
          <p class="muted">Amostra usada pela IA: {learning.get("sample_size", 0)} resultados fechados.</p>
        </section>
        <section class="card section" id="ia">
          <h2>Rankings IA</h2>
          {rankings}
        </section>
        <section class="card section" id="fantasy">
          <h2>Fantasy Campeao</h2>
          <p class="muted">Cole jogadores no formato <code>Nome;Posicao;Time;Preco;Projecao</code>. Ex.: <code>Yuri Alberto;ATA;Corinthians;16.5;8.2</code>.</p>
          <label>Formacao</label>
          <select id="fantasy-formation">
            <option value="4-4-2">4-4-2</option>
            <option value="4-3-3">4-3-3</option>
            <option value="5-3-2">5-3-2</option>
            <option value="3-5-2">3-5-2</option>
            <option value="3-4-3">3-4-3</option>
            <option value="4-5-1">4-5-1</option>
            <option value="5-4-1">5-4-1</option>
          </select>
          <label>Orcamento</label>
          <input id="fantasy-budget" type="number" step="0.1" min="20" value="120" />
          <textarea id="fantasy-players" placeholder="Nome;Posicao;Time;Preco;Projecao&#10;Cacá;ZAG;Corinthians;9.8;5.1&#10;Hugo;LAT;Corinthians;11.3;5.9"></textarea>
          <button type="button" class="ghost" onclick="buildFantasyLineup()">Montar escalação campea</button>
          <div id="fantasy-note" class="notice muted"></div>
          <div id="fantasy-result">{fantasy_help}</div>
        </section>
        <section class="card section">
          <h2>Aprendizado Rapido</h2>
          {fast_panel}
        </section>
        <section class="card section">
          <h2>Fontes Para IA</h2>
          {source_panel}
        </section>
        <section class="card section">
          <h2>Radar De Ligas</h2>
          {league_radar}
        </section>
        <section class="card section" id="comercial">
          <h2>Oferta Comercial</h2>
          {commercial_panel}
        </section>
        <section class="card section">
          <h2>Acessos</h2>
          <div class="links">{domains}</div>
          <p class="muted">Se o dominio nao abrir, confirme DNS A para 2.24.217.214.</p>
        </section>
        <section class="card section">
          <h2>Suporte</h2>
          <p class="subtle">{support}</p>
          <p class="muted">No Telegram use /suporte para resumo tecnico.</p>
        </section>
      </aside>
    </div>
    <section class="section wide-section" id="historico">
      <h2>Historico de Operacoes</h2>
      <div class="table-wrap"><table class="responsive history-table">
        <thead><tr><th>Data</th><th>Jogo</th><th>Liga</th><th>Entrada</th><th>Valor real</th><th>Conf.</th><th>Edge</th><th>Stake</th><th>Resultado</th><th>Acao</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="10">Nenhum green/red registrado ainda.</td></tr>'}</tbody>
      </table></div>
    </section>
  </main>
  </div>
  <div class="fab-shell" id="fab-menu">
    <div class="fab-links">
      <a class="fab-link" href="#scanner">Scanner</a>
      <a class="fab-link" href="#mercado">Mercado</a>
      <a class="fab-link" href="#simulador">Simulador</a>
      <a class="fab-link" href="#fantasy">Fantasy</a>
      <a class="fab-link" href="#ativo">Jogo ativo</a>
      <a class="fab-link" href="#entradas">Entradas</a>
      <a class="fab-link" href="#historico">Historico</a>
      <a class="fab-link" href="#ia">IA</a>
      <a class="fab-link" href="#top">Topo</a>
    </div>
    <button class="fab-main" id="fab-main" type="button" onclick="toggleFabMenu()" aria-label="Abrir menu rapido" aria-expanded="false">
      ☰
      <span class="fab-main-text">Operacao</span>
    </button>
  </div>
</body>
<script>
  function applyTheme(theme) {{
    const nextTheme = theme === 'light' ? 'light' : 'dark';
    document.body.setAttribute('data-theme', nextTheme);
    const button = document.getElementById('theme-toggle');
    if (button) {{
      const isLight = nextTheme === 'light';
      button.textContent = isLight ? 'Tema escuro' : 'Tema claro';
      button.setAttribute('aria-pressed', isLight ? 'true' : 'false');
    }}
    try {{
      localStorage.setItem('apexgol-theme', nextTheme);
    }} catch (error) {{
      // nao bloqueia a tela se localStorage estiver indisponivel
    }}
  }}

  function toggleTheme() {{
    const current = document.body.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'light' ? 'dark' : 'light');
  }}

  function initTheme() {{
    let preferred = 'dark';
    try {{
      const stored = localStorage.getItem('apexgol-theme');
      if (stored === 'light' || stored === 'dark') {{
        preferred = stored;
      }} else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {{
        preferred = 'light';
      }}
    }} catch (error) {{
      preferred = 'dark';
    }}
    applyTheme(preferred);
  }}

  function toggleFabMenu() {{
    const shell = document.getElementById('fab-menu');
    const button = document.getElementById('fab-main');
    if (!shell) return;
    shell.classList.toggle('open');
    if (button) {{
      const expanded = shell.classList.contains('open');
      button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }}
  }}

  document.querySelectorAll('#fab-menu .fab-link').forEach((link) => {{
    link.addEventListener('click', () => {{
      const shell = document.getElementById('fab-menu');
      const button = document.getElementById('fab-main');
      if (!shell) return;
      shell.classList.remove('open');
      if (button) button.setAttribute('aria-expanded', 'false');
    }});
  }});

  document.addEventListener('click', (event) => {{
    const shell = document.getElementById('fab-menu');
    const button = document.getElementById('fab-main');
    if (!shell || !shell.classList.contains('open')) return;
    if (shell.contains(event.target)) return;
    shell.classList.remove('open');
    if (button) button.setAttribute('aria-expanded', 'false');
  }});

  document.addEventListener('keydown', (event) => {{
    if (event.key !== 'Escape') return;
    const shell = document.getElementById('fab-menu');
    const button = document.getElementById('fab-main');
    if (!shell) return;
    shell.classList.remove('open');
    if (button) button.setAttribute('aria-expanded', 'false');
  }});

  function setScanModeButtons(mode) {{
    document.querySelectorAll('.scan-mode-btn').forEach((button) => {{
      const isActive = button.dataset.mode === mode;
      button.classList.toggle('active', isActive);
    }});
  }}

  async function loadScanConfig() {{
    const note = document.getElementById('scan-control-note');
    try {{
      const response = await fetch('/api/scanner-preference', {{cache: 'no-store'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao carregar scanner');
      setScanModeButtons(data.mode);
      if (note) note.textContent = `Modo atual: ${{data.mode_label}}`;
    }} catch (error) {{
      if (note) note.textContent = error.message;
    }}
  }}

  async function setScanMode(mode) {{
    const note = document.getElementById('scan-control-note');
    if (note) note.textContent = 'Salvando modo de scanner...';
    try {{
      const response = await fetch('/api/scanner-preference', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{mode}})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao salvar modo');
      setScanModeButtons(data.mode);
      if (note) note.textContent = `Modo atual: ${{data.mode_label}}`;
      window.setTimeout(() => requestScanNow(), 120);
    }} catch (error) {{
      if (note) note.textContent = error.message;
    }}
  }}

  function applyScannerSnapshot(data) {{
    const scanner = (data && data.scanner) || {{}};
    const mappings = [
      ['scan-mode-current', scanner.mode],
      ['scan-profile-current', scanner.scan_profile],
      ['scan-last-current', scanner.last_scan],
      ['scan-candidates-current', scanner.candidates],
      ['scan-today-current', scanner.today_games],
      ['scan-status-current', scanner.status],
    ];
    mappings.forEach(([id, value]) => {{
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.textContent = String(value);
    }});
  }}

  async function refreshRuntimeState() {{
    const response = await fetch('/api/state', {{cache: 'no-store'}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Falha ao atualizar estado');
    applyScannerSnapshot(data);
    return data;
  }}

  async function requestScanNow(options = {{}}) {{
    const note = document.getElementById('scan-control-note');
    const silent = Boolean(options.silent);
    if (window.__scanInFlight) return;
    window.__scanInFlight = true;
    if (note) note.textContent = silent ? 'Executando scanner automatico...' : 'Executando scanner agora...';
    try {{
      const response = await fetch('/api/scanner-run', {{method: 'POST'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao solicitar scan');
      if (note) note.textContent = `${{data.message}} (${{data.candidates}} candidatos)`;
      await loadScanConfig();
      await refreshRuntimeState();
      window.setTimeout(refreshSimulator, 800);
    }} catch (error) {{
      if (note) note.textContent = error.message;
    }} finally {{
      window.__scanInFlight = false;
    }}
  }}

  async function autoScanTick() {{
    const note = document.getElementById('scan-control-note');
    try {{
      const data = await refreshRuntimeState();
      const scanner = data.scanner || {{}};
      const lastScanIso = scanner.last_scan_iso ? Date.parse(scanner.last_scan_iso) : 0;
      const intervalSeconds = Number(scanner.auto_scan_interval_seconds || 1800);
      const overdue = !lastScanIso || ((Date.now() - lastScanIso) >= intervalSeconds * 1000);
      if (!overdue || window.__scanInFlight) return;
      if (note) note.textContent = `Scanner em atraso, novo ciclo automatico (${{
        Math.round(intervalSeconds / 60)
      }} min)...`;
      await requestScanNow({{silent: true}});
    }} catch (error) {{
      if (note) note.textContent = error.message;
    }}
  }}

  async function runSimulationSession(totalGames = 30) {{
    const panel = document.getElementById('sim-session-panel');
    if (!panel) return;
    panel.innerHTML = '<p class="muted">Rodando simulacao ao vivo com banca fake...</p>';
    try {{
      const response = await fetch('/api/simulator-session', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{games: totalGames, bankroll: 100, stake_percent: 10}})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao rodar simulacao');
      panel.innerHTML = data.panel_html || '<p class="muted">Sem dados de simulacao.</p>';
      const historyPanel = document.getElementById('sim-session-history');
      if (historyPanel && data.history_html) {{
        historyPanel.innerHTML = data.history_html;
      }}
      const status = document.getElementById('simulator-status');
      if (status && data.updated_at) {{
        status.textContent = `Atualizado: ${{data.updated_at}} | auto 60 min`;
      }}
    }} catch (error) {{
      panel.innerHTML = `<p class="muted">${{error.message}}</p>`;
    }}
  }}

  async function buildFantasyLineup() {{
    const note = document.getElementById('fantasy-note');
    const result = document.getElementById('fantasy-result');
    const playersText = (document.getElementById('fantasy-players')?.value || '').trim();
    const formation = document.getElementById('fantasy-formation')?.value || '4-4-2';
    const budget = Number(document.getElementById('fantasy-budget')?.value || '120');
    if (!playersText) {{
      if (note) note.textContent = 'Cole os jogadores para gerar a escalação.';
      return;
    }}
    if (note) note.textContent = 'Calculando melhor escalação...';
    if (result) result.innerHTML = '<p class=\"muted\">Processando lista de jogadores...</p>';
    try {{
      const response = await fetch('/api/fantasy-lineup', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          players_text: playersText,
          formation,
          budget
        }})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao montar escalação.');
      if (result) result.innerHTML = data.html || '<p class=\"muted\">Sem resultado.</p>';
      if (note) note.textContent = data.message || 'Escalação gerada.';
    }} catch (error) {{
      if (result) result.innerHTML = '';
      if (note) note.textContent = error.message;
    }}
  }}

  async function importHistory() {{
    const box = document.getElementById('bet365-import');
    const button = document.getElementById('import-button');
    const result = document.getElementById('import-result');
    const text = box.value.trim();
    if (!text) {{
      result.textContent = 'Cole o historico antes de importar.';
      return;
    }}
    button.disabled = true;
    result.textContent = 'Importando...';
    try {{
      const response = await fetch('/api/import-history', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{text}})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao importar');
      result.textContent = `Importado: ${{data.imported}} | Greens: ${{data.wins}} | Reds: ${{data.losses}}`;
      setTimeout(() => window.location.reload(), 900);
    }} catch (error) {{
      result.textContent = error.message;
    }} finally {{
      button.disabled = false;
    }}
  }}

  function parseMoneyInput(value) {{
    if (value === null) return null;
    let clean = value.trim().replace('R$', '').replace(/\\s/g, '');
    if (clean.includes(',')) {{
      clean = clean.replace(/\\./g, '').replace(',', '.');
    }}
    if (!clean) return null;
    const number = Number(clean);
    return Number.isFinite(number) ? number : null;
  }}

  async function editHistoryValue(signalId, currentValue, currentOdds, currentProfit) {{
    const value = parseMoneyInput(prompt('Valor real apostado em R$:', currentValue || ''));
    if (value === null) return;
    const odds = parseMoneyInput(prompt('Odd real usada:', currentOdds || ''));
    const profit = parseMoneyInput(prompt('Lucro real em R$ se foi Green. Deixe vazio para calcular pela odd:', currentProfit || ''));
    try {{
      const response = await fetch('/api/history-value', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          signal_id: signalId,
          entry_value: value,
          entry_odds: odds,
          profit_value: profit
        }})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao salvar valor');
      window.location.reload();
    }} catch (error) {{
      alert(error.message);
    }}
  }}

  async function deleteHistoryRecord(signalId) {{
    if (!confirm('Excluir este registro do historico?')) return;
    try {{
      const response = await fetch('/api/history-delete', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{signal_id: signalId}})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao excluir registro');
      window.location.reload();
    }} catch (error) {{
      alert(error.message);
    }}
  }}

  async function closeEntry(signalId, outcome) {{
    const label = outcome === 'win' ? 'Green' : 'Red';
    if (!confirm(`Marcar esta entrada como ${{label}}?`)) return;
    try {{
      const response = await fetch('/api/history-outcome', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{signal_id: signalId, outcome}})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao fechar entrada');
      window.location.reload();
    }} catch (error) {{
      alert(error.message);
    }}
  }}

  async function refreshSimulator() {{
    const status = document.getElementById('simulator-status');
    const best = document.getElementById('simulator-best');
    const rows = document.getElementById('simulator-rows');
    if (!status || !best || !rows) return;
    try {{
      status.textContent = 'Atualizando simulador...';
      const response = await fetch('/api/simulator', {{cache: 'no-store'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao atualizar simulador');
      best.innerHTML = data.best_html;
      rows.innerHTML = data.rows_html || '<tr><td colspan="8">Aguardando sinais do scanner.</td></tr>';
      const thermometer = document.getElementById('thermometer-rows');
      if (thermometer) thermometer.innerHTML = data.thermometer_html;
      const champRows = document.getElementById('champ-board-rows');
      if (champRows) champRows.innerHTML = data.championship_rows_html || '<tr><td colspan="5">Sem campeonatos ao vivo.</td></tr>';
      const leaderRows = document.getElementById('leader-board-rows');
      if (leaderRows) leaderRows.innerHTML = data.leadership_rows_html || '<tr><td colspan="5">Sem liderancas ao vivo.</td></tr>';
      const tape = document.getElementById('league-tape');
      if (tape) tape.innerHTML = data.market_tape_html || '<span class="tape-chip mid">Aguardando feed ao vivo</span>';
      const marketStatus = document.getElementById('market-board-status');
      if (marketStatus) marketStatus.textContent = `Atualizado: ${{data.updated_at}}`;
      const stats = document.getElementById('match-stats');
      if (stats && window.selectedGameId) {{
        await updateSelectedMatchStats(window.selectedGameId, false);
      }} else if (stats && data.match_stats_html) {{
        stats.innerHTML = data.match_stats_html;
        window.selectedGameId = data.default_game_id || null;
      }}
      status.textContent = `Atualizado: ${{data.updated_at}} | auto 60 min`;
    }} catch (error) {{
      status.textContent = `Falha ao atualizar: ${{error.message}}`;
    }}
  }}

  window.setInterval(refreshSimulator, 60 * 1000);
  window.setInterval(autoScanTick, 60 * 1000);
  window.setInterval(() => {{
    const active = document.activeElement;
    const typing = active && ['TEXTAREA', 'INPUT'].includes(active.tagName);
    if (!typing) window.location.reload();
  }}, 5 * 60 * 1000);

  async function selectScannedGame(gameId) {{
    window.selectedGameId = gameId;
    await updateSelectedMatchStats(gameId, true);
  }}

  async function updateSelectedMatchStats(gameId, showLoading) {{
    const panel = document.getElementById('match-stats');
    if (!panel) return;
    if (showLoading) {{
      panel.scrollTo({{top: 0, behavior: 'smooth'}});
      panel.innerHTML = '<p class="muted">Carregando estatisticas do jogo...</p>';
    }}
    try {{
      const response = await fetch(`/api/match-stats?game_id=${{encodeURIComponent(gameId)}}`, {{cache: 'no-store'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Falha ao carregar jogo');
      panel.innerHTML = data.html;
      if (showLoading) panel.scrollTo({{top: 0, behavior: 'smooth'}});
    }} catch (error) {{
      panel.innerHTML = `<p class="muted">${{error.message}}</p>`;
      if (showLoading) panel.scrollTo({{top: 0, behavior: 'smooth'}});
    }}
  }}
  window.selectedGameId = null;
  window.__scanInFlight = false;
  initTheme();
  loadScanConfig();
  refreshRuntimeState().catch(() => null);
  autoScanTick().catch(() => null);

  function switchStatsTab(button, paneId) {{
    const panel = button.closest('.stats-panel');
    if (!panel) return;
    panel.querySelectorAll('.stats-tab').forEach(tab => tab.classList.remove('active'));
    panel.querySelectorAll('.stats-pane').forEach(pane => pane.classList.remove('active'));
    button.classList.add('active');
    const pane = panel.querySelector(`#${{paneId}}`);
    if (pane) pane.classList.add('active');
  }}
</script>
</html>"""


@app.get("/api/state")
def api_state(_: None = Depends(_auth)) -> JSONResponse:
    settings = load_settings()
    state = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    history = state.history or []
    visible_history = _green_red(history)
    return JSONResponse(
        {
            "active_signal": state.active_signal,
            "active_entries": _active_entries(history, state.active_signal),
            "history": visible_history,
            "stats": _stats(state, visible_history),
            "learning": _learning_context(state, visible_history),
            "scanner": _scanner_status(state, settings),
            "paper_opportunities": paper_opportunities(_simulation_signals(state)),
            "best_paper_entry": best_paper_entry(_simulation_signals(state)),
            "simulation_sessions": state.simulation_sessions or [],
            "domains": [
                item.strip()
                for item in settings.dashboard_domains.split(",")
                if item.strip()
            ],
        }
    )


@app.get("/api/scanner-preference")
def api_scanner_preference(_: None = Depends(_auth)) -> JSONResponse:
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state = store.load()
    mode = str(getattr(state, "scan_preference", "brazil_first") or "brazil_first")
    return JSONResponse({"ok": True, "mode": mode, "mode_label": _scan_mode_label(mode)})


@app.post("/api/scanner-preference")
def api_set_scanner_preference(
    payload: ScannerPreferencePayload,
    _: None = Depends(_auth),
) -> JSONResponse:
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state = store.set_scan_preference(payload.mode)
    mode = str(getattr(state, "scan_preference", "brazil_first") or "brazil_first")
    return JSONResponse(
        {
            "ok": True,
            "mode": mode,
            "mode_label": _scan_mode_label(mode),
            "message": f"Modo de scanner ajustado para { _scan_mode_label(mode) }.",
        }
    )


@app.post("/api/scanner-request")
def api_scanner_request(_: None = Depends(_auth)) -> JSONResponse:
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state = store.request_scan_now()
    return JSONResponse(
        {
            "ok": True,
            "requested_at": state.scan_requested_at,
            "message": "Pedido de scan enviado. O bot aplica no proximo ciclo.",
        }
    )


@app.post("/api/scanner-run")
async def api_scanner_run(_: None = Depends(_auth)) -> JSONResponse:
    settings = load_settings()
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state = store.load()
    provider = build_provider(settings)
    mode = str(getattr(state, "scan_preference", "brazil_first") or "brazil_first")
    try:
        games, scan_scope = await scan_games(
            provider,
            mode,
            block_esports=settings.block_esports,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha no scanner: {type(exc).__name__}",
        ) from exc

    store.set_last_games([_to_plain_dict(game) for game in games])

    prepared: list[dict[str, Any]] = []
    raw_signals = ranked_signals(
        games,
        settings.min_confidence,
        settings.bankroll,
        settings.unit_percent,
        settings.max_stake_units,
    )
    if raw_signals:
        for signal in raw_signals[:8]:
            enriched = prepare_signal(signal, state, settings)
            enriched["scan_scope"] = scan_scope
            prepared.append(enriched)
    elif games:
        for game in games[:8]:
            watch = _watch_signal_from_game(game, state, settings)
            watch["scan_scope"] = scan_scope
            prepared.append(watch)

    store.set_candidates(prepared)
    store.request_scan_now()
    return JSONResponse(
        {
            "ok": True,
            "scan_scope": scan_scope,
            "mode": mode,
            "mode_label": _scan_mode_label(mode),
            "games": len(games),
            "candidates": len(prepared),
            "message": f"Scanner executado agora: {scan_scope}.",
        }
    )


@app.get("/api/simulator")
def api_simulator(_: None = Depends(_auth)) -> JSONResponse:
    state = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    simulation_signals = _simulation_signals(state)
    opportunities = paper_opportunities(simulation_signals)
    default_signal = simulation_signals[0] if simulation_signals else None
    simulation_rows = "\n".join(
        _simulation_row(item) for item in opportunities
    )
    return JSONResponse(
        {
            "best_html": _best_simulation(best_paper_entry(simulation_signals)),
            "rows_html": simulation_rows,
            "thermometer_html": _thermometer_rows(opportunities),
            "championship_rows_html": _championship_rows(state.last_games or []),
            "leadership_rows_html": _leadership_rows(state.last_games or []),
            "market_tape_html": _market_tape(state.last_games or []),
            "match_stats_html": _match_stats_panel(default_signal, _green_red(state.history or [])),
            "default_game_id": (default_signal.get("game") or {}).get("game_id") if default_signal else None,
            "updated_at": _short_datetime(state.last_scan_at),
        }
    )


@app.post("/api/simulator-session")
async def api_simulator_session(
    payload: SimulationRunPayload,
    _: None = Depends(_auth),
) -> JSONResponse:
    settings = load_settings()
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state = store.load()
    mode = str(getattr(state, "scan_preference", "brazil_first") or "brazil_first")
    provider = build_provider(settings)

    live_games: list[Any] = []
    try:
        live_games, _ = await scan_games(
            provider,
            mode,
            block_esports=settings.block_esports,
        )
    except Exception:
        live_games = []

    if not live_games:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sem jogos ao vivo reais neste momento. A simulação foi configurada para operar somente com jogos reais.",
        )

    if live_games:
        state = store.set_last_games([_to_plain_dict(game) for game in live_games])

    prepared: list[dict[str, Any]] = []
    if live_games:
        raw_signals = ranked_signals(
            live_games,
            settings.min_confidence,
            settings.bankroll,
            settings.unit_percent,
            settings.max_stake_units,
        )
        if raw_signals:
            for signal in raw_signals[:12]:
                prepared.append(prepare_signal(signal, state, settings))
        else:
            for game in live_games[:12]:
                prepared.append(_watch_signal_from_game(game, state, settings))
    if not prepared:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não foi possível montar oportunidades com os jogos ao vivo reais atuais.",
        )

    opportunities = paper_opportunities(prepared)
    if not opportunities:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sem oportunidades válidas nos jogos ao vivo reais atuais.",
        )
    session = _simulate_live_session(
        opportunities,
        total_games=payload.games,
        bankroll_units=payload.bankroll,
        stake_percent=payload.stake_percent,
    )
    session["trigger"] = "dashboard_manual"
    session["source_games"] = len(live_games)
    session["scan_scope"] = _scan_mode_label(mode)
    state = store.add_simulation_session(session)
    learning = _learning_context(state)
    return JSONResponse(
        {
            "ok": True,
            "total_games": session.get("total_games", 0),
            "greens": session.get("greens", 0),
            "reds": session.get("reds", 0),
            "hit_rate": session.get("hit_rate", 0),
            "source_games": len(live_games),
            "learning_sample_size": learning.get("sample_size", 0),
            "learning_real_sample_size": learning.get("real_sample_size", 0),
            "learning_simulation_sample_size": learning.get("simulation_sample_size", 0),
            "panel_html": _simulation_session_panel(session),
            "history_html": _simulation_history_panel(state.simulation_sessions or []),
            "updated_at": _short_datetime(state.last_scan_at),
        }
    )


@app.post("/api/fantasy-lineup")
def api_fantasy_lineup(
    payload: FantasyLineupPayload,
    _: None = Depends(_auth),
) -> JSONResponse:
    players = _parse_fantasy_players(payload.players_text)
    if len(players) < 11:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Envie ao menos 11 jogadores validos no formato Nome;Posicao;Time;Preco;Projecao.",
        )
    result = _optimize_fantasy_lineup(
        players,
        formation=payload.formation,
        budget=float(payload.budget),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(result.get("message") or "Falha"))
    return JSONResponse(
        {
            "ok": True,
            "message": result.get("message"),
            "html": _fantasy_result_panel(result),
            "lineup": result,
        }
    )


@app.get("/api/match-stats")
def api_match_stats(game_id: str, _: None = Depends(_auth)) -> JSONResponse:
    state = StateStore(os.getenv("STATE_FILE", "data/state.json")).load()
    history = _green_red(state.history or [])
    signals = _simulation_signals(state)
    selected = next(
        (
            signal
            for signal in signals
            if str((signal.get("game") or {}).get("game_id")) == str(game_id)
        ),
        None,
    )
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Jogo nao encontrado nos escaneados.",
        )
    return JSONResponse({"html": _match_stats_panel(selected, history)})


@app.post("/api/import-history")
async def import_history(payload: ImportPayload, _: None = Depends(_auth)) -> JSONResponse:
    records = parse_manual_bets(payload.text)
    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nenhuma aposta encontrada no texto enviado.",
        )
    settings = load_settings()
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state = store.add_history_records(records)
    sink = SupabaseSink.from_settings(settings)
    await sink.sync_signals(records)
    await sink.sync_ai_memory(state.history or [])
    return JSONResponse(
        {
            "imported": len(records),
            "wins": sum(1 for item in records if item.get("outcome") == "win"),
            "losses": sum(1 for item in records if item.get("outcome") == "loss"),
            "voids": sum(1 for item in records if item.get("outcome") == "void"),
            "opens": sum(1 for item in records if item.get("outcome") == "open"),
            "profit_currency": _manual_stats(records)["profit_currency"],
        }
    )


@app.post("/api/history-value")
async def update_history_value(
    payload: HistoryValuePayload,
    _: None = Depends(_auth),
) -> JSONResponse:
    if payload.entry_value is None and payload.entry_odds is None and payload.profit_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe pelo menos valor, odd ou lucro.",
        )
    settings = load_settings()
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    before = store.load()
    if not any(str(item.get("signal_id")) == str(payload.signal_id) for item in before.history or []):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro nao encontrado no historico.",
        )
    state = store.update_history_value(
        payload.signal_id,
        payload.entry_value,
        payload.entry_odds,
        payload.profit_value,
    )
    updated = next(
        item for item in state.history or [] if str(item.get("signal_id")) == str(payload.signal_id)
    )
    sink = SupabaseSink.from_settings(settings)
    await sink.sync_signal(updated)
    await sink.sync_ai_memory(state.history or [])
    return JSONResponse({"ok": True, "record": updated})


@app.post("/api/history-delete")
async def delete_history_record(
    payload: HistoryDeletePayload,
    _: None = Depends(_auth),
) -> JSONResponse:
    settings = load_settings()
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state, deleted = store.delete_history_record(payload.signal_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro nao encontrado no historico.",
        )
    sink = SupabaseSink.from_settings(settings)
    await sink.sync_ai_memory(state.history or [])
    return JSONResponse({"ok": True})


@app.post("/api/history-outcome")
async def close_history_entry(
    payload: HistoryOutcomePayload,
    _: None = Depends(_auth),
) -> JSONResponse:
    outcome = payload.outcome.lower().strip()
    if outcome not in {"win", "loss"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resultado invalido. Use win ou loss.",
        )
    settings = load_settings()
    store = StateStore(os.getenv("STATE_FILE", "data/state.json"))
    state, updated = store.mark_history_outcome(payload.signal_id, outcome)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entrada nao encontrada.",
        )
    sink = SupabaseSink.from_settings(settings)
    await sink.sync_signal(updated)
    await sink.sync_ai_memory(state.history or [])
    return JSONResponse({"ok": True, "record": updated})


def _learning_context(state, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return summarize_history_with_simulation(
        history if history is not None else (state.history or []),
        state.simulation_sessions or [],
        simulation_weight=0.35,
        max_simulation_rows=240,
    )


def _stats(state, history: list[dict[str, Any]]) -> dict[str, Any]:
    learning = _learning_context(state, history)
    settled = _green_red(history)
    wins = sum(1 for item in settled if item.get("outcome") == "win")
    losses = sum(1 for item in settled if item.get("outcome") == "loss")
    hit_rate = round((wins / len(settled)) * 100, 1) if settled else 0
    sample = learning.get("sample_size", 0)
    readiness = "baixa" if sample < 30 else "media" if sample < 100 else "alta"
    return {
        "total": len(settled),
        "wins": wins,
        "losses": losses,
        "hit_rate": hit_rate,
        "profit_units": learning.get("profit_units", 0),
        "roi_units": learning.get("roi_units", 0),
        "brier_score": learning.get("brier_score") or "-",
        "readiness": readiness,
    }


def _manual_stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    manual = [item for item in _green_red(history) if item.get("source") == "manual_import"]
    wins = [item for item in manual if item.get("outcome") == "win"]
    losses = [item for item in manual if item.get("outcome") == "loss"]
    stake_loss = sum(float(item.get("entry_value") or 0) for item in losses)
    profit = -stake_loss + sum(float(item.get("profit_value") or 0) for item in wins)
    closed = len(wins) + len(losses)
    return {
        "total": len(wins) + len(losses),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": round((len(wins) / closed) * 100, 1) if closed else 0,
        "profit_currency": round(profit, 2),
    }


def _green_red(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in history if item.get("outcome") in {"win", "loss"}]


def _simulation_signals(state) -> list[dict[str, Any]]:
    signals = []
    if state.active_signal:
        signals.append(state.active_signal)
    signals.extend(state.candidate_signals or [])
    seen = set()
    deduped = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        game_id = (signal.get("game") or {}).get("game_id")
        if not game_id or game_id in seen:
            continue
        seen.add(game_id)
        deduped.append(signal)
    return deduped


def _best_simulation(item: dict[str, Any] | None) -> str:
    if not item:
        return "<p class='muted'>Nenhuma oportunidade simulada no momento.</p>"
    return (
        "<div class='active-line'>"
        f"<div class='mini'><div class='muted'>Melhor jogo</div><strong>{_esc(item.get('match'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Mercado</div><strong>{_esc(item.get('market'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Entrada</div><strong>{_esc(item.get('selection'))} {_esc(item.get('line'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Odd</div><strong>{_esc(item.get('odds') or 'sem odd')}</strong></div>"
        f"<div class='mini'><div class='muted'>Score</div><strong>{_esc(item.get('score'))}/100</strong></div>"
        f"<div class='mini'><div class='muted'>Acao</div>{_action_badge(item.get('action'))}</div>"
        "</div>"
    )


def _simulation_row(item: dict[str, Any]) -> str:
    game_id = _js_string(item.get("game_id") or "")
    return (
        f"<tr class='clickable-row' onclick=\"selectScannedGame('{game_id}')\">"
        f"<td data-label='Jogo'>{_esc(item.get('match'))}<br><span class='muted'>{_esc(item.get('league'))} | { _esc(item.get('minute'))}' | { _esc(item.get('scoreline'))}</span></td>"
        f"<td data-label='Mercado'>{_esc(item.get('market'))}</td>"
        f"<td data-label='Selecao'>{_esc(item.get('selection'))}</td>"
        f"<td data-label='Linha'>{_esc(item.get('line'))}</td>"
        f"<td data-label='Odd'>{_esc(item.get('odds') or '-')}</td>"
        f"<td data-label='Acao'>{_action_badge(item.get('action'))}</td>"
        f"<td data-label='Score'>{_esc(item.get('score'))}/100<br><span class='muted'>risco { _esc(item.get('risk'))}/100</span></td>"
        f"<td data-label='Leitura'>{_esc(item.get('reason'))}</td>"
        "</tr>"
    )


def _thermometer_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p class='muted'>Aguardando sinais para montar o termometro.</p>"
    ranked = sorted(
        items,
        key=lambda item: (
            _action_weight(item.get("action")),
            int(item.get("score") or 0) - int(item.get("risk") or 0),
            int(item.get("score") or 0),
        ),
        reverse=True,
    )
    rows = []
    for idx, item in enumerate(ranked[:6], start=1):
        score = max(0, min(100, int(item.get("score") or 0)))
        risk = max(0, min(100, int(item.get("risk") or 0)))
        heat = max(4, min(100, score if score else 100 - risk))
        rows.append(
            f"<div class='pulse-row clickable-row' onclick=\"selectScannedGame('{_js_string(item.get('game_id') or '')}')\">"
            f"<div class='pulse-rank'>{idx}</div>"
            "<div class='pulse-main'>"
            f"<strong>{_esc(item.get('selection'))} {_esc(item.get('line'))}</strong>"
            f"<span>{_esc(item.get('match'))} | {_esc(item.get('market'))} | odd {_esc(item.get('odds') or '-')}</span>"
            "</div>"
            "<div class='pulse-meter'>"
            f"<div class='pulse-fill' style='width:{heat}%'></div>"
            "</div>"
            f"<div class='pulse-score'>{_esc(item.get('action'))}<br><span class='muted'>{score}/100</span></div>"
            "</div>"
        )
    return "".join(rows)


def _match_stats_panel(signal: dict[str, Any] | None, history: list[dict[str, Any]]) -> str:
    if not signal:
        return (
            "<div class='stats-tabs'>"
            "<button class='stats-tab active' type='button'>Estat.</button>"
            "<button class='stats-tab' type='button'>Historico</button>"
            "<button class='stats-tab' type='button'>Jogadores</button>"
            "</div>"
            "<p class='muted'>Clique em um jogo escaneado para abrir estatisticas, historico dos times e termometro.</p>"
        )
    game = signal.get("game") or {}
    home = str(game.get("home") or "Mandante")
    away = str(game.get("away") or "Visitante")
    metrics = _dynamic_match_metrics(signal)
    home_goals = int(game.get("home_goals") or 0)
    away_goals = int(game.get("away_goals") or 0)
    home_hist = _team_history(home, history)
    away_hist = _team_history(away, history)
    panel_id = _safe_dom_id(game.get("game_id") or f"{home}-{away}")
    stats_id = f"stats-{panel_id}"
    history_id = f"history-{panel_id}"
    players_id = f"players-{panel_id}"
    lineup_id = f"lineup-{panel_id}"
    return (
        "<div class='stats-tabs'>"
        f"<button class='stats-tab active' type='button' onclick=\"switchStatsTab(this, '{stats_id}')\">Estat.</button>"
        f"<button class='stats-tab' type='button' onclick=\"switchStatsTab(this, '{history_id}')\">Historico</button>"
        f"<button class='stats-tab' type='button' onclick=\"switchStatsTab(this, '{players_id}')\">Jogadores</button>"
        f"<button class='stats-tab' type='button' onclick=\"switchStatsTab(this, '{lineup_id}')\">Escalacao</button>"
        "</div>"
        f"<div class='stats-title'>{_esc(home)} x {_esc(away)}</div>"
        f"<div class='muted'>{_esc(game.get('league') or game.get('division') or '-')} | { _esc(game.get('minute', '-'))}' | Placar {home_goals}x{away_goals}</div>"
        f"<div id='{stats_id}' class='stats-pane active'>"
        f"{_match_visual_card(signal, metrics)}"
        f"{_live_match_card(metrics)}"
        f"{_stat_line('Termometro jogadores ' + home, f'{metrics['home_player_heat']}/100', metrics['home_player_heat'])}"
        f"{_stat_line('Termometro jogadores ' + away, f'{metrics['away_player_heat']}/100', metrics['away_player_heat'])}"
        "</div>"
        f"<div id='{history_id}' class='stats-pane'>{_history_tab(home, away, home_hist, away_hist, history)}</div>"
        f"<div id='{players_id}' class='stats-pane'>{_players_tab(home, away, metrics)}</div>"
        f"<div id='{lineup_id}' class='stats-pane'>{_lineup_tab(home, away, signal, metrics)}</div>"
    )


def _history_tab(
    home: str,
    away: str,
    home_hist: dict[str, int],
    away_hist: dict[str, int],
    history: list[dict[str, Any]],
) -> str:
    recent = _recent_team_history(home, away, history)
    recent_rows = []
    for item in recent[:6]:
        game = item.get("game") or {}
        recent_rows.append(
            "<tr>"
            f"<td>{_esc((item.get('created_at') or '')[:10])}</td>"
            f"<td>{_esc(game.get('home', '-'))} x {_esc(game.get('away', '-'))}</td>"
            f"<td>{_esc(game.get('home_goals', 0))}x{_esc(game.get('away_goals', 0))}</td>"
            f"<td class='{_esc(item.get('outcome', 'open'))}'>{_label(item.get('outcome', 'open'))}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Data</th><th>Jogo</th><th>Placar</th><th>Bet</th></tr></thead>"
        f"<tbody>{''.join(recent_rows) or '<tr><td colspan=\"4\">Sem historico local para estes times.</td></tr>'}</tbody></table>"
    )
    return (
        "<div class='stats-grid'>"
        f"{_team_history_box(home, home_hist)}"
        f"{_team_history_box(away, away_hist)}"
        "</div>"
        f"{table}"
    )


def _players_tab(home: str, away: str, metrics: dict[str, Any]) -> str:
    home_rows = _player_heat_rows(home, metrics, "home")
    away_rows = _player_heat_rows(away, metrics, "away")
    return (
        "<p class='muted'>Termometro estimado por pressao, xG, ataques perigosos, chutes e placar. Quando a fonte entregar jogador nominal, este painel pode receber nomes reais.</p>"
        "<div class='stats-grid'>"
        f"<div>{_player_table(home, home_rows)}</div>"
        f"<div>{_player_table(away, away_rows)}</div>"
        "</div>"
    )


def _lineup_tab(home: str, away: str, signal: dict[str, Any], metrics: dict[str, Any]) -> str:
    game = signal.get("game") or {}
    target = signal.get("team") or "-"
    home_shape = "4-2-3-1" if metrics["home_danger"] >= metrics["away_danger"] else "4-4-2"
    away_shape = "4-2-3-1" if metrics["away_danger"] > metrics["home_danger"] else "4-4-2"
    return (
        "<p class='muted'>Escalacao estimada. A fonte atual nao entregou nomes oficiais de titulares, entao o agente mostra estrutura, lado forte e foco da entrada.</p>"
        "<div class='stats-grid'>"
        f"{_formation_box(home, home_shape, metrics['home_player_heat'])}"
        f"{_formation_box(away, away_shape, metrics['away_player_heat'])}"
        "</div>"
        "<div class='stat-line'>"
        "<div class='muted'>Foco da IA</div>"
        f"<strong>{_esc(target)}</strong>"
        f"<div>Mercado: {_esc(signal.get('entry_market') or signal.get('market') or '-')}</div>"
        f"<div class='muted'>Liga: {_esc(game.get('league') or game.get('division') or '-')}</div>"
        "</div>"
    )


def _match_visual_card(signal: dict[str, Any], metrics: dict[str, Any]) -> str:
    game = signal.get("game") or {}
    home = str(game.get("home") or "Mandante")
    away = str(game.get("away") or "Visitante")
    minute = max(0, _safe_int(game.get("minute")))
    league = str(game.get("league") or game.get("division") or "-")
    home_goals = _safe_int(game.get("home_goals"))
    away_goals = _safe_int(game.get("away_goals"))
    home_heat = max(0, min(100, _safe_int(metrics.get("home_player_heat"))))
    away_heat = max(0, min(100, _safe_int(metrics.get("away_player_heat"))))
    home_pressure = max(0, min(100, _safe_int(game.get("home_pressure")) or home_heat))
    away_pressure = max(0, min(100, _safe_int(game.get("away_pressure")) or away_heat))
    return (
        "<div class='match-visual'>"
        "<div class='match-visual-head'>"
        f"<span class='match-visual-league'>{_esc(league)}</span>"
        f"<span class='match-visual-minute'>{minute}'</span>"
        "</div>"
        "<div class='match-visual-score'>"
        "<div class='match-team'>"
        f"<span class='match-avatar'>{_esc(_team_initials(home))}</span>"
        f"<span class='match-team-name'>{_esc(home)}</span>"
        "</div>"
        f"<div class='match-goals'>{home_goals}x{away_goals}</div>"
        "<div class='match-team'>"
        f"<span class='match-avatar'>{_esc(_team_initials(away))}</span>"
        f"<span class='match-team-name'>{_esc(away)}</span>"
        "</div>"
        "</div>"
        "<div class='match-visual-bars'>"
        "<div class='match-visual-row'>"
        f"<strong>{home_pressure}% pressao</strong>"
        f"<div class='match-visual-track' style='--fill:{home_heat}%'><span></span></div>"
        f"<strong>term. {home_heat}</strong>"
        "</div>"
        "<div class='match-visual-row'>"
        f"<strong>{away_pressure}% pressao</strong>"
        f"<div class='match-visual-track' style='--fill:{away_heat}%'><span></span></div>"
        f"<strong>term. {away_heat}</strong>"
        "</div>"
        "</div>"
        "</div>"
    )


def _live_match_card(metrics: dict[str, Any]) -> str:
    left_share = _share(metrics["home_shots_total"], metrics["away_shots_total"])
    right_share = 100 - left_share
    return (
        "<div class='live-card'>"
        "<div class='live-xg'>"
        f"<strong>{_esc(metrics['home_xg'])}</strong><span>xG</span><strong>{_esc(metrics['away_xg'])}</strong>"
        "</div>"
        "<div class='live-metrics'>"
        f"{_live_metric('Ataques', metrics['home_attacks'], metrics['away_attacks'], 'A')}"
        f"{_live_metric('Ataques Perigosos', metrics['home_danger'], metrics['away_danger'], '»')}"
        f"{_live_metric('% de Posse', metrics['home_possession'], metrics['away_possession'], '%')}"
        "</div>"
        "<div class='live-metrics'>"
        f"{_discipline_box(metrics['home_corners'], metrics['home_red_cards'], metrics['home_yellow_cards'])}"
        "<div>"
        "<div class='live-label'>Finalizacoes / Chutes ao Gol</div>"
        "<div class='shots-line'>"
        f"<strong>{_esc(metrics['home_shots_total'])}/{_esc(metrics['home_shots_on'])}</strong>"
        f"<div class='shots-track' style='--left-share:{left_share}%;--right-share:{right_share}%'><span></span></div>"
        f"<strong>{_esc(metrics['away_shots_total'])}/{_esc(metrics['away_shots_on'])}</strong>"
        "</div>"
        "</div>"
        f"{_discipline_box(metrics['away_yellow_cards'], metrics['away_red_cards'], metrics['away_corners'], reverse=True)}"
        "</div>"
        f"<p class='muted'>Atualiza junto com o scanner. Quando a fonte nao entrega algum dado, o painel estima pela pressao, minuto, chutes, placar e odds.</p>"
        "</div>"
    )


def _live_metric(label: str, left: int, right: int, icon: str) -> str:
    total = max(1, left + right)
    pct = int((left / total) * 100)
    return (
        "<div class='live-stat'>"
        f"<div class='live-label'>{_esc(label)}</div>"
        "<div class='live-dial-row'>"
        f"<span class='live-num'>{_esc(left)}</span>"
        f"<div class='dial' style='background:conic-gradient(var(--red) 0 {pct}%, #454c56 {pct}% 100%)'><span>{_esc(icon)}</span></div>"
        f"<span class='live-num'>{_esc(right)}</span>"
        "</div>"
        "</div>"
    )


def _discipline_box(first: int, second: int, third: int, *, reverse: bool = False) -> str:
    if reverse:
        icons = [
            ("card-dot yellow-card", first),
            ("card-dot red-card", second),
            ("flag", third),
        ]
    else:
        icons = [
            ("flag", first),
            ("card-dot red-card", second),
            ("card-dot yellow-card", third),
        ]
    return (
        "<div class='live-icons'>"
        + "".join(f"<div><div class='{klass}'></div>{_esc(value)}</div>" for klass, value in icons)
        + "</div>"
    )


def _dynamic_match_metrics(signal: dict[str, Any]) -> dict[str, Any]:
    game = signal.get("game") or {}
    minute = max(1, _safe_int(game.get("minute")))
    home_goals = _safe_int(game.get("home_goals"))
    away_goals = _safe_int(game.get("away_goals"))
    home_pressure = _safe_int(game.get("home_pressure"))
    away_pressure = _safe_int(game.get("away_pressure"))
    home_shots_on = _safe_int(game.get("home_shots_on"))
    away_shots_on = _safe_int(game.get("away_shots_on"))

    home_pressure = home_pressure or _estimated_pressure(signal, "home")
    away_pressure = away_pressure or _estimated_pressure(signal, "away")
    home_shots_on = home_shots_on or max(0, round((home_pressure / 100) * max(1, minute / 18)))
    away_shots_on = away_shots_on or max(0, round((away_pressure / 100) * max(1, minute / 18)))

    home_shots_total = max(home_shots_on, round(home_shots_on * 2.1 + home_pressure / 18 + home_goals))
    away_shots_total = max(away_shots_on, round(away_shots_on * 2.1 + away_pressure / 18 + away_goals))
    home_possession = _possession_share(home_pressure, away_pressure)
    away_possession = 100 - home_possession
    home_attacks = max(5, round(home_pressure * 1.12 + minute * 0.28 + home_shots_total * 1.8))
    away_attacks = max(5, round(away_pressure * 1.12 + minute * 0.28 + away_shots_total * 1.8))
    home_danger = max(1, round(home_pressure * 0.62 + home_shots_on * 4.5 + home_goals * 5))
    away_danger = max(1, round(away_pressure * 0.62 + away_shots_on * 4.5 + away_goals * 5))
    home_corners = _market_corner_hint(game, "home") or max(0, round(home_danger / 16 + home_shots_total / 7))
    away_corners = _market_corner_hint(game, "away") or max(0, round(away_danger / 16 + away_shots_total / 7))
    pressure_delta = abs(home_pressure - away_pressure)
    home_yellow = max(0, round((minute / 45) + (away_pressure > home_pressure) + pressure_delta / 55))
    away_yellow = max(0, round((minute / 45) + (home_pressure > away_pressure) + pressure_delta / 55))
    home_red = 1 if home_yellow >= 4 and pressure_delta > 35 else 0
    away_red = 1 if away_yellow >= 4 and pressure_delta > 35 else 0

    return {
        "home_xg": _fmt_decimal(home_shots_on * 0.31 + home_pressure * 0.012 + home_goals * 0.2),
        "away_xg": _fmt_decimal(away_shots_on * 0.31 + away_pressure * 0.012 + away_goals * 0.2),
        "home_attacks": home_attacks,
        "away_attacks": away_attacks,
        "home_danger": home_danger,
        "away_danger": away_danger,
        "home_possession": home_possession,
        "away_possession": away_possession,
        "home_shots_total": home_shots_total,
        "away_shots_total": away_shots_total,
        "home_shots_on": home_shots_on,
        "away_shots_on": away_shots_on,
        "home_corners": home_corners,
        "away_corners": away_corners,
        "home_yellow_cards": home_yellow,
        "away_yellow_cards": away_yellow,
        "home_red_cards": home_red,
        "away_red_cards": away_red,
        "home_player_heat": min(100, max(8, home_pressure + home_shots_on * 6 + home_goals * 10)),
        "away_player_heat": min(100, max(8, away_pressure + away_shots_on * 6 + away_goals * 10)),
    }


def _estimated_pressure(signal: dict[str, Any], side: str) -> int:
    game = signal.get("game") or {}
    confidence = _safe_int(signal.get("confidence"))
    score = _safe_int(signal.get("entry_score"))
    minute = _safe_int(game.get("minute"))
    target = str(signal.get("team") or "").lower()
    team = str(game.get(side) or "").lower()
    base = 38 + min(28, confidence // 4) + min(12, score // 8) + min(8, minute // 12)
    if target and target in team:
        base += 12
    return max(12, min(92, base))


def _market_corner_hint(game: dict[str, Any], side: str) -> int:
    corners = ((game.get("markets") or {}).get("corners") or {})
    item = corners.get(side)
    if isinstance(item, dict):
        return _safe_int(item.get("line"))
    return 0


def _share(left: int, right: int) -> int:
    total = max(1, left + right)
    return max(0, min(100, int((left / total) * 100)))


def _fmt_decimal(value: float) -> str:
    return f"{max(0, value):.2f}"


def _stat_dial(label: str, left: int, right: int) -> str:
    total = max(1, left + right)
    pct = int((left / total) * 100)
    return (
        "<div class='stat-dial'>"
        f"<strong>{_esc(left)}</strong>"
        f"<div class='dial' style='background:conic-gradient(var(--green) 0 {pct}%, var(--red) {pct}% 100%)'><span>{_esc(label[:1])}</span></div>"
        f"<strong>{_esc(right)}</strong>"
        f"<span class='muted' style='grid-column:1/-1'>{_esc(label)}</span>"
        "</div>"
    )


def _stat_line(label: str, value: str, pct: int) -> str:
    pct = max(0, min(100, int(pct or 0)))
    return (
        "<div class='stat-line'>"
        f"<div class='muted'>{_esc(label)}</div>"
        f"<strong>{_esc(value)}</strong>"
        f"<div class='stat-bar'><span style='width:{pct}%'></span></div>"
        "</div>"
    )


def _team_history_box(team: str, data: dict[str, int]) -> str:
    total = data["wins"] + data["draws"] + data["losses"]
    return (
        "<div class='stat-line'>"
        f"<strong>{_esc(team)}</strong>"
        f"<div class='muted'>Jogos parecidos no historico local: {total}</div>"
        f"<div>V {data['wins']} | E {data['draws']} | D {data['losses']}</div>"
        f"<div class='muted'>Bets: Green {data['greens']} | Red {data['reds']}</div>"
        "</div>"
    )


def _team_history(team: str, history: list[dict[str, Any]]) -> dict[str, int]:
    data = {"wins": 0, "draws": 0, "losses": 0, "greens": 0, "reds": 0}
    needle = team.lower()
    for item in history:
        game = item.get("game") or {}
        home = str(game.get("home") or "")
        away = str(game.get("away") or "")
        if needle not in home.lower() and needle not in away.lower():
            continue
        if item.get("outcome") == "win":
            data["greens"] += 1
        elif item.get("outcome") == "loss":
            data["reds"] += 1
        hg = _safe_int(game.get("home_goals"))
        ag = _safe_int(game.get("away_goals"))
        if hg == ag:
            data["draws"] += 1
        elif (needle in home.lower() and hg > ag) or (needle in away.lower() and ag > hg):
            data["wins"] += 1
        else:
            data["losses"] += 1
    return data


def _recent_team_history(home_team: str, away_team: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    home_needle = home_team.lower()
    away_needle = away_team.lower()
    rows = []
    for item in history:
        game = item.get("game") or {}
        home = str(game.get("home") or "").lower()
        away = str(game.get("away") or "").lower()
        if (
            home_needle in home
            or home_needle in away
            or away_needle in home
            or away_needle in away
        ):
            rows.append(item)
    return rows


def _player_heat_rows(team: str, metrics: dict[str, Any], side: str) -> list[dict[str, Any]]:
    prefix = "home" if side == "home" else "away"
    heat = int(metrics[f"{prefix}_player_heat"])
    danger = int(metrics[f"{prefix}_danger"])
    shots = int(metrics[f"{prefix}_shots_on"])
    xg = float(metrics[f"{prefix}_xg"])
    base_name = _short_team_name(team)
    rows = [
        ("Finalizador", heat + shots * 4 + int(xg * 6)),
        ("Armador", heat + danger // 3),
        ("Ponta", heat + int(metrics[f"{prefix}_attacks"] / 7)),
        ("Volante", max(15, heat - 12 + metrics[f"{prefix}_yellow_cards"] * 5)),
    ]
    return [
        {"name": f"{base_name} {role}", "heat": max(0, min(100, value))}
        for role, value in rows
    ]


def _player_table(team: str, rows: list[dict[str, Any]]) -> str:
    body = []
    for row in sorted(rows, key=lambda item: item["heat"], reverse=True):
        body.append(
            "<tr>"
            f"<td>{_esc(row['name'])}</td>"
            f"<td>{_esc(row['heat'])}/100</td>"
            f"<td><div class='stat-bar'><span style='width:{_esc(row['heat'])}%'></span></div></td>"
            "</tr>"
        )
    return (
        f"<h2>{_esc(team)}</h2>"
        "<table><thead><tr><th>Perfil</th><th>Term.</th><th>Pressao</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _formation_box(team: str, shape: str, heat: int) -> str:
    return (
        "<div class='stat-line'>"
        f"<strong>{_esc(team)}</strong>"
        f"<div class='muted'>Formacao estimada: {shape}</div>"
        f"<div>Intensidade: {heat}/100</div>"
        f"<div class='stat-bar'><span style='width:{max(0, min(100, int(heat)))}%'></span></div>"
        "</div>"
    )


def _short_team_name(team: str) -> str:
    parts = [part for part in team.split() if part]
    if not parts:
        return "Time"
    return parts[0][:10]


def _team_initials(team: str) -> str:
    parts = [part[0].upper() for part in team.split() if part]
    if not parts:
        return "TM"
    if len(parts) == 1:
        return f"{parts[0]}{parts[0]}"
    return "".join(parts[:2])


def _possession_share(left: int, right: int) -> int:
    total = max(1, left + right)
    return max(0, min(100, int((left / total) * 100)))


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_dom_id(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "match")).strip("-")
    return text[:60] or "match"


def _format_brl(value: Any) -> str:
    amount = _safe_float(value)
    signal = "-" if amount < 0 else ""
    amount = abs(amount)
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{signal}R$ {formatted}"


def _action_weight(action: Any) -> int:
    text = str(action or "").upper()
    if text == "ENTRAR":
        return 4
    if text == "AGUARDAR":
        return 3
    if text == "SEGURAR":
        return 2
    if text == "SAIR":
        return 1
    return 0


def _action_class(action: Any) -> str:
    action = str(action or "").upper()
    if action == "ENTRAR":
        return "win"
    if action == "SAIR":
        return "loss"
    return "open"


def _action_badge(action: Any) -> str:
    text = str(action or "-").upper()
    cls = {
        "ENTRAR": "enter",
        "AGUARDAR": "wait",
        "SEGURAR": "hold",
        "SAIR": "exit",
    }.get(text, "hold")
    return f"<span class='action-pill {cls}'>{_esc(text)}</span>"


def _scanner_status(state, settings=None) -> dict[str, Any]:
    candidates = len(state.candidate_signals or [])
    has_active = bool(state.active_signal)
    scan_mode = str(getattr(state, "scan_preference", "brazil_first") or "brazil_first")
    idle_seconds = int(getattr(settings, "idle_scan_interval_seconds", 1800) or 1800)
    active_seconds = int(getattr(settings, "active_scan_interval_seconds", 300) or 300)
    current_seconds = active_seconds if has_active else idle_seconds
    return {
        "mode": (
            f"{max(1, round(active_seconds / 60))} min com jogo ativo"
            if has_active
            else f"{max(1, round(idle_seconds / 60))} min aguardando escolha"
        ),
        "last_scan": _short_datetime(state.last_scan_at),
        "last_scan_iso": state.last_scan_at,
        "candidates": candidates,
        "today_games": len(state.last_games or []),
        "status": "monitorando entrada" if has_active else "scanner livre",
        "scan_preference": scan_mode,
        "scan_profile": _scan_mode_label(scan_mode),
        "idle_scan_interval_seconds": idle_seconds,
        "active_scan_interval_seconds": active_seconds,
        "auto_scan_interval_seconds": current_seconds,
    }


def _scan_mode_label(mode: str) -> str:
    labels = {
        "brazil_first": "Brasil -> Mundo",
        "world_first": "Mundo -> Brasil",
        "live_only": "Somente ao vivo",
    }
    return labels.get(str(mode or "").strip().lower(), "Brasil -> Mundo")


def _supabase_info(settings) -> dict[str, Any]:
    fallback = {
        "status": "nao configurado",
        "games": "-",
        "signals": "-",
        "memory": "-",
    }
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return fallback
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Prefer": "count=exact",
    }
    tables = {
        "games": "betsignal_games",
        "signals": "betsignal_signals",
        "memory": "betsignal_ai_memory",
    }
    result = {"status": "online", "games": "-", "signals": "-", "memory": "-"}
    try:
        with httpx.Client(timeout=5) as client:
            for key, table in tables.items():
                response = client.get(
                    f"{settings.supabase_url}/rest/v1/{table}",
                    headers=headers,
                    params={"select": "*", "limit": "1"},
                )
                if response.status_code not in {200, 206}:
                    result["status"] = f"erro {response.status_code}"
                    continue
                content_range = response.headers.get("content-range", "")
                result[key] = content_range.rsplit("/", 1)[-1] if "/" in content_range else "ok"
    except Exception:
        result["status"] = "indisponivel"
    return result


def _short_datetime(value: Any) -> str:
    if not value:
        return "-"
    return str(value).replace("T", " ")[:16]


def _equity_curve(backtest: dict[str, Any]) -> str:
    points = backtest.get("last_equity_units") or []
    if not points:
        return "<p class='muted'>Aguardando historico fechado para desenhar a curva.</p>"
    max_abs = max(abs(float(point)) for point in points) or 1
    bars = []
    for point in points[-12:]:
        value = float(point)
        height = max(8, int((abs(value) / max_abs) * 62))
        klass = "bar neg" if value < 0 else "bar"
        bars.append(f"<span class='{klass}' style='height:{height}px' title='{_esc(value)}u'></span>")
    return "<div class='sparkline'>" + "".join(bars) + "</div>"


def _row(item: dict[str, Any]) -> str:
    game = item.get("game", {})
    outcome = item.get("outcome", "open")
    match = f"{game.get('home', '-')} x {game.get('away', '-')}"
    entry = _entry_summary(item)
    signal_id = _js_string(item.get("signal_id") or "")
    value = item.get("entry_value")
    odds = item.get("entry_odds") or item.get("target_odds")
    profit = item.get("profit_value")
    real_value = _real_value_summary(item)
    return (
        "<tr>"
        f"<td data-label='Data'>{_esc((item.get('created_at') or '')[:16])}</td>"
        f"<td data-label='Jogo'>{_esc(match)}</td>"
        f"<td data-label='Liga'>{_esc(game.get('league', '-'))}</td>"
        f"<td data-label='Entrada'>{entry}</td>"
        f"<td data-label='Valor real'>{real_value}</td>"
        f"<td data-label='Conf.'>{_esc(item.get('confidence', '-'))}%</td>"
        f"<td data-label='Edge' class='{_value_class(item.get('value_edge'), multiplier=100)}'>{_edge(item.get('value_edge'))}</td>"
        f"<td data-label='Stake'>{_esc(item.get('stake_units', 0))}u</td>"
        f"<td data-label='Resultado' class='{_esc(outcome)}'>{_label(outcome)}</td>"
        "<td data-label='Acao'>"
        "<div class='action-buttons'>"
        f"<button class='ghost' type='button' title='Editar valor real' onclick=\"editHistoryValue('{signal_id}', '{_js_string(value or '')}', '{_js_string(odds or '')}', '{_js_string(profit or '')}')\">✎ Editar</button>"
        f"<button class='ghost danger' type='button' title='Excluir registro' onclick=\"deleteHistoryRecord('{signal_id}')\">× Excluir</button>"
        "</div>"
        "</td>"
        "</tr>"
    )


def _active_entries(
    history: list[dict[str, Any]],
    active_signal: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [active_signal, *history]:
        if not isinstance(item, dict):
            continue
        signal_id = str(item.get("signal_id") or "")
        if signal_id in seen:
            continue
        if item.get("entered") and item.get("outcome") == "open":
            entries.append(item)
            seen.add(signal_id)
    return entries


def _active_entry_row(item: dict[str, Any]) -> str:
    game = item.get("game", {})
    match = f"{game.get('home', '-')} x {game.get('away', '-')}"
    market = item.get("entry_market") or item.get("market") or "-"
    selection = item.get("entry_selection")
    line = item.get("entry_line")
    if selection:
        market = f"{market} - {selection}"
    if line:
        market = f"{market} {line}"
    value = item.get("entry_value")
    odds = item.get("entry_odds") or item.get("target_odds")
    signal_id = _js_string(item.get("signal_id") or "")
    return (
        "<tr>"
        f"<td data-label='Entrada'>{_esc((item.get('entered_at') or item.get('created_at') or '')[:16])}</td>"
        f"<td data-label='Jogo'>{_esc(match)}</td>"
        f"<td data-label='Mercado'>{_esc(market)}</td>"
        f"<td data-label='Valor'>{_esc(value if value is not None else item.get('stake_value', '-'))}</td>"
        f"<td data-label='Odd'>{_esc(odds or '-')}</td>"
        "<td data-label='Acao'>"
        "<div class='action-buttons'>"
        f"<button class='ghost' type='button' title='Informar valor da entrada' onclick=\"editHistoryValue('{signal_id}', '{_js_string(value or '')}', '{_js_string(odds or '')}', '')\">✎ Valor</button>"
        f"<button class='ghost success' type='button' title='Marcar Green' onclick=\"closeEntry('{signal_id}', 'win')\">Green</button>"
        f"<button class='ghost danger' type='button' title='Marcar Red' onclick=\"closeEntry('{signal_id}', 'loss')\">Red</button>"
        "</div>"
        "</td>"
        "</tr>"
    )


def _active(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "<p class='subtle'>Nenhum jogo ativo no momento.</p>"
    game = signal.get("game", {})
    entry = _entry_summary(signal)
    edge = _edge(signal.get("value_edge"))
    edge_class = _value_class(signal.get("value_edge"), multiplier=100)
    score = signal.get("entry_score", "-")
    grade = signal.get("grade", "-")
    risk = signal.get("risk_score", "-")
    return (
        f"<div class='active-title'>{_esc(game.get('home'))} {_esc(game.get('home_goals', 0))} x "
        f"{_esc(game.get('away_goals', 0))} {_esc(game.get('away'))}</div>"
        f"<div class='muted'>{_esc(game.get('league', '-'))} | minuto {_esc(game.get('minute', '-'))}</div>"
        "<div class='active-line'>"
        f"<div class='mini'><div class='muted'>Acao</div>{_action_badge(signal.get('action'))}</div>"
        f"<div class='mini'><div class='muted'>Score IA</div><strong>{_esc(score)}/100 | {_esc(grade)}</strong></div>"
        f"<div class='mini'><div class='muted'>Risco</div><strong>{_esc(risk)}/100</strong></div>"
        f"<div class='mini'><div class='muted'>Confianca</div><strong>{_esc(signal.get('confidence'))}%</strong></div>"
        f"<div class='mini'><div class='muted'>Edge</div><strong class='{edge_class}'>{edge}</strong></div>"
        f"<div class='mini'><div class='muted'>Entrada</div>{entry}</div>"
        f"<div class='mini'><div class='muted'>Stake</div><strong>{_esc(signal.get('stake_value', 0))} / {_esc(signal.get('stake_units', 0))}u</strong></div>"
        f"<div class='mini'><div class='muted'>Dados</div><strong>{_esc(signal.get('data_quality', '-'))}%</strong></div>"
        "</div>"
        f"<p class='subtle'>{_esc(signal.get('reason'))}</p>"
    )


def _rankings(learning: dict[str, Any]) -> str:
    blocks = []
    for title, key in (
        ("Ligas", "by_league"),
        ("Times", "by_team"),
        ("Mercados", "by_market"),
    ):
        rows = learning.get(key) or []
        if not rows:
            blocks.append(f"<p class='muted'>{title}: aguardando historico fechado.</p>")
            continue
        items = []
        for row in rows[:5]:
            items.append(
                "<tr>"
                f"<td>{_esc(row.get('name'))}</td>"
                f"<td>{_esc(row.get('hit_rate'))}%</td>"
                f"<td class='{_value_class(row.get('profit_units'))}'>{_esc(row.get('profit_units'))}u</td>"
                f"<td>{_esc(row.get('total'))}</td>"
                "</tr>"
            )
        blocks.append(
            f"<h2>{title}</h2>"
            "<table><thead><tr><th>Nome</th><th>Acerto</th><th>Lucro</th><th>Sinais</th></tr></thead>"
            f"<tbody>{''.join(items)}</tbody></table>"
        )
    return "".join(blocks)


def _fast_learning_panel(fast: dict[str, Any]) -> str:
    recent_5 = fast.get("recent_5") or {}
    recent_10 = fast.get("recent_10") or {}
    blocks = [
        "<div class='active-line'>"
        f"<div class='mini'><div class='muted'>Modo</div><strong>{_esc(fast.get('mode', 'neutro'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Momentum</div><strong>{_esc(fast.get('momentum_score', 50))}/100</strong></div>"
        f"<div class='mini'><div class='muted'>Ultimos 5</div><strong>{_esc(recent_5.get('wins', 0))}G / {_esc(recent_5.get('losses', 0))}R</strong></div>"
        f"<div class='mini'><div class='muted'>Ultimos 10</div><strong>{_esc(recent_10.get('wins', 0))}G / {_esc(recent_10.get('losses', 0))}R</strong></div>"
        "</div>"
    ]
    for title, key in (
        ("Mercados quentes", "hot_markets"),
        ("Mercados frios", "cold_markets"),
        ("Times frios", "cold_teams"),
    ):
        rows = fast.get(key) or []
        if not rows:
            blocks.append(f"<p class='muted'>{title}: aguardando mais resultados.</p>")
            continue
        body = []
        for row in rows[:4]:
            body.append(
                "<tr>"
                f"<td>{_esc(row.get('name'))}</td>"
                f"<td>{_esc(row.get('wins'))}G / {_esc(row.get('losses'))}R</td>"
                f"<td class='{_value_class(row.get('profit_units'))}'>{_esc(row.get('profit_units'))}u</td>"
                f"<td>{_esc(row.get('confidence'))}</td>"
                "</tr>"
            )
        blocks.append(
            f"<h2>{title}</h2>"
            "<table><thead><tr><th>Nome</th><th>Forma</th><th>Lucro</th><th>Conf.</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>"
        )
    return "".join(blocks)


def _simulate_live_session(
    opportunities: list[dict[str, Any]],
    *,
    total_games: int = 30,
    bankroll_units: float = 100.0,
    stake_percent: float = 10.0,
) -> dict[str, Any]:
    games_target = max(30, min(120, _safe_int(total_games) or 30))
    bankroll_start = max(20.0, min(10000.0, _safe_float(bankroll_units, 100.0)))
    stake_pct = max(1.0, min(20.0, _safe_float(stake_percent, 10.0)))
    if not opportunities:
        return {
            "total_games": games_target,
            "greens": 0,
            "reds": 0,
            "hit_rate": 0.0,
            "start_bankroll": bankroll_start,
            "end_bankroll": bankroll_start,
            "profit_units": 0.0,
            "roi": 0.0,
            "max_drawdown": 0.0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "stake_percent": stake_pct,
            "rows": [],
            "note": "Sem jogos ao vivo para simular agora. Rode o scanner e tente novamente.",
        }

    ranked = sorted(
        opportunities,
        key=lambda item: (
            -_safe_int(item.get("score")),
            _safe_int(item.get("risk")),
            str(item.get("match") or ""),
            str(item.get("market") or ""),
        ),
    )
    seed_material = "|".join(
        f"{item.get('game_id')}:{item.get('market')}:{item.get('selection')}:{item.get('minute')}"
        for item in ranked[:48]
    ) or "sim-session-default"
    seed_value = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed_value)

    bankroll = bankroll_start
    peak = bankroll_start
    max_drawdown = 0.0
    greens = 0
    reds = 0
    current_win_streak = 0
    current_loss_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    total_staked = 0.0
    rows: list[dict[str, Any]] = []

    for idx in range(games_target):
        pick = ranked[idx % len(ranked)]
        score = max(1, min(100, _safe_int(pick.get("score"))))
        risk = max(1, min(100, _safe_int(pick.get("risk")) or (100 - score)))
        confidence = max(1, min(100, _safe_int(pick.get("confidence")) or score))
        odds = _safe_float(pick.get("odds"))
        if not odds:
            odds = _synthetic_live_odds(score, risk, rng)

        win_prob = _probability_from_live_read(score, risk, confidence, pick, rng)
        stake = round(max(0.2, bankroll * (stake_pct / 100.0)), 2)
        total_staked += stake
        won = rng.random() <= win_prob

        if won:
            payout = round(stake * max(0.01, odds - 1.0), 2)
            bankroll = round(bankroll + payout, 2)
            greens += 1
            current_win_streak += 1
            current_loss_streak = 0
            max_win_streak = max(max_win_streak, current_win_streak)
            outcome = "GREEN"
            profit = payout
        else:
            bankroll = round(max(0.0, bankroll - stake), 2)
            reds += 1
            current_loss_streak += 1
            current_win_streak = 0
            max_loss_streak = max(max_loss_streak, current_loss_streak)
            outcome = "RED"
            profit = -stake

        peak = max(peak, bankroll)
        drawdown = peak - bankroll
        max_drawdown = max(max_drawdown, drawdown)

        rows.append(
            {
                "idx": idx + 1,
                "match": pick.get("match") or "-",
                "market": pick.get("market") or "-",
                "selection": pick.get("selection") or "-",
                "line": pick.get("line") or "-",
                "minute": pick.get("minute") or "-",
                "scoreline": pick.get("scoreline") or "-",
                "odds": round(odds, 3),
                "stake": stake,
                "outcome": outcome,
                "profit": round(profit, 2),
                "bankroll": bankroll,
                "win_prob_pct": round(win_prob * 100.0, 1),
            }
        )

    hit_rate = round((greens / max(1, games_target)) * 100.0, 1)
    profit_units = round(bankroll - bankroll_start, 2)
    roi = round((profit_units / max(1.0, total_staked)) * 100.0, 1)
    return {
        "total_games": games_target,
        "greens": greens,
        "reds": reds,
        "hit_rate": hit_rate,
        "start_bankroll": round(bankroll_start, 2),
        "end_bankroll": round(bankroll, 2),
        "profit_units": profit_units,
        "roi": roi,
        "max_drawdown": round(max_drawdown, 2),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "stake_percent": stake_pct,
        "rows": rows,
        "note": "Simulacao paper/live: usa leitura atual do scanner ao vivo, sem executar aposta real.",
    }


def _synthetic_live_odds(score: int, risk: int, rng: random.Random) -> float:
    base = 1.38 + ((100 - score) / 170.0) + (risk / 310.0)
    noisy = base + rng.uniform(-0.08, 0.22)
    return round(max(1.25, min(3.65, noisy)), 3)


def _probability_from_live_read(
    score: int,
    risk: int,
    confidence: int,
    pick: dict[str, Any],
    rng: random.Random,
) -> float:
    action = str(pick.get("action") or "").upper()
    action_bias = {"ENTRAR": 0.08, "AGUARDAR": -0.02, "SEGURAR": -0.05, "SAIR": -0.1}.get(action, 0.0)
    score_component = (score - 50) / 150.0
    conf_component = (confidence - 50) / 190.0
    risk_component = (50 - risk) / 180.0
    market_text = str(pick.get("market") or "").lower()
    market_bias = 0.03 if "gols" in market_text else 0.02 if "1x2" in market_text else 0.0
    noise = rng.uniform(-0.05, 0.05)
    probability = 0.5 + score_component + conf_component + risk_component + action_bias + market_bias + noise
    return max(0.08, min(0.92, probability))


def _simulation_session_panel(session: dict[str, Any]) -> str:
    rows = session.get("rows") or []
    if not rows:
        return f"<p class='muted'>{_esc(session.get('note') or 'Sem dados para simulacao.')}</p>"
    return (
        "<div class='sim-lab-grid'>"
        f"<div class='sim-lab-kpi'><div class='muted'>Jogos simulados</div><div class='metric'>{_esc(session.get('total_games', 0))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Greens</div><div class='metric green'>{_esc(session.get('greens', 0))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Reds</div><div class='metric red'>{_esc(session.get('reds', 0))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Hit rate</div><div class='metric'>{_esc(session.get('hit_rate', 0))}%</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Banca inicial</div><div class='metric'>{_esc(_format_brl(session.get('start_bankroll', 0)))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Banca final</div><div class='metric {_value_class(session.get('profit_units', 0))}'>{_esc(_format_brl(session.get('end_bankroll', 0)))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Lucro</div><div class='metric {_value_class(session.get('profit_units', 0))}'>{_esc(_format_brl(session.get('profit_units', 0)))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Max drawdown</div><div class='metric red'>{_esc(_format_brl(session.get('max_drawdown', 0)))}</div></div>"
        f"<div class='sim-lab-kpi'><div class='muted'>Sequencia max</div><div class='metric'>{_esc(session.get('max_win_streak', 0))}G / {_esc(session.get('max_loss_streak', 0))}R</div></div>"
        "</div>"
        "<div class='sim-lab-table-wrap'>"
        "<table>"
        "<thead><tr><th>#</th><th>Jogo</th><th>Mercado</th><th>Leitura</th><th>Odd</th><th>Stake</th><th>Resultado</th><th>Lucro</th><th>Banca</th></tr></thead>"
        f"<tbody>{_sim_result_rows_html(rows)}</tbody>"
        "</table>"
        "</div>"
        f"<p class='muted sim-lab-note'>{_esc(session.get('note'))}</p>"
    )


def _simulation_history_panel(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return "<p class='muted'>Ainda sem simulacoes salvas. Rode a sessao de 30 jogos para iniciar o historico.</p>"
    rows: list[str] = []
    for idx, session in enumerate(sessions[:40], start=1):
        created_at = _short_datetime(session.get("created_at"))
        total_games = _safe_int(session.get("total_games"))
        greens = _safe_int(session.get("greens"))
        reds = _safe_int(session.get("reds"))
        hit_rate = _safe_float(session.get("hit_rate"))
        profit = _safe_float(session.get("profit_units"))
        end_bankroll = _safe_float(session.get("end_bankroll"))
        drawdown = _safe_float(session.get("max_drawdown"))
        rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{_esc(created_at)}</td>"
            f"<td>{_esc(total_games)}</td>"
            f"<td class='win'>{_esc(greens)}</td>"
            f"<td class='loss'>{_esc(reds)}</td>"
            f"<td>{_esc(round(hit_rate, 1))}%</td>"
            f"<td class='{_value_class(profit)}'>{_esc(_format_brl(round(profit, 2)))}</td>"
            f"<td>{_esc(_format_brl(round(end_bankroll, 2)))}</td>"
            f"<td class='red'>{_esc(_format_brl(round(drawdown, 2)))}</td>"
            "</tr>"
        )
    return (
        "<div class='sim-history-wrap'>"
        "<table>"
        "<thead><tr><th>#</th><th>Data</th><th>Jogos</th><th>Green</th><th>Red</th><th>Hit</th><th>Lucro</th><th>Banca final</th><th>DD max</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )


def _sim_result_rows_html(rows: list[dict[str, Any]]) -> str:
    html_rows: list[str] = []
    for row in rows:
        outcome = str(row.get("outcome") or "").upper()
        outcome_cls = "win" if outcome == "GREEN" else "loss"
        html_rows.append(
            "<tr>"
            f"<td>{_esc(row.get('idx'))}</td>"
            f"<td>{_esc(row.get('match'))}<br><span class='muted'>{_esc(row.get('minute'))}' | {_esc(row.get('scoreline'))}</span></td>"
            f"<td>{_esc(row.get('market'))}</td>"
            f"<td>{_esc(row.get('selection'))} {_esc(row.get('line'))}<br><span class='muted'>p={_esc(row.get('win_prob_pct'))}%</span></td>"
            f"<td>{_esc(row.get('odds'))}</td>"
            f"<td>{_esc(_format_brl(row.get('stake')))}</td>"
            f"<td class='{outcome_cls}'>{_esc(outcome)}</td>"
            f"<td class='{_value_class(row.get('profit'))}'>{_esc(_format_brl(row.get('profit')))}</td>"
            f"<td>{_esc(_format_brl(row.get('bankroll')))}</td>"
            "</tr>"
        )
    return "".join(html_rows)


def _championship_rows(last_games: list[dict[str, Any]]) -> str:
    if not last_games:
        return "<tr><td colspan='5'>Sem campeonatos ao vivo no momento.</td></tr>"
    leagues: dict[str, dict[str, Any]] = {}
    for game in last_games:
        if not isinstance(game, dict):
            continue
        league = str(game.get("league") or game.get("division") or "Sem liga")
        entry = leagues.setdefault(
            league,
            {"games": 0, "goals": 0, "pace": 0.0, "leader": "-", "leader_momentum": -1},
        )
        home_goals = _safe_int(game.get("home_goals"))
        away_goals = _safe_int(game.get("away_goals"))
        home_pressure = _safe_int(game.get("home_pressure"))
        away_pressure = _safe_int(game.get("away_pressure"))
        home_shots = _safe_int(game.get("home_shots_on"))
        away_shots = _safe_int(game.get("away_shots_on"))
        entry["games"] += 1
        entry["goals"] += home_goals + away_goals
        entry["pace"] += home_pressure + away_pressure + ((home_shots + away_shots) * 9) + ((home_goals + away_goals) * 22)

        home_momentum = _team_momentum(game, "home")
        away_momentum = _team_momentum(game, "away")
        if home_momentum > entry["leader_momentum"]:
            entry["leader"] = str(game.get("home") or "-")
            entry["leader_momentum"] = home_momentum
        if away_momentum > entry["leader_momentum"]:
            entry["leader"] = str(game.get("away") or "-")
            entry["leader_momentum"] = away_momentum

    ranked = sorted(
        leagues.items(),
        key=lambda item: (int(item[1]["games"]), float(item[1]["pace"]) / max(1, int(item[1]["games"]))),
        reverse=True,
    )
    rows: list[str] = []
    for league, data in ranked[:12]:
        avg_pace = round(float(data["pace"]) / max(1, int(data["games"])))
        if avg_pace >= 135:
            trend = "▲ Forte"
            trend_class = "pos"
        elif avg_pace >= 95:
            trend = "■ Estavel"
            trend_class = "void"
        else:
            trend = "▼ Frio"
            trend_class = "neg"
        rows.append(
            "<tr>"
            f"<td data-label='Campeonato'>{_esc(league)}</td>"
            f"<td data-label='Jogos'>{_esc(data['games'])}</td>"
            f"<td data-label='Gols'>{_esc(data['goals'])}</td>"
            f"<td data-label='Lider live'>{_esc(data['leader'])}</td>"
            f"<td data-label='Ritmo' class='{trend_class}'>{_esc(trend)} ({avg_pace})</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='5'>Sem campeonatos ao vivo no momento.</td></tr>"


def _leadership_rows(last_games: list[dict[str, Any]]) -> str:
    leaders: list[dict[str, Any]] = []
    for game in last_games:
        if not isinstance(game, dict):
            continue
        league = str(game.get("league") or game.get("division") or "Sem liga")
        minute = _safe_int(game.get("minute"))
        scoreline = f"{_safe_int(game.get('home_goals'))}x{_safe_int(game.get('away_goals'))}"
        for side in ("home", "away"):
            team = str(game.get(side) or "-")
            leaders.append(
                {
                    "team": team,
                    "league": league,
                    "minute": minute,
                    "scoreline": scoreline,
                    "momentum": _team_momentum(game, side),
                }
            )
    ranked = sorted(leaders, key=lambda item: int(item["momentum"]), reverse=True)
    rows: list[str] = []
    for item in ranked[:14]:
        momentum = int(item["momentum"])
        klass = "pos" if momentum >= 95 else "void" if momentum >= 65 else "neg"
        rows.append(
            "<tr>"
            f"<td data-label='Time'>{_esc(item['team'])}</td>"
            f"<td data-label='Liga'>{_esc(item['league'])}</td>"
            f"<td data-label='Placar'>{_esc(item['scoreline'])}</td>"
            f"<td data-label='Min'>{_esc(item['minute'])}'</td>"
            f"<td data-label='Momentum' class='{klass}'>{_esc(momentum)}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='5'>Sem liderancas ao vivo no momento.</td></tr>"


def _market_tape(last_games: list[dict[str, Any]]) -> str:
    if not last_games:
        return "<span class='tape-chip mid'>Aguardando feed ao vivo</span>"
    leagues: dict[str, dict[str, int]] = {}
    for game in last_games:
        if not isinstance(game, dict):
            continue
        league = str(game.get("league") or game.get("division") or "Sem liga")
        entry = leagues.setdefault(league, {"games": 0, "goals": 0, "pace": 0})
        home_goals = _safe_int(game.get("home_goals"))
        away_goals = _safe_int(game.get("away_goals"))
        home_pressure = _safe_int(game.get("home_pressure"))
        away_pressure = _safe_int(game.get("away_pressure"))
        entry["games"] += 1
        entry["goals"] += home_goals + away_goals
        entry["pace"] += home_pressure + away_pressure + ((home_goals + away_goals) * 22)

    ranked = sorted(
        leagues.items(),
        key=lambda item: (item[1]["games"], item[1]["pace"]),
        reverse=True,
    )
    chips: list[str] = []
    for league, data in ranked[:10]:
        avg_pace = round(data["pace"] / max(1, data["games"]))
        if avg_pace >= 130:
            klass = "up"
            symbol = "▲"
        elif avg_pace >= 95:
            klass = "mid"
            symbol = "■"
        else:
            klass = "down"
            symbol = "▼"
        chips.append(
            f"<span class='tape-chip {klass}'>{symbol} {_esc(league)} | {_esc(data['games'])}j | {_esc(data['goals'])}g</span>"
        )
    return "".join(chips) or "<span class='tape-chip mid'>Aguardando feed ao vivo</span>"


def _team_momentum(game: dict[str, Any], side: str) -> int:
    goals_for = _safe_int(game.get(f"{side}_goals"))
    opposite = "away" if side == "home" else "home"
    goals_against = _safe_int(game.get(f"{opposite}_goals"))
    pressure = _safe_int(game.get(f"{side}_pressure"))
    shots = _safe_int(game.get(f"{side}_shots_on"))
    minute = _safe_int(game.get("minute"))
    time_bonus = max(0, 90 - minute) * 0.2
    raw = (goals_for * 36) - (goals_against * 18) + (pressure * 0.9) + (shots * 7.5) + time_bonus
    return max(0, int(round(raw)))


def _source_catalog_panel() -> str:
    rows = []
    for source in FOOTBALL_DATA_SOURCES[:8]:
        rows.append(
            "<tr>"
            f"<td><a href='{_esc(source['url'])}' target='_blank' rel='noopener'>{_esc(source['name'])}</a></td>"
            f"<td>{_esc(source['role'])}</td>"
            f"<td>{_esc(source['tier'])}</td>"
            f"<td>{_esc(source['integration'])}</td>"
            "</tr>"
        )
    return (
        "<p class='muted'>Prioridade: API/CSV estavel primeiro; scraping de casa de aposta fica fora do caminho principal.</p>"
        "<table><thead><tr><th>Fonte</th><th>Uso</th><th>Tipo</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _league_radar_panel(last_games: list[dict[str, Any]]) -> str:
    if not last_games:
        return "<p class='muted'>Sem jogos recentes para montar radar agora.</p>"
    by_league: dict[str, int] = Counter(
        str((game or {}).get("league") or (game or {}).get("division") or "Sem liga")
        for game in last_games
    )
    rows = []
    for league, total in by_league.most_common(6):
        rows.append(
            "<tr>"
            f"<td>{_esc(league)}</td>"
            f"<td>{_esc(total)}</td>"
            f"<td class='muted'>monitoramento ativo</td>"
            "</tr>"
        )
    return (
        "<p class='muted'>Distribuicao dos jogos mais recentes no scanner, com prioridade para Brasil e expansao global.</p>"
        "<table><thead><tr><th>Liga</th><th>Jogos</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


FANTASY_FORMATIONS: dict[str, dict[str, int]] = {
    "4-4-2": {"GOL": 1, "ZAG": 2, "LAT": 2, "MEI": 4, "ATA": 2},
    "4-3-3": {"GOL": 1, "ZAG": 2, "LAT": 2, "MEI": 3, "ATA": 3},
    "5-3-2": {"GOL": 1, "ZAG": 3, "LAT": 2, "MEI": 3, "ATA": 2},
    "3-5-2": {"GOL": 1, "ZAG": 3, "LAT": 0, "MEI": 5, "ATA": 2},
    "3-4-3": {"GOL": 1, "ZAG": 3, "LAT": 0, "MEI": 4, "ATA": 3},
    "4-5-1": {"GOL": 1, "ZAG": 2, "LAT": 2, "MEI": 5, "ATA": 1},
    "5-4-1": {"GOL": 1, "ZAG": 3, "LAT": 2, "MEI": 4, "ATA": 1},
}


def _fantasy_help_panel() -> str:
    return (
        "<p class='muted'>Motor de escalação por orçamento e projeção. Use dados próprios/permitidos para jogadores "
        "e gere a melhor combinação para o fantasy.</p>"
        "<table><thead><tr><th>Campo</th><th>Formato</th></tr></thead><tbody>"
        "<tr><td>Nome</td><td>Texto livre</td></tr>"
        "<tr><td>Posição</td><td>GOL, ZAG, LAT, MEI, ATA, TEC</td></tr>"
        "<tr><td>Time</td><td>Clube do atleta</td></tr>"
        "<tr><td>Preço</td><td>Número (ex: 12.5)</td></tr>"
        "<tr><td>Projeção</td><td>Pontos esperados (ex: 6.7)</td></tr>"
        "</tbody></table>"
    )


def _normalize_fantasy_position(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    mapping = {
        "GOLEIRO": "GOL",
        "GK": "GOL",
        "GOL": "GOL",
        "ZAGUEIRO": "ZAG",
        "CB": "ZAG",
        "ZAG": "ZAG",
        "LATERAL": "LAT",
        "LB": "LAT",
        "RB": "LAT",
        "LAT": "LAT",
        "MEIA": "MEI",
        "MID": "MEI",
        "MEI": "MEI",
        "ATACANTE": "ATA",
        "FWD": "ATA",
        "ATA": "ATA",
        "TECNICO": "TEC",
        "TÉCNICO": "TEC",
        "COACH": "TEC",
        "TEC": "TEC",
    }
    return mapping.get(raw)


def _parse_fantasy_players(players_text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    dedupe: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw_line in str(players_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("nome;") or low.startswith("jogador;") or low.startswith("player;"):
            continue
        parts = [part.strip() for part in re.split(r"[;\t|,]+", line) if part.strip()]
        if len(parts) < 5:
            continue
        name = parts[0][:80]
        pos = _normalize_fantasy_position(parts[1])
        team = parts[2][:80] if len(parts) >= 3 else "-"
        price = _safe_float(str(parts[3]).replace("R$", "").replace(",", "."))
        proj = _safe_float(str(parts[4]).replace(",", "."))
        if not name or not pos or price <= 0 or proj <= 0:
            continue
        player = {
            "name": name,
            "pos": pos,
            "team": team or "-",
            "price": round(price, 2),
            "proj": round(proj, 2),
            "value": round(proj / max(price, 0.01), 4),
        }
        key = (player["name"].lower(), player["pos"], player["team"].lower())
        current = dedupe.get(key)
        if current is None or float(player["proj"]) > float(current["proj"]):
            dedupe[key] = player
    parsed.extend(dedupe.values())
    parsed.sort(key=lambda item: (item["pos"], -float(item["proj"]), item["name"]))
    return parsed


def _position_pool(players: list[dict[str, Any]], pos: str, required: int) -> list[dict[str, Any]]:
    pool = [item for item in players if item.get("pos") == pos]
    pool.sort(
        key=lambda item: (
            float(item.get("proj") or 0),
            float(item.get("value") or 0),
        ),
        reverse=True,
    )
    if required <= 1:
        return pool[:30]
    if required <= 3:
        return pool[:24]
    return pool[:18]


def _position_combos(pool: list[dict[str, Any]], required: int) -> list[dict[str, Any]]:
    if required <= 0:
        return [{"players": [], "price": 0.0, "proj": 0.0, "value": 0.0}]
    if len(pool) < required:
        return []
    combo_limits = {1: 40, 2: 180, 3: 260, 4: 320, 5: 360}
    limit = combo_limits.get(required, 240)
    combos_list: list[dict[str, Any]] = []
    for group in combinations(pool, required):
        price = round(sum(float(item["price"]) for item in group), 2)
        proj = round(sum(float(item["proj"]) for item in group), 2)
        value = round(sum(float(item["value"]) for item in group), 4)
        combos_list.append(
            {
                "players": list(group),
                "price": price,
                "proj": proj,
                "value": value,
            }
        )
    combos_list.sort(key=lambda item: (float(item["proj"]), float(item["value"])), reverse=True)
    return combos_list[:limit]


def _merge_lineup_states(
    states: list[dict[str, Any]],
    options: list[dict[str, Any]],
    budget: float,
    *,
    max_states: int = 2200,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for state in states:
        for option in options:
            next_price = round(float(state["price"]) + float(option["price"]), 2)
            if next_price > budget:
                continue
            merged.append(
                {
                    "players": [*state["players"], *option["players"]],
                    "price": next_price,
                    "proj": round(float(state["proj"]) + float(option["proj"]), 2),
                    "value": round(float(state["value"]) + float(option["value"]), 4),
                }
            )
    if not merged:
        return []
    merged.sort(key=lambda item: (float(item["proj"]), float(item["value"])), reverse=True)
    compact: dict[int, dict[str, Any]] = {}
    for state in merged:
        bucket = int(round(float(state["price"]) * 10))
        current = compact.get(bucket)
        if current is None or float(state["proj"]) > float(current["proj"]):
            compact[bucket] = state
    selected = sorted(compact.values(), key=lambda item: (float(item["proj"]), float(item["value"])), reverse=True)
    return selected[:max_states]


def _optimize_fantasy_lineup(
    players: list[dict[str, Any]],
    *,
    formation: str,
    budget: float,
) -> dict[str, Any]:
    chosen_formation = formation if formation in FANTASY_FORMATIONS else "4-4-2"
    target = dict(FANTASY_FORMATIONS[chosen_formation])
    budget = max(20.0, min(9999.0, float(budget)))
    position_order = ["GOL", "ZAG", "LAT", "MEI", "ATA"]

    states = [{"players": [], "price": 0.0, "proj": 0.0, "value": 0.0}]
    for pos in position_order:
        required = int(target.get(pos, 0))
        options = _position_combos(_position_pool(players, pos, required), required)
        if not options:
            return {
                "ok": False,
                "message": f"Faltam jogadores para {pos}. Necessário: {required}.",
            }
        states = _merge_lineup_states(states, options, budget)
        if not states:
            return {
                "ok": False,
                "message": "Nenhuma combinação coube no orçamento informado. Aumente o orçamento ou revise os preços.",
            }

    coaches = _position_pool(players, "TEC", 1)
    best_lineup: dict[str, Any] | None = None
    for state in states:
        candidate_players = list(state["players"])
        candidate_price = float(state["price"])
        candidate_proj = float(state["proj"])
        candidate_value = float(state["value"])
        coach_used = None
        remaining = budget - candidate_price
        affordable = [coach for coach in coaches if float(coach["price"]) <= remaining]
        if affordable:
            coach_used = max(affordable, key=lambda item: (float(item["proj"]), float(item["value"])))
            candidate_players.append(coach_used)
            candidate_price = round(candidate_price + float(coach_used["price"]), 2)
            candidate_proj = round(candidate_proj + float(coach_used["proj"]), 2)
            candidate_value = round(candidate_value + float(coach_used["value"]), 4)
        candidate_score = round(candidate_proj + (candidate_value * 0.22), 4)
        candidate = {
            "players": candidate_players,
            "price": candidate_price,
            "proj": candidate_proj,
            "value": candidate_value,
            "score": candidate_score,
            "coach": coach_used,
        }
        if best_lineup is None:
            best_lineup = candidate
            continue
        if (
            float(candidate["score"]) > float(best_lineup["score"])
            or (
                float(candidate["score"]) == float(best_lineup["score"])
                and float(candidate["price"]) < float(best_lineup["price"])
            )
        ):
            best_lineup = candidate

    if best_lineup is None:
        return {"ok": False, "message": "Não foi possível montar escalação com os dados enviados."}

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in ["GOL", "ZAG", "LAT", "MEI", "ATA", "TEC"]}
    for player in best_lineup["players"]:
        grouped.setdefault(str(player["pos"]), []).append(player)

    ordered_players: list[dict[str, Any]] = []
    for pos in ["GOL", "ZAG", "LAT", "MEI", "ATA", "TEC"]:
        grouped[pos].sort(key=lambda item: float(item["proj"]), reverse=True)
        ordered_players.extend(grouped[pos])

    used_budget = round(float(best_lineup["price"]), 2)
    remaining = round(max(0.0, budget - used_budget), 2)
    return {
        "ok": True,
        "formation": chosen_formation,
        "budget": round(budget, 2),
        "used_budget": used_budget,
        "remaining_budget": remaining,
        "projected_points": round(float(best_lineup["proj"]), 2),
        "players": ordered_players,
        "has_coach": bool(best_lineup.get("coach")),
        "message": f"Escalação otimizada em {chosen_formation} com projeção de {round(float(best_lineup['proj']), 2)} pts.",
    }


def _fantasy_result_panel(result: dict[str, Any]) -> str:
    players = result.get("players") or []
    if not players:
        return "<p class='muted'>Sem jogadores suficientes para montar a escalação.</p>"
    rows: list[str] = []
    for player in players:
        rows.append(
            "<tr>"
            f"<td>{_esc(player.get('pos'))}</td>"
            f"<td>{_esc(player.get('name'))}</td>"
            f"<td>{_esc(player.get('team'))}</td>"
            f"<td>{_esc(player.get('price'))}</td>"
            f"<td>{_esc(player.get('proj'))}</td>"
            "</tr>"
        )
    coach_note = " + técnico" if result.get("has_coach") else ""
    return (
        "<div class='active-line'>"
        f"<div class='mini'><div class='muted'>Formação</div><strong>{_esc(result.get('formation'))}{coach_note}</strong></div>"
        f"<div class='mini'><div class='muted'>Orçamento</div><strong>{_esc(result.get('budget'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Usado</div><strong>{_esc(result.get('used_budget'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Saldo</div><strong>{_esc(result.get('remaining_budget'))}</strong></div>"
        f"<div class='mini'><div class='muted'>Projeção</div><strong>{_esc(result.get('projected_points'))} pts</strong></div>"
        "</div>"
        "<div class='table-wrap'><table class='responsive'>"
        "<thead><tr><th>Pos</th><th>Jogador</th><th>Time</th><th>Preço</th><th>Proj.</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def _commercial_panel(settings) -> str:
    website = _esc(settings.website_url or "-")
    whatsapp = _esc(settings.sales_whatsapp or "-")
    email = _esc(settings.sales_email or "-")
    starter = _esc(f"R$ {settings.plan_starter_price_brl:.0f}/mes")
    pro = _esc(f"R$ {settings.plan_pro_price_brl:.0f}/mes")
    team = _esc(f"R$ {settings.plan_team_price_brl:.0f}/mes")
    return (
        "<div class='active-line'>"
        "<div class='mini'><div class='muted'>Produto</div>"
        f"<strong>{_esc(settings.product_name)}</strong><div class='muted'>{_esc(settings.product_tagline)}</div></div>"
        f"<div class='mini'><div class='muted'>Starter</div><strong>{starter}</strong><div class='muted'>scanner + telegram + dashboard</div></div>"
        f"<div class='mini'><div class='muted'>Pro</div><strong>{pro}</strong><div class='muted'>tudo do starter + memoria IA + suporte</div></div>"
        f"<div class='mini'><div class='muted'>Team</div><strong>{team}</strong><div class='muted'>multi-operador + operacao guiada</div></div>"
        f"<div class='mini'><div class='muted'>Site</div><strong>{website}</strong></div>"
        f"<div class='mini'><div class='muted'>WhatsApp</div><strong>{whatsapp}</strong></div>"
        f"<div class='mini'><div class='muted'>Email</div><strong>{email}</strong></div>"
        "</div>"
        "<p class='muted'>Dor resolvida: excesso de ruido, entrada sem criterio e falta de disciplina operacional. "
        "Oferta focada em decisao clara, rotina e controle de risco.</p>"
    )


def _best_name(rows: Any) -> str:
    if not rows:
        return "-"
    name = str(rows[0].get("name", "-"))
    return _esc(name[:14])


def _advice(history: list[dict[str, Any]]) -> str:
    settled = [item for item in history if item.get("outcome") in {"win", "loss"}]
    if len(settled) < 5:
        return "Ainda ha poucos resultados marcados. Marque green/red no Telegram para a IA estatistica aprender quais ligas, minutos e niveis de confianca funcionam melhor."

    by_league: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in settled:
        league = item.get("game", {}).get("league", "Sem liga")
        by_league[league].append(item)

    ranked = []
    for league, items in by_league.items():
        if len(items) < 2:
            continue
        wins = sum(1 for item in items if item.get("outcome") == "win")
        ranked.append((wins / len(items), league, len(items)))
    if not ranked:
        return "Continue acumulando historico. A recomendacao fica mais forte quando cada liga tiver pelo menos 2 sinais finalizados."
    ranked.sort(reverse=True)
    rate, league, count = ranked[0]
    return f"Melhor recorte ate agora: {league}, com {round(rate * 100, 1)}% de acerto em {count} sinais. Priorize entradas parecidas e reduza stake em ligas sem historico."


def _to_plain_dict(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        return asdict(item)
    return dict(item)


def _label(outcome: str) -> str:
    return {"win": "Green", "loss": "Red", "void": "Anulada", "open": "Aberta"}.get(
        outcome, outcome
    )


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _edge(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{round(float(value) * 100, 1)}%"
    except (TypeError, ValueError):
        return "-"


def _value_class(value: Any, multiplier: int = 1) -> str:
    try:
        numeric = float(value) * multiplier
    except (TypeError, ValueError):
        return "flat"
    if numeric > 0:
        return "pos"
    if numeric < 0:
        return "neg"
    return "flat"


def _entry_summary(item: dict[str, Any]) -> str:
    market = item.get("entry_market") or item.get("market") or item.get("action", "-")
    selection = item.get("entry_selection")
    line = item.get("entry_line")
    value = item.get("entry_value")
    odds = item.get("entry_odds") or item.get("target_odds")
    detail = []
    if selection:
        detail.append(f"seleção {_esc(selection)}")
    if line:
        detail.append(f"linha {_esc(line)}")
    if odds:
        detail.append(f"odd {_esc(odds)}")
    if value:
        detail.append(f"valor {_esc(value)}")
    if detail:
        return f"<strong>{_esc(market)}</strong><br><span class='muted'>{' | '.join(detail)}</span>"
    return f"<strong>{_esc(market)}</strong>"


def _real_value_summary(item: dict[str, Any]) -> str:
    value = item.get("entry_value")
    odds = item.get("entry_odds") or item.get("target_odds")
    profit = item.get("profit_value")
    parts = []
    if value is not None:
        parts.append(f"R$ {_esc(value)}")
    if odds:
        parts.append(f"odd {_esc(odds)}")
    if profit is not None:
        parts.append(f"lucro R$ {_esc(profit)}")
    if parts:
        return "<br>".join(parts)
    return "<span class='muted'>clique em editar</span>"


def _js_string(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _domains(raw: str) -> str:
    links = []
    for item in raw.split(","):
        url = item.strip()
        if not url:
            continue
        links.append(f"<a href='{_esc(url)}'>{_esc(url)}</a>")
    return " ".join(links)
