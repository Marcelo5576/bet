from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from services.ai_brain import get_ai_brain_metrics_service
from src.portal_web import _optional_user, _require_user


router = APIRouter()
NOTICE = "Este painel mostra aprendizado estatístico e qualidade dos dados. Não garante resultados futuros."


def _service():
    return get_ai_brain_metrics_service()


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _brain_page() -> str:
    template = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cérebro IA | ApexGol AI</title>
  <style>
    :root {
      color-scheme: dark;
      --bg:#05070d;
      --panel:rgba(8,18,33,.78);
      --panel-strong:rgba(12,28,51,.92);
      --line:rgba(99,179,255,.24);
      --line-strong:rgba(34,211,238,.48);
      --text:#eef8ff;
      --muted:#9db5ca;
      --cyan:#22d3ee;
      --blue:#4ea1ff;
      --violet:#a78bfa;
      --green:#16f2a4;
      --amber:#ffd166;
      --red:#ff5c82;
      --shadow:0 24px 70px rgba(0,0,0,.44);
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      min-height:100vh;
      background:
        radial-gradient(circle at 50% 12%, rgba(34,211,238,.18), transparent 34%),
        radial-gradient(circle at 80% 4%, rgba(167,139,250,.16), transparent 28%),
        linear-gradient(180deg,#05101a 0%,#04070d 58%,#020409 100%);
      color:var(--text);
      font-family:Inter,Segoe UI,Arial,sans-serif;
    }
    body::before {
      content:"";
      position:fixed;
      inset:0;
      pointer-events:none;
      background-image:
        linear-gradient(rgba(34,211,238,.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(34,211,238,.035) 1px, transparent 1px);
      background-size:42px 42px;
      mask-image:linear-gradient(180deg, rgba(0,0,0,.95), rgba(0,0,0,.08));
    }
    a { color:inherit; }
    .wrap { width:min(1480px,100%); margin:0 auto; padding:18px; position:relative; z-index:1; }
    .topbar {
      align-items:center;
      display:flex;
      gap:14px;
      justify-content:space-between;
      padding:12px 0 18px;
      position:sticky;
      top:0;
      z-index:10;
      backdrop-filter:blur(18px);
    }
    .brand { display:grid; gap:4px; min-width:0; }
    .eyebrow { color:var(--cyan); font-size:12px; font-weight:900; letter-spacing:.18em; text-transform:uppercase; }
    h1 { font-size:clamp(28px,5vw,58px); line-height:1; margin:0; letter-spacing:0; }
    .subtitle { color:var(--muted); font-size:15px; max-width:780px; }
    .nav { display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; }
    .btn {
      align-items:center;
      background:rgba(9,23,42,.72);
      border:1px solid var(--line);
      border-radius:999px;
      color:var(--text);
      display:inline-flex;
      font-weight:900;
      min-height:42px;
      padding:0 16px;
      text-decoration:none;
    }
    .btn.primary { background:linear-gradient(90deg,rgba(34,211,238,.22),rgba(167,139,250,.2)); border-color:var(--line-strong); }
    .hero-grid { display:grid; gap:18px; grid-template-columns:minmax(0,1.25fr) minmax(330px,.75fr); }
    .glass {
      background:linear-gradient(180deg,var(--panel),rgba(6,14,26,.66));
      border:1px solid var(--line);
      border-radius:22px;
      box-shadow:var(--shadow);
      overflow:hidden;
      position:relative;
    }
    .glass::after {
      background:linear-gradient(120deg, transparent, rgba(255,255,255,.07), transparent);
      content:"";
      height:100%;
      left:-60%;
      pointer-events:none;
      position:absolute;
      top:0;
      transform:skewX(-18deg);
      width:38%;
    }
    .pad { padding:18px; }
    .status-line { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
    .chip {
      align-items:center;
      border:1px solid var(--line);
      border-radius:999px;
      color:var(--muted);
      display:inline-flex;
      font-size:13px;
      font-weight:900;
      gap:8px;
      padding:8px 11px;
    }
    .chip strong { color:var(--text); }
    .status-operacional { color:var(--green); border-color:rgba(22,242,164,.45); }
    .status-aprendendo { color:var(--amber); border-color:rgba(255,209,102,.45); }
    .status-dados-insuficientes, .status-offline { color:var(--red); border-color:rgba(255,92,130,.45); }
    .brain-stage {
      align-items:center;
      display:grid;
      grid-template-columns:repeat(4,minmax(92px,1fr));
      gap:14px;
      min-height:440px;
      padding:18px;
      position:relative;
    }
    .brain-core {
      align-items:center;
      aspect-ratio:1;
      background:
        radial-gradient(circle, rgba(34,211,238,.18), transparent 58%),
        radial-gradient(circle at 62% 42%, rgba(167,139,250,.18), transparent 34%);
      border:1px solid rgba(34,211,238,.22);
      border-radius:50%;
      display:grid;
      grid-column:2 / span 2;
      grid-row:1 / span 2;
      justify-items:center;
      margin:auto;
      max-width:360px;
      min-width:260px;
      padding:28px;
      position:relative;
      width:70%;
    }
    .brain-core::before,
    .brain-core::after {
      border:1px solid rgba(34,211,238,.2);
      border-radius:50%;
      content:"";
      inset:18px;
      position:absolute;
    }
    .brain-core::after {
      animation:spin 18s linear infinite;
      border-color:transparent rgba(167,139,250,.6) transparent rgba(34,211,238,.55);
      inset:36px;
    }
    .brain-svg { filter:drop-shadow(0 0 22px rgba(34,211,238,.42)); width:min(170px,48vw); z-index:1; }
    .pulse-dot {
      animation:pulse 1.8s ease-in-out infinite;
      background:var(--green);
      border-radius:50%;
      box-shadow:0 0 24px var(--green);
      height:10px;
      position:absolute;
      right:22%;
      top:24%;
      width:10px;
    }
    .orbit-metric {
      background:rgba(5,12,23,.8);
      border:1px solid rgba(99,179,255,.22);
      border-radius:18px;
      padding:14px;
      min-height:104px;
    }
    .orbit-metric small,
    .section-title small { color:var(--muted); display:block; font-size:12px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
    .orbit-metric strong { display:block; font-size:clamp(22px,4vw,34px); margin-top:8px; overflow-wrap:anywhere; }
    .grid { display:grid; gap:16px; grid-template-columns:repeat(12,minmax(0,1fr)); margin-top:18px; }
    .span-12 { grid-column:span 12; }
    .span-8 { grid-column:span 8; }
    .span-6 { grid-column:span 6; }
    .span-4 { grid-column:span 4; }
    .span-3 { grid-column:span 3; }
    .kpis { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(172px,1fr)); }
    .kpi { background:rgba(5,12,23,.7); border:1px solid var(--line); border-radius:18px; padding:15px; }
    .kpi span { color:var(--muted); display:block; font-size:12px; font-weight:900; letter-spacing:.08em; text-transform:uppercase; }
    .kpi strong { display:block; font-size:28px; margin-top:8px; overflow-wrap:anywhere; }
    .section-title { align-items:flex-start; display:flex; justify-content:space-between; gap:12px; margin-bottom:14px; }
    .section-title h2 { font-size:18px; margin:0; }
    .module { display:grid; gap:8px; margin:12px 0; }
    .module-top { align-items:center; display:flex; justify-content:space-between; gap:10px; font-weight:900; }
    .bar { background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.06); border-radius:999px; height:12px; overflow:hidden; }
    .bar span { background:linear-gradient(90deg,var(--cyan),var(--violet)); display:block; height:100%; width:var(--w); }
    .source, .rec, .alert {
      background:rgba(5,12,23,.64);
      border:1px solid rgba(99,179,255,.18);
      border-radius:16px;
      display:grid;
      gap:8px;
      padding:13px;
    }
    .source-top, .rec-top { display:flex; justify-content:space-between; gap:10px; align-items:center; }
    .status-pill { border:1px solid var(--line); border-radius:999px; font-size:11px; font-weight:900; padding:5px 9px; text-transform:uppercase; }
    .ok { color:var(--green); border-color:rgba(22,242,164,.45); }
    .warn { color:var(--amber); border-color:rgba(255,209,102,.45); }
    .bad { color:var(--red); border-color:rgba(255,92,130,.45); }
    .muted { color:var(--muted); }
    .chart {
      align-items:end;
      display:flex;
      gap:6px;
      min-height:150px;
      padding:12px 2px 2px;
      overflow-x:auto;
    }
    .chart-bar {
      background:linear-gradient(180deg,var(--cyan),rgba(34,211,238,.2));
      border-radius:8px 8px 2px 2px;
      flex:0 0 18px;
      height:var(--h);
      min-height:4px;
      position:relative;
    }
    .chart-bar.empty { background:rgba(255,255,255,.08); }
    .chart-bar:hover::after {
      background:#07111f;
      border:1px solid var(--line);
      border-radius:10px;
      bottom:100%;
      color:var(--text);
      content:attr(data-tip);
      font-size:11px;
      left:50%;
      min-width:130px;
      padding:7px;
      position:absolute;
      transform:translateX(-50%);
      white-space:normal;
      z-index:5;
    }
    .empty { color:var(--muted); padding:16px; text-align:center; }
    .notice { color:#b6c7d9; font-size:12px; line-height:1.45; }
    @keyframes spin { to { transform:rotate(360deg); } }
    @keyframes pulse { 0%,100% { transform:scale(1); opacity:.7; } 50% { transform:scale(1.8); opacity:1; } }
    @media (max-width:1100px) {
      .hero-grid { grid-template-columns:1fr; }
      .span-8,.span-6,.span-4,.span-3 { grid-column:span 12; }
      .brain-stage { grid-template-columns:repeat(2,minmax(0,1fr)); min-height:auto; }
      .brain-core { grid-column:1 / span 2; grid-row:auto; width:min(360px,86vw); }
    }
    @media (max-width:720px) {
      .wrap { padding:12px; }
      .topbar { align-items:flex-start; flex-direction:column; position:relative; }
      .nav { justify-content:flex-start; width:100%; }
      .btn { flex:1 1 auto; justify-content:center; }
      .brain-stage { grid-template-columns:1fr; }
      .brain-core { grid-column:auto; min-width:0; padding:20px; width:min(310px,100%); }
      .orbit-metric { min-height:auto; }
      .kpis { grid-template-columns:1fr 1fr; }
    }
  </style>
</head>
<body>
  <main class="wrap">
    <header class="topbar">
      <div class="brand">
        <div class="eyebrow">ApexGol Live Intelligence</div>
        <h1>Cérebro IA</h1>
        <div class="subtitle">Painel premium de aprendizado estatístico, qualidade de dados, memória, backtests e fontes ativas. Todos os números vêm da base real do sistema.</div>
      </div>
      <nav class="nav">
        <a class="btn" href="/dashboard">Dashboard</a>
        <a class="btn" href="/dashboard#scanner">Scanner</a>
        <a class="btn" href="/app/backtesting-lab">Backtesting</a>
        <a class="btn primary" href="/app/global-ai-control-center">Global AI</a>
      </nav>
    </header>

    <section class="hero-grid">
      <div class="glass">
        <div class="brain-stage" id="brain-stage">
          <div class="brain-core">
            <span class="pulse-dot"></span>
            <svg class="brain-svg" viewBox="0 0 220 160" fill="none" aria-label="Cérebro holográfico">
              <path d="M75 24c-22 0-38 17-38 37 0 5 1 10 3 14-16 5-27 19-27 36 0 22 18 39 41 39h103c28 0 50-22 50-49 0-23-16-42-38-48-5-18-22-31-43-31-12 0-23 5-31 13-6-7-13-11-20-11Z" stroke="url(#g)" stroke-width="5"/>
              <path d="M62 75h36m0 0 22-28m-22 28 26 29m-2-57h35m-33 57h45m-97 10h38m0 0 22 28m-22-28 17-39" stroke="rgba(238,248,255,.9)" stroke-width="4" stroke-linecap="round"/>
              <circle cx="62" cy="75" r="7" fill="#22d3ee"/><circle cx="122" cy="47" r="7" fill="#a78bfa"/><circle cx="124" cy="104" r="7" fill="#16f2a4"/><circle cx="157" cy="47" r="6" fill="#4ea1ff"/><circle cx="169" cy="104" r="6" fill="#22d3ee"/>
              <defs><linearGradient id="g" x1="13" x2="207" y1="24" y2="150"><stop stop-color="#22d3ee"/><stop offset=".54" stop-color="#a78bfa"/><stop offset="1" stop-color="#16f2a4"/></linearGradient></defs>
            </svg>
            <strong id="brain-status" style="z-index:1;font-size:20px;margin-top:12px">Carregando...</strong>
            <span id="brain-status-reason" class="muted" style="z-index:1;text-align:center;margin-top:6px"></span>
          </div>
        </div>
      </div>
      <aside class="glass pad">
        <div class="section-title">
          <div><small>Status operacional</small><h2>Maturidade da IA</h2></div>
          <span id="maturity-pill" class="status-pill warn">...</span>
        </div>
        <div class="kpis" id="top-kpis"></div>
        <div class="status-line" id="criteria-line"></div>
        <p class="notice">__NOTICE__</p>
      </aside>
    </section>

    <section class="grid">
      <div class="span-8 glass pad">
        <div class="section-title"><div><small>Aprendizado real</small><h2>Módulos cognitivos</h2></div></div>
        <div id="modules"></div>
      </div>
      <div class="span-4 glass pad">
        <div class="section-title"><div><small>Fontes</small><h2>Dados ativos</h2></div></div>
        <div id="sources" style="display:grid;gap:10px"></div>
      </div>
      <div class="span-4 glass pad">
        <div class="section-title"><div><small>Ações seguras</small><h2>Recomendações do Cérebro</h2></div></div>
        <div id="recommendations" style="display:grid;gap:10px"></div>
      </div>
      <div class="span-4 glass pad">
        <div class="section-title"><div><small>Alertas reais</small><h2>Monitor operacional</h2></div></div>
        <div id="alerts" style="display:grid;gap:10px"></div>
      </div>
      <div class="span-4 glass pad">
        <div class="section-title"><div><small>Resumo</small><h2>Leitura atual</h2></div></div>
        <div id="summary" class="source muted">Carregando resumo...</div>
      </div>
      <div class="span-6 glass pad"><div class="section-title"><div><small>Gráfico real</small><h2>Jogos importados por dia</h2></div></div><div id="chart-imports" class="chart"></div></div>
      <div class="span-6 glass pad"><div class="section-title"><div><small>Gráfico real</small><h2>Sinais por dia</h2></div></div><div id="chart-signals" class="chart"></div></div>
      <div class="span-6 glass pad"><div class="section-title"><div><small>Gráfico real</small><h2>ROI simulado</h2></div></div><div id="chart-roi" class="chart"></div></div>
      <div class="span-6 glass pad"><div class="section-title"><div><small>Gráfico real</small><h2>Bloqueios por risco</h2></div></div><div id="chart-blocked" class="chart"></div></div>
    </section>
  </main>
  <script>
    const fmt = new Intl.NumberFormat('pt-BR');
    const pct = value => value === null || value === undefined ? 'Sem dados suficientes' : `${Number(value).toFixed(2)}%`;
    const val = value => value === null || value === undefined || value === 0 ? (value === 0 ? '0' : 'aguardando') : fmt.format(value);
    const clsStatus = status => String(status || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/\\s+/g,'-');
    function statusClass(source) {
      if (source.status === 'ativa') return 'ok';
      if (source.status === 'erro_recente') return 'bad';
      return 'warn';
    }
    function severityClass(value) {
      if (value === 'alta' || value === 'critical') return 'bad';
      if (value === 'media' || value === 'warning') return 'warn';
      return 'ok';
    }
    function metricCard(label, value, hint='') {
      return `<div class="kpi"><span>${label}</span><strong>${value}</strong>${hint ? `<div class="muted">${hint}</div>` : ''}</div>`;
    }
    function orbitMetric(label, value) {
      return `<div class="orbit-metric"><small>${label}</small><strong>${value}</strong></div>`;
    }
    function renderChart(id, series) {
      const el = document.getElementById(id);
      if (!series || !series.length) {
        el.innerHTML = '<div class="empty">Sem dados suficientes</div>';
        return;
      }
      const values = series.map(item => Number(item.value)).filter(Number.isFinite);
      if (!values.length) {
        el.innerHTML = '<div class="empty">Sem dados suficientes</div>';
        return;
      }
      const max = Math.max(...values.map(v => Math.abs(v)), 1);
      el.innerHTML = series.map(item => {
        const raw = Number(item.value);
        if (!Number.isFinite(raw)) return `<span class="chart-bar empty" style="--h:8px" data-tip="${item.label}: sem dados"></span>`;
        const h = Math.max(7, Math.round((Math.abs(raw) / max) * 140));
        return `<span class="chart-bar" style="--h:${h}px" data-tip="${item.label}: ${raw}"></span>`;
      }).join('');
    }
    async function loadBrain() {
      const [data, summary] = await Promise.all([
        fetch('/api/ai-brain/metrics', {credentials:'include'}).then(r => r.json()),
        fetch('/api/ai-brain/summary', {credentials:'include'}).then(r => r.json()).catch(() => ({summary:'Resumo indisponível agora.'}))
      ]);
      const m = data.metrics || {};
      const stage = document.getElementById('brain-stage');
      stage.insertAdjacentHTML('afterbegin', [
        orbitMetric('Jogos', val(m.total_jogos_analisados)),
        orbitMetric('Sinais', val(m.total_sinais_registrados)),
        orbitMetric('Backtests', val(m.total_backtests)),
        orbitMetric('Memória IA', val((data.raw_counts?.research?.learning_events || 0) + (data.raw_counts?.global?.long_term_memory || 0))),
        orbitMetric('Odds confirmadas', val(m.dados_com_odds_confirmadas)),
        orbitMetric('Ligas', val(m.total_ligas_monitoradas)),
        orbitMetric('Mercados', val(m.total_mercados_monitorados)),
        orbitMetric('Brier médio', m.brier_score_medio == null ? 'aguardando' : Number(m.brier_score_medio).toFixed(4))
      ].join(''));
      const status = document.getElementById('brain-status');
      status.textContent = data.status || 'Dados insuficientes';
      status.className = `status-${clsStatus(data.status)}`;
      document.getElementById('brain-status-reason').textContent = data.status_reason || '';
      document.getElementById('maturity-pill').textContent = `${data.ia_maturity_score}/100 ${data.ia_maturity_label}`;
      document.getElementById('maturity-pill').className = `status-pill ${data.ia_maturity_score >= 70 ? 'ok' : data.ia_maturity_score >= 40 ? 'warn' : 'bad'}`;
      document.getElementById('top-kpis').innerHTML = [
        metricCard('ROI simulado', pct(m.ROI_simulado)),
        metricCard('Hit rate histórico', pct(m.taxa_acerto_historica)),
        metricCard('Lucro/prejuízo simulado', m.lucro_prejuizo_simulado == null ? 'Sem dados suficientes' : fmt.format(m.lucro_prejuizo_simulado)),
        metricCard('Drawdown máximo', m.drawdown_maximo == null ? 'Sem dados suficientes' : fmt.format(m.drawdown_maximo)),
        metricCard('Entradas liberadas', val(m.entradas_liberadas)),
        metricCard('Entradas rejeitadas', val(m.entradas_rejeitadas))
      ].join('');
      document.getElementById('criteria-line').innerHTML = [
        `<span class="chip"><strong>Última atualização</strong> ${m.ultima_atualizacao || '-'}</span>`,
        `<span class="chip"><strong>Sem odds</strong> ${val(m.dados_sem_odds)}</span>`,
        `<span class="chip"><strong>Bloqueios de risco</strong> ${val(m.sinais_bloqueados_por_risco)}</span>`
      ].join('');
      document.getElementById('modules').innerHTML = (data.cognitive_modules || []).map(item => `
        <div class="module">
          <div class="module-top"><span>${item.name}</span><span>${Number(item.progress || 0).toFixed(1)}%</span></div>
          <div class="bar"><span style="--w:${Math.max(0, Math.min(100, Number(item.progress || 0)))}%"></span></div>
          <div class="muted">${item.detail || ''}</div>
        </div>
      `).join('') || '<div class="empty">Sem dados suficientes</div>';
      document.getElementById('sources').innerHTML = (data.data_sources || []).map(source => `
        <div class="source">
          <div class="source-top"><strong>${source.name}</strong><span class="status-pill ${statusClass(source)}">${source.status}</span></div>
          <div class="muted">Requests: ${source.requests || 0} • Último sucesso: ${source.last_success || 'aguardando'}</div>
          ${source.last_error ? `<div class="bad">Erro: ${source.last_error}</div>` : ''}
          ${source.notes ? `<div class="muted">${source.notes}</div>` : ''}
        </div>
      `).join('');
      document.getElementById('recommendations').innerHTML = (data.recommendations || []).map(rec => `
        <div class="rec">
          <div class="rec-top"><strong>${rec.title}</strong><span class="status-pill ${severityClass(rec.severity)}">${rec.severity}</span></div>
          <div class="muted">${rec.reason}</div>
        </div>
      `).join('') || '<div class="empty">Sem recomendações agora</div>';
      document.getElementById('alerts').innerHTML = (data.alerts || []).map(alert => `
        <div class="alert">
          <div><span class="status-pill ${severityClass(alert.level)}">${alert.level}</span></div>
          <strong>${alert.message}</strong>
          ${alert.detail ? `<div class="muted">${alert.detail}</div>` : ''}
        </div>
      `).join('') || '<div class="empty">Sem alertas reais agora</div>';
      document.getElementById('summary').textContent = summary.summary || 'Resumo local indisponível.';
      renderChart('chart-imports', data.charts?.imports_by_day || []);
      renderChart('chart-signals', data.charts?.signals_by_day || []);
      renderChart('chart-roi', data.charts?.roi_evolution || []);
      renderChart('chart-blocked', data.charts?.blocked_by_day || []);
    }
    loadBrain().catch(err => {
      document.body.insertAdjacentHTML('beforeend', `<div class="wrap"><div class="glass pad bad">Falha ao carregar Cérebro IA: ${err.message}</div></div>`);
    });
  </script>
</body>
</html>"""
    return template.replace("__NOTICE__", _esc(NOTICE))


@router.get("/cerebro-ia", response_class=HTMLResponse)
@router.get("/app/cerebro-ia", response_class=HTMLResponse)
async def ai_brain_page(request: Request):
    user = _optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return _brain_page()


@router.get("/api/ai-brain/metrics")
async def ai_brain_metrics(
    user: dict[str, Any] = Depends(_require_user),
    service=Depends(_service),
) -> JSONResponse:
    return JSONResponse(service.metrics())


@router.get("/api/ai-brain/summary")
async def ai_brain_summary(
    user: dict[str, Any] = Depends(_require_user),
    service=Depends(_service),
) -> JSONResponse:
    return JSONResponse(service.summary())
