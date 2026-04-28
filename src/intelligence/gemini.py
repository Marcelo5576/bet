from __future__ import annotations

from typing import Any

import httpx


async def refine_signal(
    signal: dict,
    api_key: str | None,
    model: str,
    learning_context: dict[str, Any] | None = None,
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
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:
        signal["gemini_note"] = f"Gemini indisponivel: {exc}"
        return signal

    signal["gemini_note"] = text.strip()[:1200]
    return signal


async def answer_question(
    question: str,
    context: dict,
    api_key: str | None,
    model: str,
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
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()[:1400]
    except Exception as exc:
        return f"Gemini indisponivel agora: {exc}"
