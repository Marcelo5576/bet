from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx
from src.cache import CacheResult, get_runtime_cache
from src.rate_limiter import get_provider_limiter, retry_after_seconds, sanitize_text
from src.request_queue_service import get_request_queue_service
from src.usage_metrics import UsageTracker, estimate_text_tokens

_GEMINI_PROVIDER = "gemini"
_GEMINI_BACKOFF_DELAYS = (2, 5, 10, 30)
_GEMINI_CACHE_TTL_SECONDS = 15 * 60


async def refine_signal(
    signal: dict,
    api_key: str | None,
    model: str,
    learning_context: dict[str, Any] | None = None,
    usage_tracker: UsageTracker | None = None,
    input_cost_per_1m_brl: float = 0.0,
    output_cost_per_1m_brl: float = 0.0,
    *,
    max_rpm: int = 10,
    ttl_seconds: int = _GEMINI_CACHE_TTL_SECONDS,
    cooldown_seconds: int = 60,
    min_score: int = 60,
    min_confidence: int = 55,
    user_id: str | int | None = None,
) -> dict:
    if not api_key:
        return signal
    cache = get_runtime_cache()
    limiter = get_provider_limiter()
    request_queue = get_request_queue_service()
    cache_key = _refine_cache_key(signal)
    cached = cache.get(cache_key)
    if cached:
        return _apply_cached_refine(signal, cached)
    if not _should_refine_signal(signal, min_score=min_score, min_confidence=min_confidence):
        return signal

    resolved_user_id = _resolve_user_id(user_id, signal)
    user_cooldown = request_queue.user_status(_GEMINI_PROVIDER, resolved_user_id)
    if user_cooldown.active:
        signal["gemini_note"] = (
            "Gemini em cooldown para este usuario. "
            f"Nova tentativa em {user_cooldown.wait_seconds}s."
        )
        signal["gemini_user_cooldown"] = True
        return signal

    fallback_cached = cache.get(cache_key, allow_stale=True)
    decision = limiter.acquire(_GEMINI_PROVIDER, max_rpm)
    if not decision.allowed:
        if fallback_cached:
            return _apply_cached_refine(signal, fallback_cached)
        signal["gemini_note"] = (
            "Gemini em espera por limite local."
            if not decision.cooling_down
            else "Gemini em cooldown temporario."
        )
        return signal

    model_name = model.removeprefix("models/")
    prompt = (
        "Voce e um analista quantitativo de futebol ao vivo. Avalie o sinal em "
        "portugues com disciplina, sem prometer lucro e sem recomendar aposta "
        "automatica. Considere estatisticas do jogo, minuto, placar, odds, "
        "probabilidade estimada, probabilidade implicita, edge de valor, stake "
        "sugerida e historico de acertos. Mantenha a decisao original, mas "
        "explique quando a entrada deve ser aguardada por falta de valor. "
        "Responda em no maximo 900 caracteres, direto ao ponto, sem markdown longo. "
        f"Sinal: {signal}. Historico resumido: {learning_context or {}}"
    )
    prompt_cache_key = _prompt_cache_key("refine_signal", prompt)
    prompt_cached = cache.get(prompt_cache_key)
    if prompt_cached:
        return _apply_cached_refine(signal, prompt_cached)
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent"
    )
    try:
        payload, text, response_bytes = await request_queue.run(
            _GEMINI_PROVIDER,
            prompt_cache_key,
            lambda: _call_gemini(
                url=url,
                api_key=api_key,
                prompt=prompt,
                cooldown_seconds=cooldown_seconds,
            ),
            max_concurrency=2,
        )
    except Exception as exc:
        error_text = sanitize_text(str(exc), api_key)
        if "429" in error_text or "rate" in error_text.lower():
            request_queue.cooldown_user(
                _GEMINI_PROVIDER,
                resolved_user_id,
                cooldown_seconds,
                reason=error_text[:160],
            )
        _track_gemini(
            usage_tracker,
            operation="refine_signal",
            prompt=prompt,
            response_text="",
            usage=None,
            response_bytes=0,
            success=False,
            error=error_text,
            input_cost_per_1m_brl=input_cost_per_1m_brl,
            output_cost_per_1m_brl=output_cost_per_1m_brl,
        )
        if fallback_cached:
            return _apply_cached_refine(signal, fallback_cached)
        signal["gemini_note"] = f"Gemini indisponivel: {error_text}"
        return signal

    _track_gemini(
        usage_tracker,
        operation="refine_signal",
        prompt=prompt,
        response_text=text,
        usage=payload.get("usageMetadata"),
        response_bytes=response_bytes,
        success=True,
        error=None,
        input_cost_per_1m_brl=input_cost_per_1m_brl,
        output_cost_per_1m_brl=output_cost_per_1m_brl,
    )
    note = text.strip()[:1200]
    cached_payload = {"note": note}
    cache.set(cache_key, cached_payload, ttl_seconds, stale_seconds=max(ttl_seconds * 6, cooldown_seconds * 2))
    cache.set(prompt_cache_key, cached_payload, ttl_seconds, stale_seconds=max(ttl_seconds * 6, cooldown_seconds * 2))
    signal["gemini_note"] = note
    return signal


async def answer_question(
    question: str,
    context: dict,
    api_key: str | None,
    model: str,
    usage_tracker: UsageTracker | None = None,
    input_cost_per_1m_brl: float = 0.0,
    output_cost_per_1m_brl: float = 0.0,
    *,
    max_rpm: int = 10,
    cooldown_seconds: int = 60,
    ttl_seconds: int = _GEMINI_CACHE_TTL_SECONDS,
    user_id: str | int | None = None,
) -> str:
    if not api_key:
        return "Gemini nao esta configurado no momento."
    cache = get_runtime_cache()
    request_queue = get_request_queue_service()
    resolved_user_id = _resolve_user_id(user_id, context)
    user_cooldown = request_queue.user_status(_GEMINI_PROVIDER, resolved_user_id)
    if user_cooldown.active:
        return f"IA em cooldown para este usuario. Tente novamente em {user_cooldown.wait_seconds}s."
    cache_seed = {"question": question, "context": _compact_context_for_cache(context)}
    cache_key = _prompt_cache_key("answer_question", cache_seed)
    cached = cache.get(cache_key)
    if cached:
        return str(cached.value or "")[:1400]

    decision = get_provider_limiter().acquire(_GEMINI_PROVIDER, max_rpm)
    if not decision.allowed:
        stale = cache.get(cache_key, allow_stale=True)
        if stale:
            return str(stale.value or "")[:1400]
        if decision.cooling_down:
            return "IA em cooldown rapido para respeitar o limite do provider. Tente de novo em instantes."
        return "IA em espera por limite local de requisicoes. Vamos tentar de novo daqui a pouco."

    model_name = model.removeprefix("models/")
    prompt = (
        "Voce e o assistente do BetSignal Cloud. Responda em portugues, com "
        "clareza, objetividade, sem prometer lucro e sem sugerir aposta automatica. Use o "
        "contexto do jogo ativo, historico, metricas e memoria Supabase quando existirem. "
        "Se a memoria Supabase trouxer ligas, times ou mercados parecidos, use isso "
        "como evidencia estatistica e diga quando a amostra ainda for pequena. "
        "Formato obrigatorio, curto e especifico: "
        "1) Decisao: ENTRAR/AGUARDAR/SAIR. "
        "2) Onde entrar: mercado + selecao + linha + odd. "
        "3) Mercados: 1X2, Gols, Escanteios, Asiatica/Handicap, cada um com odd se existir; se nao existir diga 'sem odd'. "
        "4) Risco em uma frase. "
        "Nao escreva texto longo. Resposta em no maximo 1200 caracteres. "
        f"Pergunta: {question}. Contexto: {context}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent"
    )
    try:
        payload, text, response_bytes = await request_queue.run(
            _GEMINI_PROVIDER,
            cache_key,
            lambda: _call_gemini(
                url=url,
                api_key=api_key,
                prompt=prompt,
                cooldown_seconds=cooldown_seconds,
            ),
            max_concurrency=2,
        )
        _track_gemini(
            usage_tracker,
            operation="answer_question",
            prompt=prompt,
            response_text=text,
            usage=payload.get("usageMetadata"),
            response_bytes=response_bytes,
            success=True,
            error=None,
            input_cost_per_1m_brl=input_cost_per_1m_brl,
            output_cost_per_1m_brl=output_cost_per_1m_brl,
        )
        answer = text.strip()[:1400]
        cache.set(cache_key, answer, ttl_seconds, stale_seconds=max(ttl_seconds * 6, cooldown_seconds * 2))
        return answer
    except Exception as exc:
        error_text = sanitize_text(str(exc), api_key)
        if "429" in error_text or "rate" in error_text.lower():
            request_queue.cooldown_user(
                _GEMINI_PROVIDER,
                resolved_user_id,
                cooldown_seconds,
                reason=error_text[:160],
            )
        stale = cache.get(cache_key, allow_stale=True)
        if stale:
            return str(stale.value or "")[:1400]
        _track_gemini(
            usage_tracker,
            operation="answer_question",
            prompt=prompt,
            response_text="",
            usage=None,
            response_bytes=0,
            success=False,
            error=error_text,
            input_cost_per_1m_brl=input_cost_per_1m_brl,
            output_cost_per_1m_brl=output_cost_per_1m_brl,
        )
        return f"Gemini indisponivel agora: {error_text}"


def _track_gemini(
    usage_tracker: UsageTracker | None,
    *,
    operation: str,
    prompt: str,
    response_text: str,
    usage: dict[str, Any] | None,
    response_bytes: int,
    success: bool,
    error: Exception | str | None,
    input_cost_per_1m_brl: float,
    output_cost_per_1m_brl: float,
) -> None:
    if not usage_tracker:
        return
    prompt_tokens = 0
    output_tokens = 0
    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("promptTokenCount") or usage.get("inputTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or usage.get("outputTokenCount") or 0)
        if not prompt_tokens and int(usage.get("totalTokenCount") or 0) and not output_tokens:
            prompt_tokens = int(usage.get("totalTokenCount") or 0)
    if not prompt_tokens:
        prompt_tokens = estimate_text_tokens(prompt)
    if not output_tokens and response_text:
        output_tokens = estimate_text_tokens(response_text)
    estimated_cost_brl = round(
        (prompt_tokens / 1_000_000) * float(input_cost_per_1m_brl or 0)
        + (output_tokens / 1_000_000) * float(output_cost_per_1m_brl or 0),
        6,
    )
    usage_tracker.record(
        "gemini",
        category="ai",
        request_count=1,
        success=success,
        input_tokens=prompt_tokens,
        output_tokens=output_tokens,
        response_bytes=response_bytes,
        estimated_cost_brl=estimated_cost_brl,
        operation=operation,
        error=sanitize_text(str(error), None)[:240] if error else None,
    )


async def _call_gemini(
    *,
    url: str,
    api_key: str,
    prompt: str,
    cooldown_seconds: int,
) -> tuple[dict[str, Any], str, int]:
    last_error: Exception | None = None
    for attempt, fallback_delay in enumerate(_GEMINI_BACKOFF_DELAYS, start=1):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    url,
                    params={"key": api_key},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                )
            if response.status_code == 429:
                last_error = RuntimeError(_gemini_429_message(response))
                delay = retry_after_seconds(response.headers.get("Retry-After")) or fallback_delay
                if attempt >= len(_GEMINI_BACKOFF_DELAYS):
                    break
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            return payload, text, len(response.content)
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                last_error = RuntimeError(_gemini_429_message(exc.response))
                delay = retry_after_seconds(exc.response.headers.get("Retry-After")) or fallback_delay
                if attempt >= len(_GEMINI_BACKOFF_DELAYS):
                    break
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(sanitize_text(str(exc), api_key)) from exc
        except Exception as exc:
            raise RuntimeError(sanitize_text(str(exc), api_key)) from exc
    get_provider_limiter().cooldown(
        _GEMINI_PROVIDER,
        cooldown_seconds,
        reason="429 do Gemini",
    )
    if last_error:
        raise RuntimeError(sanitize_text(str(last_error), api_key))
    raise RuntimeError("Gemini indisponivel.")


def _apply_cached_refine(signal: dict[str, Any], cached: CacheResult) -> dict[str, Any]:
    payload = cached.value if isinstance(cached.value, dict) else {"note": str(cached.value or "")}
    note = str(payload.get("note") or "").strip()
    if note:
        signal["gemini_note"] = note[:1200]
    signal["gemini_cached"] = True
    signal["gemini_cache_age_seconds"] = cached.age_seconds
    return signal


def _refine_cache_key(signal: dict[str, Any]) -> str:
    game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
    game_id = str(game.get("game_id") or signal.get("signal_id") or "unknown").strip().lower()
    market = str(signal.get("market") or _best_market_from_signal(signal) or "default").strip().lower()
    market = "".join(ch if ch.isalnum() else "_" for ch in market).strip("_") or "default"
    return f"refine_signal:{game_id}:{market}"


def _prompt_cache_key(prefix: str, payload: Any) -> str:
    try:
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        serialized = str(payload)
    digest = hashlib.sha256(serialized.encode("utf-8", errors="ignore")).hexdigest()[:32]
    return f"gemini:{prefix}:{digest}"


def _resolve_user_id(explicit_user_id: str | int | None, payload: Any) -> str | int | None:
    if explicit_user_id is not None:
        return explicit_user_id
    if isinstance(payload, dict):
        for key in ("user_id", "client_id", "chat_id", "session_id"):
            value = payload.get(key)
            if value not in (None, ""):
                return value
        game = payload.get("game")
        if isinstance(game, dict):
            for key in ("user_id", "client_id", "chat_id", "session_id"):
                value = game.get(key)
                if value not in (None, ""):
                    return value
    return None


def _compact_context_for_cache(context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    keys = (
        "game_id",
        "fixture_id",
        "market",
        "minute",
        "score",
        "decision",
        "entry_score",
        "confidence",
        "ev",
        "risk_level",
    )
    compact = {key: context.get(key) for key in keys if key in context}
    if not compact:
        return {"fingerprint": str(context)[:700]}
    return compact


def _should_refine_signal(signal: dict[str, Any], *, min_score: int, min_confidence: int) -> bool:
    score = _safe_int(signal.get("entry_score"))
    confidence = _safe_int(signal.get("confidence"))
    odd = _signal_target_odd(signal)
    if odd is None or odd <= 1.0:
        return False
    return score >= int(min_score or 0) or confidence >= int(min_confidence or 0)


def _signal_target_odd(signal: dict[str, Any]) -> float | None:
    target = _safe_float(signal.get("target_odds"))
    if target and target > 1.0:
        return target
    for rec in signal.get("market_recommendations") or []:
        if not isinstance(rec, dict):
            continue
        odds = _safe_float(rec.get("odds"))
        if odds and odds > 1.0:
            return odds
    return None


def _best_market_from_signal(signal: dict[str, Any]) -> str:
    for rec in signal.get("market_recommendations") or []:
        if not isinstance(rec, dict):
            continue
        action = str(rec.get("action") or "").upper()
        if action == "ENTRAR":
            return str(rec.get("market") or "")
    return str(signal.get("market") or "")


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None
    return round(parsed, 3)


def _gemini_429_message(response: httpx.Response) -> str:
    retry_after = retry_after_seconds(response.headers.get("Retry-After"))
    if retry_after:
        return f"Gemini em 429. Retry-After={retry_after}s."
    return "Gemini em 429 por excesso de chamadas."
