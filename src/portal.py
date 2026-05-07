from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import smtplib
import sqlite3
from typing import Any
import unicodedata


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money_or(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return round(float(default), 2)


def _hash_password(password: str) -> str:
    iterations = 200_000
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iter_raw, salt_hex, digest_hex = password_hash.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    try:
        iterations = int(iter_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    current = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(current, expected)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_session_token(user_id: int, secret: str, hours: int) -> str:
    payload = {
        "uid": int(user_id),
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp()),
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return f"{body}.{_b64url(sig)}"


def read_session_token(token: str | None, secret: str) -> int | None:
    if not token or "." not in token:
        return None
    body, sig = token.split(".", 1)
    expected = _b64url(hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return int(payload.get("uid"))
    except Exception:
        return None


@dataclass
class PasswordResetDelivery:
    ok: bool
    message: str


DEFAULT_AI_SUPPORT_SKILLS: list[dict[str, Any]] = [
    {
        "skill_id": "public_login_password",
        "title": "Login e senha",
        "intent": "Resolver dúvidas de acesso, login, cadastro e recuperação de senha.",
        "keywords": ["senha", "login", "entrar", "acesso", "cadastro", "recuperar"],
        "answer": "Acesse em /login. Se esqueceu a senha, use 'Esqueci minha senha'. Nova conta: /signup.",
        "priority": 10,
    },
    {
        "skill_id": "public_telegram_connect",
        "title": "Conectar Telegram",
        "intent": "Explicar como ligar o Telegram à conta do cliente.",
        "keywords": ["telegram", "bot", "chat id", "chatid", "alerta", "notificacao", "notificação"],
        "answer": "Para receber alertas: abra o bot, use /chatid, cole o ID na Área do Cliente e salve as notificações.",
        "priority": 20,
    },
    {
        "skill_id": "public_scanner_live",
        "title": "Scanner ao vivo",
        "intent": "Explicar scanner, ciclos de monitoramento e ausência de jogos.",
        "keywords": ["scanner", "jogo", "jogos", "ao vivo", "sinal", "sinais", "odds", "sem jogos"],
        "answer": "O scanner lê jogos ao vivo, odds e pressão. Sem jogo ativo usa o ciclo configurado; com jogo ativo, entra no ciclo curto.",
        "priority": 30,
    },
    {
        "skill_id": "public_plans_trial",
        "title": "Planos e teste",
        "intent": "Responder dúvidas de preço, teste grátis, cobrança e cancelamento.",
        "keywords": ["plano", "preco", "preço", "valor", "teste", "gratis", "grátis", "cobrar", "cancelar", "fatura"],
        "answer": "Planos têm 7 dias de teste. Starter é individual, Pro tem IA com memória, Team é multioperador.",
        "priority": 40,
    },
    {
        "skill_id": "plans_ai_agent_scope",
        "title": "Agente por cliente",
        "intent": "Explicar como a IA funciona para cada cliente e plano.",
        "keywords": ["agente", "cliente", "intermediario", "intermediário", "pro", "memoria", "memória", "isolado", "individual"],
        "answer": "No Pro, cada cliente tem IA lógica própria: histórico, Telegram, preferências e banca separados.",
        "priority": 41,
    },
    {
        "skill_id": "public_risk_notice",
        "title": "Gestão de risco",
        "intent": "Reforçar responsabilidade e limites das sugestões.",
        "keywords": ["risco", "green", "red", "lucro", "garantia", "banca", "stake", "responsabilidade"],
        "answer": "A IA apoia análise e risco, mas não promete lucro. A decisão final é sempre do usuário.",
        "priority": 60,
    },
    {
        "skill_id": "risk_bankroll_units",
        "title": "Banca e unidades",
        "intent": "Orientar banca, unidade e exposição por entrada.",
        "keywords": ["banca", "unidade", "unidades", "stake", "exposicao", "exposição", "gestao", "gestão"],
        "answer": "Separe uma banca fixa e opere por unidades. Evite stake alta em um único jogo.",
        "priority": 70,
    },
    {
        "skill_id": "client_bankroll_panel",
        "title": "Banca do cliente",
        "intent": "Explicar controle de banca, entrada e saída monitorada pela IA.",
        "keywords": ["saldo", "banca", "depositar", "entrada", "monitorar", "saida", "saída", "deduzir"],
        "answer": "Na conta, informe banca e entrada. A IA monitora; Green credita retorno, Red mantém débito.",
        "priority": 71,
    },
    {
        "skill_id": "telegram_entry_form",
        "title": "Entrada pelo Telegram",
        "intent": "Explicar o formulário de entrada pelo botão Entrei no Telegram.",
        "keywords": ["telegram", "entrei", "odd", "odds", "valor", "formulario", "formulário", "monitoramento"],
        "answer": "No Telegram, clique Entrei. Mercado e odd vêm sugeridos; envie só o valor ou valor | odd.",
        "priority": 72,
    },
    {
        "skill_id": "scanner_active_monitoring",
        "title": "Monitoramento ativo",
        "intent": "Explicar intervalo do scanner após escolher um jogo.",
        "keywords": ["monitoramento", "2 minutos", "dois minutos", "jogo escolhido", "scanner", "ativo", "sair"],
        "answer": "Com jogo escolhido, o scanner monitora a cada 2 minutos. Ao sair, volta ao ciclo normal.",
        "priority": 73,
    },
    {
        "skill_id": "ai_live_efficiency",
        "title": "Eficiência ao vivo",
        "intent": "Orientar leitura curta de valor, risco e saída dinâmica.",
        "keywords": ["eficiencia", "eficiência", "edge", "pressao", "pressão", "tempo real", "saida", "saída"],
        "answer": "A IA prioriza edge, pressão real, odd justa, risco baixo e saída antes de reforçar entrada.",
        "priority": 74,
    },
    {
        "skill_id": "security_operational_limits",
        "title": "Segurança operacional",
        "intent": "Explicar proteções de sessão, origem e botões sensíveis.",
        "keywords": ["seguranca", "segurança", "csrf", "sessao", "sessão", "login", "callback", "telegram"],
        "answer": "Ações sensíveis exigem sessão válida, origem confiável e callback reconhecido pelo sistema.",
        "priority": 75,
    },
    {
        "skill_id": "risk_no_chasing",
        "title": "Não perseguir red",
        "intent": "Evitar aumento emocional de stake após perdas.",
        "keywords": ["recuperar", "perdi", "red", "martingale", "dobrar", "loss", "prejuizo", "prejuízo"],
        "answer": "Depois de red, não dobre stake. Pare, registre o motivo e espere nova leitura limpa.",
        "priority": 80,
    },
    {
        "skill_id": "risk_record_every_bet",
        "title": "Registrar entradas",
        "intent": "Incentivar histórico real para aprendizado.",
        "keywords": ["historico", "histórico", "registrar", "green", "red", "anular", "resultado", "aprendizado"],
        "answer": "Registre odd, mercado, stake e resultado. Sem histórico real, a IA aprende devagar.",
        "priority": 90,
    },
    {
        "skill_id": "risk_daily_stop",
        "title": "Limite diário",
        "intent": "Aplicar disciplina em sequência negativa.",
        "keywords": ["limite", "diario", "diário", "sequencia", "sequência", "stop", "pausar", "disciplina"],
        "answer": "Defina limite diário de reds. Ao bater o limite, reduza risco ou pare a operação.",
        "priority": 100,
    },
    {
        "skill_id": "live_odds_quality",
        "title": "Qualidade das odds",
        "intent": "Explicar leitura de odds e qualidade dos dados.",
        "keywords": ["odds", "odd", "valor", "edge", "probabilidade", "mercado", "justa"],
        "answer": "A IA compara odd alvo, probabilidade estimada e edge. Odd ruim reduz valor da entrada.",
        "priority": 110,
    },
    {
        "skill_id": "live_data_quality",
        "title": "Qualidade dos dados",
        "intent": "Alertar sobre dados incompletos ou atrasados.",
        "keywords": ["dados", "atrasado", "estatistica", "estatística", "pressao", "pressão", "placar"],
        "answer": "Se dados estiverem incompletos ou atrasados, trate o sinal como fraco e reduza exposição.",
        "priority": 120,
    },
    {
        "skill_id": "support_import_bets",
        "title": "Importar aposta",
        "intent": "Orientar importação manual de entradas reais.",
        "keywords": ["importar", "bet365", "aposta", "bilhete", "entrada real", "colar"],
        "answer": "Cole o texto bruto da aposta em Importar. Depois confira mercado, odd, valor e resultado.",
        "priority": 130,
    },
    {
        "skill_id": "product_dashboard",
        "title": "Dashboard",
        "intent": "Explicar áreas principais do dashboard.",
        "keywords": ["dashboard", "painel", "app", "area", "área", "cliente", "operacional"],
        "answer": "O dashboard mostra scanner, histórico, Green/Red, perfil, Telegram, planos e suporte.",
        "priority": 140,
    },
    {
        "skill_id": "ai_memory_supabase",
        "title": "Memória IA",
        "intent": "Explicar memória operacional e Supabase.",
        "keywords": ["memoria", "memória", "supabase", "skills", "aprende", "aprendizado", "ia"],
        "answer": "A IA usa histórico e skills curtas. Com Supabase ativo, essas skills sincronizam e a memória fica persistente.",
        "priority": 150,
    },
    {
        "skill_id": "compliance_brazil",
        "title": "Regulação Brasil",
        "intent": "Resposta curta sobre cuidado regulatório no Brasil.",
        "keywords": ["brasil", "regulacao", "regulação", "lei", "aposta", "responsavel", "responsável", "bet.br"],
        "answer": "No Brasil, apostas têm regras federais. Use linguagem educativa e jogo responsável.",
        "priority": 160,
    },
    {
        "skill_id": "responsible_gambling",
        "title": "Jogo responsável",
        "intent": "Reforçar jogo responsável e autoproteção.",
        "keywords": ["compulsao", "compulsão", "vicio", "vício", "responsavel", "responsável", "autoexclusao", "autoexclusão"],
        "answer": "Se apostar virou pressão ou perda de controle, pause e busque ajuda. Saúde vem primeiro.",
        "priority": 170,
    },
    {
        "skill_id": "sales_positioning",
        "title": "Posicionamento comercial",
        "intent": "Explicar proposta sem promessa de lucro.",
        "keywords": ["promessa", "resultado", "vender", "oferta", "garantia", "ganhar"],
        "answer": "Venda como apoio estatístico e disciplina operacional, nunca como garantia de lucro.",
        "priority": 180,
    },
    {
        "skill_id": "product_differentiators",
        "title": "Diferenciais ApexGol",
        "intent": "Explicar diferenciais do produto frente a scanners, tips e bots simples.",
        "keywords": ["diferencial", "concorrente", "comparar", "scanner", "tip", "bot", "mercado", "vantagem"],
        "answer": "Diferencial: scanner quantitativo, Cérebro IA, Telegram, backtesting, gestão de risco e memória Supabase juntos.",
        "priority": 181,
    },
    {
        "skill_id": "team_plan",
        "title": "Plano Team",
        "intent": "Explicar uso para equipe.",
        "keywords": ["team", "equipe", "operadores", "multioperador", "admin", "gestao comercial"],
        "answer": "O Team é para equipes, multioperadores e gestão comercial/admin centralizada.",
        "priority": 190,
    },
    {
        "skill_id": "support_checkout",
        "title": "Diagnóstico",
        "intent": "Orientar diagnóstico quando algo não funciona.",
        "keywords": ["erro", "bug", "nao funciona", "não funciona", "diagnostico", "diagnóstico", "suporte"],
        "answer": "Se algo falhar, informe tela, horário, ação feita e mensagem exibida. Isso acelera correção.",
        "priority": 200,
    },
]

_DEFAULT_SKILL_METADATA: dict[str, dict[str, Any]] = {
    "public_login_password": {"topic": "auth", "section": "access"},
    "public_telegram_connect": {"topic": "telegram", "section": "delivery"},
    "public_scanner_live": {"topic": "scanner", "section": "live"},
    "public_plans_trial": {"topic": "billing", "section": "plans"},
    "plans_ai_agent_scope": {"topic": "product", "section": "agent"},
    "public_risk_notice": {"topic": "risk", "section": "responsibility"},
    "risk_bankroll_units": {"topic": "risk", "section": "bankroll"},
    "client_bankroll_panel": {"topic": "bankroll", "section": "client"},
    "telegram_entry_form": {"topic": "telegram", "section": "entry"},
    "scanner_active_monitoring": {"topic": "scanner", "section": "active"},
    "ai_live_efficiency": {"topic": "ai", "section": "live"},
    "security_operational_limits": {"topic": "security", "section": "ops"},
    "risk_no_chasing": {"topic": "risk", "section": "discipline"},
    "risk_record_every_bet": {"topic": "history", "section": "learning"},
    "risk_daily_stop": {"topic": "risk", "section": "daily-stop"},
    "live_odds_quality": {"topic": "odds", "section": "quality"},
    "live_data_quality": {"topic": "data", "section": "quality"},
    "support_import_bets": {"topic": "import", "section": "slip"},
    "product_dashboard": {"topic": "product", "section": "dashboard"},
    "ai_memory_supabase": {"topic": "memory", "section": "supabase"},
    "compliance_brazil": {"topic": "compliance", "section": "brazil"},
    "responsible_gambling": {"topic": "compliance", "section": "responsible"},
    "sales_positioning": {"topic": "sales", "section": "positioning"},
    "product_differentiators": {"topic": "sales", "section": "differentiators"},
    "team_plan": {"topic": "billing", "section": "team"},
    "support_checkout": {"topic": "support", "section": "diagnostic"},
}


def _decorate_default_skills(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in skills:
        skill = dict(item)
        payload = dict(skill.get("payload") or {})
        payload.update(_DEFAULT_SKILL_METADATA.get(str(skill.get("skill_id") or ""), {}))
        payload.setdefault("version", "2026-05-01")
        payload.setdefault("compact", True)
        payload.setdefault("answer_chars", len(str(skill.get("answer") or "")))
        payload.setdefault("keywords_count", len(skill.get("keywords") or []))
        skill["payload"] = payload
        enriched.append(skill)
    return enriched


DEFAULT_AI_SUPPORT_SKILLS = _decorate_default_skills(DEFAULT_AI_SUPPORT_SKILLS)


class PortalStore:
    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path.as_posix(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists users (
                  id integer primary key autoincrement,
                  email text not null unique,
                  name text not null,
                  avatar_url text,
                  password_hash text not null,
                  is_admin integer not null default 0,
                  plan text not null default 'starter',
                  status text not null default 'trial',
                  monthly_price_brl real not null default 97,
                  trial_started_at text,
                  trial_ends_at text,
                  gateway text,
                  gateway_subscription_id text,
                  created_at text not null,
                  updated_at text not null,
                  last_payment_at text,
                  next_due_at text,
                  cancel_reason text
                );
                create table if not exists password_resets (
                  token text primary key,
                  user_id integer not null,
                  expires_at text not null,
                  used integer not null default 0,
                  created_at text not null,
                  foreign key(user_id) references users(id) on delete cascade
                );
                create table if not exists support_logs (
                  id integer primary key autoincrement,
                  user_id integer not null,
                  role text not null,
                  message text not null,
                  created_at text not null,
                  foreign key(user_id) references users(id) on delete cascade
                );
                create table if not exists payment_logs (
                  id integer primary key autoincrement,
                  user_id integer not null,
                  gateway text not null,
                  amount_brl real not null default 0,
                  status text not null,
                  payment_id text,
                  checkout_url text,
                  created_at text not null,
                  paid_at text,
                  foreign key(user_id) references users(id) on delete cascade
                );
                create table if not exists user_preferences (
                  user_id integer primary key,
                  scan_enabled integer not null default 1,
                  idle_scan_seconds integer not null default 60,
                  active_scan_seconds integer not null default 120,
                  telegram_enabled integer not null default 0,
                  telegram_chat_id text,
                  updated_at text not null,
                  foreign key(user_id) references users(id) on delete cascade
                );
                create table if not exists pricing_config (
                  plan text primary key,
                  monthly_price_brl real not null,
                  updated_at text not null
                );
                create table if not exists system_settings (
                  key text primary key,
                  value text not null,
                  updated_at text not null
                );
                create table if not exists ai_skills (
                  skill_id text primary key,
                  title text not null,
                  intent text not null,
                  keywords_json text not null default '[]',
                  answer text not null,
                  priority integer not null default 100,
                  active integer not null default 1,
                  payload_json text not null default '{}',
                  updated_at text not null
                );
                create table if not exists bankroll_accounts (
                  user_id integer primary key,
                  initial_bankroll_brl real not null default 0,
                  balance_brl real not null default 0,
                  default_stake_percent real not null default 2,
                  updated_at text not null,
                  foreign key(user_id) references users(id) on delete cascade
                );
                create table if not exists bankroll_entries (
                  id integer primary key autoincrement,
                  user_id integer not null,
                  signal_id text,
                  game_label text not null,
                  market text not null,
                  amount_brl real not null,
                  odds real,
                  status text not null default 'open',
                  profit_brl real not null default 0,
                  ai_notes text,
                  opened_at text not null,
                  closed_at text,
                  updated_at text not null,
                  foreign key(user_id) references users(id) on delete cascade
                );
                """
            )
            self._ensure_column(conn, "users", "is_admin", "integer not null default 0")
            self._ensure_column(conn, "users", "avatar_url", "text")
            self._ensure_column(conn, "users", "trial_started_at", "text")
            self._ensure_column(conn, "users", "trial_ends_at", "text")
            self._ensure_column(conn, "users", "gateway", "text")
            self._ensure_column(conn, "users", "gateway_subscription_id", "text")
            self._ensure_column(conn, "user_preferences", "scan_enabled", "integer not null default 1")
            self._ensure_column(conn, "bankroll_accounts", "default_stake_percent", "real not null default 2")
            conn.execute("update user_preferences set active_scan_seconds = 120 where active_scan_seconds = 300")

    def get_system_setting(self, key: str, default: str = "") -> str:
        clean_key = str(key or "").strip()
        if not clean_key:
            return default
        with self._connect() as conn:
            row = conn.execute(
                "select value from system_settings where key = ?",
                (clean_key,),
            ).fetchone()
        if not row:
            return default
        return str(row["value"] or default)

    def set_system_setting(self, key: str, value: str) -> None:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("Chave de configuracao invalida.")
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                insert into system_settings (key, value, updated_at)
                values (?, ?, ?)
                on conflict(key) do update set
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (clean_key, str(value or "").strip(), now),
            )

    def set_system_settings(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set_system_setting(str(key), str(value if value is not None else ""))

    def approved_signal_telegram_config(self) -> dict[str, Any]:
        enabled_raw = self.get_system_setting("telegram_approved_signals_enabled", "0")
        chat_id = self.get_system_setting("telegram_approved_signals_chat_id", "")
        updated_at = self.get_system_setting("telegram_approved_signals_updated_at", "")
        enabled = str(enabled_raw).strip().lower() in {"1", "true", "yes", "on"}
        return {
            "enabled": enabled,
            "chat_id": chat_id,
            "updated_at": updated_at,
        }

    def approved_signal_telegram_chat_ids(self) -> list[int]:
        config = self.approved_signal_telegram_config()
        if not config.get("enabled"):
            return []
        raw_items = str(config.get("chat_id") or "").replace(";", ",").split(",")
        result: list[int] = []
        for raw in raw_items:
            clean = raw.strip()
            if not clean:
                continue
            try:
                result.append(int(clean))
            except ValueError:
                continue
        return sorted(set(result))

    def seed_ai_skills(self, skills: list[dict[str, Any]]) -> None:
        now = _now_iso()
        with self._connect() as conn:
            for skill in skills:
                skill_id = str(skill.get("skill_id") or "").strip()
                answer = str(skill.get("answer") or "").strip()
                if not skill_id or not answer:
                    continue
                keywords = skill.get("keywords") or []
                if isinstance(keywords, str):
                    keywords = [item.strip() for item in keywords.split(",") if item.strip()]
                payload = skill.get("payload") or {}
                conn.execute(
                    """
                    insert into ai_skills (
                        skill_id, title, intent, keywords_json, answer, priority, active, payload_json, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(skill_id) do update set
                        title = excluded.title,
                        intent = excluded.intent,
                        keywords_json = excluded.keywords_json,
                        answer = excluded.answer,
                        priority = excluded.priority,
                        active = excluded.active,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        skill_id,
                        str(skill.get("title") or skill_id)[:160],
                        str(skill.get("intent") or "")[:500],
                        json.dumps([str(item).strip().lower() for item in keywords if str(item).strip()], ensure_ascii=False),
                        answer[:700],
                        int(skill.get("priority") or 100),
                        1 if bool(skill.get("active", True)) else 0,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )

    def list_ai_skills(self, active_only: bool = True, limit: int = 80) -> list[dict[str, Any]]:
        query = """
            select skill_id, title, intent, keywords_json, answer, priority, active, payload_json, updated_at
              from ai_skills
        """
        params: list[Any] = []
        if active_only:
            query += " where active = 1"
        query += " order by priority asc, title asc limit ?"
        params.append(max(1, min(200, int(limit))))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        skills = []
        for row in rows:
            data = dict(row)
            try:
                keywords = json.loads(data.pop("keywords_json") or "[]")
            except json.JSONDecodeError:
                keywords = []
            try:
                payload = json.loads(data.pop("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            data["keywords"] = keywords if isinstance(keywords, list) else []
            data["payload"] = payload if isinstance(payload, dict) else {}
            data["active"] = bool(data.get("active"))
            skills.append(data)
        return skills

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
        rows = conn.execute(f"pragma table_info({table})").fetchall()
        current = {str(row["name"]) for row in rows}
        if column in current:
            return
        conn.execute(f"alter table {table} add column {column} {ddl_type}")

    def get_bankroll_account(self, user_id: int) -> dict[str, Any]:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                insert into bankroll_accounts (user_id, initial_bankroll_brl, balance_brl, default_stake_percent, updated_at)
                values (?, 0, 0, 2, ?)
                on conflict(user_id) do nothing
                """,
                (int(user_id), now),
            )
            row = conn.execute(
                "select * from bankroll_accounts where user_id = ?",
                (int(user_id),),
            ).fetchone()
        return self._row_to_dict(row) if row else {
            "user_id": int(user_id),
            "initial_bankroll_brl": 0.0,
            "balance_brl": 0.0,
            "default_stake_percent": 2.0,
            "updated_at": now,
        }

    def update_bankroll_account(
        self,
        user_id: int,
        initial_bankroll_brl: float | None = None,
        balance_brl: float | None = None,
        default_stake_percent: float | None = None,
    ) -> dict[str, Any]:
        current = self.get_bankroll_account(user_id)
        initial = _money_or(current.get("initial_bankroll_brl"), 0.0)
        balance = _money_or(current.get("balance_brl"), 0.0)
        stake_percent = _money_or(current.get("default_stake_percent"), 2.0)
        if initial_bankroll_brl is not None:
            initial = max(0.0, round(float(initial_bankroll_brl), 2))
        if balance_brl is not None:
            balance = max(0.0, round(float(balance_brl), 2))
        if default_stake_percent is not None:
            stake_percent = max(0.1, min(100.0, round(float(default_stake_percent), 2)))
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                update bankroll_accounts
                   set initial_bankroll_brl = ?,
                       balance_brl = ?,
                       default_stake_percent = ?,
                       updated_at = ?
                 where user_id = ?
                """,
                (initial, balance, stake_percent, now, int(user_id)),
            )
        return self.get_bankroll_account(user_id)

    def list_bankroll_entries(self, user_id: int, limit: int = 80) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select * from bankroll_entries
                 where user_id = ?
                 order by case status when 'open' then 0 else 1 end, opened_at desc, id desc
                 limit ?
                """,
                (int(user_id), max(1, min(200, int(limit)))),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def open_bankroll_entry(
        self,
        user_id: int,
        game_label: str,
        market: str,
        amount_brl: float,
        odds: float | None = None,
        signal_id: str | None = None,
        ai_notes: str | None = None,
    ) -> dict[str, Any]:
        amount = round(float(amount_brl), 2)
        if amount <= 0:
            raise ValueError("Valor de entrada invalido.")
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                insert into bankroll_accounts (user_id, initial_bankroll_brl, balance_brl, default_stake_percent, updated_at)
                values (?, 0, 0, 2, ?)
                on conflict(user_id) do nothing
                """,
                (int(user_id), now),
            )
            account = conn.execute(
                "select * from bankroll_accounts where user_id = ?",
                (int(user_id),),
            ).fetchone()
            balance = _money_or(account["balance_brl"] if account else 0, 0.0)
            if amount > balance:
                raise ValueError("Saldo insuficiente para registrar essa entrada.")
            new_balance = round(balance - amount, 2)
            cursor = conn.execute(
                """
                insert into bankroll_entries (
                    user_id, signal_id, game_label, market, amount_brl, odds, status,
                    profit_brl, ai_notes, opened_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, 'open', 0, ?, ?, ?)
                """,
                (
                    int(user_id),
                    str(signal_id or "").strip() or None,
                    str(game_label or "Jogo selecionado").strip()[:220],
                    str(market or "Entrada manual").strip()[:180],
                    amount,
                    float(odds) if odds is not None else None,
                    str(ai_notes or "").strip()[:500] or None,
                    now,
                    now,
                ),
            )
            conn.execute(
                "update bankroll_accounts set balance_brl = ?, updated_at = ? where user_id = ?",
                (new_balance, now, int(user_id)),
            )
            row = conn.execute(
                "select * from bankroll_entries where id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self._row_to_dict(row) if row else {}

    def close_bankroll_entry(self, user_id: int, entry_id: int, outcome: str) -> dict[str, Any]:
        clean = str(outcome or "").strip().lower()
        if clean not in {"win", "loss", "void"}:
            raise ValueError("Resultado invalido.")
        now = _now_iso()
        with self._connect() as conn:
            entry = conn.execute(
                "select * from bankroll_entries where id = ? and user_id = ?",
                (int(entry_id), int(user_id)),
            ).fetchone()
            if not entry:
                raise ValueError("Entrada nao encontrada.")
            if str(entry["status"]) != "open":
                raise ValueError("Entrada ja foi fechada.")
            account = conn.execute(
                "select * from bankroll_accounts where user_id = ?",
                (int(user_id),),
            ).fetchone()
            balance = _money_or(account["balance_brl"] if account else 0, 0.0)
            amount = _money_or(entry["amount_brl"], 0.0)
            odds = _money_or(entry["odds"], 0.0)
            credit = 0.0
            profit = -amount
            if clean == "win":
                credit = round(amount * max(1.0, odds or 1.0), 2)
                profit = round(credit - amount, 2)
            elif clean == "void":
                credit = amount
                profit = 0.0
            new_balance = round(balance + credit, 2)
            conn.execute(
                """
                update bankroll_entries
                   set status = ?, profit_brl = ?, closed_at = ?, updated_at = ?
                 where id = ? and user_id = ?
                """,
                (clean, profit, now, now, int(entry_id), int(user_id)),
            )
            conn.execute(
                "update bankroll_accounts set balance_brl = ?, updated_at = ? where user_id = ?",
                (new_balance, now, int(user_id)),
            )
            row = conn.execute(
                "select * from bankroll_entries where id = ?",
                (int(entry_id),),
            ).fetchone()
        return self._row_to_dict(row) if row else {}

    def create_user(
        self,
        name: str,
        email: str,
        password: str,
        plan: str,
        monthly_price_brl: float,
        trial_days: int,
    ) -> dict[str, Any]:
        clean_email = email.strip().lower()
        clean_name = " ".join(name.strip().split())[:120]
        if not clean_name or "@" not in clean_email or len(password) < 8:
            raise ValueError("Dados invalidos para cadastro.")
        now = _now_iso()
        trial_end = (datetime.now(timezone.utc) + timedelta(days=max(1, int(trial_days)))).isoformat()
        password_hash = _hash_password(password)
        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    insert into users (
                        email, name, password_hash, plan, status, monthly_price_brl,
                        trial_started_at, trial_ends_at, created_at, updated_at
                    )
                    values (?, ?, ?, ?, 'trial', ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_email,
                        clean_name,
                        password_hash,
                        plan,
                        float(monthly_price_brl),
                        now,
                        trial_end,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Email ja cadastrado.") from exc
            user_id = int(cursor.lastrowid)
            conn.execute(
                """
                insert into user_preferences (
                    user_id, scan_enabled, idle_scan_seconds, active_scan_seconds, telegram_enabled, telegram_chat_id, updated_at
                ) values (?, 1, 60, 120, 0, null, ?)
                """,
                (user_id, now),
            )
        return self.get_user(user_id) or {}

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        clean_email = email.strip().lower()
        with self._connect() as conn:
            row = conn.execute("select * from users where email = ?", (clean_email,)).fetchone()
        if not row:
            return None
        if not _verify_password(password, str(row["password_hash"])):
            return None
        return self._row_to_dict(row)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from users where id = ?", (int(user_id),)).fetchone()
        return self._row_to_dict(row) if row else None

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from users where email = ?", (email.strip().lower(),)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("select * from users order by created_at desc").fetchall()
        return [self._row_to_dict(row) for row in rows]

    def ensure_admin(self, email: str, name: str, password: str) -> dict[str, Any]:
        existing = self.find_user_by_email(email)
        if existing:
            with self._connect() as conn:
                conn.execute(
                    "update users set is_admin = 1, password_hash = ?, updated_at = ? where id = ?",
                    (_hash_password(password), _now_iso(), int(existing["id"])),
                )
            return self.get_user(int(existing["id"])) or existing
        created = self.create_user(
            name=name,
            email=email,
            password=password,
            plan="team",
            monthly_price_brl=0,
            trial_days=30,
        )
        with self._connect() as conn:
            conn.execute(
                "update users set is_admin = 1, status = 'active', trial_ends_at = null, updated_at = ? where id = ?",
                (_now_iso(), int(created["id"])),
            )
        return self.get_user(int(created["id"])) or created

    def update_user(
        self,
        user_id: int,
        plan: str | None = None,
        status: str | None = None,
        monthly_price_brl: float | None = None,
        next_due_at: str | None = None,
        cancel_reason: str | None = None,
    ) -> dict[str, Any] | None:
        user = self.get_user(user_id)
        if not user:
            return None
        next_plan = plan[:50] if plan else str(user.get("plan") or "starter")
        next_status = status[:30] if status else str(user.get("status") or "trial")
        next_price = float(monthly_price_brl) if monthly_price_brl is not None else float(user.get("monthly_price_brl") or 0)
        next_due = next_due_at if next_due_at is not None else user.get("next_due_at")
        if cancel_reason is None:
            next_cancel_reason = user.get("cancel_reason")
        else:
            next_cancel_reason = cancel_reason[:300]
        updated_at = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                update users
                   set plan = ?,
                       status = ?,
                       monthly_price_brl = ?,
                       next_due_at = ?,
                       cancel_reason = ?,
                       updated_at = ?
                 where id = ?
                """,
                (
                    next_plan,
                    next_status,
                    next_price,
                    next_due,
                    next_cancel_reason,
                    updated_at,
                    int(user_id),
                ),
            )
        return self.get_user(user_id)

    def update_profile(
        self,
        user_id: int,
        name: str | None = None,
        email: str | None = None,
        avatar_url: str | None = None,
        new_password: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            current = conn.execute("select * from users where id = ?", (int(user_id),)).fetchone()
        if not current:
            return None
        current_data = dict(current)
        next_name = str(current_data.get("name") or "")
        next_email = str(current_data.get("email") or "")
        next_avatar = current_data.get("avatar_url")
        next_password_hash = str(current_data.get("password_hash") or "")
        if name is not None:
            clean_name = " ".join(name.strip().split())[:120]
            if not clean_name:
                raise ValueError("Nome invalido.")
            next_name = clean_name
        if email is not None:
            clean_email = email.strip().lower()
            if "@" not in clean_email:
                raise ValueError("Email invalido.")
            next_email = clean_email
        if avatar_url is not None:
            clean_avatar = avatar_url.strip()[:600]
            next_avatar = clean_avatar or None
        if new_password is not None:
            if len(new_password.strip()) < 8:
                raise ValueError("Senha deve ter no minimo 8 caracteres.")
            next_password_hash = _hash_password(new_password.strip())
        updated_at = _now_iso()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    update users
                       set name = ?,
                           email = ?,
                           avatar_url = ?,
                           password_hash = ?,
                           updated_at = ?
                     where id = ?
                    """,
                    (
                        next_name,
                        next_email,
                        next_avatar,
                        next_password_hash,
                        updated_at,
                        int(user_id),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Email ja cadastrado.") from exc
        return self.get_user(user_id)

    def cancel_user(self, user_id: int, reason: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                """
                update users
                   set status = 'canceled',
                       cancel_reason = ?,
                       updated_at = ?
                 where id = ?
                """,
                (reason[:300], _now_iso(), int(user_id)),
            )
        return self.get_user(user_id)

    def mark_charge(self, user_id: int, cycle_days: int = 30) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        next_due = now + timedelta(days=max(1, int(cycle_days)))
        with self._connect() as conn:
            conn.execute(
                """
                update users
                   set last_payment_at = ?, next_due_at = ?, status = 'active', cancel_reason = null, updated_at = ?
                 where id = ?
                """,
                (now.isoformat(), next_due.isoformat(), now.isoformat(), int(user_id)),
            )
        return self.get_user(user_id)

    def log_payment(
        self,
        user_id: int,
        gateway: str,
        amount_brl: float,
        status: str,
        payment_id: str | None = None,
        checkout_url: str | None = None,
        paid_at: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into payment_logs (
                    user_id, gateway, amount_brl, status, payment_id, checkout_url, created_at, paid_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    (gateway or "manual")[:32],
                    float(amount_brl or 0),
                    (status or "created")[:32],
                    (payment_id or "")[:160] or None,
                    (checkout_url or "")[:1200] or None,
                    _now_iso(),
                    paid_at,
                ),
            )

    def list_payments(self, user_id: int, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select gateway, amount_brl, status, payment_id, checkout_url, created_at, paid_at
                  from payment_logs
                 where user_id = ?
                 order by id desc
                 limit ?
                """,
                (int(user_id), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_preferences(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                select user_id, scan_enabled, idle_scan_seconds, active_scan_seconds, telegram_enabled, telegram_chat_id, updated_at
                  from user_preferences
                where user_id = ?
                """,
                (int(user_id),),
            ).fetchone()
            if row:
                return dict(row)
            now = _now_iso()
            conn.execute(
                """
                insert into user_preferences (
                    user_id, scan_enabled, idle_scan_seconds, active_scan_seconds, telegram_enabled, telegram_chat_id, updated_at
                ) values (?, 1, 60, 300, 0, null, ?)
                """,
                (int(user_id), now),
            )
        return {
            "user_id": int(user_id),
            "scan_enabled": 1,
            "idle_scan_seconds": 60,
            "active_scan_seconds": 120,
            "telegram_enabled": 0,
            "telegram_chat_id": None,
            "updated_at": now,
        }

    def update_preferences(
        self,
        user_id: int,
        scan_enabled: bool | None = None,
        idle_scan_seconds: int | None = None,
        active_scan_seconds: int | None = None,
        telegram_enabled: bool | None = None,
        telegram_chat_id: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_preferences(user_id)
        updates = {
            "scan_enabled": 1 if scan_enabled else 0 if scan_enabled is not None else int(current.get("scan_enabled", 1)),
            "idle_scan_seconds": int(idle_scan_seconds) if idle_scan_seconds is not None else int(current["idle_scan_seconds"]),
            "active_scan_seconds": int(active_scan_seconds) if active_scan_seconds is not None else int(current["active_scan_seconds"]),
            "telegram_enabled": 1 if telegram_enabled else 0 if telegram_enabled is not None else int(current["telegram_enabled"]),
            "telegram_chat_id": (telegram_chat_id or current.get("telegram_chat_id") or None),
            "updated_at": _now_iso(),
        }
        updates["idle_scan_seconds"] = max(30, min(1800, updates["idle_scan_seconds"]))
        updates["active_scan_seconds"] = max(60, min(1800, updates["active_scan_seconds"]))
        with self._connect() as conn:
            conn.execute(
                """
                update user_preferences
                   set scan_enabled = ?,
                       idle_scan_seconds = ?,
                       active_scan_seconds = ?,
                       telegram_enabled = ?,
                       telegram_chat_id = ?,
                       updated_at = ?
                 where user_id = ?
                """,
                (
                    updates["scan_enabled"],
                    updates["idle_scan_seconds"],
                    updates["active_scan_seconds"],
                    updates["telegram_enabled"],
                    updates["telegram_chat_id"],
                    updates["updated_at"],
                    int(user_id),
                ),
            )
        return self.get_preferences(user_id)

    def telegram_enabled_chat_ids(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select telegram_chat_id
                  from user_preferences
                 where scan_enabled = 1
                   and telegram_enabled = 1
                   and telegram_chat_id is not null
                   and trim(telegram_chat_id) <> ''
                """
            ).fetchall()
        result: list[int] = []
        for row in rows:
            raw = str(row["telegram_chat_id"]).strip()
            try:
                result.append(int(raw))
            except ValueError:
                continue
        return result

    def registered_chat_ids(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select telegram_chat_id
                  from user_preferences
                 where telegram_chat_id is not null
                   and trim(telegram_chat_id) <> ''
                """
            ).fetchall()
        result: list[int] = []
        for row in rows:
            raw = str(row["telegram_chat_id"]).strip()
            try:
                result.append(int(raw))
            except ValueError:
                continue
        return result

    def notification_scan_preferences(
        self,
        default_idle_scan_seconds: int,
        default_active_scan_seconds: int,
    ) -> tuple[int, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select idle_scan_seconds, active_scan_seconds
                  from user_preferences
                 where scan_enabled = 1
                   and telegram_enabled = 1
                """
            ).fetchall()
        if not rows:
            return int(default_idle_scan_seconds), int(default_active_scan_seconds)
        idle_values = [max(30, min(1800, int(row["idle_scan_seconds"] or default_idle_scan_seconds))) for row in rows]
        active_values = [max(60, min(1800, int(row["active_scan_seconds"] or default_active_scan_seconds))) for row in rows]
        return min(idle_values), min(active_values)

    def seed_pricing(self, defaults: dict[str, float]) -> None:
        now = _now_iso()
        with self._connect() as conn:
            for plan, price in defaults.items():
                conn.execute(
                    """
                    insert into pricing_config (plan, monthly_price_brl, updated_at)
                    values (?, ?, ?)
                    on conflict(plan) do nothing
                    """,
                    (plan, float(price), now),
                )

    def pricing_map(self, defaults: dict[str, float]) -> dict[str, float]:
        self.seed_pricing(defaults)
        with self._connect() as conn:
            rows = conn.execute("select plan, monthly_price_brl from pricing_config").fetchall()
        result = dict(defaults)
        for row in rows:
            result[str(row["plan"])] = float(row["monthly_price_brl"])
        return result

    def update_plan_price(self, plan: str, price: float) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                insert into pricing_config (plan, monthly_price_brl, updated_at)
                values (?, ?, ?)
                on conflict(plan) do update
                  set monthly_price_brl = excluded.monthly_price_brl,
                      updated_at = excluded.updated_at
                """,
                (plan[:20], float(price), now),
            )

    def system_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            users_total = int(conn.execute("select count(*) from users").fetchone()[0])
            users_active = int(conn.execute("select count(*) from users where status = 'active'").fetchone()[0])
            users_trial = int(conn.execute("select count(*) from users where status = 'trial'").fetchone()[0])
            users_canceled = int(conn.execute("select count(*) from users where status = 'canceled'").fetchone()[0])
            payments_total = int(conn.execute("select count(*) from payment_logs").fetchone()[0])
            payments_paid = int(conn.execute("select count(*) from payment_logs where status = 'paid'").fetchone()[0])
            support_total = int(conn.execute("select count(*) from support_logs").fetchone()[0])
            telegram_linked = int(
                conn.execute(
                    """
                    select count(*) from user_preferences
                     where telegram_chat_id is not null
                       and trim(telegram_chat_id) <> ''
                    """
                ).fetchone()[0]
            )
            scan_enabled = int(conn.execute("select count(*) from user_preferences where scan_enabled = 1").fetchone()[0])

        db_size = self.path.stat().st_size if self.path.exists() else 0
        return {
            "db_file": self.path.as_posix(),
            "db_size_bytes": db_size,
            "users_total": users_total,
            "users_active": users_active,
            "users_trial": users_trial,
            "users_canceled": users_canceled,
            "payments_total": payments_total,
            "payments_paid": payments_paid,
            "support_total": support_total,
            "telegram_linked": telegram_linked,
            "scan_enabled_users": scan_enabled,
        }

    def create_reset_token(self, email: str, ttl_minutes: int) -> str | None:
        user = self.find_user_by_email(email)
        if not user:
            return None
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=max(5, int(ttl_minutes)))
        with self._connect() as conn:
            conn.execute(
                "insert into password_resets (token, user_id, expires_at, used, created_at) values (?, ?, ?, 0, ?)",
                (token, int(user["id"]), expires.isoformat(), now.isoformat()),
            )
        return token

    def reset_password_with_token(self, token: str, new_password: str) -> bool:
        if len(new_password) < 8:
            return False
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute(
                "select * from password_resets where token = ? and used = 0",
                (token,),
            ).fetchone()
            if not row:
                return False
            try:
                expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            except ValueError:
                return False
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                return False
            conn.execute(
                "update users set password_hash = ?, updated_at = ? where id = ?",
                (_hash_password(new_password), now.isoformat(), int(row["user_id"])),
            )
            conn.execute(
                "update password_resets set used = 1 where token = ?",
                (token,),
            )
        return True

    def log_support(self, user_id: int, role: str, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into support_logs (user_id, role, message, created_at) values (?, ?, ?, ?)",
                (int(user_id), role[:20], message[:2000], _now_iso()),
            )

    def list_support_logs(self, user_id: int, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select role, message, created_at
                  from support_logs
                 where user_id = ?
                 order by id desc
                 limit ?
                """,
                (int(user_id), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows][::-1]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data.pop("password_hash", None)
        return data


def send_password_reset_email(settings, to_email: str, token: str) -> PasswordResetDelivery:
    if not settings.smtp_host or not settings.smtp_from:
        return PasswordResetDelivery(False, "SMTP nao configurado.")
    reset_url = f"{(settings.website_url or '').rstrip('/')}/reset-password?token={token}"
    message = EmailMessage()
    message["Subject"] = f"{settings.product_name}: redefinicao de senha"
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                f"Ola, voce solicitou redefinicao de senha no {settings.product_name}.",
                "",
                f"Use este link: {reset_url}",
                "",
                "Se voce nao solicitou, ignore este email.",
            ]
        )
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_starttls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password or "")
            server.send_message(message)
        return PasswordResetDelivery(True, "Email enviado.")
    except Exception as exc:
        return PasswordResetDelivery(False, f"Falha ao enviar email: {exc}")


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _skill_match_score(message: str, skill: dict[str, Any]) -> int:
    text = _plain_text(message)
    keywords = skill.get("keywords") or []
    score = 0
    for keyword in keywords:
        token = _plain_text(str(keyword).strip())
        if token and token in text:
            score += 3 if " " in token else 1
    intent = _plain_text(str(skill.get("intent") or ""))
    for word in text.split():
        if len(word) > 3 and word in intent:
            score += 1
    return score


def _skill_topic(skill: dict[str, Any]) -> str:
    payload = skill.get("payload") or {}
    topic = str(payload.get("topic") or "").strip()
    if topic:
        return topic
    return str(skill.get("skill_id") or skill.get("title") or "general")


def _ranked_skill_matches(message: str, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        (skill for skill in skills if skill.get("answer")),
        key=lambda item: (-_skill_match_score(message, item), int(item.get("priority") or 100)),
    )
    return [item for item in ranked if _skill_match_score(message, item) > 0]


def _compact_skill_reply(message: str, skills: list[dict[str, Any]], max_items: int = 2) -> str:
    chosen: list[str] = []
    seen_topics: set[str] = set()
    for skill in _ranked_skill_matches(message, skills):
        topic = _skill_topic(skill)
        if topic in seen_topics:
            continue
        answer = str(skill.get("answer") or "").strip()
        if not answer:
            continue
        chosen.append(answer)
        seen_topics.add(topic)
        if len(chosen) >= max_items:
            break
    if not chosen:
        return ""
    reply = " | ".join(chosen)
    if len(reply) <= 480:
        return reply
    trimmed = reply[:477].rsplit(" ", 1)[0].rstrip(" |")
    return f"{trimmed}..."


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _runtime_support_notes(message: str, context: dict[str, Any]) -> list[str]:
    text = _plain_text(message)
    notes: list[str] = []
    telegram = context.get("telegram_status") or {}
    ai_memory = context.get("ai_memory_status") or {}
    if _contains_any(text, ("telegram", "chatid", "chat id", "alerta", "notificacao", "notificação")):
        summary = str(telegram.get("summary") or "").strip()
        if summary:
            notes.append(summary)
    if _contains_any(text, ("supabase", "memoria", "memória", "skills", "aprendizado", "ia")):
        summary = str(ai_memory.get("summary") or "").strip()
        if summary:
            notes.append(summary)
    deduped: list[str] = []
    seen = set()
    for note in notes:
        key = _plain_text(note)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(note)
    return deduped


def support_agent_reply(message: str, context: dict[str, Any]) -> str:
    skills = context.get("skills") or DEFAULT_AI_SUPPORT_SKILLS
    skill_reply = _compact_skill_reply(message, skills)
    runtime_notes = _runtime_support_notes(message, context)
    if skill_reply or runtime_notes:
        parts = [part for part in [skill_reply, *runtime_notes] if part]
        reply = " | ".join(dict.fromkeys(parts))
        if len(reply) <= 520:
            return reply
        return f"{reply[:517].rsplit(' ', 1)[0]}..."

    text = _plain_text(message)
    if any(token in text for token in ("senha", "login", "entrar")):
        return (
            "Para acesso: use seu email e senha em /login. "
            "Se esqueceu a senha, clique em 'Esqueci a senha' e confirme o link no email."
        )
    if any(token in text for token in ("scanner", "nao chega", "nao envia", "sem jogos")):
        lock = context.get("red_lock")
        if lock and lock.get("discipline_alert"):
            return (
                "A IA marcou alerta de disciplina por sequencia de reds. "
                f"Janela de controle: {lock.get('unlock_at')}. O scanner segue ativo."
            )
        return (
            "Verifique se o jogo ativo esta selecionado. Sem jogo ativo o ciclo e 1 min; "
            "com jogo ativo fica 5 min. Use /checkout para diagnostico completo."
        )
    if any(token in text for token in ("importar", "bet365", "historico")):
        return (
            "No menu Importar, cole o texto bruto da aposta com valor e odd. "
            "Depois confira no historico se ficou Green/Red e ajuste em 'Editar' se necessario."
        )
    if any(token in text for token in ("cancelar", "plano", "cobrar", "fatura")):
        return (
            "Assuntos de plano, cobranca e cancelamento sao feitos pelo admin em /admin/users. "
            "Se precisar, informe seu email e o motivo para suporte agil."
        )
    if any(token in text for token in ("dashboard", "dominio", "site")):
        return (
            "A dashboard fica no dominio configurado. "
            "Se nao abrir, valide DNS A para o IP do servidor e execute /checkout."
        )
    return (
        "Posso ajudar com login, senha, scanner, Telegram, planos, importação, backtesting e dashboard. "
        "Me diga em uma frase o problema e o que você esperava que acontecesse."
    )
