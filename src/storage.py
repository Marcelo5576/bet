from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from typing import Any


@dataclass
class BotState:
    active_game_id: str | None = None
    active_signal: dict[str, Any] | None = None
    last_scan_at: str | None = None
    chat_ids: list[int] | None = None
    history: list[dict[str, Any]] | None = None
    candidate_signals: list[dict[str, Any]] | None = None
    last_games: list[dict[str, Any]] | None = None
    pregame_watchlist: list[dict[str, Any]] | None = None
    simulation_sessions: list[dict[str, Any]] | None = None
    last_auto_simulation_date: str | None = None
    last_auto_simulation_at: str | None = None
    scan_preference: str = "brazil_first"
    risk_profile: str = "moderado"
    scan_requested_at: str | None = None
    pregame_last_scan_at: str | None = None


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            raw = self._normalize(raw)
            return BotState(**raw)
        except Exception:
            backup = self.path.with_suffix(".broken.json")
            self.path.replace(backup)
            return BotState()

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        preference = str(raw.get("scan_preference") or "brazil_first").strip().lower()
        if preference not in {"brazil_first", "world_first", "live_only"}:
            preference = "brazil_first"
        raw["scan_preference"] = preference
        risk_profile = str(raw.get("risk_profile") or "moderado").strip().lower()
        if risk_profile not in {"conservador", "moderado", "agressivo"}:
            risk_profile = "moderado"
        raw["risk_profile"] = risk_profile
        if raw.get("scan_requested_at") is not None:
            raw["scan_requested_at"] = str(raw.get("scan_requested_at"))
        sessions = raw.get("simulation_sessions")
        if not isinstance(sessions, list):
            raw["simulation_sessions"] = []
        else:
            raw["simulation_sessions"] = [
                item for item in sessions if isinstance(item, dict)
            ][:120]
        watchlist = raw.get("pregame_watchlist")
        if not isinstance(watchlist, list):
            raw["pregame_watchlist"] = []
        else:
            raw["pregame_watchlist"] = [
                item for item in watchlist if isinstance(item, dict)
            ][:24]
        if raw.get("last_auto_simulation_date") is not None:
            raw["last_auto_simulation_date"] = str(raw.get("last_auto_simulation_date"))
        if raw.get("last_auto_simulation_at") is not None:
            raw["last_auto_simulation_at"] = str(raw.get("last_auto_simulation_at"))
        if raw.get("pregame_last_scan_at") is not None:
            raw["pregame_last_scan_at"] = str(raw.get("pregame_last_scan_at"))

        active_signal = raw.get("active_signal")
        if isinstance(active_signal, dict):
            active_signal = _freeze_signal_memory(active_signal)
            raw["active_signal"] = active_signal
            active_signal.setdefault("signal_id", uuid4().hex)
            active_signal.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            active_signal.setdefault("outcome", "open")
            history = list(raw.get("history") or [])
            if not any(
                item.get("signal_id") == active_signal["signal_id"]
                for item in history
                if isinstance(item, dict)
            ):
                history.insert(0, active_signal)
            raw["history"] = history[:500]
        return raw

    def save(self, state: BotState) -> None:
        self.path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def touch_scan(self) -> BotState:
        state = self.load()
        state.last_scan_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state

    def set_active(self, game_id: str, signal: dict[str, Any]) -> BotState:
        state = self.load()
        signal = _freeze_signal_memory(signal)
        signal.setdefault("signal_id", uuid4().hex)
        signal.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        signal.setdefault("outcome", "open")
        state.active_game_id = game_id
        state.active_signal = signal
        history = list(state.history or [])
        if not any(item.get("signal_id") == signal["signal_id"] for item in history):
            history.insert(0, signal)
        state.history = history[:500]
        self.save(state)
        return state

    def set_candidates(self, signals: list[dict[str, Any]]) -> BotState:
        state = self.load()
        state.candidate_signals = signals[:10]
        state.last_scan_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state

    def set_last_games(self, games: list[dict[str, Any]]) -> BotState:
        state = self.load()
        state.last_games = games[:300]
        state.last_scan_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state

    def set_pregame_watchlist(self, games: list[dict[str, Any]]) -> BotState:
        state = self.load()
        state.pregame_watchlist = games[:24]
        state.pregame_last_scan_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state

    def clear_scanner_cache(self) -> BotState:
        state = self.load()
        state.candidate_signals = []
        state.last_games = []
        state.pregame_watchlist = []
        state.last_scan_at = datetime.now(timezone.utc).isoformat()
        state.pregame_last_scan_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state

    def set_scan_preference(self, mode: str) -> BotState:
        state = self.load()
        clean = str(mode or "").strip().lower()
        if clean not in {"brazil_first", "world_first", "live_only"}:
            clean = "brazil_first"
        state.scan_preference = clean
        self.save(state)
        return state

    def set_risk_profile(self, profile: str) -> BotState:
        state = self.load()
        clean = str(profile or "").strip().lower()
        if clean not in {"conservador", "moderado", "agressivo"}:
            clean = "moderado"
        state.risk_profile = clean
        self.save(state)
        return state

    def request_scan_now(self) -> BotState:
        state = self.load()
        state.scan_requested_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state

    def consume_scan_request(self) -> tuple[BotState, bool]:
        state = self.load()
        requested = bool(state.scan_requested_at)
        if requested:
            state.scan_requested_at = None
            self.save(state)
        return state, requested

    def choose_candidate(self, index: int) -> BotState:
        state = self.load()
        candidates = state.candidate_signals or []
        if index < 0 or index >= len(candidates):
            return state
        signal = _freeze_signal_memory(candidates[index])
        signal.setdefault("signal_id", uuid4().hex)
        signal.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        signal.setdefault("outcome", "open")
        state.active_game_id = signal["game"]["game_id"]
        state.active_signal = signal
        history = list(state.history or [])
        if not any(item.get("signal_id") == signal["signal_id"] for item in history):
            history.insert(0, signal)
        state.history = history[:500]
        self.save(state)
        return state

    def clear_active(self) -> BotState:
        state = self.load()
        state.active_game_id = None
        state.active_signal = None
        self.save(state)
        return state

    def add_chat(self, chat_id: int) -> BotState:
        state = self.load()
        chat_ids = set(state.chat_ids or [])
        chat_ids.add(chat_id)
        state.chat_ids = sorted(chat_ids)
        self.save(state)
        return state

    def mark_active_outcome(self, outcome: str) -> BotState:
        state = self.load()
        if not state.active_signal:
            return state

        finished_at = datetime.now(timezone.utc).isoformat()
        signal_id = state.active_signal.get("signal_id")
        state.active_signal["outcome"] = outcome
        state.active_signal["finished_at"] = finished_at
        state.active_signal["profit_units"] = _profit_units(state.active_signal, outcome)
        state.active_signal.update(_auto_review(state.active_signal, outcome))
        state.active_signal = _freeze_signal_memory(state.active_signal)

        history = list(state.history or [])
        for item in history:
            if item.get("signal_id") == signal_id:
                item["outcome"] = outcome
                item["finished_at"] = finished_at
                item["profit_units"] = state.active_signal["profit_units"]
                item.update(_auto_review(item, outcome))
                item.update(_freeze_signal_memory(item))
                break
        else:
            history.insert(0, state.active_signal)

        state.history = history[:500]
        state.active_game_id = None
        state.active_signal = None
        self.save(state)
        return state

    def mark_history_outcome(self, signal_id: str, outcome: str) -> tuple[BotState, dict[str, Any] | None]:
        state = self.load()
        finished_at = datetime.now(timezone.utc).isoformat()
        history = list(state.history or [])
        updated: dict[str, Any] | None = None

        for item in history:
            if str(item.get("signal_id")) != str(signal_id):
                continue
            item["outcome"] = outcome
            item["finished_at"] = finished_at
            item["profit_units"] = _profit_units(item, outcome)
            if outcome == "win" and item.get("profit_value") is None:
                entry_value = item.get("entry_value") or item.get("stake_value")
                odds = item.get("entry_odds") or item.get("target_odds")
                if entry_value is not None and odds:
                    item["profit_value"] = round(float(entry_value) * (float(odds) - 1), 2)
            elif outcome == "loss":
                item["profit_value"] = -float(item.get("entry_value") or item.get("stake_value") or 0)
            item.update(_auto_review(item, outcome))
            item.update(_freeze_signal_memory(item))
            updated = item
            break

        if updated and state.active_signal and str(state.active_signal.get("signal_id")) == str(signal_id):
            state.active_signal = None
            state.active_game_id = None

        state.history = history[:500]
        self.save(state)
        return state, updated

    def mark_entry(self, entered: bool) -> BotState:
        state = self.load()
        if not state.active_signal:
            return state

        now = datetime.now(timezone.utc).isoformat()
        state.active_signal["entered"] = entered
        state.active_signal["entered_at"] = now if entered else None
        signal_id = state.active_signal.get("signal_id")
        state.active_signal = _freeze_signal_memory(state.active_signal)

        history = list(state.history or [])
        for item in history:
            if item.get("signal_id") == signal_id:
                item["entered"] = entered
                item["entered_at"] = state.active_signal["entered_at"]
                item.update(_freeze_signal_memory(item))
                break

        state.history = history[:500]
        self.save(state)
        return state

    def mark_entry_details(
        self,
        market: str,
        amount: float | None = None,
        odds: float | None = None,
        notes: str | None = None,
    ) -> BotState:
        state = self.load()
        if not state.active_signal:
            return state

        now = datetime.now(timezone.utc).isoformat()
        details = {
            "entry_market": market.strip(),
            "entry_value": amount,
            "entry_odds": odds,
            "entry_notes": notes.strip() if notes else None,
            "entered": True,
            "entered_at": state.active_signal.get("entered_at") or now,
        }
        state.active_signal.update(details)
        signal_id = state.active_signal.get("signal_id")
        state.active_signal = _freeze_signal_memory(state.active_signal)

        history = list(state.history or [])
        for item in history:
            if item.get("signal_id") == signal_id:
                item.update(details)
                item.update(_freeze_signal_memory(item))
                break

        state.history = history[:500]
        self.save(state)
        return state

    def add_history_records(self, records: list[dict[str, Any]]) -> BotState:
        state = self.load()
        history = list(state.history or [])
        seen = {item.get("signal_id") for item in history if isinstance(item, dict)}
        for record in records:
            if record.get("signal_id") not in seen:
                history.insert(0, _freeze_signal_memory(record))
                seen.add(record.get("signal_id"))
        state.history = history[:500]
        self.save(state)
        return state

    def update_history_value(
        self,
        signal_id: str,
        entry_value: float | None = None,
        entry_odds: float | None = None,
        profit_value: float | None = None,
    ) -> BotState:
        state = self.load()
        history = list(state.history or [])
        updated: dict[str, Any] | None = None
        for item in history:
            if str(item.get("signal_id")) != str(signal_id):
                continue
            if entry_value is not None:
                item["entry_value"] = entry_value
                item["stake_value"] = entry_value
            if entry_odds is not None:
                item["entry_odds"] = entry_odds
                item["target_odds"] = entry_odds
            if profit_value is not None:
                item["profit_value"] = profit_value
            elif item.get("outcome") == "win" and entry_value is not None and entry_odds:
                item["profit_value"] = round(entry_value * (entry_odds - 1), 2)
            item["value_updated_at"] = datetime.now(timezone.utc).isoformat()
            item.update(_freeze_signal_memory(item))
            updated = item
            break

        if updated and state.active_signal and str(state.active_signal.get("signal_id")) == str(signal_id):
            state.active_signal.update(updated)

        state.history = history[:500]
        self.save(state)
        return state

    def delete_history_record(self, signal_id: str) -> tuple[BotState, bool]:
        state = self.load()
        history = list(state.history or [])
        before = len(history)
        history = [
            item
            for item in history
            if str(item.get("signal_id")) != str(signal_id)
        ]
        deleted = len(history) != before

        if (
            state.active_signal
            and str(state.active_signal.get("signal_id")) == str(signal_id)
        ):
            state.active_signal = None
            state.active_game_id = None
            deleted = True

        state.history = history[:500]
        self.save(state)
        return state, deleted

    def add_simulation_session(self, session: dict[str, Any]) -> BotState:
        state = self.load()
        sessions = list(state.simulation_sessions or [])
        record = dict(session or {})
        record.setdefault("session_id", uuid4().hex)
        record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        rows = record.get("rows")
        if isinstance(rows, list):
            record["rows"] = [item for item in rows if isinstance(item, dict)][:60]
        sessions.insert(0, record)
        state.simulation_sessions = sessions[:120]
        self.save(state)
        return state

    def mark_auto_simulation_run(self, date_key: str) -> BotState:
        state = self.load()
        state.last_auto_simulation_date = str(date_key)
        state.last_auto_simulation_at = datetime.now(timezone.utc).isoformat()
        self.save(state)
        return state


def _profit_units(signal: dict[str, Any], outcome: str) -> float:
    stake = float(signal.get("stake_units") or 0)
    odds = signal.get("target_odds")
    if outcome == "win" and odds:
        return round(stake * (float(odds) - 1), 2)
    if outcome == "loss":
        return round(-stake, 2)
    return 0


def _freeze_signal_memory(signal: dict[str, Any]) -> dict[str, Any]:
    record = dict(signal or {})
    game = _to_dict(record.get("game"))
    brain = _to_dict(record.get("brain"))
    facts = _to_dict(brain.get("facts"))
    best_skill = _to_dict(brain.get("best_skill"))
    home_goals = _safe_int(game.get("home_goals"))
    away_goals = _safe_int(game.get("away_goals"))
    estimated_probability = max(
        _safe_float(record.get("estimated_probability")) or 0.0,
        (_safe_float(record.get("confidence")) or 0.0) / 100.0,
        (_safe_float(best_skill.get("confidence")) or 0.0) / 100.0,
    )
    record.update(
        {
            "match_id": str(game.get("game_id") or record.get("match_id") or ""),
            "league_name": str(game.get("division") or game.get("league") or record.get("league_name") or ""),
            "captured_minute": _safe_int(game.get("minute")),
            "captured_score": f"{home_goals}x{away_goals}",
            "home_team": str(game.get("home") or record.get("home_team") or ""),
            "away_team": str(game.get("away") or record.get("away_team") or ""),
            "market_name": str(record.get("market") or record.get("market_name") or ""),
            "selection_name": str(record.get("selection") or record.get("team") or record.get("selection_name") or ""),
            "estimated_probability": round(estimated_probability, 4),
            "implied_probability": _safe_float(record.get("implied_probability")),
            "expected_value": _safe_float(record.get("expected_value")),
            "confidence_score": _safe_float(record.get("confidence_score")),
            "final_score": _safe_float(record.get("final_score")),
            "recommendation": str(record.get("recommendation") or ""),
            "entry_allowed": bool(record.get("entry_allowed")),
            "risk_level": str(record.get("risk_level") or ""),
            "decision_reasons": list(record.get("decision_reasons") or []),
            "ai_explanation": str(record.get("ai_explanation") or ""),
            "market_category": str(record.get("market_category") or ""),
            "risk_profile": str(record.get("risk_profile") or ""),
            "effective_risk_profile": str(record.get("effective_risk_profile") or ""),
            "decision_entry": str(record.get("action") or record.get("decision_entry") or ""),
            "decision_class": str(record.get("decision_class") or record.get("decision_entry") or ""),
            "odd_house": _safe_float(record.get("target_odds")),
            "odd_fair": _safe_float(record.get("fair_odds")),
            "xg_home": _safe_float(facts.get("xg_home")) if facts.get("xg_home") is not None else _safe_float(game.get("xg_home")),
            "xg_away": _safe_float(facts.get("xg_away")) if facts.get("xg_away") is not None else _safe_float(game.get("xg_away")),
            "shots_home": _safe_int(facts.get("shots_home") if facts.get("shots_home") is not None else game.get("home_shots")),
            "shots_away": _safe_int(facts.get("shots_away") if facts.get("shots_away") is not None else game.get("away_shots")),
            "shots_on_home": _safe_int(facts.get("shots_on_home") if facts.get("shots_on_home") is not None else game.get("home_shots_on")),
            "shots_on_away": _safe_int(facts.get("shots_on_away") if facts.get("shots_on_away") is not None else game.get("away_shots_on")),
            "dangerous_attacks_home": _safe_int(facts.get("dangerous_attacks_home") if facts.get("dangerous_attacks_home") is not None else game.get("home_pressure")),
            "dangerous_attacks_away": _safe_int(facts.get("dangerous_attacks_away") if facts.get("dangerous_attacks_away") is not None else game.get("away_pressure")),
            "corners_home": _safe_int(facts.get("corners_home") if facts.get("corners_home") is not None else game.get("home_corners")),
            "corners_away": _safe_int(facts.get("corners_away") if facts.get("corners_away") is not None else game.get("away_corners")),
            "red_cards_total": _safe_int(facts.get("red_home")) + _safe_int(facts.get("red_away")),
            "brain_sample_size": _safe_int(brain.get("sample_size")),
            "brain_momentum_score": _safe_float(brain.get("momentum_score")),
            "brain_risk_score": _safe_float(brain.get("risk_score")),
            "brain_best_skill": str(best_skill.get("name") or record.get("brain_best_skill") or ""),
            "brain_best_market": str(best_skill.get("market") or record.get("brain_market") or ""),
            "brain_best_decision": str(best_skill.get("decision") or record.get("brain_decision") or ""),
            "brain_best_confidence": _safe_int(best_skill.get("confidence") if best_skill else record.get("brain_confidence")),
            "brain_best_reason": str(best_skill.get("reason") or record.get("brain_reason") or ""),
        }
    )
    if record.get("review_reason") and not record.get("error_reason"):
        record["error_reason"] = record.get("review_reason")
    return record


def _auto_review(signal: dict[str, Any], outcome: str) -> dict[str, Any]:
    action = str(signal.get("action") or "").upper()
    edge = _safe_float(signal.get("value_edge"))
    target_odds = _safe_float(signal.get("target_odds"))
    fair_odds = _safe_float(signal.get("fair_odds"))
    minute = _safe_int((signal.get("game") or {}).get("minute"))
    risk_note = str(signal.get("risk_note") or "").lower()
    data_quality = _safe_int(signal.get("data_quality"))
    entry_score = _safe_int(signal.get("entry_score"))

    if outcome not in {"win", "loss"}:
        return {"review_label": "void_sem_erro", "review_reason": "mercado nao fechou em green/red."}
    if target_odds and fair_odds and target_odds <= fair_odds:
        return {
            "review_label": "erro_de_preco",
            "review_reason": "entrada registrada com odd da casa abaixo ou igual a odd justa.",
        }
    if minute >= 80 or minute < 15:
        return {
            "review_label": "erro_de_timing",
            "review_reason": f"entrada avaliada fora da janela ideal de minuto ({minute}').",
        }
    if "gestao" in risk_note or "cash" in risk_note or action == "SAIR":
        return {
            "review_label": "erro_de_gestao" if outcome == "loss" else "boa_aposta_green",
            "review_reason": "resultado marcado em contexto de gestao/saida.",
        }
    if edge is not None and edge <= 0:
        return {
            "review_label": "erro_de_mercado",
            "review_reason": "mercado sem edge positivo sustentavel no momento da decisao.",
        }
    if data_quality < 70:
        return {
            "review_label": "erro_de_mercado",
            "review_reason": "dados insuficientes ou pouco limpos para sustentar a entrada.",
        }
    if outcome == "win":
        if entry_score >= 70 and (edge is None or edge > 0):
            return {
                "review_label": "boa_aposta_green",
                "review_reason": "entrada forte com contexto e preco coerentes.",
            }
        return {
            "review_label": "ma_aposta_green",
            "review_reason": "green aconteceu, mas a entrada nao era das mais limpas ou repetiveis.",
        }
    if entry_score >= 70 and (edge is None or edge > 0):
        return {
            "review_label": "boa_aposta_red",
            "review_reason": "red aceitavel: leitura era boa, mas o desfecho nao acompanhou.",
        }
    return {
        "review_label": "ma_aposta_red",
        "review_reason": "red em entrada fraca, com preco/tempo/qualidade abaixo do ideal.",
    }


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
