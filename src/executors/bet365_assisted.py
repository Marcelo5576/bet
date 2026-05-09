from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import unicodedata
from typing import Any

from src.config import Settings, load_settings
from src.models.executor_models import PrepareBet365Request, PrepareBet365Response
from src.storage import StateStore

logger = logging.getLogger("betsignal.bet365_assisted")

_OPEN_ASSISTED_SESSIONS: dict[str, dict[str, Any]] = {}
_BROWSER_LAUNCH_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_app_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return _repo_root()
    if value.startswith("/app/"):
        return _repo_root() / value.removeprefix("/app/")
    return Path(value).expanduser()


def _normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\b(vs?|versus)\b", " x ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _tokenize_match_name(value: str | None) -> list[str]:
    clean = _normalize_text(value)
    return [token for token in clean.split() if token]


def _match_label(home: str | None, away: str | None) -> str:
    parts = [str(home or "").strip(), str(away or "").strip()]
    parts = [part for part in parts if part]
    return " x ".join(parts)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        text = str(value).strip().replace(" ", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        return float(text)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pick_first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def build_prepare_request_from_signal(signal: Any = None, **overrides: Any) -> PrepareBet365Request:
    data: dict[str, Any] = {}
    if isinstance(signal, dict):
        data.update(signal)
    data.update({key: value for key, value in overrides.items() if value is not None})

    game = data.get("game") if isinstance(data.get("game"), dict) else {}
    match_name = _pick_first(
        data,
        "match_name",
        "match",
    ) or _match_label(game.get("home"), game.get("away"))
    market = _pick_first(data, "market", "entry_market")
    selection = _pick_first(data, "selection", "entry_selection", "pick")
    min_odd = _safe_float(_pick_first(data, "min_odd", "target_odds", "entry_odds"))
    stake = _safe_float(_pick_first(data, "stake", "stake_value", "entry_value"))
    signal_id = str(_pick_first(data, "signal_id", "id") or "").strip() or None

    if not signal_id:
        raise ValueError("signal_id obrigatório para preparar entrada assistida.")
    if not match_name:
        raise ValueError("Nao encontrei o nome do jogo para preparar a Bet365.")
    if not market:
        raise ValueError("Nao encontrei o mercado do sinal.")
    if not selection:
        raise ValueError("Nao encontrei a seleção do sinal.")
    if not min_odd or min_odd <= 1:
        raise ValueError("Odd mínima obrigatória e maior que 1.00.")
    if not stake or stake <= 0:
        raise ValueError("Stake obrigatória e maior que zero.")

    return PrepareBet365Request(
        match_name=str(match_name).strip(),
        market=str(market).strip(),
        selection=str(selection).strip(),
        min_odd=float(min_odd),
        stake=float(stake),
        signal_id=signal_id,
    )


def persist_prepare_response(
    store: StateStore,
    request: PrepareBet365Request,
    response: PrepareBet365Response,
    *,
    assisted_chat_id: int | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    signal_id = str(request.signal_id or "").strip()
    if not signal_id:
        return store.load(), None

    updates: dict[str, Any] = {
        "assisted_prepare_status": response.status,
        "assisted_prepare_message": response.message,
        "assisted_prepare_updated_at": _now_iso(),
    }
    if response.screenshot_path:
        updates["assisted_screenshot_path"] = response.screenshot_path
    if response.current_odd is not None:
        updates["entry_odds"] = float(response.current_odd)
        updates["target_odds"] = float(response.current_odd)
    if assisted_chat_id is not None:
        updates["assisted_chat_id"] = int(assisted_chat_id)

    activate = False
    if response.ok and response.status == "prepared":
        updates.update(
            {
                "status": "prepared_waiting_manual_confirmation",
                "entered": False,
                "entry_value": float(request.stake),
                "stake_value": float(request.stake),
                "entry_market": request.market,
                "entry_selection": request.selection,
                "assisted_prepared_at": _now_iso(),
            }
        )
        activate = True

    return store.update_signal_fields(signal_id, updates, activate=activate)


def confirm_prepared_signal(
    store: StateStore,
    signal_id: str,
    *,
    assisted_chat_id: int | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    updates: dict[str, Any] = {
        "status": "position_open",
        "entered": True,
        "entered_at": _now_iso(),
        "assisted_prepare_status": "confirmed_manual",
    }
    if assisted_chat_id is not None:
        updates["assisted_chat_id"] = int(assisted_chat_id)
    return store.update_signal_fields(signal_id, updates, activate=True)


def monitor_signal_without_entry(
    store: StateStore,
    signal_id: str,
    *,
    assisted_chat_id: int | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    updates: dict[str, Any] = {
        "status": "monitoring_without_entry",
        "entered": False,
        "assisted_prepare_status": "monitor_only",
        "assisted_prepare_updated_at": _now_iso(),
    }
    if assisted_chat_id is not None:
        updates["assisted_chat_id"] = int(assisted_chat_id)
    return store.update_signal_fields(signal_id, updates, activate=False)


def ignore_signal_for_assisted_flow(
    store: StateStore,
    signal_id: str,
    *,
    assisted_chat_id: int | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    updates: dict[str, Any] = {
        "status": "ignored_by_user",
        "entered": False,
        "assisted_prepare_status": "ignored",
        "assisted_prepare_updated_at": _now_iso(),
    }
    if assisted_chat_id is not None:
        updates["assisted_chat_id"] = int(assisted_chat_id)
    return store.update_signal_fields(signal_id, updates, activate=False)


async def _await_maybe(result: Any) -> Any:
    if asyncio.iscoroutine(result):
        return await result
    return result


def assisted_session_snapshot() -> dict[str, Any]:
    sessions: list[dict[str, Any]] = []
    for signal_id, session in list(_OPEN_ASSISTED_SESSIONS.items()):
        page = session.get("page")
        page_url = getattr(page, "url", "") if page else ""
        is_closed_callable = getattr(page, "is_closed", None)
        page_open = False
        if callable(is_closed_callable):
            try:
                page_open = not bool(is_closed_callable())
            except Exception:
                page_open = False
        sessions.append(
            {
                "signal_id": signal_id,
                "page_url": str(page_url or ""),
                "page_open": page_open,
                "updated_at": str(session.get("updated_at") or ""),
            }
        )
    sessions.sort(key=lambda item: item["signal_id"])
    return {
        "count": len(sessions),
        "signal_ids": [item["signal_id"] for item in sessions],
        "sessions": sessions,
    }


async def close_assisted_session(signal_id: str) -> bool:
    target = str(signal_id or "").strip()
    if not target:
        return False
    session = _OPEN_ASSISTED_SESSIONS.pop(target, None)
    if not session:
        return False
    context = session.get("context")
    playwright = session.get("playwright")
    try:
        if context is not None and hasattr(context, "close"):
            await _await_maybe(context.close())
    finally:
        if playwright is not None and hasattr(playwright, "stop"):
            await _await_maybe(playwright.stop())
    return True


class Bet365AssistedExecutor:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.base_url = self.settings.bet365_base_url
        self.profile_dir = _resolve_app_path(self.settings.bet365_profile_dir)
        self.screenshot_dir = _resolve_app_path(self.settings.bet365_screenshot_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def prepare_entry(self, req: PrepareBet365Request) -> PrepareBet365Response:
        logger.info(
            "BET365_PREPARE_START signal_id=%s match=%s",
            req.signal_id,
            req.match_name,
        )
        if not req.signal_id:
            return PrepareBet365Response(
                ok=False,
                status="signal_id_required",
                message="signal_id obrigatório para registrar histórico.",
                signal_id=req.signal_id,
            )
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - depends on runtime package
            logger.exception("BET365_PREPARE_FAILED playwright unavailable")
            return PrepareBet365Response(
                ok=False,
                status="playwright_unavailable",
                message=f"Playwright indisponível neste ambiente: {exc}",
                signal_id=req.signal_id,
            )

        playwright = None
        context = None
        page = None
        try:
            async with _BROWSER_LAUNCH_LOCK:
                playwright = await async_playwright().start()
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=bool(self.settings.bet365_headless),
                    viewport={"width": 1440, "height": 900},
                )
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(self.settings.bet365_timeout_ms)
            await self.open_home(page)

            if await self._manual_login_required(page):
                screenshot = await self.take_screenshot(page, req.signal_id)
                self._remember_session(req.signal_id, playwright, context, page)
                return PrepareBet365Response(
                    ok=False,
                    status="manual_login_required",
                    message="Faça login manualmente na janela aberta e rode novamente.",
                    screenshot_path=screenshot,
                    signal_id=req.signal_id,
                )

            matches = await self.search_match(page, req.match_name)
            if len(matches) > 1:
                screenshot = await self.take_screenshot(page, req.signal_id)
                self._remember_session(req.signal_id, playwright, context, page)
                return PrepareBet365Response(
                    ok=False,
                    status="multiple_matches_found",
                    message="Encontrei múltiplos jogos parecidos. Abra o evento correto manualmente e tente novamente.",
                    screenshot_path=screenshot,
                    signal_id=req.signal_id,
                )
            if not matches:
                screenshot = await self.take_screenshot(page, req.signal_id)
                await close_assisted_session(req.signal_id)
                return PrepareBet365Response(
                    ok=False,
                    status="match_not_found",
                    message="Nao encontrei o jogo informado na Bet365.",
                    screenshot_path=screenshot,
                    signal_id=req.signal_id,
                )

            await self.open_match(page, req.match_name)
            market_root = await self.find_market(page, req.market)
            if market_root is None:
                screenshot = await self.take_screenshot(page, req.signal_id)
                self._remember_session(req.signal_id, playwright, context, page)
                return PrepareBet365Response(
                    ok=False,
                    status="market_not_found",
                    message="Nao encontrei o mercado informado na Bet365.",
                    screenshot_path=screenshot,
                    signal_id=req.signal_id,
                )

            current_odd = await self.find_selection_and_odd(page, req.selection, market_root=market_root)
            if current_odd is None:
                screenshot = await self.take_screenshot(page, req.signal_id)
                self._remember_session(req.signal_id, playwright, context, page)
                return PrepareBet365Response(
                    ok=False,
                    status="selection_not_found",
                    message="Nao encontrei a seleção informada na Bet365.",
                    screenshot_path=screenshot,
                    signal_id=req.signal_id,
                )

            if current_odd < req.min_odd:
                logger.warning("BET365_ODD_BELOW_MINIMUM current=%s min=%s", current_odd, req.min_odd)
                screenshot = await self.take_screenshot(page, req.signal_id)
                self._remember_session(req.signal_id, playwright, context, page)
                return PrepareBet365Response(
                    ok=False,
                    status="odd_below_minimum",
                    message=f"Odd atual {current_odd:.2f} abaixo da mínima {req.min_odd:.2f}. Entrada cancelada.",
                    current_odd=current_odd,
                    screenshot_path=screenshot,
                    signal_id=req.signal_id,
                )

            await self.fill_stake(page, req.stake)
            screenshot = await self.take_screenshot(page, req.signal_id)
            self._remember_session(req.signal_id, playwright, context, page)
            return PrepareBet365Response(
                ok=True,
                status="prepared",
                message="Entrada preparada na Bet365. Confirme manualmente.",
                current_odd=current_odd,
                screenshot_path=screenshot,
                signal_id=req.signal_id,
            )
        except PlaywrightTimeoutError as exc:  # pragma: no cover - depends on real UI
            logger.exception("BET365_PREPARE_FAILED")
            screenshot = await self.take_screenshot(page, req.signal_id) if page else None
            self._remember_session(req.signal_id, playwright, context, page)
            return PrepareBet365Response(
                ok=False,
                status="timeout",
                message=f"A janela da Bet365 demorou mais do que o esperado: {exc}",
                screenshot_path=screenshot,
                signal_id=req.signal_id,
            )
        except Exception as exc:  # pragma: no cover - defensive path for runtime browser issues
            logger.exception("BET365_PREPARE_FAILED")
            screenshot = await self.take_screenshot(page, req.signal_id) if page else None
            self._remember_session(req.signal_id, playwright, context, page)
            return PrepareBet365Response(
                ok=False,
                status="prepare_failed",
                message=f"Falha ao preparar a entrada assistida: {exc}",
                screenshot_path=screenshot,
                signal_id=req.signal_id,
            )

    async def open_home(self, page: Any) -> None:
        current = str(getattr(page, "url", "") or "")
        if not current.startswith(self.base_url):
            await page.goto(self.base_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)

    async def search_match(self, page: Any, match_name: str) -> list[str]:
        normalized = _normalize_text(match_name)
        parts = [part for part in re.split(r"\bx\b", normalized) if part.strip()]
        patterns = [normalized] + parts
        body = await page.locator("body").inner_text()
        body_norm = _normalize_text(body)
        hits = [pattern for pattern in patterns if pattern and pattern in body_norm]
        if not hits:
            return []
        if len(parts) >= 2:
            if all(part in body_norm for part in parts[:2]):
                return [match_name]
        return [match_name] if normalized in body_norm else []

    async def open_match(self, page: Any, match_name: str) -> None:
        tokens = _tokenize_match_name(match_name)
        if not tokens:
            return
        text_variants = [
            match_name,
            match_name.replace(" x ", " v "),
            match_name.replace(" x ", " - "),
        ]
        for variant in text_variants:
            locator = page.get_by_text(variant, exact=False).first
            if await locator.count():
                await self.safe_click(locator, f"match:{variant}")
                await page.wait_for_timeout(1200)
                return

    async def find_market(self, page: Any, market: str) -> Any | None:
        candidates = [market, _normalize_text(market)]
        for candidate in candidates:
            locator = page.get_by_text(candidate, exact=False).first
            if await locator.count():
                try:
                    await locator.scroll_into_view_if_needed()
                except Exception:
                    pass
                return locator
        return None

    async def find_selection_and_odd(self, page: Any, selection: str, *, market_root: Any | None = None) -> float | None:
        root = market_root or page.locator("body")
        locator = root.get_by_text(selection, exact=False).first if hasattr(root, "get_by_text") else page.get_by_text(selection, exact=False).first
        if not await locator.count():
            locator = page.get_by_text(selection, exact=False).first
            if not await locator.count():
                return None
        await self.safe_click(locator, f"selection:{selection}")
        text = ""
        try:
            text = await locator.text_content() or ""
        except Exception:
            text = selection
        odd_match = re.search(r"(\d+[.,]\d+|\d+)", text)
        if odd_match:
            return _safe_float(odd_match.group(1))
        return _safe_float(selection)

    async def fill_stake(self, page: Any, stake: float) -> None:
        selectors = [
            "input[type='number']",
            "input[name*='stake' i]",
            "input[placeholder*='valor' i]",
            "input[placeholder*='stake' i]",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count():
                await self.safe_fill(locator, f"{stake:.2f}", "stake")
                return
        raise RuntimeError("Nao encontrei o campo de stake na Bet365.")

    async def take_screenshot(self, page: Any, signal_id: str | None) -> str | None:
        if page is None:
            return None
        safe_signal = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(signal_id or "manual"))[:80]
        target = self.screenshot_dir / f"bet365_{safe_signal}.png"
        try:
            await page.screenshot(path=str(target), full_page=True)
            return str(target)
        except Exception as exc:
            logger.info("Nao consegui salvar screenshot Bet365: %s", exc)
            return None

    async def safe_click(self, locator: Any, label: str) -> None:
        try:
            await locator.scroll_into_view_if_needed()
        except Exception:
            pass
        await locator.click(delay=120)
        await asyncio.sleep(0.35)

    async def safe_fill(self, locator: Any, value: str, label: str) -> None:
        try:
            await locator.scroll_into_view_if_needed()
        except Exception:
            pass
        await locator.click(delay=80)
        await locator.fill("")
        await locator.type(str(value), delay=70)
        await asyncio.sleep(0.2)

    async def _manual_login_required(self, page: Any) -> bool:
        try:
            content = _normalize_text(await page.locator("body").inner_text())
        except Exception:
            return False
        hints = [
            "login",
            "entrar",
            "sign in",
            "2fa",
            "verification",
            "verificacao",
            "captcha",
            "security check",
        ]
        return any(hint in content for hint in hints)

    def _remember_session(self, signal_id: str | None, playwright: Any, context: Any, page: Any) -> None:
        if not signal_id or context is None or playwright is None:
            return
        _OPEN_ASSISTED_SESSIONS[str(signal_id)] = {
            "playwright": playwright,
            "context": context,
            "page": page,
            "updated_at": _now_iso(),
        }


async def execute_prepare_request(
    request: PrepareBet365Request,
    *,
    settings: Settings | None = None,
    store: StateStore | None = None,
    assisted_chat_id: int | None = None,
) -> PrepareBet365Response:
    current_settings = settings or load_settings()
    if not current_settings.bet365_assisted_enabled:
        return PrepareBet365Response(
            ok=False,
            status="disabled",
            message="Executor assistido Bet365 está desativado neste ambiente.",
            signal_id=request.signal_id,
        )
    signal_id = str(request.signal_id or "").strip()
    if not signal_id:
        return PrepareBet365Response(
            ok=False,
            status="signal_id_required",
            message="signal_id obrigatório para registrar histórico.",
            signal_id=request.signal_id,
        )
    if float(request.stake) > float(current_settings.max_assisted_stake):
        return PrepareBet365Response(
            ok=False,
            status="stake_above_maximum",
            message=(
                f"Stake {float(request.stake):.2f} acima do máximo permitido "
                f"{float(current_settings.max_assisted_stake):.2f}."
            ),
            signal_id=signal_id,
        )

    executor = Bet365AssistedExecutor(current_settings)
    response = await executor.prepare_entry(request)

    target_store = store or StateStore(current_settings.state_file)
    if response.signal_id:
        persist_prepare_response(
            target_store,
            request,
            response,
            assisted_chat_id=assisted_chat_id,
        )
    return response


__all__ = [
    "Bet365AssistedExecutor",
    "PrepareBet365Request",
    "PrepareBet365Response",
    "_OPEN_ASSISTED_SESSIONS",
    "assisted_session_snapshot",
    "build_prepare_request_from_signal",
    "close_assisted_session",
    "confirm_prepared_signal",
    "execute_prepare_request",
    "ignore_signal_for_assisted_flow",
    "monitor_signal_without_entry",
    "persist_prepare_response",
]
