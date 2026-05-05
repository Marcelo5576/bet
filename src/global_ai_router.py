from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from services.globalAdaptiveIntelligence import get_global_adaptive_intelligence
from src.portal_web import _require_admin, _require_user


router = APIRouter()
NOTICE = "Este sistema é uma ferramenta estatística de apoio. Não garante lucro. Use com responsabilidade."


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
    return get_global_adaptive_intelligence()


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


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
    .muted {{ color:var(--muted); }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    h3 {{ margin:0 0 8px; font-size:13px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }}
    .row {{ display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); }}
    input, select, textarea, button {{
      width:100%; border:1px solid var(--line); background:#0f151d; color:var(--text); padding:12px; border-radius:12px; font:inherit;
    }}
    button {{ cursor:pointer; font-weight:800; }}
    button.primary {{ background:linear-gradient(180deg,#1e6fff,#0d4ed9); border-color:#2563eb; }}
    button.good {{ background:linear-gradient(180deg,#169b61,#0d7b4d); border-color:#0f9a5e; }}
    button.warn {{ background:linear-gradient(180deg,#d9a31a,#b6850f); border-color:#d9a31a; color:#101317; }}
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
        throw new Error(detail);
      }}
      return response.json();
    }}
    function card(title, body) {{
      return `<section class="card">${{title ? `<h2>${{title}}</h2>` : ''}}${{body}}</section>`;
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
          <div class="span-6">${{card('Saúde e auditoria', `<pre>${{JSON.stringify({{ research_health:data.research_health, global_snapshot:data.global_snapshot }}, null, 2)}}</pre>`)}}</div>
          <div class="span-6">${{card('Avisos e aprendizado', `<pre>${{JSON.stringify(data.learning, null, 2)}}</pre>`)}}</div>
        </div>`;
    }}
    async function loadFootballAnalysis() {{
      const board = await api('/api/global-ai/football-analysis');
      root.innerHTML = `
        <div class="grid">
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
            <div class="actions" style="margin-top:12px"><button class="good" onclick="runFootballEvent()">Analisar evento</button></div>
            <div id="fa-result" class="mini muted" style="margin-top:12px">Escolha um evento histórico e analise.</div>
          `)}}</div>
        </div>`;
    }}
    async function runFootballEvent() {{
      const payload = {{ event_id: Number(document.getElementById('fa-event').value || 0), market: document.getElementById('fa-market').value }};
      const data = await api('/api/global-ai/football-analysis/event', {{ method:'POST', body: JSON.stringify(payload) }});
      document.getElementById('fa-result').outerHTML = `<pre id="fa-result">${{JSON.stringify(data, null, 2)}}</pre>`;
    }}
    async function loadBacktesting() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${{card('Rodar backtest', `
            <div class="row"><div><h3>Liga</h3><input id="bt-league" placeholder="Ex: Brasil - Serie A"></div><div><h3>Mercado</h3><select id="bt-market"><option value="match_winner_home">Casa vence</option><option value="over_2_5">Over 2.5</option><option value="btts_yes">BTTS</option></select></div></div>
            <div class="row"><div><h3>EV mínimo</h3><input id="bt-ev" type="number" step="0.01" value="0.03"></div><div><h3>Confiança mínima</h3><input id="bt-confidence" type="number" step="1" value="60"></div></div>
            <div class="actions" style="margin-top:12px"><button class="good" onclick="runBacktest()">Rodar backtest</button></div>
          `)}}</div>
          <div class="span-8">${{card('Resultado', `<div id="backtest-result" class="mini muted">Ainda sem backtest nesta tela.</div>`)}}</div>
        </div>`;
    }}
    async function runBacktest() {{
      const payload = {{
        league: document.getElementById('bt-league').value || null,
        market: document.getElementById('bt-market').value,
        ev_min: Number(document.getElementById('bt-ev').value || 0.03),
        confidence_min: Number(document.getElementById('bt-confidence').value || 60),
      }};
      const data = await api('/api/global-ai/backtest', {{ method:'POST', body: JSON.stringify(payload) }});
      document.getElementById('backtest-result').outerHTML = `<pre id="backtest-result">${{JSON.stringify(data, null, 2)}}</pre>`;
    }}
    async function loadMonteCarlo() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${{card('Simular banca', `
            <div class="row"><div><h3>Hit rate</h3><input id="mc-hit" type="number" step="0.01" value="0.55"></div><div><h3>Odd média</h3><input id="mc-odd" type="number" step="0.01" value="1.90"></div></div>
            <div class="row"><div><h3>Banca</h3><input id="mc-bank" type="number" step="10" value="1000"></div><div><h3>Stake %</h3><input id="mc-stake" type="number" step="0.001" value="0.015"></div></div>
            <div class="actions" style="margin-top:12px"><button class="good" onclick="runMonteCarlo()">Rodar Monte Carlo</button></div>
          `)}}</div>
          <div class="span-8">${{card('Resultado Monte Carlo', `<div id="mc-result" class="mini muted">Ainda não rodamos nenhuma simulação.</div>`)}}</div>
        </div>`;
    }}
    async function runMonteCarlo() {{
      const payload = {{
        hit_rate: Number(document.getElementById('mc-hit').value || 0.55),
        average_odd: Number(document.getElementById('mc-odd').value || 1.9),
        bankroll: Number(document.getElementById('mc-bank').value || 1000),
        stake_pct: Number(document.getElementById('mc-stake').value || 0.015),
      }};
      const data = await api('/api/global-ai/monte-carlo', {{ method:'POST', body: JSON.stringify(payload) }});
      document.getElementById('mc-result').outerHTML = `<pre id="mc-result">${{JSON.stringify(data, null, 2)}}</pre>`;
    }}
    async function loadEvolution() {{
      root.innerHTML = `<div class="grid"><div class="span-4">${{card('Strategy Evolution Lab', `<div class="actions"><button class="good" onclick="runEvolution()">Gerar nova evolução</button></div><div id="evo-result" class="mini muted" style="margin-top:12px">Aguardando evolução.</div>`)}}</div></div>`;
    }}
    async function runEvolution() {{
      const data = await api('/api/global-ai/strategy-evolution', {{ method:'POST' }});
      document.getElementById('evo-result').outerHTML = `<pre id="evo-result">${{JSON.stringify(data, null, 2)}}</pre>`;
    }}
    async function loadAgentArena() {{
      root.innerHTML = `<div class="grid"><div class="span-4">${{card('Agent Arena', `<textarea id="agent-prompt" rows="6" placeholder="Ex: qual liga teve melhor ROI?"></textarea><div class="actions" style="margin-top:12px"><button class="primary" onclick="askAgent()">Perguntar</button></div>`)}}</div><div class="span-8">${{card('Resposta', `<div id="agent-result" class="mini muted">Sem resposta ainda.</div>`)}}</div></div>`;
    }}
    async function askAgent() {{
      const data = await api('/api/global-ai/agent-arena', {{ method:'POST', body: JSON.stringify({{ prompt: document.getElementById('agent-prompt').value || 'qual mercado performou melhor?' }}) }});
      document.getElementById('agent-result').outerHTML = `<pre id="agent-result">${{JSON.stringify(data, null, 2)}}</pre>`;
    }}
    async function loadFeatureLab() {{
      const data = await api('/api/global-ai/feature-lab');
      root.innerHTML = `<div class="grid"><div class="span-12">${{card('Feature Lab', `<pre>${{JSON.stringify(data, null, 2)}}</pre>`)}}</div></div>`;
    }}
    async function loadDriftRegime() {{
      const data = await api('/api/global-ai/drift-regime');
      root.innerHTML = `<div class="grid"><div class="span-12">${{card('Drift & Regime Monitor', `<pre>${{JSON.stringify(data, null, 2)}}</pre>`)}}</div></div>`;
    }}
    async function loadBiasAnomaly() {{
      const data = await api('/api/global-ai/bias-anomaly');
      root.innerHTML = `<div class="grid"><div class="span-12">${{card('Market Bias & Anomaly Center', `<pre>${{JSON.stringify(data, null, 2)}}</pre>`)}}</div></div>`;
    }}
    async function loadRagMemory() {{
      root.innerHTML = `<div class="grid"><div class="span-4">${{card('RAG Memory Explorer', `<textarea id="rag-q" rows="6" placeholder="Ex: o que funcionou em situações parecidas?"></textarea><div class="actions" style="margin-top:12px"><button class="primary" onclick="askRag()">Consultar</button></div>`)}}</div><div class="span-8">${{card('Contexto', `<div id="rag-result" class="mini muted">Sem consulta ainda.</div>`)}}</div></div>`;
    }}
    async function askRag() {{
      const data = await api('/api/global-ai/rag-query', {{ method:'POST', body: JSON.stringify({{ question: document.getElementById('rag-q').value || 'qual mercado teve melhor ROI?' }}) }});
      document.getElementById('rag-result').outerHTML = `<pre id="rag-result">${{JSON.stringify(data, null, 2)}}</pre>`;
    }}
    async function loadGovernance() {{
      const data = await api('/api/global-ai/governance');
      root.innerHTML = `<div class="grid"><div class="span-12">${{card('Governance Center', `<pre>${{JSON.stringify(data, null, 2)}}</pre>`)}}</div></div>`;
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
      root.innerHTML = `<div class="card"><h2>Falha na leitura</h2><pre>${{String(error.message || error)}}</pre></div>`;
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
async def global_ai_health(user: dict[str, Any] = Depends(_require_user), platform=Depends(_global)) -> JSONResponse:
    return JSONResponse({"ok": True, **platform.control_center_snapshot(user_id=user.get("id"))})


@router.get("/api/global-ai/audit")
async def global_ai_audit(user: dict[str, Any] = Depends(_require_admin), platform=Depends(_global)) -> JSONResponse:
    return JSONResponse(platform.audit_report())


@router.get("/api/global-ai/control-center")
async def global_ai_control_center(user: dict[str, Any] = Depends(_require_user), platform=Depends(_global)) -> JSONResponse:
    return JSONResponse(platform.control_center_snapshot(user_id=user.get("id")))


@router.get("/api/global-ai/football-analysis")
async def global_ai_football_analysis(user: dict[str, Any] = Depends(_require_user), platform=Depends(_global)) -> JSONResponse:
    return JSONResponse(platform.football_analysis_board(user_id=user.get("id")))


@router.post("/api/global-ai/football-analysis/event")
async def global_ai_football_analysis_event(
    payload: FootballAnalysisPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.analyze_football_event(payload.event_id, market=payload.market, offered_odd=payload.offered_odd, user_id=user.get("id")))


@router.post("/api/global-ai/backtest")
async def global_ai_backtest(
    payload: BacktestPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.run_backtest({**payload.model_dump(), "user_id": user.get("id")}))


@router.post("/api/global-ai/monte-carlo")
async def global_ai_monte_carlo(
    payload: MonteCarloPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.run_monte_carlo(**payload.model_dump(), user_id=user.get("id")))


@router.post("/api/global-ai/strategy-evolution")
async def global_ai_strategy_evolution(
    user: dict[str, Any] = Depends(_require_admin),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.evolve_strategy(user_id=user.get("id")))


@router.post("/api/global-ai/agent-arena")
async def global_ai_agent_arena(
    payload: AgentPromptPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.agent_arena(prompt=payload.prompt, user_id=user.get("id")))


@router.get("/api/global-ai/feature-lab")
async def global_ai_feature_lab(
    user: dict[str, Any] = Depends(_require_admin),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(
        {
            "features": platform.repository.list_generated_features(limit=80),
            "snapshot": platform.repository.snapshot(),
        }
    )


@router.get("/api/global-ai/drift-regime")
async def global_ai_drift_regime(
    user: dict[str, Any] = Depends(_require_admin),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(
        {
            "drift_events": platform.repository.list_drift_events(limit=40),
            "risk_events": platform.repository.list_risk_events(limit=40),
            "exposure": platform.repository.list_exposure_snapshots(limit=20),
        }
    )


@router.get("/api/global-ai/bias-anomaly")
async def global_ai_bias_anomaly(
    user: dict[str, Any] = Depends(_require_admin),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(
        {
            "pattern_insights": platform.repository.list_pattern_insights(limit=40),
            "drift_events": platform.repository.list_drift_events(limit=20),
        }
    )


@router.post("/api/global-ai/rag-query")
async def global_ai_rag_query(
    payload: RAGPayload,
    user: dict[str, Any] = Depends(_require_user),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.rag_explorer(payload.question))


@router.get("/api/global-ai/governance")
async def global_ai_governance(
    user: dict[str, Any] = Depends(_require_admin),
    platform=Depends(_global),
) -> JSONResponse:
    return JSONResponse(platform.governance.snapshot())


@router.post("/api/global-ai/governance/{request_id}")
async def global_ai_governance_decide(
    request_id: int,
    payload: GovernanceDecisionPayload,
    user: dict[str, Any] = Depends(_require_admin),
    platform=Depends(_global),
) -> JSONResponse:
    decided = platform.governance.decide(request_id, payload.decision, user_id=user.get("id"))
    return JSONResponse({"request": decided})
