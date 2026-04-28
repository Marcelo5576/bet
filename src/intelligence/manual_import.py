from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from typing import Any
from uuid import uuid4


def parse_manual_bets(raw_text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    pending: dict[str, Any] | None = None

    for text in _import_lines(raw_text):
        if _is_noise(text):
            continue

        if _is_loss_marker(text):
            if pending and pending.get("outcome") == "open":
                pending["outcome"] = "loss"
                pending["finished_at"] = now
                pending["profit_units"] = -float(pending.get("stake_units") or 0)
                pending = None
            else:
                records.append(_record(text, now, outcome="loss"))
            continue

        if _is_win_marker(text):
            if pending and pending.get("outcome") == "open":
                pending["outcome"] = "win"
                pending["finished_at"] = now
                pending["profit_units"] = _profit_units_from_record(pending, "win")
                pending["profit_value"] = _profit_value(text)
                pending = None
            else:
                records.append(_record(text, now, outcome="win"))
            continue

        if _is_closed_marker(text):
            if pending and pending.get("outcome") == "open":
                _close_pending_with_return(pending, text, now)
                pending = None
            else:
                records.append(_record(text, now, outcome="win" if _amount(text) else "void"))
            continue

        if _looks_like_bet_line(text):
            if pending and pending.get("outcome") == "open" and not _starts_new_bet(text):
                continue
            pending = _record(text, now, outcome="open")
            records.append(pending)
            continue

        if pending and pending.get("outcome") == "open" and _is_context_line(text):
            _append_context(pending, text)

    return records


def _import_lines(raw_text: str) -> list[str]:
    text = _plain_text(raw_text or "")
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = " ".join(line.strip().split())
        if not cleaned:
            continue
        lines.append(cleaned)
    return lines


def _plain_text(raw_text: str) -> str:
    text = re.sub(
        r"(?is)<(script|style|svg|head|noscript)\b.*?</\1>",
        "\n",
        raw_text,
    )
    text = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    return text.replace("\u00a0", " ")


def _looks_like_bet_line(text: str) -> bool:
    lowered = text.lower()
    return (
        "r$" in lowered
        or "perdida" in lowered
        or "ganha" in lowered
        or "green" in lowered
        or "red" in lowered
        or "aposta encerr" in lowered
    )


def _is_context_line(text: str) -> bool:
    lowered = text.lower()
    if len(text) > 80:
        return False
    blocked = (
        "reutilizar sele",
        "próximo gol",
        "proximo gol",
        "aposta",
        "ratotrio",
        "retorno",
        "total",
        "rattamaa",
        "ratotrio",
        "lider por",
    )
    if any(token in lowered for token in blocked):
        return False
    return bool(re.search(r"[a-zA-ZÀ-ÿ]", text))


def _starts_new_bet(text: str) -> bool:
    lowered = text.lower()
    return "simples" in lowered or "múltipla" in lowered or "multipla" in lowered


def _is_noise(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "doctype",
            "meta ",
            "href=",
            "script",
            "favicon",
            "preloader",
            "websiteconfig",
            "manifest",
            "websocket",
            "bet365 - apostas esportivas online",
        )
    )


def _record(text: str, created_at: str, outcome: str) -> dict[str, Any]:
    amount = _amount(text)
    market = _market(text)
    teams = _teams(text)
    selection = _selection(text, teams)
    line = _line_value(text)
    stake_units = round(amount / 10, 2) if amount else 0
    profit_value = _profit_value(text) if outcome == "win" else None
    return {
        "signal_id": "manual-" + uuid4().hex,
        "created_at": created_at,
        "finished_at": created_at if outcome in {"loss", "void"} else None,
        "source": "manual_import",
        "action": "IMPORTADO",
        "confidence": 0,
        "entry_score": 0,
        "grade": "M",
        "risk_score": 100,
        "market": market,
        "entry_market": market,
        "entry_selection": selection,
        "entry_line": line,
        "entry_value": amount,
        "stake_units": stake_units,
        "stake_value": amount or 0,
        "outcome": outcome,
        "profit_units": _profit_units(stake_units, outcome),
        "profit_value": profit_value,
        "reason": "Historico manual importado pelo usuario.",
        "entry_odds": _odds(text),
        "target_odds": _odds(text),
        "game": {
            "game_id": "manual-" + uuid4().hex,
            "home": teams[0] if teams else _team_hint(text),
            "away": teams[1] if len(teams) > 1 else "Manual",
            "home_goals": 0,
            "away_goals": 0,
            "minute": 0,
            "league": "Historico manual",
            "division": "Historico manual",
        },
        "manual_raw": text,
    }


def _append_context(record: dict[str, Any], text: str) -> None:
    raw = " ".join([str(record.get("manual_raw") or ""), text]).strip()
    record["manual_raw"] = raw
    market = _market(raw)
    teams = _teams(raw)
    record["market"] = market
    record["entry_market"] = market
    record["entry_selection"] = _selection(raw, teams)
    line = _line_value(raw)
    if line:
        record["entry_line"] = line
    odds = _odds(raw)
    if odds:
        record["entry_odds"] = odds
        record["target_odds"] = odds
    if teams:
        record["game"]["home"] = teams[0]
        record["game"]["away"] = teams[1] if len(teams) > 1 else record["game"].get("away") or "Manual"
    else:
        record["game"]["home"] = _team_hint(raw)


def _close_pending_with_return(record: dict[str, Any], text: str, finished_at: str) -> None:
    stake = float(record.get("entry_value") or 0)
    payout = _amount(text)
    profit_value = None
    if payout is not None and stake:
        profit_value = round(payout - stake, 2)
    outcome = "win" if profit_value is not None and profit_value > 0 else "loss"
    record["outcome"] = outcome
    record["finished_at"] = finished_at
    record["settlement_value"] = payout
    record["profit_value"] = profit_value
    record["profit_units"] = round((profit_value or 0) / 10, 2)


def _amount(text: str) -> float | None:
    match = re.search(r"R\$\s*(\d+(?:[,.]\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _odds(text: str) -> float | None:
    cleaned = re.sub(r"R\$\s*\d+(?:[,.]\d+)?", "", text, flags=re.IGNORECASE)
    matches = re.findall(r"(?<![+-])\b(\d+[,.]\d{2,3})\b", cleaned)
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", "."))
    except ValueError:
        return None


def _profit_value(text: str) -> float | None:
    match = re.search(r"(?:ganh(?:ou|a|o)|retorno|green|lucro)[^\d+-]*([+-]?\s*R\$\s*\d+(?:[,.]\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    number = re.search(r"[+-]?\s*R\$\s*(\d+(?:[,.]\d+)?)", match.group(1), flags=re.IGNORECASE)
    if not number:
        return None
    value = float(number.group(1).replace(",", "."))
    return round(value, 2)


def _market(text: str) -> str:
    cleaned = re.sub(r"R\$\s*\d+(?:[,.]\d+)?", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSimples\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(\d+\s*-\s*\d+\)", "", cleaned)
    cleaned = re.sub(r"\b\d+[,.]\d{2,3}\b", "", cleaned)
    cleaned = cleaned.replace("Aposta Encuerada", "Aposta Encerrada")
    cleaned = cleaned.replace("Aposta incorrada", "Aposta Encerrada")
    cleaned = cleaned.replace("Aposta Encerrasia", "Aposta Encerrada")
    cleaned = cleaned.replace("Aposta Encertada", "Aposta Encerrada")
    cleaned = cleaned.replace("Aposta Ermrada", "Aposta Encerrada")
    cleaned = _strip_context_words(cleaned)
    for team in _teams(text):
        cleaned = re.sub(re.escape(team), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?<!\d)[+-]\d+(?:[,.]\d+)?\b", " ", cleaned)
    cleaned = re.sub(r"\bo\d+(?:[,.]\d+)?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bu\d+(?:[,.]\d+)?\b", " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()) or "Historico manual"


def _strip_context_words(text: str) -> str:
    cleaned = text
    for token in (
        "Reutilizar Seleções",
        "Reutilizar Selecoes",
        "Aposta",
        "Rattamaa Tutal",
        "Ratotrio Total",
        "Lider por",
    ):
        cleaned = re.sub(re.escape(token), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _team_hint(text: str) -> str:
    teams = _teams(text)
    if teams:
        return teams[0]
    known = (
        "Gremio",
        "Grêmio",
        "Corinthians",
        "Goias",
        "Goiás",
        "Cruzeiro",
        "Santos Laguna",
        "Fatih Karagumruk",
        "Besiktas",
    )
    for name in known:
        if name.lower() in text.lower():
            return name
    match = re.search(r"\)\s*([A-ZÀ-Ý][\wÀ-ÿ' .-]{2,40}?)(?:\s+[+-]?\d|$)", text)
    if match:
        return " ".join(match.group(1).split())
    return "Aposta manual"


def _teams(text: str) -> list[str]:
    known = (
        "Fatih Karagumruk",
        "Vasco da Gama",
        "Santos Laguna",
        "Manchester United",
        "Brentford",
        "Besiktas",
        "Corinthians",
        "Gremio",
        "Grêmio",
        "Goias",
        "Goiás",
        "Cruzeiro",
        "ADT",
        "Los Chankas",
        "Kalmar FF",
        "IF Elfsborg",
    )
    found: list[tuple[int, str]] = []
    lowered = text.lower()
    for name in known:
        index = lowered.find(name.lower())
        if index >= 0 and name not in [item[1] for item in found]:
            found.append((index, name))
    found.sort(key=lambda item: item[0])
    return [name for _, name in found[:2]]


def _selection(text: str, teams: list[str]) -> str | None:
    for team in teams:
        if re.search(re.escape(team) + r"\s+[+-]?\d", text, flags=re.IGNORECASE):
            return team
    if "sem 2" in text.lower():
        return "Sem 2 gol"
    if teams:
        return teams[0]
    return None


def _line_value(text: str) -> str | None:
    cleaned = re.sub(r"\(\d+\s*-\s*\d+\)", " ", text)
    handicap = re.search(r"(?<!\d)([+-]\d+(?:[,.]\d+)?)\b", cleaned)
    if handicap:
        return handicap.group(1).replace(",", ".")
    totals = re.search(r"\b([ou])\s*(\d+(?:[,.]\d+)?)\b", cleaned, flags=re.IGNORECASE)
    if totals:
        prefix = "Over" if totals.group(1).lower() == "o" else "Under"
        return f"{prefix} {totals.group(2).replace(',', '.')}"
    explicit = re.search(r"\b(Mais|Menos|Over|Under)\s+de\s+(\d+(?:[,.]\d+)?)", cleaned, flags=re.IGNORECASE)
    if explicit:
        label = explicit.group(1).capitalize()
        return f"{label} {explicit.group(2).replace(',', '.')}"
    return None


def _is_loss_marker(text: str) -> bool:
    lowered = text.lower()
    return "perdida" in lowered or lowered.strip() == "red"


def _is_win_marker(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("ganha", "ganhou", "vencedora", "green"))


def _is_closed_marker(text: str) -> bool:
    lowered = text.lower()
    return (
        "encerr" in lowered
        or "encuer" in lowered
        or "encert" in lowered
        or "ermr" in lowered
        or "incorr" in lowered
        or ("retorno" in lowered and _amount(text) is not None)
    )


def _profit_units(stake_units: float, outcome: str) -> float:
    if outcome == "loss":
        return round(-stake_units, 2)
    return 0


def _profit_units_from_record(record: dict[str, Any], outcome: str) -> float:
    return _profit_units(float(record.get("stake_units") or 0), outcome)
