from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from services.footballQuantAiSkill import get_football_quant_ai_skill
from services.footballQuantAiSkill.schemas import BacktestRequest
from src.portal_web import _require_admin, _require_user


router = APIRouter()
NOTICE = "Este sistema é apenas uma ferramenta estatística de apoio. Não garante lucro. Aposte com responsabilidade."


class ImportLocalPayload(BaseModel):
    filename: str


class PredictPayload(BaseModel):
    market: str = "match_winner_home"
    offered_odd: float | None = None
    bankroll: float | None = None
    bankroll_profile: str | None = None
    model_version: str = "baseline"


class BacktestPayload(BaseModel):
    league: str | None = None
    season: int | None = None
    market: str = "match_winner_home"
    ev_min: float = 0.0
    confidence_min: float = 55.0
    date_from: str | None = None
    date_to: str | None = None
    bankroll: float = 1000.0
    bankroll_profile: str = "moderado"
    model_version: str = "baseline"


class SuggestionDecisionPayload(BaseModel):
    decision: str


class AgentPayload(BaseModel):
    prompt: str


def _skill():
    return get_football_quant_ai_skill()


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _shell(title: str, mode: str) -> str:
    tabs = [
        ("/app/analise-futebol", "Análise Futebol"),
        ("/app/backtesting", "Backtesting"),
        ("/app/skill-futebol", "Skill Futebol"),
        ("/app/aperfeicoamento-ia", "Aperfeiçoamento IA"),
        ("/app/explicacao-ia", "Explicação da IA"),
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
      --bg:#0b0f14; --panel:#131922; --line:#243143; --muted:#95a3b8; --text:#eef3f8;
      --green:#15d48a; --amber:#ffd24d; --red:#ff6b7a; --blue:#5da8ff;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:Inter,Segoe UI,Arial,sans-serif; }}
    header {{ position:sticky; top:0; z-index:5; background:rgba(11,15,20,.96); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); }}
    .wrap {{ max-width:1300px; margin:0 auto; padding:18px; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; gap:14px; flex-wrap:wrap; }}
    .brand h1 {{ margin:0; font-size:28px; }}
    .brand p {{ margin:6px 0 0; color:var(--muted); }}
    .notice {{ border:1px solid rgba(255,210,77,.25); background:rgba(255,210,77,.08); color:#ffe69a; padding:10px 14px; border-radius:14px; font-size:13px; }}
    .tabs {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    .tab {{ color:var(--text); text-decoration:none; border:1px solid var(--line); background:var(--panel); padding:10px 14px; border-radius:999px; font-weight:800; }}
    .tab.active {{ border-color:var(--green); color:var(--green); box-shadow:0 0 0 1px rgba(21,212,138,.18) inset; }}
    .grid {{ display:grid; gap:16px; grid-template-columns:repeat(12, minmax(0,1fr)); margin-top:18px; }}
    .card {{ background:linear-gradient(180deg,#141c27,#111821); border:1px solid var(--line); border-radius:18px; padding:16px; box-shadow:0 12px 28px rgba(0,0,0,.18); }}
    .span-12 {{ grid-column:span 12; }} .span-8 {{ grid-column:span 8; }} .span-6 {{ grid-column:span 6; }} .span-4 {{ grid-column:span 4; }}
    .span-3 {{ grid-column:span 3; }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    h3 {{ margin:0 0 8px; font-size:14px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }}
    .kpis {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }}
    .mini {{ background:#0f151d; border:1px solid var(--line); border-radius:14px; padding:14px; }}
    .mini strong {{ display:block; font-size:24px; margin-top:6px; }}
    .muted {{ color:var(--muted); }}
    .toolbar {{ display:flex; gap:10px; flex-wrap:wrap; }}
    input, select, textarea, button {{
      width:100%; border:1px solid var(--line); background:#0f151d; color:var(--text);
      border-radius:12px; padding:12px; font:inherit;
    }}
    button {{ cursor:pointer; font-weight:800; }}
    button.primary {{ background:linear-gradient(180deg,#1e6fff,#0d4ed9); border-color:#2563eb; }}
    button.good {{ background:linear-gradient(180deg,#169b61,#0d7b4d); border-color:#0f9a5e; }}
    button.warn {{ background:linear-gradient(180deg,#d9a31a,#b6850f); border-color:#d9a31a; color:#101317; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid rgba(255,255,255,.05); vertical-align:top; }}
    .pill {{ display:inline-flex; gap:6px; align-items:center; border:1px solid var(--line); border-radius:999px; padding:6px 10px; font-size:12px; font-weight:800; }}
    .pill.green {{ color:var(--green); border-color:rgba(21,212,138,.35); }}
    .pill.amber {{ color:var(--amber); border-color:rgba(255,210,77,.35); }}
    .pill.red {{ color:var(--red); border-color:rgba(255,107,122,.35); }}
    .list {{ display:grid; gap:10px; }}
    .row {{ display:grid; gap:10px; grid-template-columns:repeat(2,minmax(0,1fr)); }}
    .actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:#0c1117; border:1px solid var(--line); padding:12px; border-radius:12px; }}
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
          <h1>Football Quant AI Research Skill</h1>
          <p>Pesquisa estatística plugada no ApexGol, sem automatizar aposta real.</p>
        </div>
        <div class="notice">__NOTICE__</div>
      </div>
      <nav class="tabs">__NAV__</nav>
    </div>
  </header>
  <main class="wrap">
    <div id="page-root" data-mode="__MODE__"></div>
  </main>
  <script>
    const root = document.getElementById('page-root');
    const mode = root?.dataset?.mode || 'analise-futebol';
    const fmt = (value) => value == null ? '-' : value;
    const pct = (value) => value == null ? '-' : `${Number(value).toFixed(2)}%`;
    const money = (value) => value == null ? '-' : new Intl.NumberFormat('pt-BR', {{ style:'currency', currency:'BRL' }}).format(Number(value));
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
        }} catch (_) {{}}
        throw new Error(detail);
      }}
      return response.json();
    }}
    function card(title, body) {{
      return `<section class="card">${{title ? `<h2>${{title}}</h2>` : ''}}${{body}}</section>`;
    }}
    async function loadAnalysis() {{
      const health = await api('/api/football-research/health');
      const matches = await api('/api/football-research/matches');
      const predictions = await api('/api/football-research/predictions');
      root.innerHTML = `
        <div class="grid">
          <div class="span-12">${card('Análise Futebol', `
            <div class="kpis">
              <div class="mini"><div class="muted">Jogos históricos</div><strong>${{fmt(health.counts.historical_matches)}}</strong></div>
              <div class="mini"><div class="muted">Odds salvas</div><strong>${{fmt(health.counts.historical_odds)}}</strong></div>
              <div class="mini"><div class="muted">Previsões</div><strong>${{fmt(health.counts.predictions)}}</strong></div>
              <div class="mini"><div class="muted">Runs</div><strong>${{fmt(health.counts.simulation_runs)}}</strong></div>
            </div>
          `)}</div>
          <div class="span-8">${card('Jogos para análise', `
            <div class="list">
              ${matches.matches.map(row => `
                <div class="mini">
                  <div class="muted">${row.league} • ${String(row.match_date).slice(0,16).replace('T',' ')}</div>
                  <strong>${row.home_team} x ${row.away_team}</strong>
                  <div class="actions" style="margin-top:10px">
                    <button class="primary" onclick="predictMatch(${row.id}, 'match_winner_home')">1 casa</button>
                    <button class="primary" onclick="predictMatch(${row.id}, 'over_2_5')">Over 2.5</button>
                    <button class="primary" onclick="predictMatch(${row.id}, 'btts_yes')">BTTS</button>
                  </div>
                </div>
              `).join('') || `<div class="mini muted">Sem jogos arquivados ainda.</div>`}
            </div>
          `)}</div>
          <div class="span-4">${card('Últimas previsões', `
            <div id="prediction-box" class="list">
              ${predictions.items.map(row => `
                <div class="mini">
                  <div class="pill ${row.recommendation.includes('ENTRA') ? 'green' : row.recommendation === 'ESPERA' ? 'amber' : 'red'}">${row.recommendation}</div>
                  <div style="margin-top:10px"><strong>${row.market}</strong></div>
                  <div class="muted">EV ${row.expected_value == null ? '-' : Number(row.expected_value).toFixed(4)} • confiança ${Number(row.confidence_score || 0).toFixed(1)}</div>
                </div>
              `).join('') || `<div class="mini muted">Sem previsões geradas ainda.</div>`}
            </div>
          `)}</div>
        </div>`;
    }}
    async function predictMatch(matchId, market) {{
      try {{
        const data = await api(`/api/football-research/predict/${{matchId}}`, {{ method:'POST', body: JSON.stringify({{ market }}) }});
        alert(`Recomendação: ${{data.prediction.recommendation}}\\nEV: ${{data.prediction.expected_value ?? '-'}}\\nStake: ${{data.prediction.bankroll.suggested_stake}}`);
        await loadAnalysis();
      }} catch (error) {{
        alert(error.message);
      }}
    }}
    async function loadBacktesting() {{
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${card('Rodar simulação', `
            <div class="row">
              <div><h3>Liga</h3><input id="bt-league" placeholder="Ex: Brasil - Serie A"></div>
              <div><h3>Mercado</h3><select id="bt-market"><option value="match_winner_home">Casa vence</option><option value="over_2_5">Over 2.5</option><option value="btts_yes">BTTS</option></select></div>
            </div>
            <div class="row">
              <div><h3>EV mínimo</h3><input id="bt-ev" type="number" step="0.01" value="0.03"></div>
              <div><h3>Confiança mínima</h3><input id="bt-confidence" type="number" step="1" value="60"></div>
            </div>
            <div class="row">
              <div><h3>Banca</h3><input id="bt-bankroll" type="number" step="10" value="1000"></div>
              <div><h3>Perfil</h3><select id="bt-profile"><option>conservador</option><option selected>moderado</option><option>agressivo</option></select></div>
            </div>
            <div class="actions" style="margin-top:12px"><button class="good" onclick="runBacktest()">Rodar backtest</button></div>
          `)}</div>
          <div class="span-8">${card('Métricas do backtest', `<div id="backtest-result" class="mini muted">Ainda não rodamos nenhuma simulação nesta tela.</div>`)}</div>
        </div>`;
    }}
    async function runBacktest() {{
      try {{
        const payload = {{
          league: document.getElementById('bt-league').value || null,
          market: document.getElementById('bt-market').value,
          ev_min: Number(document.getElementById('bt-ev').value || 0),
          confidence_min: Number(document.getElementById('bt-confidence').value || 0),
          bankroll: Number(document.getElementById('bt-bankroll').value || 1000),
          bankroll_profile: document.getElementById('bt-profile').value
        }};
        const data = await api('/api/football-research/backtest', {{ method:'POST', body: JSON.stringify(payload) }});
        document.getElementById('backtest-result').outerHTML = `
          <div id="backtest-result" class="list">
            <div class="mini"><div class="muted">Entradas</div><strong>${{data.summary.total_entries}}</strong></div>
            <div class="mini"><div class="muted">Hit rate</div><strong>${{pct(data.summary.hit_rate)}}</strong></div>
            <div class="mini"><div class="muted">ROI</div><strong>${{pct(data.summary.roi)}}</strong></div>
            <div class="mini"><div class="muted">Lucro / prejuízo</div><strong>${{money(data.summary.profit_loss)}}</strong></div>
            <div class="mini"><div class="muted">Banca final</div><strong>${{money(data.summary.final_bankroll)}}</strong></div>
            <pre>${{JSON.stringify(data.summary, null, 2)}}</pre>
          </div>`;
      }} catch (error) {{
        alert(error.message);
      }}
    }}
    async function loadSkill() {{
      const discovery = await api('/api/football-research/discovery');
      const sources = await api('/api/football-research/sources');
      root.innerHTML = `
        <div class="grid">
          <div class="span-4">${card('Fontes conectadas', `
            <div class="list">
              ${sources.items.map(item => `
                <div class="mini">
                  <strong>${item.name}</strong>
                  <div class="muted">${item.provider_type} • prioridade ${item.priority}</div>
                  <div class="pill ${item.is_active ? 'green' : 'amber'}">${item.is_active ? 'ativa' : 'fallback'}</div>
                </div>
              `).join('')}
            </div>
          `)}</div>
          <div class="span-4">${card('Importação', `
            <div class="actions">
              <button class="good" onclick="importMock()">Importar mock</button>
              <button class="primary" onclick="importLocal()">Importar arquivo local</button>
            </div>
            <div style="margin-top:10px"><input id="import-file" placeholder="football_research_import.json"></div>
            <div id="import-status" class="muted" style="margin-top:10px">Pronto.</div>
          `)}</div>
          <div class="span-4">${card('Descoberta do banco', `<pre>${JSON.stringify(discovery, null, 2)}</pre>`)}</div>
        </div>`;
    }}
    async function importMock() {{
      const data = await api('/api/football-research/import-mock', {{ method:'POST' }});
      document.getElementById('import-status').textContent = `Importação mock concluída: ${data.imported_matches} jogos.`;
    }}
    async function importLocal() {{
      const filename = document.getElementById('import-file').value || 'football_research_import.json';
      const data = await api('/api/football-research/import-local', {{ method:'POST', body: JSON.stringify({{ filename }}) }});
      document.getElementById('import-status').textContent = `Importação local concluída: ${data.imported_matches} jogos de ${data.path}.`;
    }}
    async function loadLearning() {{
      const data = await api('/api/football-research/learning');
      root.innerHTML = `
        <div class="grid">
          <div class="span-6">${card('Performance atual', `<pre>${JSON.stringify(data.snapshot.performance, null, 2)}</pre>`)}</div>
          <div class="span-6">${card('Sugestões pendentes', `
            <div class="list">
              ${(data.snapshot.pending_suggestions || []).map(item => `
                <div class="mini">
                  <strong>${item.title}</strong>
                  <div class="muted">${item.description}</div>
                  <div class="actions" style="margin-top:10px">
                    <button class="good" onclick="decideSuggestion(${item.id}, 'approved')">Aprovar</button>
                    <button class="warn" onclick="decideSuggestion(${item.id}, 'rejected')">Rejeitar</button>
                  </div>
                </div>
              `).join('') || `<div class="mini muted">Sem sugestões pendentes.</div>`}
            </div>
            <div class="actions" style="margin-top:12px"><button class="primary" onclick="refreshSuggestions()">Gerar sugestões</button></div>
          `)}</div>
        </div>`;
    }}
    async function refreshSuggestions() {{
      await api('/api/football-research/learning/suggestions', {{ method:'POST' }});
      await loadLearning();
    }}
    async function decideSuggestion(id, decision) {{
      await api(`/api/football-research/suggestions/${id}`, {{ method:'POST', body: JSON.stringify({{ decision }}) }});
      await loadLearning();
    }}
    async function loadExplanation() {{
      const predictions = await api('/api/football-research/predictions');
      const first = predictions.items[0];
      root.innerHTML = `
        <div class="grid">
          <div class="span-8">${card('Explicação da IA', first ? `<pre>${JSON.stringify(first.explanation, null, 2)}</pre>` : `<div class="mini muted">Ainda não há previsão salva para explicar.</div>`)}</div>
          <div class="span-4">${card('Agente interno', `
            <textarea id="agent-prompt" rows="7" placeholder="Ex: qual liga teve melhor ROI?"></textarea>
            <div class="actions" style="margin-top:12px"><button class="good" onclick="askAgent()">Perguntar ao agente</button></div>
            <pre id="agent-answer">Aguardando pergunta.</pre>
          `)}</div>
        </div>`;
    }}
    async function askAgent() {{
      const prompt = document.getElementById('agent-prompt').value;
      const data = await api('/api/football-research/agent', {{ method:'POST', body: JSON.stringify({{ prompt }}) }});
      document.getElementById('agent-answer').textContent = JSON.stringify(data, null, 2);
    }}
    if (mode === 'analise-futebol') loadAnalysis();
    if (mode === 'backtesting') loadBacktesting();
    if (mode === 'skill-futebol') loadSkill();
    if (mode === 'aperfeicoamento-ia') loadLearning();
    if (mode === 'explicacao-ia') loadExplanation();
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


@router.get("/app/analise-futebol", response_class=HTMLResponse)
def football_analysis_page(_: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Análise Futebol", "analise-futebol")


@router.get("/app/backtesting", response_class=HTMLResponse)
def football_backtesting_page(_: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Backtesting", "backtesting")


@router.get("/app/skill-futebol", response_class=HTMLResponse)
def football_skill_page(_: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Skill Futebol", "skill-futebol")


@router.get("/app/aperfeicoamento-ia", response_class=HTMLResponse)
def football_learning_page(_: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Aperfeiçoamento IA", "aperfeicoamento-ia")


@router.get("/app/explicacao-ia", response_class=HTMLResponse)
def football_explanation_page(_: dict[str, Any] = Depends(_require_user)) -> str:
    return _shell("Explicação da IA", "explicacao-ia")


@router.get("/api/football-research/health")
def football_research_health(_: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse(_skill().health())


@router.get("/api/football-research/discovery")
def football_research_discovery(_: dict[str, Any] = Depends(_require_admin)) -> JSONResponse:
    return JSONResponse(_skill().discovery.scan())


@router.get("/api/football-research/sources")
def football_research_sources(_: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse({"items": _skill().data_sources.source_status(), "notice": NOTICE})


@router.post("/api/football-research/import-mock")
async def football_research_import_mock(user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    result = await _skill().historical.import_from_source(preferred_source="Mock Local", user_id=int(user["id"]))
    return JSONResponse(result)


@router.post("/api/football-research/import-local")
async def football_research_import_local(payload: ImportLocalPayload, user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    result = await _skill().historical.import_local_file(payload.filename.strip(), user_id=int(user["id"]))
    return JSONResponse(result)


@router.get("/api/football-research/matches")
def football_research_matches(league: str | None = None, season: int | None = None, limit: int = 80, _: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    matches = _skill().repository.list_historical_matches(league=league, season=season, limit=limit)
    return JSONResponse({"matches": matches, "notice": NOTICE})


@router.post("/api/football-research/predict/{historical_match_id}")
def football_research_predict(historical_match_id: int, payload: PredictPayload, user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    prediction = _skill().prediction.predict_match(
        historical_match_id,
        market=payload.market,
        offered_odd=payload.offered_odd,
        bankroll=payload.bankroll,
        bankroll_profile=payload.bankroll_profile,
        model_version=payload.model_version,
    )
    prediction_id = _skill().repository.save_prediction(prediction, user_id=int(user["id"]))
    _skill().learning_memory.record_prediction_feedback(
        prediction_id,
        "predicted",
        {"market": payload.market, "recommendation": prediction.recommendation},
        user_id=int(user["id"]),
    )
    return JSONResponse({"prediction_id": prediction_id, "prediction": prediction.__dict__, "notice": NOTICE})


@router.get("/api/football-research/predictions")
def football_research_predictions(limit: int = 20, _: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse({"items": _skill().repository.list_predictions(limit=limit)})


@router.post("/api/football-research/backtest")
def football_research_backtest(payload: BacktestPayload, user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    summary = _skill().backtesting.runBacktest(
        BacktestRequest(
            league=payload.league,
            season=payload.season,
            market=payload.market,
            ev_min=payload.ev_min,
            confidence_min=payload.confidence_min,
            date_from=payload.date_from,
            date_to=payload.date_to,
            bankroll=payload.bankroll,
            bankroll_profile=payload.bankroll_profile,
            model_version=payload.model_version,
            user_id=int(user["id"]),
        )
    )
    return JSONResponse({"summary": summary.__dict__, "notice": NOTICE})


@router.get("/api/football-research/learning")
def football_research_learning(_: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse({"snapshot": _skill().evaluation.current_snapshot(), "notice": NOTICE})


@router.post("/api/football-research/learning/suggestions")
def football_research_learning_suggestions(user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse(_skill().continuous_learning.evaluate_and_suggest(user_id=int(user["id"])))


@router.post("/api/football-research/suggestions/{suggestion_id}")
def football_research_suggestion_decision(suggestion_id: int, payload: SuggestionDecisionPayload, _: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    decision = payload.decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Decisão inválida.")
    row = _skill().repository.decide_strategy_suggestion(suggestion_id, decision)
    if not row:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada.")
    return JSONResponse({"item": row})


@router.post("/api/football-research/agent")
def football_research_agent(payload: AgentPayload, user: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    if len(payload.prompt.strip()) < 4:
        raise HTTPException(status_code=400, detail="Pergunta muito curta.")
    return JSONResponse(_skill().agent.answer(payload.prompt, user_id=int(user["id"])))
