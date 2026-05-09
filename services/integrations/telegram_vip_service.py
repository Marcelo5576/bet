from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

from services.observability import get_observability_service


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_percent(value: Any, default: float = 0.0) -> float:
    raw = _safe_float(value, default)
    if 0 <= raw <= 1:
        raw *= 100.0
    return max(0.0, min(100.0, raw))


class TelegramVipService:
    def __init__(self, db_file: str | Path):
        self.db_file = str(Path(db_file).expanduser())
        self.observability = get_observability_service(self.db_file)

    def dispatch_key(self, signal: dict[str, Any]) -> str:
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        raw = "|".join(
            str(
                part or ""
            ).strip().lower()
            for part in (
                game.get("game_id") or signal.get("match_id"),
                signal.get("entry_market") or signal.get("market"),
                signal.get("entry_selection") or signal.get("selection"),
                signal.get("entry_line") or signal.get("line"),
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def qualify_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        source = str(signal.get("source") or signal.get("provider") or "").lower()
        apex_score = _safe_float(signal.get("apex_score"), 0.0)
        data_quality = _normalize_percent(signal.get("data_quality"), 0.0)
        decision = str(signal.get("supervisor_decision") or signal.get("apex_decision") or "").upper()
        odd = _safe_float(signal.get("entry_odds") or signal.get("target_odds") or signal.get("odds"), 0.0)
        odds_confirmed = bool(signal.get("apex_odds_confirmed")) or odd > 1.0
        risk = str(signal.get("risk_level") or "").lower()
        blockers = list(signal.get("apex_blockers") or []) + list(signal.get("supervisor_blockers") or [])
        reasons: list[str] = []
        hard_blockers: list[str] = []

        if decision != "ENTER_NOW":
            hard_blockers.append("Decisão final ainda não é ENTER_NOW.")
        if not bool(signal.get("entry_allowed")):
            hard_blockers.append("Entrada não liberada pelo fluxo principal.")
        if not odds_confirmed:
            hard_blockers.append("Odd real não confirmada.")
        if apex_score < 80:
            hard_blockers.append("ApexScore abaixo de 80.")
        if data_quality < 80:
            hard_blockers.append("Qualidade de dados abaixo de 80.")
        if "alto" in risk:
            hard_blockers.append("Risco alto bloqueia Telegram VIP.")
        if source in {"mock", "fallback"}:
            hard_blockers.append("Fonte mock/fallback não pode gerar VIP.")
        if blockers:
            hard_blockers.extend(str(item) for item in blockers if item)

        if odds_confirmed:
            reasons.append("Odd confirmada")
        if _safe_float(signal.get("expected_value"), 0.0) > 0:
            reasons.append("EV aprovado")
        if apex_score >= 80:
            reasons.append("ApexScore aprovado")
        if data_quality >= 80:
            reasons.append("Dados suficientes")

        return {
            "eligible": len(hard_blockers) == 0,
            "decision": decision,
            "apex_score": round(apex_score, 1),
            "data_quality": round(data_quality, 1),
            "odds_confirmed": odds_confirmed,
            "reasons": reasons,
            "blockers": hard_blockers[:10],
            "dispatch_key": self.dispatch_key(signal),
        }

    def can_dispatch(self, signal: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        qualification = self.qualify_signal(signal)
        if not qualification["eligible"]:
            return False, qualification
        counts = self.observability.dispatch_counts()
        if counts["last_hour"] >= 3:
            qualification["blockers"].append("Limite de 3 sinais por hora atingido.")
            return False, qualification
        if counts["last_day"] >= 10:
            qualification["blockers"].append("Limite de 10 sinais por dia atingido.")
            return False, qualification
        if self.observability.has_recent_dispatch(qualification["dispatch_key"], within_minutes=5):
            qualification["blockers"].append("Cooldown de 5 minutos por jogo/mercado ainda ativo.")
            return False, qualification
        return True, qualification

    def format_message(self, signal: dict[str, Any]) -> str:
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        qualification = self.qualify_signal(signal)
        minute = str(game.get("minute") or signal.get("minute") or "-")
        league = str(game.get("league") or game.get("division") or "-")
        reason = str(signal.get("why_decision") or signal.get("ai_explanation") or signal.get("reason") or "")
        criteria = qualification["reasons"] or ["Odd confirmada", "ApexScore aprovado", "Dados suficientes"]
        return "\n".join(
            [
                "🔥 ENTRADA APROVADA — ApexGol AI",
                "",
                f"⚽ Jogo: {game.get('home') or '-'} x {game.get('away') or '-'}",
                f"⏱ Tempo: {minute}'",
                f"🏆 Liga: {league}",
                f"🎯 Mercado: {signal.get('entry_market') or signal.get('market') or '-'}",
                f"💰 Odd: {signal.get('entry_odds') or signal.get('target_odds') or '-'}",
                f"📈 EV: {signal.get('expected_value') if signal.get('expected_value') is not None else '-'}",
                f"🧠 ApexScore: {signal.get('apex_score') or '-'}",
                f"📊 Confiança: {signal.get('confidence_score') or '-'}",
                f"⚠️ Risco: {signal.get('risk_level') or '-'}",
                "",
                "✅ Critérios:",
                *[f"• {item}" for item in criteria],
                "",
                "💡 Motivo:",
                reason or "Leitura quantitativa validada por odds reais, histórico e filtros de risco.",
                "",
                "⚠️ Gestão responsável:",
                "Ferramenta estatística. Não garante lucro.",
            ]
        )

    def record_dispatch(self, signal: dict[str, Any], chat_id: int | str, *, status: str) -> None:
        qualification = self.qualify_signal(signal)
        self.observability.log_telegram_dispatch(
            str(signal.get("signal_id") or signal.get("analysis_id") or ""),
            chat_id,
            qualification["dispatch_key"],
            status=status,
            decision=str(signal.get("supervisor_decision") or signal.get("apex_decision") or "UNKNOWN"),
            apex_score=_safe_float(signal.get("apex_score"), 0.0),
            payload={
                "chat_id": str(chat_id),
                "qualification": qualification,
                "market": signal.get("entry_market") or signal.get("market"),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )


_SERVICES: dict[str, TelegramVipService] = {}


def get_telegram_vip_service(db_file: str | Path) -> TelegramVipService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = TelegramVipService(key)
        _SERVICES[key] = service
    return service
