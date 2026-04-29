from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


FOOTBALL_DATA_SOURCES: list[dict[str, Any]] = [
    {
        "source_id": "api_football",
        "name": "API-Football",
        "url": "https://www.api-football.com/",
        "tier": "api_pago",
        "role": "live_stats_odds",
        "coverage": "livescore, fixtures, standings, bookmakers, odds, eventos, lineups, jogadores, estatisticas e previsoes",
        "best_for": "base atual do agente; bom primeiro fornecedor para jogos ao vivo, odds 1X2, gols, asiatica e escanteios quando o plano cobre.",
        "integration": "ja suportado via API_FOOTBALL_KEY",
        "priority": 1,
    },
    {
        "source_id": "sportmonks",
        "name": "Sportmonks Football API",
        "url": "https://www.sportmonks.com/football-api",
        "tier": "api_pago_trial",
        "role": "live_stats_events_odds",
        "coverage": "2500+ ligas, livescores, eventos, estatisticas, lineups, H2H, odds, previsoes e xG em planos/add-ons",
        "best_for": "segunda fonte para validar placar, eventos, estatisticas, escanteios, cartoes e odds in-play.",
        "integration": "recomendado como proximo provider/fallback",
        "priority": 2,
    },
    {
        "source_id": "the_odds_api",
        "name": "The Odds API",
        "url": "https://the-odds-api.com/",
        "tier": "api_pago_free_tier",
        "role": "odds_multicasa",
        "coverage": "odds atuais por liga e regiao, mercados h2h, spreads/handicap e totals/over-under",
        "best_for": "comparar odds de varias casas e calcular consenso/fair odds sem scraping direto de book.",
        "integration": "adicionar como provider de odds",
        "priority": 3,
    },
    {
        "source_id": "odds_api_io",
        "name": "Odds-API.io",
        "url": "https://docs.odds-api.io/",
        "tier": "api_pago",
        "role": "odds_realtime_multicasa",
        "coverage": "odds em tempo real de 250+ bookmakers, 100+ mercados, scores e props conforme plano",
        "best_for": "odds ao vivo, comparacao entre casas e deteccao de value/arbitragem.",
        "integration": "adicionar como provider de odds quando houver chave",
        "priority": 4,
    },
    {
        "source_id": "football_data_co_uk",
        "name": "Football-Data.co.uk",
        "url": "https://www.football-data.co.uk/",
        "tier": "gratis_csv",
        "role": "historico_treino",
        "coverage": "CSV/Excel com resultados historicos e odds para analise quantitativa, atualizado periodicamente",
        "best_for": "treinar backtests, calibrar odds justas, ROI por liga e validar estrategias fora do ao vivo.",
        "integration": "ingestao agendada ativa (source_scraper) para memoria da IA por liga",
        "priority": 5,
    },
    {
        "source_id": "statsbomb_open_data",
        "name": "StatsBomb Open Data",
        "url": "https://github.com/statsbomb/open-data",
        "tier": "gratis_open_data",
        "role": "eventos_xg_treino",
        "coverage": "eventos detalhados por partida, xG, freeze frames e alguns dados 360 em competicoes selecionadas",
        "best_for": "ensinar modelo sobre padroes de chutes, xG, zonas de ataque e qualidade das chances.",
        "integration": "adicionar job offline para features de xG",
        "priority": 6,
    },
    {
        "source_id": "football_data_org",
        "name": "football-data.org",
        "url": "https://www.football-data.org/",
        "tier": "api_free_pago",
        "role": "fixtures_tables_scores",
        "coverage": "scores, fixtures, tabelas, elencos e substituicoes/lineups conforme plano",
        "best_for": "fallback barato para calendario, competicoes e classificacao.",
        "integration": "suportado via FOOTBALL_DATA_ORG_TOKEN como fallback oficial",
        "priority": 7,
    },
    {
        "source_id": "flashscore",
        "name": "Flashscore",
        "url": "https://www.flashscore.com/",
        "tier": "sem_api_publica",
        "role": "nao_integrar_sem_licenca",
        "coverage": "placares, odds e estatisticas exibidos no site/produto da Livesport",
        "best_for": "somente avaliacao comercial; nao usar scraping ou endpoint interno sem autorizacao/licenca.",
        "integration": "bloqueado no app ate existir contrato/API publica",
        "priority": 8,
    },
    {
        "source_id": "openfootball_json",
        "name": "openfootball/football.json",
        "url": "https://github.com/openfootball/football.json",
        "tier": "gratis_public_domain",
        "role": "fixtures_results_open",
        "coverage": "dados publicos em JSON com fixtures e resultados historicos de varias ligas",
        "best_for": "fallback gratuito para nomes de times, calendario e historico simples.",
        "integration": "adicionar ingestao leve por JSON",
        "priority": 9,
    },
    {
        "source_id": "sportradar",
        "name": "Sportradar Soccer APIs",
        "url": "https://developer.sportradar.com/soccer/docs/soccer-ig-api-basics",
        "tier": "enterprise",
        "role": "enterprise_live_stats_odds",
        "coverage": "API soccer e extended API com estatisticas profundas, dados ao vivo e produtos de odds via Sportradar/Betradar",
        "best_for": "caminho enterprise se o projeto crescer e precisar de SLA forte.",
        "integration": "avaliar apenas se custo fizer sentido",
        "priority": 10,
    },
]


def source_memory_rows() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "memory_id": f"source_catalog:{source['source_id']}",
            "scope": "source_catalog",
            "subject": source["name"],
            "source": source["source_id"],
            "sample_size": 0,
            "hit_rate": None,
            "roi_units": None,
            "profit_units": 0,
            "avg_confidence": None,
            "avg_edge": None,
            "notes": (
                f"{source['name']} ({source['tier']}): {source['best_for']} "
                f"Cobertura: {source['coverage']}."
            ),
            "payload": source,
            "updated_at": now,
        }
        for source in FOOTBALL_DATA_SOURCES
    ]
