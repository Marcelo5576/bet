from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import logging
import random
import re
import traceback
from zoneinfo import ZoneInfo

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.config import Settings, load_settings
from src.integrations.supabase import SupabaseSink
from src.intelligence.gemini import answer_question, refine_signal
from src.intelligence.learning import summarize_history_with_simulation
from src.intelligence.manual_import import parse_manual_bets
from src.intelligence.markets import market_recommendations
from src.intelligence.paper_trading import best_paper_entry, paper_opportunities
from src.intelligence.risk import (
    apply_daily_red_stop,
    apply_risk_controls,
    is_forbidden_esports_game,
    red_stop_status,
)
from src.intelligence.rules import analyze_game, position_management, ranked_signals
from src.intelligence.scoring import entry_score
from src.providers.api_football import ApiFootballProvider
from src.providers.base import LiveProvider, provider_label
from src.providers.espn import EspnProvider
from src.providers.fallback import FallbackLiveProvider
from src.providers.football_data_org import FootballDataOrgProvider
from src.providers.mock_provider import MockProvider
from src.portal import PortalStore
from src.storage import StateStore

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("betsignal")
TELEGRAM_TEXT_LIMIT = 3900
SYSTEM_CHECK_INTERVAL_SECONDS = 30 * 60
SOURCE_SYNC_INTERVAL_SECONDS = 6 * 60 * 60
DAILY_SIMULATION_CHECK_INTERVAL_SECONDS = 15 * 60
TELEGRAM_DICE = {
    "scan": "⚽",
    "signal": "🎯",
    "green": "🎯",
    "red": "🎲",
    "ia": "🎯",
}


def build_provider(settings: Settings) -> LiveProvider:
    if settings.test_mode:
        return MockProvider()
    providers: list[LiveProvider] = []
    if settings.api_football_key:
        providers.append(ApiFootballProvider(settings.api_football_key, settings.api_football_base_url))
    providers.append(EspnProvider())
    if settings.football_data_org_token:
        providers.append(FootballDataOrgProvider(settings.football_data_org_token))
    return FallbackLiveProvider(*providers)


def supabase_sink(context: ContextTypes.DEFAULT_TYPE) -> SupabaseSink:
    return context.application.bot_data["supabase"]


def signal_text(signal: dict) -> str:
    game = signal["game"]
    markets = game.get("markets") or {}
    primary = _primary_recommendation(signal)
    action = signal.get("action")
    color = _signal_color(action)
    lines = [
        f"{color} {_decision_badge(signal)}",
        f"Confianca {signal['confidence']}% | Score {signal.get('entry_score', '-')}/100 | Grau {signal.get('grade', '-')}",
        "",
        "📌 JOGO",
        f"{game['home']} {game['home_goals']} x {game['away_goals']} {game['away']}",
        f"Minuto {game['minute']}' | {game.get('division', game.get('league', '-'))}",
        "",
        "🎯 ENTRADA SUGERIDA",
        f"Mercado: {primary.get('market', signal.get('market', '-'))}",
        f"Entrar em: {primary.get('entry') or _primary_entry_text(signal)}",
        f"Odd alvo: {primary.get('odds') or signal.get('target_odds') or '-'} | Odd justa: {signal.get('fair_odds') or '-'}",
        f"Stake sugerida: {signal.get('stake_value', 0)} ({signal.get('stake_units', 0)}u)",
        "",
        "🧾 SUA ENTRADA",
        _short_entry_status(signal),
        _entry_details_text(signal),
        "",
        "📊 ONDE ENTRAR",
        _format_entry_board(signal),
        "",
        "🧠 LEITURA DA IA",
        f"Risco: {signal.get('risk_score', '-')}/100 | {signal.get('score_note', '-')}",
        f"Edge: {_pct(signal.get('value_edge'))} | Dados: {signal.get('data_quality', '-')}%",
        _compact_reason(signal),
        "",
        f"🛡️ GESTAO DE RISCO: {_compact_risk(signal)}",
    ]
    if signal.get("gemini_note"):
        lines.append("")
        lines.append("IA")
        lines.append(_shorten(signal["gemini_note"], 500))
    return _shorten("\n".join(lines), TELEGRAM_TEXT_LIMIT)


def monitor_text(signal: dict) -> str:
    management = position_management(signal)
    return _shorten(
        signal_text(signal)
        + "\n\n🚦 MONITORAMENTO\n"
        + f"{_management_color(management['decision'])} {management['decision']}: {management['reason']}",
        TELEGRAM_TEXT_LIMIT,
    )


def _entry_status(signal: dict) -> str:
    if signal.get("entered"):
        return f"ENTROU em {signal.get('entered_at', '-')}"
    return "NAO INFORMADO. Aperte Entrei quando fizer a entrada."


def _short_entry_status(signal: dict) -> str:
    return "Status: ENTROU" if signal.get("entered") else "Status: aguardando confirmacao"


def _entry_details_text(signal: dict) -> str:
    market = signal.get("entry_market")
    amount = signal.get("entry_value")
    odds = signal.get("entry_odds")
    notes = signal.get("entry_notes")
    if not any(value not in {None, ""} for value in (market, amount, odds, notes)):
        return "Real: informe mercado | valor | odd"
    parts = [
        f"mercado={market or '-'}",
        f"valor={amount if amount is not None else '-'}",
        f"odd={odds if odds is not None else '-'}",
    ]
    if notes:
        parts.append(f"obs={notes}")
    return "Real: " + " | ".join(parts)


def _decision_badge(signal: dict) -> str:
    if signal.get("risk_blocked"):
        return "🔴 BLOQUEADO"
    action = signal.get("action")
    if action == "ENTRAR":
        return "🟢 SINAL"
    if action == "AGUARDAR":
        return "🟡 AGUARDAR"
    if action == "SAIR":
        return "🔴 SAIR"
    return "🔵 OBSERVAR"


def _signal_color(action: str | None) -> str:
    return {
        "ENTRAR": "🟢🟢🟢",
        "AGUARDAR": "🟡🟡🟡",
        "SEGURAR": "🔵🔵🔵",
        "SAIR": "🔴🔴🔴",
    }.get(str(action or "").upper(), "🔵🔵🔵")


def _management_color(decision: str | None) -> str:
    text = str(decision or "").upper()
    if "SAIR" in text:
        return "🔴"
    if "MANTER" in text:
        return "🟢"
    if "PROTEGER" in text:
        return "🟡"
    return "🔵"


def _action_icon(action: str | None) -> str:
    return {
        "ENTRAR": "🟢",
        "AGUARDAR": "🟡",
        "SEGURAR": "🔵",
        "SAIR": "🔴",
        "SEM DADOS": "⚪",
    }.get(str(action or "").upper(), "🔵")


def _pct(value) -> str:
    if value is None:
        return "-"
    return f"{round(value * 100, 1)}%"


def _format_markets(markets: dict, game: dict) -> str:
    one_x_two = markets.get("1x2") or {
        "home": game.get("odds_home"),
        "draw": game.get("odds_draw"),
        "away": game.get("odds_away"),
    }
    lines = [
        "1X2: "
        f"{game.get('home')} {one_x_two.get('home') or '-'} | "
        f"Empate {one_x_two.get('draw') or '-'} | "
        f"{game.get('away')} {one_x_two.get('away') or '-'}"
    ]
    goals = markets.get("goals") or {}
    lines.append(
        "Gols: "
        f"Over {_line(goals.get('over'))} @ {_odd(goals.get('over'))} | "
        f"Under {_line(goals.get('under'))} @ {_odd(goals.get('under'))}"
    )
    corners = markets.get("corners") or {}
    lines.append(
        "Escanteios: "
        f"Over {_line(corners.get('over'))} @ {_odd(corners.get('over'))} | "
        f"Under {_line(corners.get('under'))} @ {_odd(corners.get('under'))}"
    )
    asian = markets.get("asian") or {}
    lines.append(
        "Asiaticas/Handicap: "
        f"{game.get('home')} {_line(asian.get('home'))} @ {_odd(asian.get('home'))} | "
        f"{game.get('away')} {_line(asian.get('away'))} @ {_odd(asian.get('away'))}"
    )
    return "\n".join(lines)


def _format_market_recommendations(signal: dict) -> str:
    lines = []
    for rec in signal.get("market_recommendations") or market_recommendations(signal):
        lines.append(
            f"{rec['market']}: {rec['action']} | {rec.get('entry')} | motivo: {rec['reason']}"
        )
    return "\n".join(lines)


def _format_entry_board(signal: dict) -> str:
    recs = signal.get("market_recommendations") or market_recommendations(signal)
    by_market = {str(rec.get("market", "")).lower(): rec for rec in recs}
    rows = [
        _entry_board_line("1X2", _find_market(by_market, ("1x2",))),
        _entry_board_line("Gols", _find_market(by_market, ("gols", "goals"))),
        _entry_board_line("Escanteios", _find_market(by_market, ("escanteios", "corners"))),
        _entry_board_line("Asiatica", _find_market(by_market, ("asiatica", "handicap", "asian"))),
    ]
    used = {
        id(item)
        for item in (
            _find_market(by_market, ("1x2",)),
            _find_market(by_market, ("gols", "goals")),
            _find_market(by_market, ("escanteios", "corners")),
            _find_market(by_market, ("asiatica", "handicap", "asian")),
        )
        if item
    }
    extras = [
        rec for rec in recs
        if id(rec) not in used
    ]
    for rec in extras[:2]:
        rows.append(_entry_board_line(str(rec.get("market") or "Outro"), rec))
    return "\n".join(rows)


def _find_market(by_market: dict[str, dict], tokens: tuple[str, ...]) -> dict | None:
    for name, rec in by_market.items():
        if any(token in name for token in tokens):
            return rec
    return None


def _entry_board_line(label: str, rec: dict | None) -> str:
    if not rec:
        return f"{label}: sem dados da fonte atual."
    action = rec.get("action") or "AGUARDAR"
    entry = rec.get("entry") or rec.get("selection") or "-"
    line = rec.get("line") or "-"
    odds = rec.get("odds")
    odd_text = f"odd {odds}" if odds else "odd indisponivel"
    return f"{label}: {action} | {entry} | linha {line} | {odd_text}"


def _primary_entry_text(signal: dict) -> str:
    primary = _primary_recommendation(signal)
    if not primary:
        return f"Onde entrar: {signal.get('market', '1X2')} em {signal.get('team')} na odd {signal.get('target_odds') or '-'}"
    return f"Onde entrar: {primary.get('entry')}"


def _primary_recommendation(signal: dict) -> dict:
    recs = signal.get("market_recommendations") or market_recommendations(signal)
    actionable = [rec for rec in recs if rec.get("action") == "ENTRAR"]
    if actionable:
        return actionable[0]
    return next((rec for rec in recs if rec["market"] == "1X2"), recs[0] if recs else {})


def _compact_reason(signal: dict) -> str:
    reason = signal.get("reason") or "-"
    return reason[:220].rstrip() + ("..." if len(reason) > 220 else "")


def _compact_risk(signal: dict) -> str:
    note = signal.get("risk_note") or "confirmacao manual."
    note = note.replace("Entrada somente com confirmacao manual.", "manual.")
    note = note.replace("Alerta informativo.", "").strip()
    return note[:260].rstrip() + ("..." if len(note) > 260 else "")


def _line(item) -> str:
    if not isinstance(item, dict):
        return "-"
    return str(item.get("line") or "-")


def _odd(item) -> str:
    if not isinstance(item, dict):
        return "-"
    return str(item.get("odds") or "-")


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 60].rstrip() + "\n\n[Resumo cortado para caber no Telegram]"


def _entry_prompt_text() -> str:
    return "\n".join(
        [
            "Informe sua entrada real neste formato:",
            "",
            "mercado | valor | odd",
            "",
            "Exemplos:",
            "Over 2.5 gols | 100 | 1.85",
            "Escanteios over 8.5 | 80 | 1.90",
            "Flamengo -0.5 asiatica | 100 | 2.05",
            "",
            "Tambem funciona direto com:",
            "/entrada Over 2.5 gols | 100 | 1.85",
        ]
    )


def _closed_result_text(signal: dict | None, label: str) -> str:
    if not signal:
        return f"✅ Resultado marcado como {label}.\nScanner liberado para novo jogo."
    game = signal.get("game", {})
    profit_units = signal.get("profit_units", 0)
    entry_value = signal.get("entry_value")
    odds = signal.get("entry_odds") or signal.get("target_odds")
    market = signal.get("entry_market") or signal.get("market") or "-"
    color = "🟢" if signal.get("outcome") == "win" else "🔴" if signal.get("outcome") == "loss" else "🟡"
    lines = [
        f"{color} RESULTADO FECHADO: {label}",
        "",
        "📌 JOGO",
        f"{game.get('home', '-')} x {game.get('away', '-')}",
        "",
        "🧾 ENTRADA",
        f"Mercado: {market}",
        f"Odd: {odds or '-'} | Valor: {entry_value if entry_value is not None else signal.get('stake_value', '-')}",
        f"Resultado: {profit_units}u",
        "",
        "📊 Dashboard sincronizada.",
        "Saiu de entradas em andamento e foi para o historico Green/Red.",
        "",
        "🔵 Scanner liberado para novo jogo.",
    ]
    return "\n".join(lines)


def _red_lock_text(status: dict) -> str:
    unlock_at = status.get("unlock_at")
    unlock_text = unlock_at.strftime("%d/%m/%Y %H:%M") if unlock_at else "06:00"
    return "\n".join(
        [
            "🟠 ALERTA DE DISCIPLINA",
            "",
            f"Reds no ciclo: {status.get('red_count', 0)}/{status.get('red_limit', 2)}.",
            f"Janela de controle ate: {unlock_text} (Sao Paulo).",
            "",
            "O scanner continua ativo, mas a IA recomenda reduzir risco.",
            "Revise criterios, stake e selecao de mercado antes da proxima entrada.",
        ]
    )


def _parse_entry_details(text: str) -> dict:
    cleaned = " ".join((text or "").strip().split())
    parts = [part.strip() for part in re.split(r"\s*[|;]\s*", cleaned) if part.strip()]
    market = parts[0] if parts else cleaned
    amount = _parse_number(parts[1]) if len(parts) >= 2 else None
    odds = _parse_number(parts[2]) if len(parts) >= 3 else None
    notes = " | ".join(parts[3:]) if len(parts) >= 4 else None
    return {
        "market": market or "Entrada manual",
        "amount": amount,
        "odds": odds,
        "notes": notes,
    }


def _parse_number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Jogos", callback_data="menu:games"),
                InlineKeyboardButton("I.A.", callback_data="menu:ia"),
            ],
            [
                InlineKeyboardButton("Relatorios", callback_data="menu:reports"),
                InlineKeyboardButton("Saude", callback_data="menu:health"),
            ],
            [InlineKeyboardButton("Dashboard", callback_data="dashboard")],
        ]
    )


def active_menu() -> InlineKeyboardMarkup:
    return games_menu(active=True)


def games_menu(active: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if active:
        rows.extend(
            [
                [
                    InlineKeyboardButton("Entrei", callback_data="entry:yes"),
                    InlineKeyboardButton("Nao entrei", callback_data="entry:no"),
                ],
                [InlineKeyboardButton("Informar entrada", callback_data="entry:details")],
                [
                    InlineKeyboardButton("Green", callback_data="outcome:win"),
                    InlineKeyboardButton("Red", callback_data="outcome:loss"),
                    InlineKeyboardButton("Anular", callback_data="outcome:void"),
                ],
                [InlineKeyboardButton("Sair / Fechar", callback_data="exit:prompt")],
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("Scan agora", callback_data="scan"),
                InlineKeyboardButton("Status jogo", callback_data="status"),
            ],
            [InlineKeyboardButton("Escolher jogo", callback_data="list")],
            [InlineKeyboardButton("Stop / liberar", callback_data="stop")],
            [InlineKeyboardButton("Menu principal", callback_data="menu:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def ia_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Eficiencia IA", callback_data="stats"),
                InlineKeyboardButton("Aprendizado", callback_data="learning"),
            ],
            [
                InlineKeyboardButton("Contexto ativo", callback_data="ia:context"),
                InlineKeyboardButton("Como perguntar", callback_data="ia:help"),
            ],
            [InlineKeyboardButton("Simular melhor entrada", callback_data="ia:paper")],
            [InlineKeyboardButton("Importar historico", callback_data="ia:import")],
            [InlineKeyboardButton("Menu principal", callback_data="menu:home")],
        ]
    )


def reports_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Dashboard", callback_data="dashboard"),
                InlineKeyboardButton("Eficiencia IA", callback_data="stats"),
            ],
            [
                InlineKeyboardButton("Aprendizado", callback_data="learning"),
                InlineKeyboardButton("Oferta", callback_data="offer"),
            ],
            [InlineKeyboardButton("Menu principal", callback_data="menu:home")],
        ]
    )


def health_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Checkout agora", callback_data="health:checkout"),
                InlineKeyboardButton("Teste Telegram", callback_data="health:test"),
            ],
            [
                InlineKeyboardButton("Relatorio Codex", callback_data="support"),
            ],
            [
                InlineKeyboardButton("Status jogo", callback_data="status"),
                InlineKeyboardButton("Dashboard", callback_data="dashboard"),
            ],
            [InlineKeyboardButton("Menu principal", callback_data="menu:home")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        _home_text(context),
        reply_markup=menu(),
    )


async def main_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_home_text(context), reply_markup=menu())


async def games_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    await update.effective_message.reply_text(
        _games_text(state),
        reply_markup=games_menu(active=bool(state.active_signal)),
    )


async def ia_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_ia_menu_text(), reply_markup=ia_menu())


async def reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_reports_text(context), reply_markup=reports_menu())


async def offer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_offer_text(context), reply_markup=reports_menu())


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_health_text(context), reply_markup=health_menu())


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    await update.effective_message.reply_text(
        "\n".join(
            [
                f"Seu chat_id Telegram: {chat.id}",
                "Copie esse numero e cole em Area do Cliente > Preferencias de scanner/notificacao.",
                "Depois ative a opcao de notificacao no Telegram.",
            ]
        ),
        reply_markup=health_menu(),
    )


async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, text = await run_system_checkout(context, manual=True)
    await update.effective_message.reply_text(text, reply_markup=health_menu())


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    if state.active_signal:
        text = "Jogo ativo monitorado:\n\n" + signal_text(state.active_signal)
        reply_markup = active_menu()
    else:
        text = "🔵 Nenhum jogo ativo. O scanner esta livre."
        reply_markup = menu()
    await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: StateStore = context.application.bot_data["store"]
    store.clear_active()
    await update.effective_message.reply_text(
        "Jogo ativo liberado. Proximo scan pode escolher outro jogo.",
        reply_markup=menu(),
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await send_motion(context, update.effective_chat.id, "scan")
    text = await run_scan(context, auto_pick=False)
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    reply_markup = active_menu() if state.active_signal else candidate_menu(state)
    await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🟢 Teste OK. O Telegram esta recebendo mensagens do BetSignal Cloud.",
        reply_markup=health_menu(),
    )


async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    await update.effective_message.reply_text(_dashboard_text(settings), reply_markup=reports_menu())


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_stats_text(context), reply_markup=reports_menu())


async def learning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_learning_text(context), reply_markup=ia_menu())


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _support_text(context)
    await update.effective_message.reply_text(text, reply_markup=health_menu())


async def ia_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args).strip()
    if not question:
        await update.effective_message.reply_text(
            "Use assim: /ia sua pergunta sobre o jogo, odds, entrada ou gestao.",
            reply_markup=ia_menu(),
        )
        return
    await update.effective_message.reply_text("Analisando sua pergunta...")
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    supabase_context = await supabase_sink(context).fetch_ai_context(state.active_signal)
    simulation_sessions = list(state.simulation_sessions or [])
    context_payload = {
        "active_signal": state.active_signal,
        "learning": _learning_context(state),
        "candidate_signals": state.candidate_signals,
        "simulation_learning": _simulation_learning_summary(simulation_sessions),
        "simulation_sessions": simulation_sessions[:12],
        "supabase_memory": supabase_context,
    }
    answer = await answer_question(
        question,
        context_payload,
        settings.gemini_api_key,
        settings.gemini_model,
    )
    await update.effective_message.reply_text(answer, reply_markup=ia_menu())


async def entry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text(
            _entry_prompt_text(),
            reply_markup=active_menu(),
        )
        return
    await save_entry_details(update, context, text)


async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    text = text.partition(" ")[2].strip()
    if not text:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id:
            pending = context.application.bot_data.setdefault("pending_import_chats", set())
            pending.add(chat_id)
        await update.effective_message.reply_text(_import_prompt_text(), reply_markup=ia_menu())
        return
    await import_history_text(update, context, text)


async def pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    pending_imports = context.application.bot_data.setdefault("pending_import_chats", set())
    if chat_id in pending_imports:
        pending_imports.discard(chat_id)
        await import_history_text(update, context, update.effective_message.text or "")
        return
    pending = context.application.bot_data.setdefault("pending_entry_chats", set())
    if chat_id not in pending:
        return
    pending.discard(chat_id)
    await save_entry_details(update, context, update.effective_message.text or "")


async def import_history_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_text: str,
) -> None:
    store: StateStore = context.application.bot_data["store"]
    records = parse_manual_bets(raw_text)
    if not records:
        await update.effective_message.reply_text(
            "🔴 Nao encontrei apostas para importar.\n\nCole texto com valor, mercado e resultado.",
            reply_markup=ia_menu(),
        )
        return
    state = store.add_history_records(records)
    await supabase_sink(context).sync_signals(records)
    await supabase_sink(context).sync_ai_memory(state.history or [])
    losses = sum(1 for item in records if item.get("outcome") == "loss")
    voids = sum(1 for item in records if item.get("outcome") == "void")
    opens = sum(1 for item in records if item.get("outcome") == "open")
    await update.effective_message.reply_text(
        "🟢 HISTORICO IMPORTADO\n\n"
        f"📥 Linhas uteis: {len(records)}\n"
        f"🔴 Reds: {losses}\n"
        f"🟡 Encerradas/void: {voids}\n"
        f"🔵 Abertas ignoradas no painel: {opens}\n\n"
        "🧠 IA atualizada com esse historico.",
        reply_markup=ia_menu(),
    )


async def save_entry_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_text: str,
) -> None:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    if not state.active_signal:
        await update.effective_message.reply_text(
            "Nao ha jogo ativo. Primeiro escolha um jogo em Jogos > Scan agora.",
            reply_markup=games_menu(),
        )
        return

    details = _parse_entry_details(raw_text)
    state = store.mark_entry_details(
        details["market"],
        details.get("amount"),
        details.get("odds"),
        details.get("notes"),
    )
    await supabase_sink(context).sync_signal(state.active_signal)
    await update.effective_message.reply_text(
        "🟢 ENTRADA DETALHADA REGISTRADA\n\n" + monitor_text(state.active_signal),
        reply_markup=active_menu(),
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    reply_markup = menu()
    if query.data == "menu:home":
        text = _home_text(context)
        reply_markup = menu()
    elif query.data == "menu:games":
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        text = _games_text(state)
        reply_markup = games_menu(active=bool(state.active_signal))
    elif query.data == "menu:ia":
        text = _ia_menu_text()
        reply_markup = ia_menu()
    elif query.data == "menu:reports":
        text = _reports_text(context)
        reply_markup = reports_menu()
    elif query.data == "menu:health":
        text = _health_text(context)
        reply_markup = health_menu()
    elif query.data == "scan":
        if query.message:
            await send_motion(context, query.message.chat_id, "scan")
        text = await run_scan(context, auto_pick=False)
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        reply_markup = active_menu() if state.active_signal else candidate_menu(state)
    elif query.data == "list":
        store: StateStore = context.application.bot_data["store"]
        store.clear_active()
        text = await run_scan(context, auto_pick=False, force_list=True)
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        reply_markup = active_menu() if state.active_signal else candidate_menu(state)
    elif query.data.startswith("pick:"):
        index = int(query.data.split(":", 1)[1])
        store: StateStore = context.application.bot_data["store"]
        state = store.choose_candidate(index)
        text = (
            "Jogo escolhido. Scanner ativo a cada 5 minutos:\n\n"
            + monitor_text(state.active_signal)
            if state.active_signal
            else "Nao consegui escolher esse jogo. Rode /scan novamente."
        )
        reply_markup = active_menu() if state.active_signal else games_menu()
    elif query.data == "entry:yes":
        store: StateStore = context.application.bot_data["store"]
        state = store.mark_entry(True)
        await supabase_sink(context).sync_signal(state.active_signal)
        if state.active_signal and query.message:
            await send_motion(context, query.message.chat_id, "signal")
        text = (
            "🟢🟢🟢 ENTRADA REGISTRADA\n"
            "Vou monitorar este jogo a cada 5 minutos.\n\n"
            + monitor_text(state.active_signal)
            if state.active_signal
            else "Nao ha jogo ativo para marcar entrada."
        )
        reply_markup = active_menu() if state.active_signal else games_menu()
    elif query.data == "entry:details":
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        if not state.active_signal:
            text = "Nao ha jogo ativo. Primeiro escolha um jogo."
            reply_markup = games_menu()
        else:
            chat_id = query.message.chat_id if query.message else None
            if chat_id:
                pending = context.application.bot_data.setdefault("pending_entry_chats", set())
                pending.add(chat_id)
            text = _entry_prompt_text()
            reply_markup = active_menu()
    elif query.data == "entry:no":
        store: StateStore = context.application.bot_data["store"]
        state = store.mark_entry(False)
        await supabase_sink(context).sync_signal(state.active_signal)
        text = (
            "Ok, registrei que voce ainda nao entrou. Vou continuar observando o jogo escolhido.\n\n"
            + monitor_text(state.active_signal)
            if state.active_signal
            else "Nao ha jogo ativo."
        )
        reply_markup = active_menu() if state.active_signal else games_menu()
    elif query.data == "exit:prompt":
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        text = (
            "Feche sua entrada marcando o resultado real.\n\n"
            "Use Green se saiu com lucro, Red se saiu com prejuizo, ou Anular se nao contou."
        )
        if state.active_signal:
            text += "\n\n" + monitor_text(state.active_signal)
        reply_markup = active_menu() if state.active_signal else games_menu()
    elif query.data == "dashboard":
        settings: Settings = context.application.bot_data["settings"]
        text = _dashboard_text(settings)
        reply_markup = reports_menu()
    elif query.data == "stats":
        text = _stats_text(context)
        reply_markup = reports_menu()
    elif query.data == "learning":
        text = _learning_text(context)
        reply_markup = ia_menu()
    elif query.data == "offer":
        text = _offer_text(context)
        reply_markup = reports_menu()
    elif query.data == "support":
        text = _support_text(context)
        reply_markup = health_menu()
    elif query.data == "health:test":
        text = "🟢 Teste OK. O Telegram esta recebendo mensagens do BetSignal Cloud."
        reply_markup = health_menu()
    elif query.data == "health:checkout":
        _, text = await run_system_checkout(context, manual=True)
        reply_markup = health_menu()
    elif query.data == "ia:help":
        text = _ia_help_text()
        reply_markup = ia_menu()
    elif query.data == "ia:import":
        chat_id = query.message.chat_id if query.message else None
        if chat_id:
            pending = context.application.bot_data.setdefault("pending_import_chats", set())
            pending.add(chat_id)
        text = _import_prompt_text()
        reply_markup = ia_menu()
    elif query.data == "ia:paper":
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        text = _paper_text(state)
        reply_markup = ia_menu()
    elif query.data == "ia:context":
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        text = (
            "Contexto que a IA esta usando agora:\n\n" + signal_text(state.active_signal)
            if state.active_signal
            else "A IA ainda nao tem jogo ativo. Va em Jogos > Scan agora e escolha uma partida."
        )
        reply_markup = ia_menu()
    elif query.data.startswith("outcome:"):
        outcome = query.data.split(":", 1)[1]
        store: StateStore = context.application.bot_data["store"]
        before = store.load()
        signal_id = (before.active_signal or {}).get("signal_id")
        state = store.mark_active_outcome(outcome)
        closed_signal = next(
            (item for item in state.history or [] if item.get("signal_id") == signal_id),
            state.history[0] if state.history else None,
        )
        if closed_signal:
            await supabase_sink(context).sync_signal(closed_signal)
            await supabase_sink(context).sync_ai_memory(state.history or [])
        label = {"win": "🟢 green", "loss": "🔴 red", "void": "🟡 anulada"}[outcome]
        if query.message:
            await send_motion(context, query.message.chat_id, "green" if outcome == "win" else "red")
        text = _closed_result_text(closed_signal, label)
        stop = red_stop_status(state.history or [], context.application.bot_data["settings"].daily_red_limit)
        if outcome == "loss" and stop.get("discipline_alert"):
            text += "\n\n" + _red_lock_text(stop)
        reply_markup = games_menu()
    elif query.data == "status":
        store: StateStore = context.application.bot_data["store"]
        state = store.load()
        text = (
            "Jogo ativo monitorado:\n\n" + signal_text(state.active_signal)
            if state.active_signal
            else "🔵 Nenhum jogo ativo. O scanner esta livre."
        )
        reply_markup = active_menu() if state.active_signal else games_menu()
    else:
        store: StateStore = context.application.bot_data["store"]
        store.clear_active()
        text = "Jogo ativo liberado. Proximo scan pode escolher outro jogo."
        reply_markup = games_menu()
    await safe_edit_message(query, text, reply_markup)


async def safe_edit_message(query, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return
        raise


async def send_motion(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    kind: str,
) -> None:
    emoji = TELEGRAM_DICE.get(kind)
    if not emoji:
        return
    try:
        await context.bot.send_dice(chat_id=chat_id, emoji=emoji)
    except Exception as exc:
        logger.info("Animacao Telegram ignorada: %s", exc)


async def run_scan(
    context: ContextTypes.DEFAULT_TYPE,
    auto_pick: bool = False,
    force_list: bool = False,
) -> str:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    provider: LiveProvider = context.application.bot_data["provider"]

    state = store.load()
    if state.active_game_id and state.active_signal and not force_list:
        return await refresh_active_signal(context, state)

    store.touch_scan()
    try:
        games, scan_scope = await scan_games(
            provider,
            state.scan_preference,
            block_esports=settings.block_esports,
        )
    except Exception as exc:
        logger.warning("Falha ao buscar jogos ao vivo: %s", exc)
        return "Nao consegui buscar jogos ao vivo agora. Vou tentar no proximo scan."
    await update_last_games(context, games)
    await supabase_sink(context).sync_games(games)

    signals = ranked_signals(
        games,
        settings.min_confidence,
        settings.bankroll,
        settings.unit_percent,
        settings.max_stake_units,
    )
    if not signals:
        if games:
            watchlist = [
                _watch_signal_from_game(game, state, settings)
                for game in games[:8]
            ]
            store.set_candidates(watchlist)
            await supabase_sink(context).sync_signals(watchlist)
            return (
                "Modo radar ativo: sem gatilho forte agora, mas os jogos seguem monitorados.\n\n"
                + candidate_list_text(watchlist)
            )
        return (
            "Nenhum jogo ao vivo real passou nos filtros agora.\n\n"
            f"Busca usada: {scan_scope}.\n"
            "Nenhum pre-live, grade do dia ou resultado fake foi usado neste ciclo."
        )

    signals = [
        prepare_signal(signal, state, settings)
        for signal in signals[:8]
    ]
    for signal in signals:
        signal["scan_scope"] = scan_scope
    store.set_candidates(signals)
    await supabase_sink(context).sync_signals(signals)

    if not auto_pick:
        return candidate_list_text(signals)

    signal = signals[0]
    store.set_active(signal["game"]["game_id"], signal)
    await supabase_sink(context).sync_signal(signal)
    return signal_text(signal)


async def scan_games(
    provider: LiveProvider,
    mode: str = "brazil_first",
    block_esports: bool = True,
) -> tuple[list, str]:
    mode = str(mode or "brazil_first").strip().lower()
    if mode not in {"brazil_first", "world_first", "live_only"}:
        mode = "brazil_first"
    live_games = await provider.get_live_games()
    if block_esports:
        live_games = [
            game
            for game in live_games
            if not is_forbidden_esports_game(_to_dict(game))
        ]
    brazil_live = [game for game in live_games if _is_brazil_priority(game)]
    if mode == "live_only":
        if brazil_live:
            return brazil_live, "Somente ao vivo (Brasil)"
        if live_games:
            return live_games, "Somente ao vivo (Mundo)"
        return [], "somente ao vivo sem jogos"

    if mode == "world_first":
        if live_games:
            return live_games, "Mundo ao vivo"
        if brazil_live:
            return brazil_live, "Brasil ao vivo"
    else:
        if brazil_live:
            return brazil_live, "Brasil ao vivo"
        if live_games:
            return live_games, "Mundo ao vivo"

    if mode == "world_first":
        return [], "mundo ao vivo sem jogos"
    return [], "brasil -> mundo sem jogos ao vivo"


def _is_brazil_priority(game) -> bool:
    return int(getattr(game, "priority", 50) or 50) <= 3


def _has_odds_or_markets(game) -> bool:
    return bool(
        getattr(game, "odds_home", None)
        or getattr(game, "odds_draw", None)
        or getattr(game, "odds_away", None)
        or getattr(game, "markets", None)
    )


def _watch_signal_from_game(game, state, settings: Settings) -> dict:
    signal = analyze_game(
        game,
        settings.min_confidence,
        settings.bankroll,
        settings.unit_percent,
        settings.max_stake_units,
    )
    signal = prepare_signal(signal, state, settings)
    signal["action"] = "AGUARDAR"
    signal["stake_units"] = 0
    signal["stake_value"] = 0
    signal["risk_blocked"] = False
    signal["reason"] = (
        "Modo radar: jogo monitorado sem gatilho forte neste ciclo. "
        "Aguarde confirmacao de ritmo/odds para entrada."
    )
    signal["risk_note"] = (
        str(signal.get("risk_note") or "").strip()
        + " Modo radar ativo."
    ).strip()
    signal["market_recommendations"] = market_recommendations(signal)
    return signal


def prepare_signal(signal: dict, state, settings: Settings) -> dict:
    learning_context = _learning_context(state)
    signal["learning_context"] = learning_context
    signal["market_recommendations"] = market_recommendations(signal)
    if settings.block_esports and is_forbidden_esports_game(signal.get("game", {})):
        signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["stake_value"] = 0
        signal["risk_blocked"] = True
        signal["risk_note"] = "Entrada bloqueada: e-soccer/eFootball/esports nao permitido."
        signal.update(entry_score(signal, learning_context))
        return signal
    signal = apply_risk_controls(
        signal,
        learning_context,
        settings.min_history_for_enter,
        settings.min_edge_to_enter,
        settings.kelly_fraction,
        settings.unit_percent,
        settings.max_stake_units,
    )
    signal = apply_daily_red_stop(signal, state.history or [], settings.daily_red_limit)
    signal["market_recommendations"] = market_recommendations(signal)
    signal.update(entry_score(signal, learning_context))
    return signal


async def refresh_active_signal(context: ContextTypes.DEFAULT_TYPE, state) -> str:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    provider: LiveProvider = context.application.bot_data["provider"]
    store.touch_scan()
    try:
        games = await provider.get_live_games()
    except Exception as exc:
        logger.warning("Falha ao atualizar jogo ativo: %s", exc)
        return "Jogo ativo mantido, mas nao consegui atualizar os dados agora.\n\n" + monitor_text(state.active_signal)
    await update_last_games(context, games)
    await supabase_sink(context).sync_games(games)

    active_id = state.active_game_id
    game = next((item for item in games if item.game_id == active_id), None)
    if not game:
        store.clear_active()
        next_scan = await run_scan(context, auto_pick=False)
        return (
            "O jogo ativo saiu do feed ao vivo. Liberei o scanner para nao travar "
            "sem dados novos.\n\n"
            + next_scan
        )

    signal = analyze_game(
        game,
        settings.min_confidence,
        settings.bankroll,
        settings.unit_percent,
        settings.max_stake_units,
    )
    signal = prepare_signal(signal, state, settings)
    previous = state.active_signal or {}
    for key in (
        "signal_id",
        "created_at",
        "entered",
        "entered_at",
        "entry_market",
        "entry_value",
        "entry_odds",
        "entry_notes",
        "outcome",
    ):
        if key in previous:
            signal[key] = previous[key]
    signal = await refine_signal(
        signal,
        settings.gemini_api_key,
        settings.gemini_model,
        signal.get("learning_context"),
    )
    store.set_active(signal["game"]["game_id"], signal)
    await supabase_sink(context).sync_signal(signal)
    return monitor_text(signal)


async def update_last_games(context: ContextTypes.DEFAULT_TYPE, live_games: list) -> None:
    store: StateStore = context.application.bot_data["store"]
    store.set_last_games([_to_dict(game) for game in live_games])


def _to_dict(item) -> dict:
    if is_dataclass(item):
        return asdict(item)
    return dict(item)


def candidate_list_text(signals: list[dict]) -> str:
    lines = [
        "🔵📡 SCANNER AO VIVO",
        "Escolha um jogo para monitorar de 5 em 5 minutos.",
    ]
    scope = signals[0].get("scan_scope") if signals else None
    if scope:
        lines.append(f"Busca: {scope}.")
    lines.append("")
    for idx, signal in enumerate(signals, start=1):
        game = signal["game"]
        recs = signal.get("market_recommendations") or market_recommendations(signal)
        primary = _primary_recommendation({**signal, "market_recommendations": recs})
        icon = _action_icon(signal.get("action"))
        lines.append(f"{idx}. {icon} {game['home']} x {game['away']}")
        lines.append(f"   ⏱️ {game['minute']}' | Placar {game.get('home_goals', 0)}x{game.get('away_goals', 0)}")
        lines.append(
            f"   🎯 {signal['action']} | Conf. {signal['confidence']}% | "
            f"Score {signal.get('entry_score', '-')}/100 {signal.get('grade', '-')}"
        )
        lines.append(
            f"   📊 {primary.get('market')} | odd "
            f"{primary.get('odds') or signal.get('target_odds') or '-'}"
        )
        lines.append(f"   ➜ {primary.get('entry')}")
        lines.append("")
    lines.append("")
    lines.append("Sem escolha: novo scan automatico em 1 min.")
    return _shorten("\n".join(lines), TELEGRAM_TEXT_LIMIT)


def candidate_menu(state) -> InlineKeyboardMarkup:
    candidates = state.candidate_signals or []
    rows = []
    for idx, signal in enumerate(candidates[:8], start=1):
        game = signal.get("game", {})
        label = f"{idx}. {game.get('home', '?')} x {game.get('away', '?')}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"pick:{idx - 1}")])
    rows.append(
        [
            InlineKeyboardButton("Scan agora", callback_data="scan"),
            InlineKeyboardButton("Status", callback_data="status"),
        ]
    )
    rows.append([InlineKeyboardButton("Menu principal", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def run_system_checkout(
    context: ContextTypes.DEFAULT_TYPE,
    manual: bool = False,
) -> tuple[bool, str]:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    provider: LiveProvider = context.application.bot_data["provider"]
    state = store.load()
    problems: list[str] = []
    warnings: list[str] = []
    details: list[str] = []

    details.append(f"Provider: {provider_label(provider)}")
    details.append(f"Supabase: {_supabase_status(supabase_sink(context))}")
    details.append(f"Chats Telegram: {len(state.chat_ids or [])}")
    details.append(f"Jogo ativo: {'sim' if state.active_signal else 'nao'}")
    details.append(f"Candidatos: {len(state.candidate_signals or [])}")
    details.append(f"Historico: {len(state.history or [])}")
    sim_flag = "on" if settings.auto_simulation_enabled else "off"
    details.append(
        "Simulacao diaria IA: "
        f"{sim_flag} | hora {settings.auto_simulation_hour:02d}:00 ({settings.auto_simulation_timezone})"
    )
    details.append(
        "Ultima simulacao diaria: "
        f"{state.last_auto_simulation_at or 'ainda nao executada'}"
    )
    stop = red_stop_status(state.history or [], settings.daily_red_limit)
    if stop.get("discipline_alert"):
        details.append(
            f"Disciplina de risco: alerta ativo ({stop['red_count']}/{settings.daily_red_limit})"
        )
    else:
        details.append("Disciplina de risco: ok")

    if not settings.telegram_bot_token:
        problems.append("TELEGRAM_BOT_TOKEN ausente.")
    if not state.chat_ids:
        warnings.append("Nenhum chat registrado; envie /start no Telegram para receber alertas.")

    scan_age = _scan_age_minutes(state.last_scan_at)
    details.append(f"Ultimo scan: {scan_age if scan_age is not None else '-'} min")
    max_age = max(35, int(settings.idle_scan_interval_seconds / 60) + 10)
    if scan_age is None:
        warnings.append("Ainda nao ha registro de ultimo scan.")
    elif scan_age > max_age:
        problems.append(f"Scanner atrasado: ultimo scan ha {scan_age} min.")

    sim_age = _scan_age_minutes(state.last_auto_simulation_at)
    if settings.auto_simulation_enabled and state.last_auto_simulation_at and sim_age is not None and sim_age > (36 * 60):
        warnings.append(f"Simulacao diaria sem rodar ha {sim_age} min.")

    try:
        live_games = await asyncio.wait_for(provider.get_live_games(), timeout=35)
        details.append(f"Feed ao vivo: {len(live_games)} jogos")
    except Exception as exc:
        live_games = []
        problems.append(f"Feed ao vivo falhou: {type(exc).__name__}: {exc}")

    if not live_games:
        get_today = getattr(provider, "get_today_games", None)
        if callable(get_today):
            try:
                today_games = await asyncio.wait_for(get_today(), timeout=35)
                details.append(f"Grade do dia: {len(today_games)} jogos")
                if not today_games:
                    warnings.append("Fonte nao retornou jogos ao vivo nem grade do dia.")
            except Exception as exc:
                problems.append(f"Grade do dia falhou: {type(exc).__name__}: {exc}")

    signals = _checkout_signals(state)
    opportunities = paper_opportunities(signals)
    details.append(f"Simulador: {len(opportunities)} oportunidades")
    if signals and not opportunities:
        problems.append("Simulador sem oportunidades mesmo com sinais salvos.")
    if opportunities and max(int(item.get("score") or 0) for item in opportunities) <= 1:
        problems.append("Simulador com score travado em 0/100.")

    details.append("Fontes IA: job dedicado a cada 6h")

    ok = not problems
    title = "🟢 CHECKOUT OK" if ok else "🔴 CHECKOUT COM PROBLEMAS"
    lines = [
        title,
        f"Horario: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "Resumo",
        *[f"- {item}" for item in details],
    ]
    if warnings:
        lines.extend(["", "Avisos", *[f"- {item}" for item in warnings]])
    if problems:
        lines.extend(["", "Problemas", *[f"- {item}" for item in problems]])
        lines.extend(
            [
                "",
                "Para me enviar no Codex:",
                "copie esta mensagem e mande aqui no chat. Eu analiso o erro e corrijo no servidor.",
            ]
        )
    elif manual:
        lines.extend(["", "Tudo essencial respondeu. Scanner, simulador e Telegram estao vivos."])

    return ok, _shorten("\n".join(lines), TELEGRAM_TEXT_LIMIT)


async def scheduled_system_checkout(context: ContextTypes.DEFAULT_TYPE) -> None:
    ok, text = await run_system_checkout(context, manual=False)
    if ok:
        return
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    for chat_id in _notification_chat_ids(settings, state):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=health_menu(),
            )
        except Exception as exc:
            logger.warning("Falha ao enviar checkout para chat %s: %s", chat_id, exc)


async def scheduled_source_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await supabase_sink(context).sync_ai_sources()
    except Exception as exc:
        logger.warning("Falha ao sincronizar fontes especializadas da IA: %s", exc)


def _checkout_signals(state) -> list[dict]:
    signals = []
    if state.active_signal:
        signals.append(state.active_signal)
    signals.extend(state.candidate_signals or [])
    return [signal for signal in signals if isinstance(signal, dict)]


def _scan_age_minutes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return max(0, int(age.total_seconds() // 60))


def _notification_chat_ids(settings: Settings | None, state) -> set[int]:
    chats = {int(chat_id) for chat_id in (state.chat_ids or [])}
    if not settings:
        return chats
    try:
        portal = PortalStore(settings.portal_db_file)
        enabled = set(portal.telegram_enabled_chat_ids())
    except Exception as exc:
        logger.info("Nao consegui carregar preferencias de notificacao: %s", exc)
        return chats
    if enabled:
        chats |= enabled
    return chats


def _simulation_learning_summary(sessions: list[dict]) -> dict:
    recent = [item for item in sessions if isinstance(item, dict)][:30]
    if not recent:
        return {
            "sessions": 0,
            "games": 0,
            "greens": 0,
            "reds": 0,
            "hit_rate": 0.0,
            "profit_units": 0.0,
        }
    games = sum(_safe_int(item.get("total_games"), 0) for item in recent)
    greens = sum(_safe_int(item.get("greens"), 0) for item in recent)
    reds = sum(_safe_int(item.get("reds"), 0) for item in recent)
    profits = round(sum(_safe_float(item.get("profit_units"), 0.0) for item in recent), 2)
    resolved = max(1, greens + reds)
    return {
        "sessions": len(recent),
        "games": games,
        "greens": greens,
        "reds": reds,
        "hit_rate": round((greens / resolved) * 100, 1),
        "profit_units": profits,
    }


def _learning_context(state) -> dict:
    return summarize_history_with_simulation(
        state.history or [],
        state.simulation_sessions or [],
        simulation_weight=0.35,
        max_simulation_rows=240,
    )


def _simulation_signals_from_state(state) -> list[dict]:
    signals = []
    if isinstance(state.active_signal, dict):
        signals.append(state.active_signal)
    for signal in state.candidate_signals or []:
        if isinstance(signal, dict):
            signals.append(signal)
    deduped = []
    seen = set()
    for signal in signals:
        game = signal.get("game") or {}
        key = str(game.get("game_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    return deduped


def _sim_probability(score: int, risk: int, confidence: int, action: str, rng: random.Random) -> float:
    action_bias = {"ENTRAR": 0.08, "AGUARDAR": -0.02, "SEGURAR": -0.05, "SAIR": -0.10}.get(action, 0.0)
    score_component = (score - 50) / 150.0
    confidence_component = (confidence - 50) / 190.0
    risk_component = (50 - risk) / 180.0
    noise = rng.uniform(-0.05, 0.05)
    probability = 0.5 + score_component + confidence_component + risk_component + action_bias + noise
    return max(0.08, min(0.92, probability))


def _simulate_learning_session(
    opportunities: list[dict],
    *,
    total_games: int,
    bankroll_units: float,
    stake_percent: float,
    seed_key: str,
) -> dict:
    games_target = max(30, min(120, _safe_int(total_games, 30)))
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
            "note": "Sem oportunidades ao vivo neste ciclo. Aguardando novo scan.",
        }

    ranked = sorted(
        opportunities,
        key=lambda item: (
            -_safe_int(item.get("score"), 0),
            _safe_int(item.get("risk"), 100),
            str(item.get("match") or ""),
            str(item.get("market") or ""),
        ),
    )
    seed_material = (
        seed_key
        + "|"
        + "|".join(
            f"{item.get('game_id')}:{item.get('market')}:{item.get('selection')}:{item.get('minute')}"
            for item in ranked[:48]
        )
    )
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
    rows: list[dict] = []

    for idx in range(games_target):
        pick = ranked[idx % len(ranked)]
        score = max(1, min(100, _safe_int(pick.get("score"), 1)))
        confidence = max(1, min(100, _safe_int(pick.get("confidence"), score)))
        risk = max(1, min(100, _safe_int(pick.get("risk"), max(1, 100 - score))))
        action = str(pick.get("action") or "").upper()
        odds = _safe_float(pick.get("odds"))
        if odds < 1.2:
            odds = round(max(1.25, min(3.65, 1.38 + ((100 - score) / 170.0) + (risk / 310.0) + rng.uniform(-0.08, 0.22))), 3)
        win_prob = _sim_probability(score, risk, confidence, action, rng)
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
        "note": "Simulacao diaria automatica (paper/live), sem executar aposta real.",
    }


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _simulation_summary_text(session: dict, when_local: datetime, timezone_label: str) -> str:
    return _shorten(
        "\n".join(
            [
                "🧠 SIMULACAO DIARIA IA CONCLUIDA",
                f"Horario local: {when_local.strftime('%d/%m/%Y %H:%M')} ({timezone_label})",
                f"Jogos simulados: {session.get('total_games', 0)}",
                f"Greens: {session.get('greens', 0)} | Reds: {session.get('reds', 0)}",
                f"Hit rate: {session.get('hit_rate', 0)}%",
                f"Lucro: {session.get('profit_units', 0)}u | ROI: {session.get('roi', 0)}%",
                f"Banca final: {session.get('end_bankroll', 0)}u",
                "Resultado salvo no historico de simulacoes da dashboard.",
            ]
        ),
        TELEGRAM_TEXT_LIMIT,
    )


async def run_daily_auto_simulation(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    manual: bool = False,
) -> tuple[bool, str | None]:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    provider: LiveProvider = context.application.bot_data["provider"]

    if not settings.auto_simulation_enabled and not manual:
        return False, None

    timezone_label = settings.auto_simulation_timezone or "America/Sao_Paulo"
    try:
        local_tz = ZoneInfo(timezone_label)
    except Exception:
        timezone_label = "UTC"
        local_tz = timezone.utc
    now_local = datetime.now(local_tz)
    today_key = now_local.date().isoformat()

    state = store.load()
    if not manual:
        if str(state.last_auto_simulation_date or "") == today_key:
            return False, None
        if now_local.hour < settings.auto_simulation_hour:
            return False, None

    games = []
    scan_scope = "sem jogos"
    try:
        games, scan_scope = await scan_games(
            provider,
            state.scan_preference,
            block_esports=settings.block_esports,
        )
    except Exception as exc:
        logger.warning("Simulacao diaria: falha ao buscar jogos ao vivo: %s", exc)

    if games:
        try:
            await update_last_games(context, games)
            await supabase_sink(context).sync_games(games)
        except Exception as exc:
            logger.info("Simulacao diaria: nao consegui atualizar grade/supabase: %s", exc)
    elif not settings.test_mode:
        return False, None

    signals = []
    if games:
        base_signals = ranked_signals(
            games,
            settings.min_confidence,
            settings.bankroll,
            settings.unit_percent,
            settings.max_stake_units,
        )
        if base_signals:
            signals = [prepare_signal(signal, state, settings) for signal in base_signals[:12]]
        else:
            signals = [_watch_signal_from_game(game, state, settings) for game in games[:12]]
    if not signals:
        if settings.test_mode:
            signals = _simulation_signals_from_state(state)
        else:
            return False, None

    opportunities = paper_opportunities(signals)
    if not opportunities:
        return False, None
    session = _simulate_learning_session(
        opportunities,
        total_games=settings.auto_simulation_games,
        bankroll_units=settings.auto_simulation_bankroll,
        stake_percent=settings.auto_simulation_stake_percent,
        seed_key=f"{today_key}|{scan_scope}",
    )
    session["scan_scope"] = scan_scope
    session["trigger"] = "manual" if manual else "daily_auto"
    session["source_games"] = len(games)

    store.add_simulation_session(session)
    store.mark_auto_simulation_run(today_key)
    return True, _simulation_summary_text(session, now_local, timezone_label)


async def scheduled_daily_simulation(context: ContextTypes.DEFAULT_TYPE) -> None:
    ran, text = await run_daily_auto_simulation(context, manual=False)
    if not ran or not text:
        return
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    for chat_id in _notification_chat_ids(settings, state):
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=ia_menu())
        except Exception as exc:
            logger.warning("Falha ao enviar resumo da simulacao diaria para chat %s: %s", chat_id, exc)


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    state, scan_requested = store.consume_scan_request()
    chat_ids = _notification_chat_ids(settings, state)
    idle_interval = settings.idle_scan_interval_seconds
    active_interval = settings.active_scan_interval_seconds
    try:
        idle_interval, active_interval = PortalStore(settings.portal_db_file).notification_scan_preferences(
            settings.idle_scan_interval_seconds,
            settings.active_scan_interval_seconds,
        )
    except Exception as exc:
        logger.info("Nao consegui aplicar preferencias de scanner por usuario: %s", exc)
    interval = (
        active_interval
        if state.active_game_id
        else idle_interval
    )
    context.job_queue.run_once(scheduled_scan, when=interval)
    if not chat_ids and not scan_requested:
        return

    stop = red_stop_status(state.history or [], settings.daily_red_limit)
    if stop.get("discipline_alert"):
        logger.info(
            "Alerta de disciplina ativo: %s/%s (scanner segue ativo).",
            stop["red_count"],
            stop["red_limit"],
        )

    if state.active_game_id and not scan_requested:
        text = await refresh_active_signal(context, state)
    else:
        text = await run_scan(context, auto_pick=False)
    if not chat_ids:
        return
    for chat_id in chat_ids:
        state = store.load()
        reply_markup = active_menu() if state.active_signal else candidate_menu(state)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning("Falha ao enviar alerta para chat %s: %s", chat_id, exc)


async def remember_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        store: StateStore = context.application.bot_data["store"]
        state = store.add_chat(update.effective_chat.id)
        context.application.bot_data["chat_ids"] = set(state.chat_ids or [])
        logger.info("Chat registrado para alertas: %s", update.effective_chat.id)


def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Preencha TELEGRAM_BOT_TOKEN no .env")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(setup_bot_commands)
        .build()
    )
    app.bot_data["settings"] = settings
    app.bot_data["store"] = StateStore(settings.state_file)
    app.bot_data["provider"] = build_provider(settings)
    app.bot_data["supabase"] = SupabaseSink.from_settings(settings)

    app.add_handler(CommandHandler("start", _with_chat_memory(start)))
    app.add_handler(CommandHandler("menu", _with_chat_memory(main_menu_cmd)))
    app.add_handler(CommandHandler("jogos", _with_chat_memory(games_cmd)))
    app.add_handler(CommandHandler("sistema", _with_chat_memory(health_cmd)))
    app.add_handler(CommandHandler("checkout", _with_chat_memory(checkout_cmd)))
    app.add_handler(CommandHandler("relatorios", _with_chat_memory(reports_cmd)))
    app.add_handler(CommandHandler("oferta", _with_chat_memory(offer_cmd)))
    app.add_handler(CommandHandler("chatid", _with_chat_memory(chatid_cmd)))
    app.add_handler(CommandHandler("iamenu", _with_chat_memory(ia_menu_cmd)))
    app.add_handler(CommandHandler("scan", _with_chat_memory(scan)))
    app.add_handler(CommandHandler("status", _with_chat_memory(status)))
    app.add_handler(CommandHandler("stop", _with_chat_memory(stop)))
    app.add_handler(CommandHandler("test", _with_chat_memory(test)))
    app.add_handler(CommandHandler("dashboard", _with_chat_memory(dashboard_cmd)))
    app.add_handler(CommandHandler("stats", _with_chat_memory(stats_cmd)))
    app.add_handler(CommandHandler("aprendizado", _with_chat_memory(learning_cmd)))
    app.add_handler(CommandHandler("suporte", _with_chat_memory(support_cmd)))
    app.add_handler(CommandHandler("ia", _with_chat_memory(ia_cmd)))
    app.add_handler(CommandHandler("entrada", _with_chat_memory(entry_cmd)))
    app.add_handler(CommandHandler("importar", _with_chat_memory(import_cmd)))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _with_chat_memory(pending_text)))
    app.add_error_handler(on_error)

    app.job_queue.run_once(scheduled_scan, when=10)
    app.job_queue.run_repeating(
        scheduled_system_checkout,
        interval=SYSTEM_CHECK_INTERVAL_SECONDS,
        first=60,
        name="system_checkout",
    )
    app.job_queue.run_repeating(
        scheduled_source_sync,
        interval=SOURCE_SYNC_INTERVAL_SECONDS,
        first=120,
        name="source_sync",
    )
    app.job_queue.run_repeating(
        scheduled_daily_simulation,
        interval=DAILY_SIMULATION_CHECK_INTERVAL_SECONDS,
        first=180,
        name="daily_simulation",
    )
    logger.info("BetSignal Cloud iniciado em polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _with_chat_memory(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await remember_chat(update, context)
        await handler(update, context)

    return wrapper


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error_text = "".join(traceback.format_exception(context.error))
    logger.error("Erro no bot: %s", error_text)
    store: StateStore | None = context.application.bot_data.get("store")
    if not store:
        return
    settings: Settings = context.application.bot_data.get("settings")
    state = store.load()
    text = _shorten(
        "🔴 ERRO NO BOT\n\n"
        "O BetSignal capturou uma excecao no Telegram.\n\n"
        "Resumo para enviar ao Codex:\n"
        + error_text,
        TELEGRAM_TEXT_LIMIT,
    )
    for chat_id in _notification_chat_ids(settings, state):
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=health_menu())
        except Exception as exc:
            logger.warning("Falha ao notificar erro para chat %s: %s", chat_id, exc)


async def setup_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("menu", "abrir menu principal"),
            BotCommand("jogos", "abrir area de jogos"),
            BotCommand("entrada", "registrar mercado, valor e odd"),
            BotCommand("ia", "perguntar para a IA"),
            BotCommand("importar", "importar historico manual"),
            BotCommand("chatid", "mostrar id para vincular notificacoes"),
            BotCommand("suporte", "diagnostico do sistema"),
            BotCommand("checkout", "verificar scanner, simulador e fontes"),
            BotCommand("oferta", "apresentacao comercial e planos"),
        ]
    )
    await app.bot_data["supabase"].sync_ai_sources()


def _home_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    status_label = "com jogo ativo" if state.active_signal else "scanner livre"
    return "\n".join(
        [
            "BetSignal Cloud online.",
            f"Status: {status_label}.",
            "",
            "Escolha uma area:",
            "Jogos: scan, escolha, entrada e resultado.",
            "I.A.: contexto, aprendizado e perguntas com /ia.",
            "Relatorios: dashboard, eficiencia, historico e oferta.",
            "Saude: teste, diagnostico e suporte.",
        ]
    )


def _games_text(state) -> str:
    if state.active_signal:
        game = state.active_signal.get("game", {})
        return "\n".join(
            [
                "Menu Jogos",
                f"Ativo: {game.get('home', '?')} x {game.get('away', '?')}",
                f"Minuto: {game.get('minute', '-')} | Entrada: {_entry_status(state.active_signal)}",
                "",
                "Use Entrei quando fizer a aposta. Depois marque Green, Red ou Anular para alimentar o aprendizado.",
            ]
        )
    return "\n".join(
        [
            "Menu Jogos",
            "🔵 Nenhum jogo ativo.",
            "Use Scan agora para listar partidas ao vivo. Sem jogo ativo o ciclo e de 1 em 1 min; com jogo escolhido vira 5 em 5 min.",
        ]
    )


def _ia_menu_text() -> str:
    return "\n".join(
        [
            "Menu I.A.",
            "Use /ia seguido da sua pergunta.",
            "",
            "Exemplos:",
            "/ia devo manter a entrada nesse jogo?",
            "/ia onde esta melhor, gols, escanteios ou asiatica?",
            "/ia qual risco dessa odd agora?",
        ]
    )


def _ia_help_text() -> str:
    return "\n".join(
        [
            "Como falar com a I.A.",
            "Digite /ia e a pergunta na mesma mensagem.",
            "",
            "Boas perguntas:",
            "- qual entrada tem mais valor agora?",
            "- devo entrar em gols, escanteios ou asiatica?",
            "- depois que entrei, devo manter ou sair?",
            "- essa odd compensa pelo risco?",
        ]
    )


def _import_prompt_text() -> str:
    return "\n".join(
        [
            "Cole aqui seu historico de apostas.",
            "",
            "Importacao conservadora:",
            "- linha com Perdida vira red;",
            "- Aposta Encerrada vira anulada/void;",
            "- sem resultado claro fica aberta e nao conta como green.",
        ]
    )


def _paper_text(state) -> str:
    signals = []
    if state.active_signal:
        signals.append(state.active_signal)
    signals.extend(state.candidate_signals or [])
    best = best_paper_entry(signals)
    opportunities = paper_opportunities(signals)
    if not best:
        today_count = len(state.last_games or [])
        return "\n".join(
            [
                "🔵🧠 SIMULADOR EM TEMPO REAL",
                "",
                "Ainda nao tenho uma entrada simulada com sinal ao vivo.",
                f"Jogos do dia no radar: {today_count}",
                "",
                "📌 Proximo passo:",
                "Rode Jogos > Scan agora para gerar candidatos ao vivo.",
                "",
                "Quando aparecer sinal, eu vou mostrar:",
                "🎯 mercado | 💰 odd | 🧮 stake simulada | 📈 retorno | 🛡️ risco",
            ]
        )
    action_color = _action_icon(best["action"])
    stake = 1.0
    odds = best.get("odds")
    potential_return = round(stake * float(odds), 2) if odds else None
    potential_profit = round(potential_return - stake, 2) if potential_return else None
    lines = [
        "🧠⚡ SIMULADOR EM TEMPO REAL",
        "",
        f"{action_color} BILHETE SIMULADO",
        f"📌 Jogo: {best['match']}",
        f"⏱️ {best['minute']}' | Placar {best['scoreline']}",
        "",
        "🎫 COMO FICARIA A APOSTA",
        f"📊 Mercado: {best['market']}",
        f"🎯 Entrada: {best['selection']} {best['line']}",
        f"💰 Odd: {odds or 'sem odd na fonte atual'}",
        f"🧮 Stake simulada: {stake}u",
        f"📈 Retorno potencial: {potential_return if potential_return else '-'}u",
        f"✅ Lucro potencial: {potential_profit if potential_profit else '-'}u",
        "",
        "🧠 DECISAO DA IA",
        f"Acao: {best['action']} | Score {best['score']}/100 | Risco {best['risk']}/100",
        f"🧠 Motivo: {best['reason']}",
        "",
        "📈 TOP MERCADOS",
    ]
    for item in opportunities[:8]:
        lines.append(
            f"{_action_icon(item['action'])} {item['market']} | {item['selection']} {item['line']} | "
            f"odd {item.get('odds') or '-'} | {item['match']}"
        )
    lines.append("")
    lines.append("🛡️ Simulacao informativa. A entrada real continua manual.")
    return _shorten("\n".join(lines), TELEGRAM_TEXT_LIMIT)


def _reports_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    learning = _learning_context(state)
    backtest = learning.get("backtest") or {}
    return "\n".join(
        [
            "Menu Relatorios",
            f"Sinais no historico: {len(state.history or [])}",
            f"Amostra fechada: {learning.get('sample_size', 0)}",
            f"Lucro: {learning.get('profit_units', 0)}u | ROI: {learning.get('roi_units', 0)}%",
            f"Backtest: {backtest.get('profit_units', 0)}u | DD max: {backtest.get('max_drawdown_units', 0)}u",
            "",
            "Abra a dashboard ou veja eficiencia/aprendizado pelos botoes.",
        ]
    )


def _offer_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings: Settings = context.application.bot_data["settings"]
    starter = f"R$ {settings.plan_starter_price_brl:.0f}/mes"
    pro = f"R$ {settings.plan_pro_price_brl:.0f}/mes"
    team = f"R$ {settings.plan_team_price_brl:.0f}/mes"
    whatsapp = settings.sales_whatsapp or "-"
    email = settings.sales_email or "-"
    return "\n".join(
        [
            f"{settings.product_name} | Oferta Comercial",
            settings.product_tagline,
            "",
            "Dor que resolvemos:",
            "- entrada confusa sem criterio;",
            "- falta de rotina e disciplina de risco;",
            "- operacao sem historico confiavel e sem aprendizado.",
            "",
            "Planos:",
            f"- Starter: {starter} (scanner + telegram + dashboard);",
            f"- Pro: {pro} (starter + memoria IA + suporte tecnico);",
            f"- Team: {team} (pro + operacao guiada para equipe).",
            "",
            "Contato comercial:",
            f"- Site: {settings.website_url or '-'}",
            f"- WhatsApp: {whatsapp}",
            f"- Email: {email}",
        ]
    )


def _health_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    settings: Settings = context.application.bot_data["settings"]
    stop = red_stop_status(state.history or [], settings.daily_red_limit)
    interval = (
        settings.active_scan_interval_seconds
        if state.active_game_id
        else settings.idle_scan_interval_seconds
    )
    supabase = supabase_sink(context)
    return "\n".join(
        [
            "Menu Saude do Sistema",
            f"Provider: {provider_label(context.application.bot_data['provider'])}",
            f"Supabase: {_supabase_status(supabase)}",
            f"Chats registrados: {len(state.chat_ids or [])}",
            f"Jogo ativo: {'sim' if state.active_signal else 'nao'}",
            f"Simulacao diaria: {'on' if settings.auto_simulation_enabled else 'off'} "
            f"({settings.auto_simulation_hour:02d}:00 {settings.auto_simulation_timezone})",
            f"Ultima simulacao: {state.last_auto_simulation_at or '-'}",
            f"Reds no ciclo: {stop['red_count']}/{settings.daily_red_limit}",
            f"Disciplina: {'alerta ate ' + stop['unlock_at'].strftime('%d/%m %H:%M') if stop.get('discipline_alert') else 'ok'}",
            f"Proximo ciclo padrao: {int(interval / 60)} min",
            "",
            "Use Diagnostico se algo parar de chegar.",
        ]
    )


def _support_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    supabase = supabase_sink(context)
    stop = red_stop_status(state.history or [], settings.daily_red_limit)
    return "\n".join(
        [
            "Diagnostico BetSignal Cloud",
            f"Provider: {provider_label(context.application.bot_data['provider'])}",
            f"Supabase: {_supabase_status(supabase)}",
            f"Chats salvos: {len(state.chat_ids or [])}",
            f"Jogo ativo: {'sim' if state.active_signal else 'nao'}",
            f"Simulacao diaria: {'on' if settings.auto_simulation_enabled else 'off'} "
            f"({settings.auto_simulation_hour:02d}:00 {settings.auto_simulation_timezone})",
            f"Ultima simulacao: {state.last_auto_simulation_at or '-'}",
            f"Reds no ciclo: {stop['red_count']}/{settings.daily_red_limit}",
            f"Disciplina: {'alerta ate ' + stop['unlock_at'].strftime('%d/%m %H:%M') if stop.get('discipline_alert') else 'ok'}",
            f"Historico: {len(state.history or [])} sinais",
            f"Dashboard: {settings.dashboard_domains}",
            settings.support_note,
        ]
    )


def _supabase_status(supabase: SupabaseSink) -> str:
    if not supabase.enabled:
        return "nao configurado"
    if supabase.available:
        return "ativo"
    return f"pausado ({supabase.disabled_reason or 'aguardando nova tentativa'})"


def _dashboard_text(settings: Settings) -> str:
    links = []
    for item in settings.dashboard_domains.split(","):
        base = item.strip().rstrip("/")
        if not base:
            continue
        links.append(f"- Landing: {base}/")
        links.append(f"- Portal cliente: {base}/login")
        links.append(f"- Dashboard trade: {base}/dashboard")
    return "Acessos BetSignal Cloud:\n" + "\n".join(links)


def _stats_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    learning = _learning_context(state)
    overall = learning.get("overall", {})
    backtest = learning.get("backtest") or {}
    lines = [
        "Eficiencia da IA",
        f"Amostra fechada: {learning.get('sample_size', 0)} sinais",
        f"Acerto geral: {overall.get('hit_rate', 0)}%",
        f"Greens: {overall.get('wins', 0)} | Reds: {overall.get('losses', 0)}",
        f"Lucro em unidades: {learning.get('profit_units', 0)}u",
        f"ROI por unidade: {learning.get('roi_units', 0)}%",
        f"Brier score: {learning.get('brier_score') or '-'}",
        f"Backtest: {backtest.get('profit_units', 0)}u | DD max: {backtest.get('max_drawdown_units', 0)}u",
    ]
    active = state.active_signal or {}
    if active:
        lines.append(f"Qualidade do jogo ativo: {active.get('data_quality', '-')}%")
        lines.append(
            f"Score jogo ativo: {active.get('entry_score', '-')} / 100 | Grau {active.get('grade', '-')}"
        )
        lines.append(
            "Notas: " + ", ".join(active.get("data_quality_notes") or ["sem notas"])
        )
    by_league = learning.get("by_league") or []
    if by_league:
        best = by_league[0]
        lines.append(
            f"Melhor liga: {best['name']} ({best['hit_rate']}% em {best['total']} sinais)"
        )
    else:
        lines.append("Ainda falta historico por liga. Marque Green/Red nos sinais.")
    by_market = learning.get("by_market") or []
    if by_market:
        best_market = by_market[0]
        lines.append(
            f"Melhor mercado: {best_market['name']} ({best_market['hit_rate']}% | {best_market['profit_units']}u)"
        )
    lines.append("Aprendizado melhora conforme voce marca os resultados.")
    return "\n".join(lines)


def _learning_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    store: StateStore = context.application.bot_data["store"]
    state = store.load()
    learning = _learning_context(state)
    sample = int(learning.get("sample_size") or 0)
    maturity = "baixa" if sample < 30 else "media" if sample < 100 else "alta"
    backtest = learning.get("backtest") or {}
    fast = learning.get("fast_learning") or {}
    recent_5 = fast.get("recent_5") or {}
    recent_10 = fast.get("recent_10") or {}
    lines = [
        "Relatorio de Aprendizado da IA",
        f"Maturidade: {maturity}",
        f"Aprendizado rapido: modo {fast.get('mode', 'neutro')} | momentum {fast.get('momentum_score', 50)}/100",
        f"Ultimos 5: {recent_5.get('wins', 0)} green / {recent_5.get('losses', 0)} red ({recent_5.get('hit_rate', 0)}%)",
        f"Ultimos 10: {recent_10.get('wins', 0)} green / {recent_10.get('losses', 0)} red ({recent_10.get('hit_rate', 0)}%)",
        f"Amostra fechada: {sample}/30 minimo para permitir ENTRAR",
        f"Brier score: {learning.get('brier_score') or '-'}",
        f"Lucro: {learning.get('profit_units', 0)}u | ROI: {learning.get('roi_units', 0)}%",
        f"Backtest: {backtest.get('profit_units', 0)}u | Drawdown max: {backtest.get('max_drawdown_units', 0)}u",
    ]
    active = state.active_signal or {}
    if active:
        lines.extend(
            [
                "",
                "Jogo ativo",
                f"Qualidade dos dados: {active.get('data_quality', '-')}%",
                f"Score IA: {active.get('entry_score', '-')} / 100 | Grau {active.get('grade', '-')}",
                "Notas: " + ", ".join(active.get("data_quality_notes") or ["sem notas"]),
                f"Acao atual: {active.get('action')}",
            ]
        )
    for title, key in (
        ("Melhores ligas", "by_league"),
        ("Melhores times", "by_team"),
        ("Melhores mercados", "by_market"),
    ):
        rows = learning.get(key) or []
        if rows:
            lines.append("")
            lines.append(title)
            for row in rows[:3]:
                lines.append(
                    f"- {row['name']}: {row['hit_rate']}% | {row['profit_units']}u | {row['total']} sinais"
                )
    for title, key in (
        ("Quentes agora", "hot_markets"),
        ("Frios agora", "cold_markets"),
        ("Times frios", "cold_teams"),
    ):
        rows = fast.get(key) or []
        if rows:
            lines.append("")
            lines.append(title)
            for row in rows[:3]:
                lines.append(
                    f"- {row['name']}: {row['wins']}G/{row['losses']}R | {row['profit_units']}u | confianca {row['confidence']}"
                )
    lines.extend(
        [
            "",
            "Para ficar mais inteligente:",
            "- marque Green/Red/Anular sempre;",
            "- informe mercado, valor e odd real em cada entrada;",
            "- priorize jogos com odds completas;",
            "- use /ia para tirar duvidas antes de expor banca;",
            "- quando possivel, configurar API-Football melhora cobertura de mercados.",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
