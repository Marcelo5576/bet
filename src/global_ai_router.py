from __future__ import annotations

import html
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from services.globalAdaptiveIntelligence import get_global_adaptive_intelligence
from src.portal_web import _require_admin, _require_user


router = APIRouter()
NOTICE = "Este sistema é uma ferramenta estatística de apoio. Não garante lucro. Use com responsabilidade."
logger = logging.getLogger(__name__)


class _GlobalDependencyFailure:
    def __init__(self, error: Exception) -> None:
        self.error = error


class FootballAnalysisPayload(BaseModel):
    event_id: int
    market: str = "match_winner_home"
    offered_odd: float | None = None


class BacktestPayload(BaseModel):
    league: str | None = None
    season: int | None = None
    market: str = "match_winner_home"
    ev_min: float = 0.03
    confidence_min: float = 60.0
    bankroll: float = 1000.0
    bankroll_profile: str = "moderado"
    model_version: str = "baseline"


class MonteCarloPayload(BaseModel):
    hit_rate: float = 0.55
    average_odd: float = 1.9
    bankroll: float = 1000.0
    stake_pct: float = 0.015


class AgentPromptPayload(BaseModel):
    prompt: str


class RAGPayload(BaseModel):
    question: str


class GovernanceDecisionPayload(BaseModel):
    decision: str


def _global():
    try:
        return get_global_adaptive_intelligence()
    except Exception as exc:  # pragma: no cover - exercised via route degradation
        logger.exception("global ai dependency bootstrap failed")
        return _GlobalDependencyFailure(exc)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _json(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=jsonable_encoder(payload), status_code=status_code)


def _research_health_fallback(error: str | None = None) -> dict[str, Any]:
    return {
        "counts": {
            "historical_matches": 0,
            "historical_features": 0,
            "league_reliability_scores": 0,
        },
        "supabase": {
            "enabled": False,
            "schema_mode": "unavailable",
            "last_error": error or "",
        },
    }


def _degraded_payload(
    message: str,
    *,
    error: Exception | None = None,
    research_health: dict[str, Any] | None = None,
    live_sources: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "degraded": True,
        "message": message,
        "error_type": error.__class__.__name__ if error else "",
        "research_health": research_health or _research_health_fallback(str(error or "")),
        "live_sources": live_sources or [],
    }
    if extra:
        payload.update(extra)
    return payload


def _safe_platform_health(platform: Any | None) -> dict[str, Any]:
    if platform is None:
        return _research_health_fallback()
    try:
        return platform.research_health_snapshot()
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception("global ai research health snapshot failed")
        return _research_health_fallback(str(exc))


def _safe_live_sources(platform: Any | None) -> list[dict[str, Any]]:
    if platform is None:
        return []
    try:
        return platform.live_source_runtime_snapshot()
    except Exception:  # pragma: no cover - defensive path
        logger.exception("global ai live source snapshot failed")
        return []


def _unwrap_platform(platform_dep: Any) -> tuple[Any | None, Exception | None]:
    if isinstance(platform_dep, _GlobalDependencyFailure):
        return None, platform_dep.error
    return platform_dep, None


def _shell(title: str, mode: str) -> str:
    tabs = [
        ("/app/global-ai-control-center", "Global AI Control Center"),
        ("/app/football-analysis", "Football Analysis"),
        ("/app/backtesting-lab", "Backtesting Lab"),
        ("/app/monte-carlo-lab", "Monte Carlo Lab"),
        ("/app/strategy-evolution-lab", "Strategy Evolution Lab"),
        ("/app/agent-arena", "Agent Arena"),
        ("/app/feature-lab", "Feature Lab"),
        ("/app/drift-regime-monitor", "Drift & Regime Monitor"),
        ("/app/market-bias-anomaly-center", "Bias & Anomaly Center"),
        ("/app/rag-memory-explorer", "RAG Memory Explorer"),
        ("/app/governance-center", "Governance Center"),
    ]
    nav = "".join(
        f"<a class='tab {'active' if href.endswith(mode) else ''}' href='{href}'>{_esc(label)}</a>"
        for href, label in tabs
    )
    template = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg:#0b0f14; --panel:#121922; --line:#243143; --text:#eef3f8; --muted:#96a4b8;
      --green:#15d48a; --amber:#ffd24d; --red:#ff6b7a; --blue:#5da8ff;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:Inter,Segoe UI,Arial,sans-serif; }}
    header {{ position:sticky; top:0; z-index:10; background:rgba(11,15,20,.96); border-bottom:1px solid var(--line); backdrop-filter:blur(12px); }}
    .wrap {{ max-width:1380px; margin:0 auto; padding:18px; }}
    .top {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; flex-wrap:wrap; }}
    .brand h1 {{ margin:0; font-size:30px; }}
    .brand p {{ margin:8px 0 0; color:var(--muted); max-width:880px; }}
    .notice {{ border:1px solid rgba(255,210,77,.35); background:rgba(255,210,77,.08); color:#ffe69a; padding:10px 14px; border-radius:16px; font-size:13px; }}
    .tabs {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }}
    .tab {{ text-decoration:none; color:var(--text); border:1px solid var(--line); background:var(--panel); padding:10px 14px; border-radius:999px; font-weight:800; }}
    .tab.active {{ color:var(--green); border-color:rgba(21,212,138,.4); box-shadow:0 0 0 1px rgba(21,212,138,.14) inset; }}
    .grid {{ display:grid; gap:16px; grid-template-columns:repeat(12,minmax(0,1fr)); margin-top:18px; }}
    .span-12 {{ grid-column:span 12; }} .span-8 {{ grid-column:span 8; }} .span-6 {{ grid-column:span 6; }} .span-4 {{ grid-column:span 4; }} .span-3 {{ grid-column:span 3; }}
    .card {{ background:linear-gradient(180deg,#141c27,#111821); border:1px solid var(--line); border-radius:18px; padding:16px; box-shadow:0 12px 28px rgba(0,0,0,.18); }}
    .kpis {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }}
    .mini {{ background:#0f151d; border:1px solid var(--line); border-radius:14px; padding:14px; }}
    .mini strong {{ display:block; font-size:24px; margin-top:8px; }}
    .mini.error {{ border-color:rgba(255,107,122,.45); color:#ffd3d8; }}
    .muted {{ color:var(--muted); }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    h3 {{ margin:0 0 8px; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }}
    .row {{ display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); }}
    input, select, textarea, button {{
      width:100%; border:1px solid var(--line); background:#0f151d; color:var(--text); padding:12px; border-radius:12px; font:inherit;
    }}
    button, .btn {{ cursor:pointer; font-weight:800; }}
    button:disabled {{ cursor:progress; opacity:.72; }}
    button.primary, .btn.primary {{ background:linear-gradient(180deg,#1e6fff,#0d4ed9); border-color:#2563eb; }}
    button.good, .btn.good {{ background:linear-gradient(180deg,#169b61,#0d7b4d); border-color:#0f9a5e; }}
    button.warn, .btn.warn {{ background:linear-gradient(180deg,#d9a31a,#b6850f); border-color:#d9a31a; color:#101317; }}
    .btn {{ display:inline-flex; align-items:center; justify-content:center; min-height:44px; padding:0 16px; border-radius:12px; border:1px solid var(--line); background:#111827; color:#fff; text-decoration:none; }}
    .pill {{ display:inline-flex; gap:6px; align-items:center; border:1px solid var(--line); border-radius:999px; padding:6px 10px; font-size:12px; font-weight:800; }}
    .pill.green {{ color:var(--green); border-color:rgba(21,212,138,.35); }}
    .pill.amber {{ color:var(--amber); border-color:rgba(255,210,77,.35); }}
    .pill.red {{ color:var(--red); border-color:rgba(255,107,122,.35); }}
    .list {{ display:grid; gap:12px; }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:#0c1117; border:1px solid var(--line); padding:12px; border-radius:12px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid rgba(255,255,255,.05); vertical-align:top; }}
    @media (max-width: 980px) {{
      .span-8,.span-6,.span-4,.span-3 {{ grid-column:span 12; }}
      .row {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="top">
        <div class="brand">
          <h1>Global Adaptive Sports & Market Intelligence</h1>
          <p>Camada global de pesquisa quantitativa, ensembles, agentes, Monte Carlo, governança e memória longa, montada por cima do ApexGol atual sem automatizar aposta real.</p>
        </div>
        <div class="notice">__NOTICE__</div>
      </div>
      <nav class="tabs">__NAV__</nav>
    </div>
  </header>
  <main class="wrap"><div id="page-root" data-mode="__MODE__"></div></main>
  <script>
    const root = document.getElementById('page-root');
    const mode = root?.dataset?.mode || 'global-ai-control-center';
    const money = (value) => value == null ? '-' : new Intl.NumberFormat('pt-BR', {{ style:'currency', currency:'BRL' }}).format(Number(value));
    const pct = (value) => value == null ? '-' : `${{Number(value).toFixed(2)}}%`;
    async function api(url, options) {{
      const response = await fetch(url, {{
        headers: {{ 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }},
        credentials: 'include',
        ...options
      }});
      if (!response.ok) {{
        let detail = response.statusText;
        try {{
          const body = await response.json();
          detail = body.detail || body.message || detail;
        }} catch (_err) {{}}
        const error = new Error(detail);
        error.status = response.status;
        error.detail = detail;
        throw error;
      }}
      return response.json();
    }}
    function card(title, body) {{
      return `<section class="card">${{title ? `<h2>${{title}}</h2>` : ''}}${{body}}</section>`;
    }}
    function escapeHtml(value) {{
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}
    function parseLocaleNumber(value, fallback) {{
      const raw = String(value ?? '').trim();
      if (!raw) return fallback;
      let normalized = raw.replace(/\\s+/g, '');
      if (normalized.includes(',') && normalized.includes('.')) {{
        if (normalized.lastIndexOf(',') > normalized.lastIndexOf('.')) {{
          normalized = normalized.replace(/\\./g, '').replace(',', '.');
        }} else {{
          normalized = normalized.replace(/,/g, '');
        }}
      }} else if (normalized.includes(',')) {{
        normalized = normalized.replace(',', '.');
      }}
      const parsed = Number(normalized);
      return Number.isFinite(parsed) ? parsed : fallback;
    }}
    function renderJson(targetId, payload) {{
      const target = document.getElementById(targetId);
      if (!target) return;
      target.className = 'mini';
      target.innerHTML = `<pre>${{escapeHtml(JSON.stringify(payload, null, 2))}}</pre>`;
    }}
    function renderMessage(targetId, text, tone='muted') {{
      const target = document.getElementById(targetId);
      if (!target) return;
      target.className = `mini ${{tone}}`;
      target.textContent = text;
    }}
    function renderFatalError(error) {{
      const status = Number(error?.status || 0);
      const detail = String(error?.detail || error?.message || error || 'Falha inesperada.');
      if (status === 401) {{
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        root.innerHTML = `
          <div class="card">
            <h2>Sessao expirada</h2>
            <div class="mini amber">Sua autenticacao expirou enquanto esta area carregava os dados. Isso nao afetou os calculos nem os historicos.</div>
            <div class="actions" style="margin-top:16px">
              <a class="btn primary" href="/login?next=${{next}}">Entrar novamente</a>
              <a class="btn" href="/dashboard">Voltar ao dashboard</a>
            </div>
            <pre style="margin-top:16px">${{escapeHtml(detail)}}</pre>
          </div>`;
        return;
      }}
      if (status === 403) {{
        root.innerHTML = `
          <div class="card">
            <h2>Acesso restrito</h2>
            <div class="mini amber">Este lab exige perfil administrador. O restante do ApexGol AI continua funcionando normalmente.</div>
            <div class="actions" style="margin-top:16px">
              <a class="btn" href="/dashboard">Voltar ao dashboard</a>
              <a class="btn" href="/app">Area do cliente</a>
            </div>
            <pre style="margin-top:16px">${{escapeHtml(detail)}}</pre>
          </div>`;
        return;
      }}
      root.innerHTML = `<div class="card"><h2>Falha na leitura</h2><pre>${{escapeHtml(detail)}}</pre></div>`;
    }}
    async function withBusy(button, busyLabel, handler) {{
      const originalLabel = button?.textContent || '';
      if (button) {{
        button.disabled = true;
        button.textContent = busyLabel;
      }}
      try {{
        await handler();
      }} finally {{
        if (button) {{
          button.disabled = false;
          button.textContent = originalLabel;
        }}
      }}
    }}
    function historicalSourceSummary(health) {{
      const counts = health?.counts || health?.supabase?.local_snapshot?.counts || {{}};
      const supabase = health?.supabase || {{}};
      const localMatches = Number(counts.historical_matches || 0);
      const localFeatures = Number(counts.historical_features || 0);
      const imported = supabase?.last_hydrate_result || {{}};
      const schemaMode = String(supabase?.schema_mode || '');
      if (supabase.enabled && schemaMode === 'historical' && !supabase.last_error) {{
        return {{
          tone: 'green',
          title: 'Supabase historico ativo',
          detail: `Cache local com ${{localMatches}} jogos e ${{localFeatures}} features. Ultima hidratacao importou ${{Number(imported.imported_matches || 0)}} jogos.`,
        }};
      }}
      if (supabase.enabled && schemaMode === 'legacy_betsignal' && !supabase.last_error) {{
        return {{
          tone: 'amber',
          title: 'Supabase legado compativel',
          detail: `O projeto remoto ainda usa betsignal_* e a ponte do app converte isso para historico util. Ultima hidratacao aproveitou ${{Number(imported.imported_matches || 0)}} jogos e ${{Number(imported.imported_features || 0)}} features.`,
        }};
      }}
      if (supabase.enabled && supabase.last_error) {{
        return {{
          tone: 'amber',
          title: 'Usando cache local',
          detail: `Supabase configurado, mas o refresh remoto falhou neste ciclo. Seguimos com ${{localMatches}} jogos e ${{localFeatures}} features locais.`,
        }};
      }}
      return {{
        tone: 'muted',
        title: 'Sem Supabase remoto',
        detail: `Os agentes estao operando so com o historico local de ${{localMatches}} jogos e ${{localFeatures}} features.`,
      }};
    }}
    function historicalContextCard(title, health) {{
      const counts = health?.counts || health?.supabase?.local_snapshot?.counts || {{}};
      const supabase = health?.supabase || {{}};
      const summary = historicalSourceSummary(health || {{}});
      const lastHydrate = supabase?.last_hydrate_at ? String(supabase.last_hydrate_at).slice(0, 16).replace('T', ' ') : 'ainda nao executada';
      const detail = supabase?.last_error ? escapeHtml(String(supabase.last_error)) : 'Sem erro recente no sincronismo.';
      const activeTables = Object.entries(supabase?.available_tables || {{}}).filter(([, value]) => Boolean(value)).map(([key]) => key);
      const schemaMode = String(supabase?.schema_mode || 'desconhecido');
      return card(title, `
        <div class="kpis">
          <div class="mini"><div class="muted">Historico local</div><strong>${{Number(counts.historical_matches || 0)}}</strong></div>
          <div class="mini"><div class="muted">Features locais</div><strong>${{Number(counts.historical_features || 0)}}</strong></div>
          <div class="mini"><div class="muted">Ligas confiaveis</div><strong>${{Number(counts.league_reliability_scores || 0)}}</strong></div>
          <div class="mini"><div class="muted">Supabase remoto</div><strong>${{supabase?.enabled ? 'ligado' : 'desligado'}}</strong></div>
          <div class="mini"><div class="muted">Modo remoto</div><strong>${{escapeHtml(schemaMode)}}</strong></div>
        </div>
        <div class="mini ${{summary.tone}}" style="margin-top:12px">
          <strong style="font-size:18px">${{summary.title}}</strong>
          <div class="muted" style="margin-top:8px">${{summary.detail}}</div>
          <div class="muted" style="margin-top:8px">Ultima hidratacao: ${{lastHydrate}}</div>
          <div class="muted" style="margin-top:8px">Tabelas remotas visiveis: ${{activeTables.length ? escapeHtml(activeTables.join(', ')) : 'nenhuma'}}</div>
          <div class="muted" style="margin-top:8px">Diagnostico: ${{detail}}</div>
        </div>
      `);
    }}
    function formatDateTime(value) {{
      if (!value) return '-';
      const iso = String(value);
      return iso.slice(0, 16).replace('T', ' ');
    }}
    function compactNumber(value) {{
      if (value == null || value === '') return '-';
      return new Intl.NumberFormat('pt-BR').format(Number(value));
    }}
    function compactPercent(value, digits = 1) {{
      if (value == null || value === '') return '-';
      return `${{Number(value).toFixed(digits)}}%`;
    }}
    function rawDetails(payload, summary='Ver payload bruto') {{
      return `<details style="margin-top:12px"><summary>${{summary}}</summary><pre style="margin-top:10px">${{escapeHtml(JSON.stringify(payload, null, 2))}}</pre></details>`;
    }}
    function setResultHtml(targetId, html) {{
      const target = document.getElementById(targetId);
      if (!target) return;
      target.className = 'mini';
      target.innerHTML = html;
    }}
    function renderMonteCarloSummary(payload) {{
      return `
        <div class="kpis">
          <div class="mini"><div class="muted">Risco de ruina</div><strong>${{compactPercent((payload.ruin_risk || 0) * 100, 2)}}</strong></div>
          <div class="mini"><div class="muted">Banca mediana</div><strong>${{money(payload.median_final_bankroll)}}</strong></div>
          <div class="mini"><div class="muted">Faixa P10 / P90</div><strong>${{money(payload.p10_final_bankroll)}} / ${{money(payload.p90_final_bankroll)}}</strong></div>
          <div class="mini"><div class="muted">Paths / passos</div><strong>${{compactNumber(payload.paths)}} / ${{compactNumber(payload.steps)}}</strong></div>
        </div>
        <div class="mini" style="margin-top:12px">
          <strong style="font-size:18px">Simulacao concluida</strong>
          <div class="muted" style="margin-top:8px">Monte Carlo rodado sobre hit rate e odd media informados manualmente. Nada desta tela automatiza aposta real.</div>
        </div>
        ${{rawDetails(payload, 'Ver simulacao detalhada')}}
      `;
    }}
    function renderFeatureLabSummary(payload) {{
      const features = payload.features || [];
      const snapshot = payload.snapshot || {{}};
      const counts = snapshot.counts || {{}};
      const preview = features.slice(0, 6).map(item => `
        <tr>
          <td>${{escapeHtml(item.scope || '-')}}</td>
          <td>${{escapeHtml(item.feature_name || '-')}}</td>
          <td>${{formatDateTime(item.created_at)}}</td>
        </tr>
      `).join('');
      return `
        <div class="kpis">
          <div class="mini"><div class="muted">Features salvas</div><strong>${{compactNumber(features.length)}}</strong></div>
          <div class="mini"><div class="muted">Catalogo total</div><strong>${{compactNumber(counts.generated_features || 0)}}</strong></div>
          <div class="mini"><div class="muted">Memoria longa</div><strong>${{compactNumber(counts.long_term_memory || 0)}}</strong></div>
          <div class="mini"><div class="muted">Decisoes consenso</div><strong>${{compactNumber(counts.consensus_decisions || 0)}}</strong></div>
        </div>
        <table style="margin-top:12px">
          <thead><tr><th>Escopo</th><th>Feature</th><th>Criada em</th></tr></thead>
          <tbody>${{preview || '<tr><td colspan=\"3\" class=\"muted\">Sem features registradas ainda.</td></tr>'}}</tbody>
        </table>
        ${{rawDetails(payload, 'Ver inventario bruto')}}
      `;
    }}
    function renderBiasSummary(payload) {{
      const patterns = payload.pattern_insights || [];
      const drifts = payload.drift_events || [];
      const preview = patterns.slice(0, 6).map(item => {{
        const raw = JSON.parse(item.payload_json || '{{}}');
        return `
          <tr>
            <td>${{escapeHtml(item.label || '-')}}</td>
            <td>${{raw.expected_value == null ? '-' : Number(raw.expected_value).toFixed(3)}}</td>
            <td>${{compactPercent(raw.confidence_score, 1)}}</td>
            <td>${{formatDateTime(item.created_at)}}</td>
          </tr>
        `;
      }}).join('');
      return `
        <div class="kpis">
          <div class="mini"><div class="muted">Padroes detectados</div><strong>${{compactNumber(patterns.length)}}</strong></div>
          <div class="mini"><div class="muted">Drifts ligados</div><strong>${{compactNumber(drifts.length)}}</strong></div>
          <div class="mini"><div class="muted">Ultimo padrao</div><strong>${{formatDateTime(patterns[0]?.created_at)}}</strong></div>
        </div>
        <table style="margin-top:12px">
          <thead><tr><th>Label</th><th>EV</th><th>Confianca</th><th>Criado em</th></tr></thead>
          <tbody>${{preview || '<tr><td colspan=\"4\" class=\"muted\">Sem anomalias salvas ainda.</td></tr>'}}</tbody>
        </table>
        ${{rawDetails(payload, 'Ver vieses e anomalias em JSON')}}
      `;
    }}
    function renderDriftSummary(payload) {{
      const drifts = payload.drift_events || [];
      const risks = payload.risk_events || [];
      const exposures = payload.exposure || [];
      const preview = drifts.slice(0, 6).map(item => {{
        const raw = JSON.parse(item.payload_json || '{{}}');
        return `
          <tr>
            <td>${{escapeHtml(item.scope || '-')}}</td>
            <td>${{escapeHtml(item.severity || '-')}}</td>
            <td>${{raw.roi_gap == null ? '-' : Number(raw.roi_gap).toFixed(3)}}</td>
            <td>${{formatDateTime(item.created_at)}}</td>
          </tr>
        `;
      }}).join('');
      return `
        <div class="kpis">
          <div class="mini"><div class="muted">Eventos de drift</div><strong>${{compactNumber(drifts.length)}}</strong></div>
          <div class="mini"><div class="muted">Eventos de risco</div><strong>${{compactNumber(risks.length)}}</strong></div>
          <div class="mini"><div class="muted">Exposicoes salvas</div><strong>${{compactNumber(exposures.length)}}</strong></div>
        </div>
        <table style="margin-top:12px">
          <thead><tr><th>Escopo</th><th>Severidade</th><th>Gap ROI</th><th>Criado em</th></tr></thead>
          <tbody>${{preview || '<tr><td colspan=\"4\" class=\"muted\">Sem drift salvo ainda.</td></tr>'}}</tbody>
        </table>
        ${{rawDetails(payload, 'Ver monitor bruto')}}
      `;
    }}
    function renderAgentArenaSummary(payload) {{
      const outputs = payload.agent_outputs || [];
      const trust = payload.trust_scores || [];
      const answer = payload.research_agent_answer || {{}};
      const preview = outputs.slice(0, 6).map(item => `
        <tr>
          <td>${{escapeHtml(item.label || item.decision || '-')}}</td>
          <td>${{escapeHtml(item.status || '-')}}</td>
          <td>${{formatDateTime(item.created_at)}}</td>
        </tr>
      `).join('');
      return `
        <div class="kpis">
          <div class="mini"><div class="muted">Respostas dos agentes</div><strong>${{compactNumber(outputs.length)}}</strong></div>
          <div class="mini"><div class="muted">Trust scores</div><strong>${{compactNumber(trust.length)}}</strong></div>
          <div class="mini"><div class="muted">Pergunta atual</div><strong>${{escapeHtml(answer.prompt || '-')}}</strong></div>
        </div>
        <div class="mini" style="margin-top:12px">
          <strong style="font-size:18px">${{escapeHtml(answer.answer || 'Sem resposta estruturada ainda.')}}</strong>
          <div class="muted" style="margin-top:8px">${{escapeHtml(answer.context_note || 'Os agentes cruzam memoria local, consenso e histórico antes de responder.')}}</div>
        </div>
        <table style="margin-top:12px">
          <thead><tr><th>Saida</th><th>Status</th><th>Criado em</th></tr></thead>
          <tbody>${{preview || '<tr><td colspan=\"3\" class=\"muted\">Sem saidas anteriores salvas.</td></tr>'}}</tbody>
        </table>
        ${{rawDetails(payload, 'Ver resposta e trilhas em JSON')}}
      `;
    }}
    function renderLiveSourceMatrix(sources) {{
      const rows = Array.isArray(sources) ? sources : [];
      const cards = rows.map(item => {{
        const markets = Array.isArray(item.markets) && item.markets.length
          ? item.markets.map(market => `<span class="pill">${{escapeHtml(market)}}</span>`).join(' ')
          : `<span class="pill">Sem detalhe</span>`;
        const tone = item.status === 'ready'
          ? 'green'
          : item.status === 'missing_key'
            ? 'amber'
            : 'red';
        const statusLabel = item.status === 'ready'
          ? 'pronto'
          : item.status === 'missing_key'
            ? 'chave ausente'
            : (item.status || 'indefinido');
        return `
          <div class="mini">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
              <div>
                <strong style="font-size:18px">${{escapeHtml(item.label || '-')}}</strong>
                <div class="muted" style="margin-top:6px">${{escapeHtml(item.role || '-')}}</div>
              </div>
              <span class="pill ${{tone}}">${{escapeHtml(statusLabel)}}</span>
            </div>
            <div class="muted" style="margin-top:10px">Base URL: ${{escapeHtml(item.base_url || '-')}}</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">${{markets}}</div>
            <div class="muted" style="margin-top:10px">${{escapeHtml(item.coverage_note || 'Sem nota operacional.')}}</div>
          </div>
        `;
      }}).join('');
      return card('Fontes ao vivo e odds reais', cards || `<div class="mini muted">Nenhuma fonte catalogada ainda.</div>`);
    }}
    async function loadControlCenter() {{
      const data = await api('/api/global-ai/control-center');
      root.innerHTML = `
        <div class="grid">
          <div class="span-12">${{card('Global AI Control Center', `
            <div class="kpis">
              <div class="mini"><div class="muted">Fontes</div><strong>${{data.sources.length}}</strong></div>
              <div class="mini"><div class="muted">Features geradas</div><strong>${{data.generated_features.length}}</strong></div>
              <div class="mini"><div class="muted">Pendências de governança</div><strong>${{(data.governance.pending || []).length}}</strong></div>
              <div class="mini"><div class="muted">Drifts recentes</div><strong>${{(data.drift_events || []).length}}</strong></div>
            </div>
          `)}}</div>
          <div class="span-6">${{historicalContextCard('Historico aplicado pelos agentes', data.research_health || {{}})}}</div>
          <div class="span-6">${{renderLiveSourceMatrix(data.live_sources || [])}}</div>
          <div class="span-6">${{card('Saúde e auditoria', `<pre>${{JSON.stringify({{ research_health:data.research_health, global_snapshot:data.global_snapshot }}, null, 2)}}</pre>`)}}</div>
          <div class="span-6">${{card('Avisos e aprendizado', `<pre>${{JSON.stringify(data.learning, null, 2)}}</pre>`)}}</div>
        </div>`;
    }}
    async function loadFootballAnalysis() {{
      const board = await api('/api/global-ai/football-analysis');
      root.innerHTML = `
        <div class="grid">
          <div class="span-12">${{historicalContextCard('Base historica usada nesta leitura', board.research_health || {{}})}}</div>
          <div class="span-12">${{renderLiveSourceMatrix(board.live_sources || [])}}</div>
          <div class="span-8">${{card('Football Analysis', `
            <div class="list">
              ${board.items.map(item => `
                <div class="mini">
                  <div class="muted">${item.match.league} • ${String(item.match.match_date).slice(0,16).replace('T',' ')}</div>
                  <strong>${item.match.home_team} x ${item.match.away_team}</strong>
                  <div class="muted" style="margin-top:8px">Recomendação ${item.prediction.recommendation} • EV ${item.prediction.expected_value ?? '-'} • confiança ${item.prediction.confidence_score}</div>
                </div>
              `).join('') || `<div class="mini muted">Sem jogos arquivados ainda.</div>`}
            </div>
          `)}}</div>
          <div class="span-4">${{card('Consulta pontual', `
            <div class="row">
              <div><h3>Event ID</h3><input id="fa-event" type="number" placeholder="1"></div>
              <div><h3>Mercado</h3><select id="fa-market"><option value="match_winner_home">Casa vence</option><option value="over_2_5">Over 2.5</option><option value="btts_yes">BTTS</option></select></div>
            </div>
            <div class="actions" style="margin-top:12px"><button id="fa-run" type="button" class="good" onclick="runFootballEvent(this); return false;">Analisar evento</button></div>
            <div id="fa-result" class="mini muted" style="margin-top:12px">Escolha um evento histórico e analise.</div>
          `)}}</div>
        </div>`;
    }}
    async function runFootballEvent(button) {{
      await withBusy(button, 'Consultando...', async () => {{
        renderMessage('fa-result', 'Consultando evento histórico...');
        try {{
          const payload = {{
            event_id: parseInt(document.getElementById('fa-event').value || '0', 10),
            market: document.getElementById('fa-market').value
          }};
          const data = await api('/api/global-ai/football-analysis/event', {{ method:'POST', body: JSON.stringify(payload) }});
          renderJson('fa-result', data);
        }} catch (error) {{
          renderMessage('fa-result', String(error.message || error), 'error');
          throw error;
        }}
      }});
    }}
    async function loadBacktesting() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${{card('Rodar backtest', `
            <div class="row"><div><h3>Liga</h3><input id="bt-league" placeholder="Ex: Brasil - Serie A"></div><div><h3>Mercado</h3><select id="bt-market"><option value="match_winner_home">Casa vence</option><option value="over_2_5">Over 2.5</option><option value="btts_yes">BTTS</option></select></div></div>
            <div class="row"><div><h3>EV mínimo</h3><input id="bt-ev" type="number" step="0.01" value="0.03"></div><div><h3>Confiança mínima</h3><input id="bt-confidence" type="number" step="1" value="60"></div></div>
            <div class="actions" style="margin-top:12px"><button id="bt-run" type="button" class="good" onclick="runBacktest(this); return false;">Rodar backtest</button></div>
          `)}}</div>
          <div class="span-8">${{card('Resultado', `<div id="backtest-result" class="mini muted">Ainda sem backtest nesta tela.</div>`)}}</div>
        </div>`;
    }}
    async function runBacktest(button) {{
      await withBusy(button, 'Rodando...', async () => {{
        renderMessage('backtest-result', 'Rodando backtest...');
        try {{
          const payload = {{
            league: document.getElementById('bt-league').value || null,
            market: document.getElementById('bt-market').value,
            ev_min: parseLocaleNumber(document.getElementById('bt-ev').value, 0.03),
            confidence_min: parseLocaleNumber(document.getElementById('bt-confidence').value, 60),
          }};
          const data = await api('/api/global-ai/backtest', {{ method:'POST', body: JSON.stringify(payload) }});
          renderJson('backtest-result', data);
        }} catch (error) {{
          renderMessage('backtest-result', String(error.message || error), 'error');
          throw error;
        }}
      }});
    }}
    async function loadMonteCarlo() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${{card('Simular banca', `
            <div class="row"><div><h3>Hit rate</h3><input id="mc-hit" type="number" step="0.01" value="0.55"></div><div><h3>Odd média</h3><input id="mc-odd" type="number" step="0.01" value="1.90"></div></div>
            <div class="row"><div><h3>Banca</h3><input id="mc-bank" type="number" step="10" value="1000"></div><div><h3>Stake %</h3><input id="mc-stake" type="number" step="0.001" value="0.015"></div></div>
            <div class="actions" style="margin-top:12px"><button id="mc-run" type="button" class="good" onclick="runMonteCarlo(this); return false;">Rodar Monte Carlo</button></div>
          `)}}</div>
          <div class="span-8">${{card('Resultado Monte Carlo', `<div id="mc-result" class="mini muted">Ainda não rodamos nenhuma simulação.</div>`)}}</div>
        </div>`;
    }}
    async function runMonteCarlo(button) {{
      await withBusy(button, 'Rodando...', async () => {{
        renderMessage('mc-result', 'Rodando Monte Carlo...');
        try {{
          const payload = {{
            hit_rate: parseLocaleNumber(document.getElementById('mc-hit').value, 0.55),
            average_odd: parseLocaleNumber(document.getElementById('mc-odd').value, 1.9),
            bankroll: parseLocaleNumber(document.getElementById('mc-bank').value, 1000),
            stake_pct: parseLocaleNumber(document.getElementById('mc-stake').value, 0.015),
          }};
          const data = await api('/api/global-ai/monte-carlo', {{ method:'POST', body: JSON.stringify(payload) }});
          setResultHtml('mc-result', renderMonteCarloSummary(data));
        }} catch (error) {{
          renderMessage('mc-result', String(error.message || error), 'error');
          throw error;
        }}
      }});
    }}
    async function loadEvolution() {{
      root.innerHTML = `<div class="grid"><div class="span-4">${{card('Strategy Evolution Lab', `<div class="actions"><button id="evo-run" type="button" class="good" onclick="runEvolution(this); return false;">Gerar nova evolução</button></div><div id="evo-result" class="mini muted" style="margin-top:12px">Aguardando evolução.</div>`)}}</div></div>`;
    }}
    async function runEvolution(button) {{
      await withBusy(button, 'Gerando...', async () => {{
        renderMessage('evo-result', 'Gerando evolução...');
        try {{
          const data = await api('/api/global-ai/strategy-evolution', {{ method:'POST' }});
          renderJson('evo-result', data);
        }} catch (error) {{
          renderMessage('evo-result', String(error.message || error), 'error');
          throw error;
        }}
      }});
    }}
    async function loadAgentArena() {{
      const control = await api('/api/global-ai/control-center');
      root.innerHTML = `<div class="grid"><div class="span-12">${{historicalContextCard('Memoria e historico usados pelos agentes', control.research_health || {{}})}}</div><div class="span-4">${{card('Agent Arena', `<textarea id="agent-prompt" rows="6" placeholder="Ex: qual liga teve melhor ROI?"></textarea><div class="actions" style="margin-top:12px"><button id="agent-run" type="button" class="primary" onclick="askAgent(this); return false;">Perguntar</button></div>`)}}</div><div class="span-8">${{card('Resposta', `<div id="agent-result" class="mini muted">Sem resposta ainda.</div>`)}}</div></div>`;
    }}
    async function askAgent(button) {{
      await withBusy(button, 'Consultando...', async () => {{
        renderMessage('agent-result', 'Consultando agentes...');
        try {{
          const data = await api('/api/global-ai/agent-arena', {{ method:'POST', body: JSON.stringify({{ prompt: document.getElementById('agent-prompt').value || 'qual mercado performou melhor?' }}) }});
          setResultHtml('agent-result', renderAgentArenaSummary(data));
        }} catch (error) {{
          renderMessage('agent-result', String(error.message || error), 'error');
          throw error;
        }}
      }});
    }}
    async function loadFeatureLab() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-12"><div id="feature-history"></div></div>
          <div class="span-4">${card('Feature Lab', `<div class="mini muted">Inventário das features geradas, prontas para revisão quantitativa.</div><div class="actions" style="margin-top:12px"><button id="feature-refresh" type="button" class="primary" onclick="loadFeatureLab(); return false;">Atualizar leitura</button></div>`)}
          </div>
          <div class="span-8">${card('Snapshot das features', `<div id="feature-result" class="mini muted">Carregando snapshot...</div>`)}
          </div>
        </div>`;
      try {{
        const data = await api('/api/global-ai/feature-lab');
        document.getElementById('feature-history').outerHTML = historicalContextCard('Historico e cache aplicados no lab', data.research_health || {{}});
        setResultHtml('feature-result', renderFeatureLabSummary(data));
      }} catch (error) {{
        renderMessage('feature-result', String(error.message || error), 'error');
        throw error;
      }}
    }}
    async function loadDriftRegime() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-12"><div id="drift-history"></div></div>
          <div class="span-4">${card('Drift & Regime Monitor', `<div class="mini muted">Acompanha degradação de ROI, drawdown e alterações de regime sem aplicar nada automaticamente.</div><div class="actions" style="margin-top:12px"><button id="drift-refresh" type="button" class="primary" onclick="loadDriftRegime(); return false;">Atualizar monitor</button></div>`)}
          </div>
          <div class="span-8">${card('Eventos e exposição', `<div id="drift-result" class="mini muted">Carregando monitor...</div>`)}
          </div>
        </div>`;
      try {{
        const data = await api('/api/global-ai/drift-regime');
        document.getElementById('drift-history').outerHTML = historicalContextCard('Historico comparado no monitor de drift', data.research_health || {{}});
        setResultHtml('drift-result', renderDriftSummary(data));
      }} catch (error) {{
        renderMessage('drift-result', String(error.message || error), 'error');
        throw error;
      }}
    }}
    async function loadBiasAnomaly() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-12"><div id="bias-history"></div></div>
          <div class="span-12"><div id="bias-sources"></div></div>
          <div class="span-4">${card('Market Bias & Anomaly Center', `<div class="mini muted">Reúne vieses detectados, padrões salvos e anomalias de performance para revisão humana.</div><div class="actions" style="margin-top:12px"><button id="bias-refresh" type="button" class="primary" onclick="loadBiasAnomaly(); return false;">Atualizar leitura</button></div>`)}
          </div>
          <div class="span-8">${card('Padrões e anomalias', `<div id="bias-result" class="mini muted">Carregando leitura...</div>`)}
          </div>
        </div>`;
      try {{
        const data = await api('/api/global-ai/bias-anomaly');
        document.getElementById('bias-history').outerHTML = historicalContextCard('Historico usado para detectar viés e anomalia', data.research_health || {{}});
        document.getElementById('bias-sources').outerHTML = renderLiveSourceMatrix(data.live_sources || []);
        setResultHtml('bias-result', renderBiasSummary(data));
      }} catch (error) {{
        renderMessage('bias-result', String(error.message || error), 'error');
        throw error;
      }}
    }}
    async function loadRagMemory() {{
      root.innerHTML = `<div class="grid"><div class="span-4">${{card('RAG Memory Explorer', `<textarea id="rag-q" rows="6" placeholder="Ex: o que funcionou em situações parecidas?"></textarea><div class="actions" style="margin-top:12px"><button id="rag-run" type="button" class="primary" onclick="askRag(this); return false;">Consultar</button></div>`)}}</div><div class="span-8">${{card('Contexto', `<div id="rag-result" class="mini muted">Sem consulta ainda.</div>`)}}</div></div>`;
    }}
    async function askRag(button) {{
      await withBusy(button, 'Consultando...', async () => {{
        renderMessage('rag-result', 'Consultando memória...');
        try {{
          const data = await api('/api/global-ai/rag-query', {{ method:'POST', body: JSON.stringify({{ question: document.getElementById('rag-q').value || 'qual mercado teve melhor ROI?' }}) }});
          renderJson('rag-result', data);
        }} catch (error) {{
          renderMessage('rag-result', String(error.message || error), 'error');
          throw error;
        }}
      }});
    }}
    async function loadGovernance() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${card('Governance Center', `<div class="mini muted">Nada entra em produção sem trilha, aprovação e histórico de mudanças.</div><div class="actions" style="margin-top:12px"><button id="gov-refresh" type="button" class="primary" onclick="loadGovernance(); return false;">Atualizar governança</button></div>`)}
          </div>
          <div class="span-8">${card('Fila de governança', `<div id="gov-result" class="mini muted">Carregando governança...</div>`)}
          </div>
        </div>`;
      try {{
        const data = await api('/api/global-ai/governance');
        renderJson('gov-result', data);
      }} catch (error) {{
        renderMessage('gov-result', String(error.message || error), 'error');
        throw error;
      }}
    }}
    const loaders = {{
      'global-ai-control-center': loadControlCenter,
      'football-analysis': loadFootballAnalysis,
      'backtesting-lab': loadBacktesting,
      'monte-carlo-lab': loadMonteCarlo,
      'strategy-evolution-lab': loadEvolution,
      'agent-arena': loadAgentArena,
      'feature-lab': loadFeatureLab,
      'drift-regime-monitor': loadDriftRegime,
      'market-bias-anomaly-center': loadBiasAnomaly,
      'rag-memory-explorer': loadRagMemory,
      'governance-center': loadGovernance,
    }};
    (loaders[mode] || loadControlCenter)().catch(error => {{
      renderFatalError(error);
    }});
  </script>
</body>
</html>"""
    return (
        template
        .replace("__TITLE__", _esc(title))
        .replace("__NOTICE__", _esc(NOTICE))
        .replace("__NAV__", nav)
        .replace("__MODE__", _esc(mode))
        .replace("{{", "{")
        .replace("}}", "}")
    )


@router.get("/app/global-ai-control-center", response_class=HTMLResponse)
async def global_control_center_page(user: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Global AI Control Center", "global-ai-control-center")


@router.get("/app/football-analysis", response_class=HTMLResponse)
async def football_analysis_page(user: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Football Analysis", "football-analysis")


@router.get("/app/backtesting-lab", response_class=HTMLResponse)
async def backtesting_lab_page(user: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Backtesting Lab", "backtesting-lab")


@router.get("/app/monte-carlo-lab", response_class=HTMLResponse)
async def monte_carlo_lab_page(user: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Monte Carlo Lab", "monte-carlo-lab")


@router.get("/app/strategy-evolution-lab", response_class=HTMLResponse)
async def strategy_evolution_lab_page(user: dict[str, Any] = Depends(_require_admin)) -> str:
    return _shell("Strategy Evolution Lab", "strategy-evolution-lab")


@router.get("/app/agent-arena", response_class=HTMLResponse)
async def agent_arena_page(user: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Agent Arena", "agent-arena")


@router.get("/app/feature-lab", response_class=HTMLResponse)
async def feature_lab_page(user: dict[str, Any] = Depends(_require_admin)) -> str:
    return _shell("Feature Lab", "feature-lab")


@router.get("/app/drift-regime-monitor", response_class=HTMLResponse)
async def drift_regime_page(user: dict[str, Any] = Depends(_require_admin)) -> str:
    return _shell("Drift & Regime Monitor", "drift-regime-monitor")


@router.get("/app/market-bias-anomaly-center", response_class=HTMLResponse)
async def market_bias_anomaly_page(user: dict[str, Any] = Depends(_require_admin)) -> str:
    return _shell("Market Bias & Anomaly Center", "market-bias-anomaly-center")


@router.get("/app/rag-memory-explorer", response_class=HTMLResponse)
async def rag_memory_page(user: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("RAG Memory Explorer", "rag-memory-explorer")


@router.get("/app/governance-center", response_class=HTMLResponse)
async def governance_center_page(user: dict[str, Any] = Depends(_require_admin)) -> str:
    return _shell("Governance Center", "governance-center")


@router.get("/api/global-ai/health")
async def global_ai_health(
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao montar a saúde global da plataforma.",
                error=dep_error,
            )
        )
    try:
        return _json({"ok": True, **platform.control_center_snapshot(user_id=user.get("id"))})
    except Exception as exc:
        logger.exception("global ai health failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao montar a saúde global da plataforma.",
                error=exc,
                research_health=_safe_platform_health(platform),
                live_sources=_safe_live_sources(platform),
            )
        )


@router.get("/api/global-ai/audit")
async def global_ai_audit(
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao montar a auditoria global.",
                error=dep_error,
                extra={"existing_stack": {}, "reused_modules": [], "created_modules": [], "risks": [], "discovery": {}, "global_snapshot": {}},
            )
        )
    try:
        return _json(platform.audit_report())
    except Exception as exc:
        logger.exception("global ai audit failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao montar a auditoria global.",
                error=exc,
                research_health=_safe_platform_health(platform),
                live_sources=_safe_live_sources(platform),
                extra={"existing_stack": {}, "reused_modules": [], "created_modules": [], "risks": [], "discovery": {}, "global_snapshot": {}},
            )
        )


@router.get("/api/global-ai/control-center")
async def global_ai_control_center(
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Global AI Control Center. A interface segue disponível com dados parciais.",
                error=dep_error,
                extra={
                    "generated_at": "",
                    "sources": [],
                    "global_snapshot": {},
                    "generated_features": [],
                    "learning": {},
                    "governance": {"pending": [], "approved": [], "rejected": [], "applied": []},
                    "drift_events": [],
                    "risk_events": [],
                },
            )
        )
    try:
        return _json(platform.control_center_snapshot(user_id=user.get("id")))
    except Exception as exc:
        logger.exception("global ai control center failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Global AI Control Center. A interface segue disponível com dados parciais.",
                error=exc,
                research_health=_safe_platform_health(platform),
                live_sources=_safe_live_sources(platform),
                extra={
                    "generated_at": "",
                    "sources": [],
                    "global_snapshot": {},
                    "generated_features": [],
                    "learning": {},
                    "governance": {"pending": [], "approved": [], "rejected": [], "applied": []},
                    "drift_events": [],
                    "risk_events": [],
                },
            )
        )


@router.get("/api/global-ai/football-analysis")
async def global_ai_football_analysis(
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Football Analysis. Seguimos mostrando a base histórica e as fontes configuradas.",
                error=dep_error,
                extra={"market": "match_winner_home", "items": []},
            )
        )
    try:
        return _json(platform.football_analysis_board(user_id=user.get("id")))
    except Exception as exc:
        logger.exception("global ai football analysis failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Football Analysis. Seguimos mostrando a base histórica e as fontes configuradas.",
                error=exc,
                research_health=_safe_platform_health(platform),
                live_sources=_safe_live_sources(platform),
                extra={"market": "match_winner_home", "items": []},
            )
        )


@router.post("/api/global-ai/football-analysis/event")
async def global_ai_football_analysis_event(
    payload: FootballAnalysisPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível analisar esse evento agora.",
                error=dep_error,
                extra={"prediction": {}, "ensemble": {}, "meta": {}, "drift": {}, "regime": {}, "anomaly": {}, "risk": {}, "agent_outputs": [], "consensus": {}, "explanation": {}},
            )
        )
    try:
        return _json(
            platform.analyze_football_event(
                payload.event_id,
                market=payload.market,
                offered_odd=payload.offered_odd,
                user_id=user.get("id"),
            )
        )
    except Exception as exc:
        logger.exception("global ai football event analysis failed")
        return _json(
            _degraded_payload(
                "Nao foi possível analisar esse evento agora.",
                error=exc,
                extra={"prediction": {}, "ensemble": {}, "meta": {}, "drift": {}, "regime": {}, "anomaly": {}, "risk": {}, "agent_outputs": [], "consensus": {}, "explanation": {}},
            )
        )


@router.post("/api/global-ai/backtest")
async def global_ai_backtest(
    payload: BacktestPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível rodar o backtest agora.",
                error=dep_error,
                extra={},
            )
        )
    try:
        return _json(platform.run_backtest({**payload.model_dump(), "user_id": user.get("id")}))
    except Exception as exc:
        logger.exception("global ai backtest failed")
        return _json(
            _degraded_payload(
                "Nao foi possível rodar o backtest agora.",
                error=exc,
                extra={},
            )
        )


@router.post("/api/global-ai/monte-carlo")
async def global_ai_monte_carlo(
    payload: MonteCarloPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível rodar o Monte Carlo agora.",
                error=dep_error,
                extra={},
            )
        )
    try:
        return _json(
            {
                **platform.run_monte_carlo(**payload.model_dump(), user_id=user.get("id")),
                "generated_at": platform.control_center_snapshot(user_id=user.get("id")).get("generated_at"),
            }
        )
    except Exception as exc:
        logger.exception("global ai monte carlo failed")
        return _json(
            _degraded_payload(
                "Nao foi possível rodar o Monte Carlo agora.",
                error=exc,
                extra={},
            )
        )


@router.post("/api/global-ai/strategy-evolution")
async def global_ai_strategy_evolution(
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível gerar a evolução de estratégia agora.",
                error=dep_error,
                extra={},
            )
        )
    try:
        return _json(platform.evolve_strategy(user_id=user.get("id")))
    except Exception as exc:
        logger.exception("global ai strategy evolution failed")
        return _json(
            _degraded_payload(
                "Nao foi possível gerar a evolução de estratégia agora.",
                error=exc,
                extra={},
            )
        )


@router.post("/api/global-ai/agent-arena")
async def global_ai_agent_arena(
    payload: AgentPromptPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível consultar os agentes agora.",
                error=dep_error,
                extra={"agent_outputs": [], "trust_scores": [], "research_agent_answer": {}},
            )
        )
    try:
        return _json(
            {
                **platform.agent_arena(prompt=payload.prompt, user_id=user.get("id")),
                "research_health": platform.research_health_snapshot(),
            }
        )
    except Exception as exc:
        logger.exception("global ai agent arena failed")
        return _json(
            _degraded_payload(
                "Nao foi possível consultar os agentes agora.",
                error=exc,
                research_health=_safe_platform_health(platform),
                extra={"agent_outputs": [], "trust_scores": [], "research_agent_answer": {}},
            )
        )


@router.get("/api/global-ai/feature-lab")
async def global_ai_feature_lab(
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Feature Lab.",
                error=dep_error,
                extra={"features": [], "snapshot": {"counts": {}}},
            )
        )
    try:
        return _json(
            {
                "features": platform.repository.list_generated_features(limit=80),
                "snapshot": platform.repository.snapshot(),
                "research_health": platform.research_health_snapshot(),
            }
        )
    except Exception as exc:
        logger.exception("global ai feature lab failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Feature Lab.",
                error=exc,
                research_health=_safe_platform_health(platform),
                extra={"features": [], "snapshot": {"counts": {}}},
            )
        )


@router.get("/api/global-ai/drift-regime")
async def global_ai_drift_regime(
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o monitor de drift.",
                error=dep_error,
                extra={"drift_events": [], "risk_events": [], "exposure": []},
            )
        )
    try:
        return _json(
            {
                "drift_events": platform.repository.list_drift_events(limit=40),
                "risk_events": platform.repository.list_risk_events(limit=40),
                "exposure": platform.repository.list_exposure_snapshots(limit=20),
                "research_health": platform.research_health_snapshot(),
            }
        )
    except Exception as exc:
        logger.exception("global ai drift regime failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o monitor de drift.",
                error=exc,
                research_health=_safe_platform_health(platform),
                extra={"drift_events": [], "risk_events": [], "exposure": []},
            )
        )


@router.get("/api/global-ai/bias-anomaly")
async def global_ai_bias_anomaly(
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Bias & Anomaly Center.",
                error=dep_error,
                extra={"pattern_insights": [], "drift_events": []},
            )
        )
    try:
        return _json(
            {
                "pattern_insights": platform.repository.list_pattern_insights(limit=40),
                "drift_events": platform.repository.list_drift_events(limit=20),
                "live_sources": platform.live_source_runtime_snapshot(),
                "research_health": platform.research_health_snapshot(),
            }
        )
    except Exception as exc:
        logger.exception("global ai bias anomaly failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar o Bias & Anomaly Center.",
                error=exc,
                research_health=_safe_platform_health(platform),
                live_sources=_safe_live_sources(platform),
                extra={"pattern_insights": [], "drift_events": []},
            )
        )


@router.post("/api/global-ai/rag-query")
async def global_ai_rag_query(
    payload: RAGPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível consultar a memória RAG agora.",
                error=dep_error,
                extra={"answer": "", "chunks": [], "citations": []},
            )
        )
    try:
        return _json(platform.rag_explorer(payload.question))
    except Exception as exc:
        logger.exception("global ai rag query failed")
        return _json(
            _degraded_payload(
                "Nao foi possível consultar a memória RAG agora.",
                error=exc,
                extra={"answer": "", "chunks": [], "citations": []},
            )
        )


@router.get("/api/global-ai/governance")
async def global_ai_governance(
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar a governança.",
                error=dep_error,
                extra={"pending": [], "approved": [], "rejected": [], "applied": []},
            )
        )
    try:
        return _json({**platform.governance.snapshot(), "research_health": platform.research_health_snapshot()})
    except Exception as exc:
        logger.exception("global ai governance failed")
        return _json(
            _degraded_payload(
                "Falha temporária ao carregar a governança.",
                error=exc,
                research_health=_safe_platform_health(platform),
                extra={"pending": [], "approved": [], "rejected": [], "applied": []},
            )
        )


@router.post("/api/global-ai/governance/{request_id}")
async def global_ai_governance_decide(
    request_id: int,
    payload: GovernanceDecisionPayload,
    user: dict[str, Any] = Depends(_require_admin),
    platform_dep: Any = Depends(_global),
) -> JSONResponse:
    platform, dep_error = _unwrap_platform(platform_dep)
    if dep_error is not None:
        return _json(
            _degraded_payload(
                "Nao foi possível aplicar essa decisão de governança agora.",
                error=dep_error,
                extra={"request": {}},
            )
        )
    try:
        decided = platform.governance.decide(request_id, payload.decision, user_id=user.get("id"))
        return _json({"request": decided})
    except Exception as exc:
        logger.exception("global ai governance decision failed")
        return _json(
            _degraded_payload(
                "Nao foi possível aplicar essa decisão de governança agora.",
                error=exc,
                extra={"request": {}},
            )
        )
