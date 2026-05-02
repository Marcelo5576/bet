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
    simulation_sessions: list[dict[str, Any]] | None = None
    last_auto_simulation_date: str | None = None
    last_auto_simulation_at: str | None = None
    scan_preference: str = "brazil_first"
    scan_requested_at: str | None = None


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
        if raw.get("scan_requested_at") is not None:
            raw["scan_requested_at"] = str(raw.get("scan_requested_at"))
        sessions = raw.get("simulation_sessions")
        if not isinstance(sessions, list):
            raw["simulation_sessions"] = []
        else:
            raw["simulation_sessions"] = [
                item for item in sessions if isinstance(item, dict)
            ][:120]
        if raw.get("last_auto_simulation_date") is not None:
            raw["last_auto_simulation_date"] = str(raw.get("last_auto_simulation_date"))
        if raw.get("last_auto_simulation_at") is not None:
            raw["last_auto_simulation_at"] = str(raw.get("last_auto_simulation_at"))

        active_signal = raw.get("active_signal")
        if isinstance(active_signal, dict):
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

    def clear_scanner_cache(self) -> BotState:
        state = self.load()
        state.candidate_signals = []
        state.last_games = []
        state.last_scan_at = datetime.now(timezone.utc).isoformat()
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
        signal = candidates[index]
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

        history = list(state.history or [])
        for item in history:
            if item.get("signal_id") == signal_id:
                item["outcome"] = outcome
                item["finished_at"] = finished_at
                item["profit_units"] = state.active_signal["profit_units"]
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

        history = list(state.history or [])
        for item in history:
            if item.get("signal_id") == signal_id:
                item["entered"] = entered
                item["entered_at"] = state.active_signal["entered_at"]
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

        history = list(state.history or [])
        for item in history:
            if item.get("signal_id") == signal_id:
                item.update(details)
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
                history.insert(0, record)
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
