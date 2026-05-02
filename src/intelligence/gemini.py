from __future__ import annotations

from typing import Any

import httpx
from src.usage_metrics import UsageTracker, estimate_text_tokens


async def refine_signal(
    signal: dict,
    api_key: str | None,
    model: str,
    learning_context: dict[str, Any] | None = None,
    usage_tracker: UsageTracker | None = None,
    input_cost_per_1m_brl: float = 0.0,
    output_cost_per_1m_brl: float = 0.0,
) -> dict:
    if not api_key:
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
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:generateContent"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            response.raise_for_status()
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:
        _track_gemini(
            usage_tracker,
            operation="refine_signal",
            prompt=prompt,
            response_text="",
            usage=None,
            response_bytes=0,
            success=False,
            error=exc,
            input_cost_per_1m_brl=input_cost_per_1m_brl,
            output_cost_per_1m_brl=output_cost_per_1m_brl,
        )
        signal["gemini_note"] = f"Gemini indisponivel: {exc}"
        return signal

    _track_gemini(
        usage_tracker,
        operation="refine_signal",
        prompt=prompt,
        response_text=text,
        usage=payload.get("usageMetadata"),
        response_bytes=len(response.content),
        success=True,
        error=None,
        input_cost_per_1m_brl=input_cost_per_1m_brl,
        output_cost_per_1m_brl=output_cost_per_1m_brl,
    )
    signal["gemini_note"] = text.strip()[:1200]
    return signal


async def answer_question(
    question: str,
    context: dict,
    api_key: str | None,
    model: str,
    usage_tracker: UsageTracker | None = None,
    input_cost_per_1m_brl: float = 0.0,
    output_cost_per_1m_brl: float = 0.0,
) -> str:
    if not api_key:
        return "Gemini nao esta configurado no momento."

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
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            response.raise_for_status()
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            _track_gemini(
                usage_tracker,
                operation="answer_question",
                prompt=prompt,
                response_text=text,
                usage=payload.get("usageMetadata"),
                response_bytes=len(response.content),
                success=True,
                error=None,
                input_cost_per_1m_brl=input_cost_per_1m_brl,
                output_cost_per_1m_brl=output_cost_per_1m_brl,
            )
            return text.strip()[:1400]
    except Exception as exc:
        _track_gemini(
            usage_tracker,
            operation="answer_question",
            prompt=prompt,
            response_text="",
            usage=None,
            response_bytes=0,
            success=False,
            error=exc,
            input_cost_per_1m_brl=input_cost_per_1m_brl,
            output_cost_per_1m_brl=output_cost_per_1m_brl,
        )
        return f"Gemini indisponivel agora: {exc}"


def _track_gemini(
    usage_tracker: UsageTracker | None,
    *,
    operation: str,
    prompt: str,
    response_text: str,
    usage: dict[str, Any] | None,
    response_bytes: int,
    success: bool,
    error: Exception | None,
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
        error=str(error)[:240] if error else None,
    )
