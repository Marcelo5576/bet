from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import unicodedata
from typing import Any


SUPPORTED_MARKET_GROUPS: dict[str, list[str]] = {
    "goals": ["goals", "goals_ht", "goals_st", "asian_totals"],
    "corners": [
        "corners_total",
        "asian_corners",
        "corners_ht",
        "corners_st",
        "race_to_corners",
        "team_corners",
    ],
    "cards": ["cards_total", "team_cards", "cards_ht", "cards_st", "referee_cards", "aggression_index"],
    "asian": ["asian_handicap", "asian_totals", "quarter_line", "half_line", "split_handicap"],
    "period": ["first_half", "second_half", "ht_st"],
}


@dataclass(frozen=True)
class NormalizedMarketOffer:
    market_type: str
    market_group: str
    selection: str
    side: str | None
    period: str
    line: str | None
    odd: float | None
    provider: str
    bookmaker: str | None
    timestamp: str
    raw_market: str
    raw_selection: str
    is_confirmed: bool
    liquidity_status: str
    unsupported_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_market_name(api_market_name: Any) -> str:
    text = _normalize_text(api_market_name)
    if not text:
        return "unsupported_market"

    if "corner" in text or "escanteio" in text or "canto" in text:
        if any(token in text for token in ("race", "corrida")):
            return "race_to_corners"
        if "asian" in text or "asiatico" in text or "handicap" in text:
            return "asian_corners"
        if _is_first_half(text):
            return "corners_ht"
        if _is_second_half(text):
            return "corners_st"
        if "team" in text or "time" in text or "equipa" in text:
            return "team_corners"
        return "corners_total"

    if any(token in text for token in ("card", "cartao", "booking", "yellow", "amarelo")):
        if "referee" in text or "arbitro" in text:
            return "referee_cards"
        if _is_first_half(text):
            return "cards_ht"
        if _is_second_half(text):
            return "cards_st"
        if "team" in text or "time" in text or "equipa" in text:
            return "team_cards"
        return "cards_total"

    if "asian" in text or "asiatico" in text or "handicap" in text:
        if "total" in text or "goal" in text or "gol" in text or "over" in text or "under" in text:
            return "asian_totals"
        return "asian_handicap"

    if any(token in text for token in ("total", "over", "under", "goal", "gol", "gols")):
        if _is_first_half(text):
            return "goals_ht"
        if _is_second_half(text):
            return "goals_st"
        return "goals"

    if any(token in text for token in ("match winner", "moneyline", "1x2", "resultado", "vencedor")):
        return "1x2"

    if any(token in text for token in ("btts", "ambos", "both teams")):
        return "btts"

    return "unsupported_market"


def normalize_selection_name(api_selection: Any, home_team: Any = "", away_team: Any = "") -> str:
    text = _normalize_text(api_selection)
    home = _normalize_text(home_team)
    away = _normalize_text(away_team)
    if not text:
        return "unknown"
    if text in {"home", "casa", "1"} or (home and (text == home or text in home or home in text)):
        return "home"
    if text in {"away", "fora", "2"} or (away and (text == away or text in away or away in text)):
        return "away"
    if text in {"draw", "empate", "x"}:
        return "draw"
    if "over" in text or text.startswith("o ") or text == "o":
        return "over"
    if "under" in text or text.startswith("u ") or text == "u":
        return "under"
    if text in {"yes", "sim"}:
        return "yes"
    if text in {"no", "nao", "não"}:
        return "no"
    return re.sub(r"\s+", "_", text).strip("_") or "unknown"


def normalize_period(raw_market_name: Any, explicit_period: Any = None) -> str:
    raw = _normalize_text(explicit_period) or _normalize_text(raw_market_name)
    if _is_first_half(raw):
        return "HT"
    if _is_second_half(raw):
        return "ST"
    return "FT"


def normalize_internal_markets(game: dict[str, Any], *, provider: str = "internal") -> list[dict[str, Any]]:
    """Normaliza mercados já presentes no LiveGame.markets.

    A função não cria mercado inexistente: só transforma odds/linhas que vieram
    de provider real/enriquecedor. Odds ausentes continuam sem confirmação.
    """

    markets = _as_dict(game.get("markets"))
    home = game.get("home") or "Casa"
    away = game.get("away") or "Fora"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    offers: list[NormalizedMarketOffer] = []

    one_x_two = _as_dict(markets.get("1x2"))
    for selection, label in (("home", home), ("draw", "Empate"), ("away", away)):
        odd = _safe_float(one_x_two.get(selection))
        if odd is None:
            odd = _safe_float(game.get(f"odds_{selection}"))
        if odd is not None:
            offers.append(
                _offer(
                    market_type="1x2",
                    selection=selection,
                    side=selection if selection in {"home", "away"} else None,
                    period="FT",
                    line=None,
                    odd=odd,
                    provider=provider,
                    raw_market="1X2",
                    raw_selection=str(label),
                    timestamp=timestamp,
                )
            )

    for family_key, raw_label in (
        ("goals", "Goals Over/Under"),
        ("corners", "Corners Over/Under"),
        ("cards", "Cards Over/Under"),
    ):
        family = _as_dict(markets.get(family_key))
        for period_key, period_label in (("", raw_label), ("first_half", f"{raw_label} 1st Half"), ("second_half", f"{raw_label} 2nd Half")):
            market = _as_dict(family.get(period_key)) if period_key else family
            period = normalize_period(period_label)
            market_type = normalize_market_name(period_label)
            for selection in ("over", "under"):
                row = _as_dict(market.get(selection))
                odd = _safe_float(row.get("odds"))
                line = _clean_line(row.get("line"))
                if odd is None and line is None:
                    continue
                offers.append(
                    _offer(
                        market_type=market_type,
                        selection=selection,
                        side=None,
                        period=period,
                        line=line,
                        odd=odd,
                        provider=provider,
                        raw_market=period_label,
                        raw_selection=selection.title(),
                        timestamp=timestamp,
                    )
                )

    asian = _as_dict(markets.get("asian"))
    for selection, label in (("home", home), ("away", away)):
        row = _as_dict(asian.get(selection))
        odd = _safe_float(row.get("odds"))
        line = _clean_line(row.get("line"))
        if odd is None and line is None:
            continue
        offers.append(
            _offer(
                market_type="asian_handicap",
                selection=selection,
                side=selection,
                period="FT",
                line=line,
                odd=odd,
                provider=provider,
                raw_market="Asian Handicap",
                raw_selection=str(label),
                timestamp=timestamp,
            )
        )

    return [item.to_dict() for item in offers]


def market_group(market_type: str) -> str:
    for group, items in SUPPORTED_MARKET_GROUPS.items():
        if market_type in items:
            return group
    if market_type in {"1x2", "btts"}:
        return "goals"
    return "unsupported"


def supported_markets_payload() -> list[dict[str, Any]]:
    return [
        {"group": group, "markets": items}
        for group, items in SUPPORTED_MARKET_GROUPS.items()
    ]


def _offer(
    *,
    market_type: str,
    selection: str,
    side: str | None,
    period: str,
    line: str | None,
    odd: float | None,
    provider: str,
    raw_market: str,
    raw_selection: str,
    timestamp: str,
    bookmaker: str | None = None,
) -> NormalizedMarketOffer:
    confirmed = bool(odd is not None and odd > 1.0)
    return NormalizedMarketOffer(
        market_type=market_type,
        market_group=market_group(market_type),
        selection=selection,
        side=side,
        period=period,
        line=line,
        odd=odd,
        provider=provider,
        bookmaker=bookmaker,
        timestamp=timestamp,
        raw_market=raw_market,
        raw_selection=raw_selection,
        is_confirmed=confirmed,
        liquidity_status="confirmed" if confirmed else "missing_odd",
        unsupported_reason=None if market_type != "unsupported_market" else "Mercado nao mapeado pelo normalizador.",
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return round(float(str(value).replace(",", ".")), 4)
    except (TypeError, ValueError):
        return None


def _clean_line(value: Any) -> str | None:
    if value in (None, ""):
        return None
    parsed = _safe_float(value)
    if parsed is None:
        return str(value).strip() or None
    if parsed.is_integer():
        return str(int(parsed))
    return f"{parsed:.2f}".rstrip("0").rstrip(".")


def _normalize_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _is_first_half(text: str) -> bool:
    return any(token in text for token in ("1st half", "first half", "1h", "1 t", "1 tempo", "ht"))


def _is_second_half(text: str) -> bool:
    return any(token in text for token in ("2nd half", "second half", "2h", "2 t", "2 tempo", "st"))
