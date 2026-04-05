import csv
import io
import uuid
import os
import re
import time
import secrets
import traceback
from datetime import datetime, date, time as dt_time
from urllib.parse import quote
from collections import deque
from contextlib import contextmanager

from flask import (
    Flask, render_template, request, jsonify,
    send_file, redirect, url_for, abort, make_response
)
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from psycopg2 import sql, errors

app = Flask(__name__)

# ==========================================================
# CONFIGURAÇÕES
# ==========================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", secrets.token_hex(32))
DEFAULT_EMPRESA_SLUG = os.getenv("DEFAULT_EMPRESA_SLUG", "barbearia")
MAX_AGENDAMENTO_POR_MINUTO = int(os.getenv("MAX_AGENDAMENTO_POR_MINUTO", "10"))
MAX_DISPONIBILIDADE_POR_MINUTO = int(os.getenv("MAX_DISPONIBILIDADE_POR_MINUTO", "60"))
DEBUG_SECRET = os.getenv("DEBUG_SECRET", "")

app.config["SECRET_KEY"] = APP_SECRET_KEY
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

PHONE_RE = re.compile(r"^\d{10,13}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATUS_VALIDOS = ["marcado", "confirmado", "concluido", "cancelado", "faltou"]

# ==========================================================
# CONNECTION POOL
# ==========================================================
pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DATABASE_URL,
    cursor_factory=RealDictCursor
)

@contextmanager
def get_conn():
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

@contextmanager
def get_cursor(conn):
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()

# ==========================================================
# UTILITÁRIOS
# ==========================================================
def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")

def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"

def limpar_texto(valor, max_len):
    valor = (valor or "").strip()
    return valor[:max_len]

def normalizar_telefone(telefone: str) -> str:
    return "".join(filter(str.isdigit, telefone or ""))

def telefone_valido(telefone: str) -> bool:
    return bool(PHONE_RE.match(telefone))

def email_valido(email: str) -> bool:
    if not email:
        return True
    return bool(EMAIL_RE.match(email))

def validar_uuid(id_str):
    try:
        uuid.UUID(str(id_str))
        return str(id_str)
    except (TypeError, ValueError, AttributeError):
        return None

def hora_str_para_minutos(hora_str: str) -> int:
    try:
        h, m = map(int, (hora_str or "00:00").split(":"))
        return h * 60 + m
    except Exception:
        return 0

def minutos_para_hora_str(minutos: int) -> str:
    return f"{minutos // 60:02d}:{minutos % 60:02d}"

def intervalo_sobrepoe(inicio_a, fim_a, inicio_b, fim_b) -> bool:
    a1 = hora_str_para_minutos(inicio_a)
    a2 = hora_str_para_minutos(fim_a)
    b1 = hora_str_para_minutos(inicio_b)
    b2 = hora_str_para_minutos(fim_b)
    return a1 < b2 and a2 > b1

def validar_data_yyyy_mm_dd(data_str: str) -> bool:
    try:
        datetime.strptime(data_str, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False

def validar_hora_hh_mm(hora_str: str) -> bool:
    try:
        if len(hora_str) != 5 or hora_str[2] != ":":
            return False
        h, m = map(int, hora_str.split(":"))
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False

def parse_data(data_str: str):
    return datetime.strptime(data_str, "%Y-%m-%d").date()

def gerar_token_empresa() -> str:
    return secrets.token_urlsafe(32)

def mascarar_telefone(telefone: str) -> str:
    digits = normalizar_telefone(telefone)
    if len(digits) < 4:
        return "***"
    return "*" * max(0, len(digits) - 4) + digits[-4:]

def row_to_dict(row):
    return dict(row) if row else {}

def rows_to_dicts(rows):
    return [row_to_dict(r) for r in rows] if rows else []

# ==========================================================
# RATE LIMIT (TABELA PERSISTENTE)
# ==========================================================
def rate_limit_check(key: str, limit: int, window_seconds: int = 60) -> bool:
    now_ts = int(time.time())
    cutoff = now_ts - window_seconds
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key_name TEXT NOT NULL,
                    request_time INTEGER NOT NULL,
                    PRIMARY KEY (key_name, request_time)
                )
            """)
            conn.commit()
            cur.execute("DELETE FROM rate_limits WHERE request_time < %s", (cutoff,))
            cur.execute("SELECT COUNT(*) AS cnt FROM rate_limits WHERE key_name = %s", (key,))
            count = cur.fetchone()["cnt"]
            if count >= limit:
                return False
            cur.execute("INSERT INTO rate_limits (key_name, request_time) VALUES (%s, %s)", (key, now_ts))
            conn.commit()
    return True

# ==========================================================
# INICIALIZAÇÃO CONTROLADA (MIGRAÇÕES)
# ==========================================================
def init_db():
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            # Tabela de controle de migrações
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            conn.commit()

            # Lock pessimista para evitar execução concorrente
            cur.execute("SELECT pg_try_advisory_lock(12345) AS locked")
            locked = cur.fetchone()["locked"]
            if not locked:
                print("[INIT] Outra instância já está inicializando. Aguardando...")
                time.sleep(2)
                return

            try:
                # Verifica última migração aplicada
                cur.execute("SELECT MAX(version) AS version FROM schema_migrations")
                last = cur.fetchone()["version"] or 0

                if last < 1:
                    print("[INIT] Aplicando migração inicial (v1)...")
                    cur.execute("""
                        -- Tabelas principais com tipos PostgreSQL
                        CREATE TABLE IF NOT EXISTS empresas (
                            id            TEXT PRIMARY KEY,
                            nome          TEXT NOT NULL,
                            slug          TEXT NOT NULL UNIQUE,
                            telefone      TEXT,
                            email         TEXT,
                            endereco      TEXT,
                            logo_url      TEXT,
                            ativo         BOOLEAN NOT NULL DEFAULT TRUE,
                            token         TEXT,
                            criado_em     TIMESTAMP NOT NULL,
                            atualizado_em TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS barbeiros (
                            id            TEXT PRIMARY KEY,
                            empresa_id    TEXT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                            nome          TEXT NOT NULL,
                            whatsapp      TEXT,
                            email         TEXT,
                            foto_url      TEXT,
                            bio           TEXT,
                            ativo         BOOLEAN NOT NULL DEFAULT TRUE,
                            criado_em     TIMESTAMP NOT NULL,
                            atualizado_em TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS servicos (
                            id            SERIAL PRIMARY KEY,
                            empresa_id    TEXT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                            nome          TEXT NOT NULL,
                            descricao     TEXT,
                            preco         NUMERIC(10,2) NOT NULL DEFAULT 0,
                            duracao_min   INTEGER NOT NULL DEFAULT 30,
                            emoji         TEXT,
                            ativo         BOOLEAN NOT NULL DEFAULT TRUE,
                            criado_em     TIMESTAMP NOT NULL,
                            atualizado_em TIMESTAMP
                        );

                        CREATE TABLE IF NOT EXISTS barbeiro_servicos (
                            id          SERIAL PRIMARY KEY,
                            barbeiro_id TEXT NOT NULL REFERENCES barbeiros(id) ON DELETE CASCADE,
                            servico_id  INTEGER NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
                            criado_em   TIMESTAMP NOT NULL,
                            UNIQUE(barbeiro_id, servico_id)
                        );

                        CREATE TABLE IF NOT EXISTS clientes (
                            id            TEXT PRIMARY KEY,
                            empresa_id    TEXT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                            nome          TEXT NOT NULL,
                            telefone      TEXT NOT NULL,
                            email         TEXT,
                            observacoes   TEXT,
                            criado_em     TIMESTAMP NOT NULL,
                            atualizado_em TIMESTAMP,
                            UNIQUE(empresa_id, telefone)
                        );

                        CREATE TABLE IF NOT EXISTS agendamentos (
                            id               TEXT PRIMARY KEY,
                            empresa_id       TEXT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                            cliente_id       TEXT REFERENCES clientes(id) ON DELETE SET NULL,
                            barbeiro_id      TEXT NOT NULL REFERENCES barbeiros(id) ON DELETE CASCADE,
                            servico_id       INTEGER NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
                            data             DATE NOT NULL,
                            hora_inicio      TIME NOT NULL,
                            hora_fim         TIME NOT NULL,
                            cliente_nome     TEXT NOT NULL,
                            cliente_telefone TEXT NOT NULL,
                            cliente_email    TEXT,
                            preco            NUMERIC(10,2) NOT NULL DEFAULT 0,
                            observacao       TEXT,
                            status           TEXT NOT NULL DEFAULT 'marcado',
                            origem           TEXT NOT NULL DEFAULT 'site',
                            criado_em        TIMESTAMP NOT NULL,
                            atualizado_em    TIMESTAMP,
                            UNIQUE(barbeiro_id, data, hora_inicio)  -- GARANTIA DE CONCORRÊNCIA
                        );

                        CREATE TABLE IF NOT EXISTS bloqueios_agenda (
                            id          TEXT PRIMARY KEY,
                            empresa_id  TEXT NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
                            barbeiro_id TEXT REFERENCES barbeiros(id) ON DELETE CASCADE,
                            data        DATE NOT NULL,
                            hora_inicio TIME,
                            hora_fim    TIME,
                            tipo        TEXT NOT NULL DEFAULT 'bloqueio',
                            motivo      TEXT,
                            criado_em   TIMESTAMP NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS configuracoes_empresa (
                            id                    SERIAL PRIMARY KEY,
                            empresa_id            TEXT NOT NULL UNIQUE REFERENCES empresas(id) ON DELETE CASCADE,
                            hora_abertura         TIME NOT NULL DEFAULT '08:00',
                            hora_fechamento       TIME NOT NULL DEFAULT '20:00',
                            intervalo_min         INTEGER NOT NULL DEFAULT 30,
                            antecedencia_max_dias INTEGER NOT NULL DEFAULT 30,
                            permite_encaixe       BOOLEAN NOT NULL DEFAULT FALSE,
                            envia_whatsapp        BOOLEAN NOT NULL DEFAULT TRUE,
                            criado_em             TIMESTAMP NOT NULL,
                            atualizado_em         TIMESTAMP
                        );

                        CREATE INDEX IF NOT EXISTS idx_barbeiros_empresa ON barbeiros (empresa_id);
                        CREATE INDEX IF NOT EXISTS idx_servicos_empresa ON servicos (empresa_id);
                        CREATE INDEX IF NOT EXISTS idx_clientes_empresa_telefone ON clientes (empresa_id, telefone);
                        CREATE INDEX IF NOT EXISTS idx_agendamentos_empresa_data ON agendamentos (empresa_id, data);
                        CREATE INDEX IF NOT EXISTS idx_agendamentos_barbeiro_data ON agendamentos (barbeiro_id, data);
                        CREATE INDEX IF NOT EXISTS idx_bloqueios_empresa_data ON bloqueios_agenda (empresa_id, data);
                    """)
                    conn.commit()
                    cur.execute("INSERT INTO schema_migrations (version) VALUES (1)")
                    conn.commit()
                    print("[INIT] Migração v1 aplicada.")

                # Criar empresa padrão se não existir
                cur.execute("SELECT id, token FROM empresas WHERE slug = %s", (DEFAULT_EMPRESA_SLUG,))
                empresa = cur.fetchone()

                if not empresa:
                    agora = datetime.utcnow()
                    empresa_id = str(uuid.uuid4())
                    token_padrao = gerar_token_empresa()
                    cur.execute("""
                        INSERT INTO empresas (id, nome, slug, telefone, email, endereco, ativo, token, criado_em)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (empresa_id, "Barbearia do Zé", DEFAULT_EMPRESA_SLUG,
                          "11999999999", "contato@barbearia.com", "São Paulo - SP",
                          True, token_padrao, agora))
                    cur.execute("""
                        INSERT INTO configuracoes_empresa (empresa_id, criado_em)
                        VALUES (%s, %s)
                    """, (empresa_id, agora))
                    # Barbeiros e serviços padrão
                    barbeiros = [
                        (str(uuid.uuid4()), empresa_id, "João Silva", "11988888888", None, None, None, True, agora, agora),
                        (str(uuid.uuid4()), empresa_id, "Carlos Souza", "11999999999", None, None, None, True, agora, agora),
                    ]
                    for b in barbeiros:
                        cur.execute("""
                            INSERT INTO barbeiros (id, empresa_id, nome, whatsapp, email, foto_url, bio, ativo, criado_em, atualizado_em)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, b)
                    servicos = [
                        (empresa_id, "Corte clássico", "Corte tradicional", 35.00, 30, "✂️", True, agora, agora),
                        (empresa_id, "Barba", "Barba completa", 25.00, 20, "🧔", True, agora, agora),
                        (empresa_id, "Corte + Barba", "Pacote completo", 55.00, 50, "🔥", True, agora, agora),
                    ]
                    for s in servicos:
                        cur.execute("""
                            INSERT INTO servicos (empresa_id, nome, descricao, preco, duracao_min, emoji, ativo, criado_em, atualizado_em)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id
                        """, s)
                        servico_id = cur.fetchone()["id"]
                        for b in barbeiros:
                            cur.execute("""
                                INSERT INTO barbeiro_servicos (barbeiro_id, servico_id, criado_em)
                                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                            """, (b[0], servico_id, agora))
                    conn.commit()
                    print(f"[INIT] Empresa padrão criada. Token: {token_padrao}")
                    print(f"[INIT] URL interna: /agenda/{DEFAULT_EMPRESA_SLUG}?token={token_padrao}")
                else:
                    token_atual = empresa["token"] or ""
                    if not token_atual:
                        token_atual = gerar_token_empresa()
                        cur.execute("UPDATE empresas SET token = %s WHERE id = %s", (token_atual, empresa["id"]))
                        conn.commit()
                        print(f"[INIT] Token gerado para '{DEFAULT_EMPRESA_SLUG}': {token_atual}")
                    else:
                        print(f"[INIT] Empresa '{DEFAULT_EMPRESA_SLUG}' carregada. Token: {token_atual}")
                    print(f"[INIT] URL interna: /agenda/{DEFAULT_EMPRESA_SLUG}?token={token_atual}")

            finally:
                cur.execute("SELECT pg_advisory_unlock(12345)")
                conn.commit()

# Executa inicialização controlada (apenas uma vez)
init_db()

# ==========================================================
# FUNÇÕES DE NEGÓCIO (ADAPTADAS PARA TIPOS)
# ==========================================================
def get_empresa_por_slug(slug):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT * FROM empresas WHERE slug = %s AND ativo = TRUE", (slug,))
            return row_to_dict(cur.fetchone())

def validar_token_empresa(empresa_slug, token):
    if not token or not empresa_slug:
        return None
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT * FROM empresas WHERE slug = %s AND token = %s AND ativo = TRUE", (empresa_slug, token))
            return row_to_dict(cur.fetchone())

def get_config_empresa(empresa_id):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("""
                SELECT hora_abertura, hora_fechamento, intervalo_min,
                       antecedencia_max_dias, permite_encaixe
                FROM configuracoes_empresa WHERE empresa_id = %s
            """, (empresa_id,))
            row = cur.fetchone()
            if row:
                # converte TIME para string HH:MM
                row["hora_abertura"] = row["hora_abertura"].strftime("%H:%M")
                row["hora_fechamento"] = row["hora_fechamento"].strftime("%H:%M")
            return row_to_dict(row)

def listar_barbeiros(empresa_id):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT id, nome, whatsapp, foto_url FROM barbeiros WHERE empresa_id = %s AND ativo = TRUE ORDER BY nome", (empresa_id,))
            return rows_to_dicts(cur.fetchall())

def listar_servicos(empresa_id):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT id, nome, preco, duracao_min, descricao FROM servicos WHERE empresa_id = %s AND ativo = TRUE ORDER BY nome", (empresa_id,))
            return rows_to_dicts(cur.fetchall())

def gerar_horarios(empresa_id):
    config = get_config_empresa(empresa_id)
    abertura = config.get("hora_abertura", "08:00")
    fechamento = config.get("hora_fechamento", "20:00")
    intervalo = int(config.get("intervalo_min", 30))
    inicio = hora_str_para_minutos(abertura)
    fim = hora_str_para_minutos(fechamento)
    return [minutos_para_hora_str(m) for m in range(inicio, fim, intervalo)]

def profissional_pertence_empresa(conn, profissional_id, empresa_id):
    with get_cursor(conn) as cur:
        cur.execute("SELECT id, nome, whatsapp FROM barbeiros WHERE id = %s AND empresa_id = %s AND ativo = TRUE", (profissional_id, empresa_id))
        return cur.fetchone()

def servico_pertence_empresa(conn, servico_id, empresa_id):
    with get_cursor(conn) as cur:
        cur.execute("SELECT id, nome, descricao, preco, duracao_min FROM servicos WHERE id = %s AND empresa_id = %s AND ativo = TRUE", (servico_id, empresa_id))
        return cur.fetchone()

def servico_vinculado_ao_barbeiro(conn, profissional_id, servico_id):
    with get_cursor(conn) as cur:
        cur.execute("SELECT 1 FROM barbeiro_servicos WHERE barbeiro_id = %s AND servico_id = %s LIMIT 1", (profissional_id, servico_id))
        return cur.fetchone() is not None

def existe_bloqueio(conn, empresa_id, barbeiro_id, data_agendamento, hora_inicio, hora_fim):
    with get_cursor(conn) as cur:
        cur.execute("""
            SELECT hora_inicio, hora_fim FROM bloqueios_agenda
            WHERE empresa_id = %s AND data = %s
              AND (barbeiro_id IS NULL OR barbeiro_id = %s)
        """, (empresa_id, data_agendamento, barbeiro_id))
        rows = cur.fetchall()
    for r in rows:
        b_ini = r["hora_inicio"].strftime("%H:%M") if r["hora_inicio"] else "00:00"
        b_fim = r["hora_fim"].strftime("%H:%M") if r["hora_fim"] else "23:59"
        if intervalo_sobrepoe(hora_inicio, hora_fim, b_ini, b_fim):
            return True
    return False

def existe_conflito_agendamento(conn, barbeiro_id, data_agendamento, hora_inicio, hora_fim):
    with get_cursor(conn) as cur:
        cur.execute("""
            SELECT hora_inicio, hora_fim FROM agendamentos
            WHERE barbeiro_id = %s AND data = %s
              AND status IN ('marcado', 'confirmado')
        """, (barbeiro_id, data_agendamento))
        rows = cur.fetchall()
    for r in rows:
        if intervalo_sobrepoe(hora_inicio, hora_fim, r["hora_inicio"].strftime("%H:%M"), r["hora_fim"].strftime("%H:%M")):
            return True
    return False

def buscar_ou_criar_cliente(conn, empresa_id, nome, telefone, email, observacoes=""):
    agora = datetime.utcnow()
    with get_cursor(conn) as cur:
        cur.execute("SELECT id FROM clientes WHERE empresa_id = %s AND telefone = %s", (empresa_id, telefone))
        row = cur.fetchone()
        if row:
            cliente_id = row["id"]
            cur.execute("""
                UPDATE clientes SET nome=%s, email=%s, observacoes=%s, atualizado_em=%s
                WHERE id=%s
            """, (nome, email or None, observacoes or None, agora, cliente_id))
            return cliente_id
        cliente_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO clientes (id, empresa_id, nome, telefone, email, observacoes, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (cliente_id, empresa_id, nome, telefone, email or None, observacoes or None, agora, agora))
        return cliente_id

def validar_regras_de_data(empresa_id, data_agendamento):
    config = get_config_empresa(empresa_id)
    antecedencia_max_dias = int(config.get("antecedencia_max_dias", 30))
    data_escolhida = parse_data(data_agendamento)
    hoje = date.today()
    if data_escolhida < hoje:
        return False, "Não é permitido agendar em data passada."
    diferenca = (data_escolhida - hoje).days
    if diferenca > antecedencia_max_dias:
        return False, f"Agendamento permitido apenas até {antecedencia_max_dias} dias à frente."
    return True, ""

def obter_agendamentos_do_dia(empresa_id, data_ref, barbeiro_id=""):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            query = """
                SELECT
                    a.id,
                    a.data,
                    a.hora_inicio,
                    a.hora_fim,
                    a.cliente_nome,
                    a.cliente_telefone,
                    a.cliente_email,
                    a.preco,
                    a.status,
                    a.observacao,
                    COALESCE(s.nome, '') AS servico_nome,
                    COALESCE(b.nome, '') AS barbeiro_nome,
                    b.id AS barbeiro_id
                FROM agendamentos a
                LEFT JOIN servicos s ON s.id = a.servico_id
                LEFT JOIN barbeiros b ON b.id = a.barbeiro_id
                WHERE a.empresa_id = %s AND a.data = %s
            """
            params = [empresa_id, data_ref]
            if barbeiro_id:
                query += " AND a.barbeiro_id = %s"
                params.append(barbeiro_id)
            query += " ORDER BY a.hora_inicio ASC, a.criado_em ASC"
            cur.execute(query, params)
            rows = cur.fetchall()
            for r in rows:
                r["hora_inicio"] = r["hora_inicio"].strftime("%H:%M")
                r["hora_fim"] = r["hora_fim"].strftime("%H:%M")
                r["data"] = r["data"].isoformat()
            return rows_to_dicts(rows)

def gerar_resumo_agendamentos(agendamentos):
    resumo = {"total": 0, "marcado": 0, "confirmado": 0, "concluido": 0, "cancelado": 0, "faltou": 0}
    for item in (agendamentos or []):
        resumo["total"] += 1
        status = str(item.get("status") or "").lower()
        if status in resumo:
            resumo[status] += 1
    return resumo

# ==========================================================
# ROTAS PÚBLICAS
# ==========================================================
@app.route("/")
def home():
    return redirect(url_for("pagina_agendamento", empresa_slug=DEFAULT_EMPRESA_SLUG))

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": now_iso()}), 200

@app.route("/agendar/<empresa_slug>")
def pagina_agendamento(empresa_slug):
    empresa = get_empresa_por_slug(empresa_slug)
    if not empresa:
        abort(404, description="Empresa não encontrada.")
    barbeiros = listar_barbeiros(empresa["id"])
    servicos = listar_servicos(empresa["id"])
    return render_template("agenda_publica.html", empresa=empresa, barbeiros=barbeiros, servicos=servicos)

@app.route("/api/agendamentos/disponibilidade")
def disponibilidade():
    ip = get_client_ip()
    if not rate_limit_check(f"disponibilidade:{ip}", MAX_DISPONIBILIDADE_POR_MINUTO, 60):
        return jsonify({"message": "Muitas consultas. Tente novamente em instantes."}), 429

    empresa_slug = limpar_texto(request.args.get("empresa_slug", ""), 80)
    profissional_id = limpar_texto(request.args.get("profissional_id", ""), 80)
    data_agendamento = limpar_texto(request.args.get("data", ""), 10)
    servico_id = limpar_texto(request.args.get("servico_id", ""), 20)

    if not all([empresa_slug, profissional_id, data_agendamento, servico_id]):
        return jsonify({"message": "Parâmetros incompletos."}), 400
    if not validar_data_yyyy_mm_dd(data_agendamento):
        return jsonify({"message": "Data inválida."}), 400

    empresa = get_empresa_por_slug(empresa_slug)
    if not empresa:
        return jsonify({"message": "Empresa não encontrada."}), 404

    ok, msg = validar_regras_de_data(empresa["id"], data_agendamento)
    if not ok:
        return jsonify({"message": msg}), 400
    if not validar_uuid(profissional_id):
        return jsonify({"message": "ID profissional inválido."}), 400

    try:
        servico_id_int = int(servico_id)
    except ValueError:
        return jsonify({"message": "ID serviço inválido."}), 400

    with get_conn() as conn:
        barbeiro = profissional_pertence_empresa(conn, profissional_id, empresa["id"])
        if not barbeiro:
            return jsonify({"message": "Profissional não encontrado."}), 404
        servico = servico_pertence_empresa(conn, servico_id_int, empresa["id"])
        if not servico:
            return jsonify({"message": "Serviço não encontrado."}), 404
        if not servico_vinculado_ao_barbeiro(conn, profissional_id, servico_id_int):
            return jsonify({"message": "Profissional não atende este serviço."}), 400

        horarios_base = gerar_horarios(empresa["id"])
        duracao = int(servico["duracao_min"])
        disponiveis = []
        ocupados = []
        config = get_config_empresa(empresa["id"])
        hora_fechamento = config.get("hora_fechamento", "20:00")
        fechamento_min = hora_str_para_minutos(hora_fechamento)

        for h in horarios_base:
            inicio_min = hora_str_para_minutos(h)
            fim_min = inicio_min + duracao
            if fim_min > fechamento_min:
                ocupados.append(h)
                continue
            hora_fim = minutos_para_hora_str(fim_min)
            if existe_bloqueio(conn, empresa["id"], profissional_id, data_agendamento, h, hora_fim):
                ocupados.append(h)
                continue
            if existe_conflito_agendamento(conn, profissional_id, data_agendamento, h, hora_fim):
                ocupados.append(h)
                continue
            disponiveis.append(h)

        return jsonify({"disponiveis": disponiveis, "ocupados": ocupados}), 200

@app.route("/agendar/<empresa_slug>/confirmar-json", methods=["POST"])
def confirmar_agendamento(empresa_slug):
    ip = get_client_ip()
    if not rate_limit_check(f"confirmar:{ip}", MAX_AGENDAMENTO_POR_MINUTO, 60):
        return jsonify({"message": "Muitas tentativas. Aguarde."}), 429

    empresa = get_empresa_por_slug(empresa_slug)
    if not empresa:
        return jsonify({"message": "Empresa não encontrada."}), 404

    data = request.get_json(silent=True) or {}
    servico_nome = limpar_texto(data.get("servico", ""), 80)
    profissional_nome = limpar_texto(data.get("profissional", ""), 80)
    data_agendamento = limpar_texto(data.get("data", ""), 10)
    hora = limpar_texto(data.get("hora", ""), 5)
    cliente = limpar_texto(data.get("cliente", ""), 120)
    telefone = normalizar_telefone(data.get("telefone", ""))
    email = limpar_texto(data.get("email", ""), 120).lower()
    observacao = limpar_texto(data.get("observacao", ""), 300)
    profissional_id = data.get("profissional_id")
    servico_id = data.get("servico_id")

    if not all([servico_nome, profissional_nome, data_agendamento, hora, cliente, telefone]):
        return jsonify({"message": "Campos obrigatórios faltando."}), 400
    if not validar_data_yyyy_mm_dd(data_agendamento):
        return jsonify({"message": "Data inválida."}), 400

    ok, msg = validar_regras_de_data(empresa["id"], data_agendamento)
    if not ok:
        return jsonify({"message": msg}), 400
    if not validar_hora_hh_mm(hora):
        return jsonify({"message": "Hora inválida."}), 400
    if not validar_uuid(profissional_id):
        return jsonify({"message": "ID profissional inválido."}), 400

    try:
        servico_id = int(servico_id)
    except (TypeError, ValueError):
        return jsonify({"message": "ID serviço inválido."}), 400

    if not telefone_valido(telefone):
        return jsonify({"message": "Telefone inválido (10 a 13 dígitos)."}), 400
    if email and not email_valido(email):
        return jsonify({"message": "Email inválido."}), 400

    with get_conn() as conn:
        # Verificações iniciais
        barbeiro = profissional_pertence_empresa(conn, profissional_id, empresa["id"])
        if not barbeiro:
            return jsonify({"message": "Barbeiro não encontrado."}), 404
        servico_valido = servico_pertence_empresa(conn, servico_id, empresa["id"])
        if not servico_valido:
            return jsonify({"message": "Serviço não encontrado."}), 404
        if not servico_vinculado_ao_barbeiro(conn, profissional_id, servico_id):
            return jsonify({"message": "Profissional não atende este serviço."}), 400

        hora_inicio = hora
        duracao = int(servico_valido["duracao_min"])
        total_min = hora_str_para_minutos(hora_inicio) + duracao
        hora_fim = minutos_para_hora_str(total_min)

        horarios_permitidos = gerar_horarios(empresa["id"])
        if hora_inicio not in horarios_permitidos:
            return jsonify({"message": "Horário não permitido."}), 400
        config = get_config_empresa(empresa["id"])
        hora_fechamento = config.get("hora_fechamento", "20:00")
        if hora_str_para_minutos(hora_fim) > hora_str_para_minutos(hora_fechamento):
            return jsonify({"message": "Serviço ultrapassa horário de funcionamento."}), 400

        # Bloqueio e conflito (ainda fora da transação final para performance, mas a constraint única garante)
        if existe_bloqueio(conn, empresa["id"], profissional_id, data_agendamento, hora_inicio, hora_fim):
            return jsonify({"message": "Horário bloqueado."}), 409
        if existe_conflito_agendamento(conn, profissional_id, data_agendamento, hora_inicio, hora_fim):
            return jsonify({"message": "Horário já ocupado."}), 409

        cliente_id = buscar_ou_criar_cliente(conn, empresa["id"], cliente, telefone, email, observacao)

        agendamento_id = str(uuid.uuid4())
        agora = datetime.utcnow()
        try:
            with get_cursor(conn) as cur:
                cur.execute("""
                    INSERT INTO agendamentos (
                        id, empresa_id, cliente_id, barbeiro_id, servico_id,
                        data, hora_inicio, hora_fim,
                        cliente_nome, cliente_telefone, cliente_email,
                        preco, observacao, status, origem, criado_em, atualizado_em
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    agendamento_id, empresa["id"], cliente_id, profissional_id, servico_id,
                    data_agendamento, hora_inicio, hora_fim,
                    cliente, telefone, email or None,
                    float(servico_valido["preco"]),
                    observacao or None, "marcado", "site", agora, agora,
                ))
                conn.commit()
        except errors.UniqueViolation:
            conn.rollback()
            return jsonify({"message": "Horário já ocupado (conflito detectado)."}), 409

        mensagem = (
            f"Olá, tudo bem? Gostaria de confirmar um agendamento.\n\n"
            f"Nome: {cliente}\n"
            f"Serviço: {servico_valido['nome']}\n"
            f"Profissional: {barbeiro['nome']}\n"
            f"Data: {data_agendamento}\n"
            f"Hora: {hora_inicio}\n"
        )
        if observacao:
            mensagem += f"Observação: {observacao}\n"
        mensagem += "\nFico no aguardo da confirmação. Obrigado."

        whatsapp_url = ""
        telefone_empresa = normalizar_telefone(empresa.get("telefone") or "")
        if telefone_empresa:
            whatsapp_url = f"https://api.whatsapp.com/send?phone=55{telefone_empresa}&text={quote(mensagem)}"

        return jsonify({
            "message": "Agendamento realizado com sucesso.",
            "agendamento_id": agendamento_id,
            "cliente_telefone_mascarado": mascarar_telefone(telefone),
            "whatsapp_url_cliente": whatsapp_url,
        }), 200

# ==========================================================
# ROTAS INTERNAS
# ==========================================================
@app.route("/agenda/<empresa_slug>")
def agenda_dia(empresa_slug):
    try:
        token = limpar_texto(request.args.get("token", ""), 200)
        if not token:
            return _html_token_ausente(empresa_slug)
        empresa = validar_token_empresa(empresa_slug, token)
        if not empresa:
            return _html_token_invalido(empresa_slug)

        data_ref = limpar_texto(request.args.get("data", date.today().strftime("%Y-%m-%d")), 10)
        if not validar_data_yyyy_mm_dd(data_ref):
            data_ref = date.today().strftime("%Y-%m-%d")

        barbeiro_id = limpar_texto(request.args.get("barbeiro_id", ""), 80)
        barbeiro_selecionado = ""
        if barbeiro_id:
            with get_conn() as conn:
                with get_cursor(conn) as cur:
                    cur.execute("SELECT id FROM barbeiros WHERE id=%s AND empresa_id=%s AND ativo=TRUE", (barbeiro_id, empresa["id"]))
                    if cur.fetchone():
                        barbeiro_selecionado = barbeiro_id

        agendamentos = obter_agendamentos_do_dia(empresa["id"], data_ref, barbeiro_selecionado)
        resumo = gerar_resumo_agendamentos(agendamentos)
        barbeiros = listar_barbeiros(empresa["id"])

        return render_template("agenda_dia.html",
                               empresa=empresa, token=token, data_ref=data_ref,
                               barbeiros=barbeiros, barbeiro_selecionado=barbeiro_selecionado,
                               resumo=resumo, agendamentos=agendamentos)
    except Exception as e:
        print(f"[ERROR] agenda_dia: {e}")
        traceback.print_exc()
        return jsonify({"message": "Erro interno no servidor."}), 500

def _html_token_ausente(slug):
    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Token ausente</title>
<style>
  body{{font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f1f5f9;padding:16px}}
  .box{{background:#fff;border:1px solid #e2e8f0;border-radius:20px;padding:36px 32px;max-width:500px;text-align:center}}
  code{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;display:block;word-break:break-all}}
</style></head><body>
<div class="box"><h2>🔐 Token não fornecido</h2>
<p>URL correta: <code>/agenda/{slug}?token=SEU_TOKEN_AQUI</code></p>
<p>Consulte os logs do Railway para obter o token.</p></div></body></html>"""
    response = make_response(html, 401)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response

def _html_token_invalido(slug):
    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Token inválido</title>
<style>
  body{{font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f1f5f9;padding:16px}}
  .box{{background:#fff;border-radius:20px;padding:36px 32px;max-width:520px;text-align:center}}
  code{{background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:12px 16px;display:block}}
</style></head><body>
<div class="box"><h2>❌ Token inválido</h2>
<p>Token não corresponde à barbearia <strong>{slug}</strong>.</p>
<p>Copie a URL exata que aparece nos logs do Railway.</p></div></body></html>"""
    response = make_response(html, 401)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response

@app.route("/api/agendamentos/<agendamento_id>/status", methods=["POST"])
def atualizar_status(agendamento_id):
    token = request.args.get("token") or request.headers.get("X-Auth-Token", "")
    token = limpar_texto(token, 200)
    if not token:
        abort(401, description="Token não fornecido.")

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT id FROM empresas WHERE token = %s AND ativo = TRUE", (token,))
            empresa = cur.fetchone()
            if not empresa:
                abort(401, description="Token inválido.")
            cur.execute("SELECT id FROM agendamentos WHERE id = %s AND empresa_id = %s", (agendamento_id, empresa["id"]))
            if not cur.fetchone():
                abort(404, description="Agendamento não encontrado.")

            data = request.get_json(silent=True) or {}
            novo_status = limpar_texto(data.get("status", ""), 20).lower()
            if novo_status not in STATUS_VALIDOS:
                return jsonify({"error": f"Status inválido. Aceitos: {STATUS_VALIDOS}"}), 400

            agora = datetime.utcnow()
            cur.execute("UPDATE agendamentos SET status=%s, atualizado_em=%s WHERE id=%s", (novo_status, agora, agendamento_id))
            conn.commit()
            return jsonify({"success": True, "status": novo_status}), 200

@app.route("/exportar-csv")
def exportar_csv():
    token = limpar_texto(request.args.get("token", ""), 200)
    empresa_slug = limpar_texto(request.args.get("empresa", ""), 80)
    data_ref = limpar_texto(request.args.get("data", ""), 10)
    barbeiro_id = limpar_texto(request.args.get("barbeiro_id", ""), 80)

    if not token:
        abort(401, description="Token não fornecido.")
    empresa = validar_token_empresa(empresa_slug, token)
    if not empresa:
        abort(401, description="Token inválido.")
    if data_ref and not validar_data_yyyy_mm_dd(data_ref):
        return jsonify({"message": "Data inválida."}), 400

    data_exportacao = data_ref or date.today().strftime("%Y-%m-%d")
    rows = obter_agendamentos_do_dia(empresa["id"], data_exportacao, barbeiro_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Data", "Hora Inicio", "Hora Fim", "Cliente", "Telefone", "Email", "Servico", "Barbeiro", "Preco", "Status", "Observacao"])
    for r in rows:
        writer.writerow([r.get("data"), r.get("hora_inicio"), r.get("hora_fim"), r.get("cliente_nome"),
                         r.get("cliente_telefone"), r.get("cliente_email"), r.get("servico_nome"),
                         r.get("barbeiro_nome"), r.get("preco"), r.get("status"), r.get("observacao")])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    output.close()
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=f"agendamentos_{empresa_slug}_{data_exportacao}.csv")

@app.route("/agenda/<empresa_slug>/exportar-csv")
def exportar_csv_interno(empresa_slug):
    token = limpar_texto(request.args.get("token", ""), 200)
    if not token:
        abort(401, description="Token não fornecido.")
    empresa = validar_token_empresa(empresa_slug, token)
    if not empresa:
        abort(401, description="Token inválido.")
    data_ref = limpar_texto(request.args.get("data", ""), 10)
    barbeiro_id = limpar_texto(request.args.get("barbeiro_id", ""), 80)
    return redirect(url_for("exportar_csv", empresa=empresa_slug, data=data_ref, barbeiro_id=barbeiro_id, token=token))

@app.route("/debug/token")
def debug_token():
    if not DEBUG_SECRET:
        abort(404)
    secret = limpar_texto(request.args.get("secret", ""), 200)
    if not secrets.compare_digest(secret, DEBUG_SECRET):
        abort(403, description="Secret incorreto.")
    slug = limpar_texto(request.args.get("slug", DEFAULT_EMPRESA_SLUG), 80)
    empresa = get_empresa_por_slug(slug)
    if not empresa:
        return jsonify({"error": f"Empresa '{slug}' não encontrada."}), 404
    token = empresa.get("token", "")
    agenda_url = f"/agenda/{slug}?token={token}"
    return jsonify({"slug": slug, "token": token, "agenda_url": agenda_url, "url_completa": f"{request.host_url.rstrip('/')}{agenda_url}"}), 200

# ==========================================================
# HANDLERS DE ERRO E SEGURANÇA
# ==========================================================
@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
    return response

@app.errorhandler(400)
def bad_request(err): return jsonify({"message": getattr(err, "description", "Requisição inválida.")}), 400
@app.errorhandler(401)
def unauthorized(err): return jsonify({"message": getattr(err, "description", "Não autorizado.")}), 401
@app.errorhandler(403)
def forbidden(err): return jsonify({"message": getattr(err, "description", "Acesso negado.")}), 403
@app.errorhandler(404)
def not_found(err): return jsonify({"message": "Recurso não encontrado."}), 404
@app.errorhandler(405)
def method_not_allowed(err): return jsonify({"message": "Método não permitido."}), 405
@app.errorhandler(413)
def payload_too_large(err): return jsonify({"message": "Payload muito grande."}), 413
@app.errorhandler(429)
def too_many_requests(err): return jsonify({"message": "Muitas requisições. Tente novamente."}), 429
@app.errorhandler(500)
def internal_error(err): return jsonify({"message": "Erro interno no servidor."}), 500

# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, host=os.getenv("FLASK_HOST", "0.0.0.0"), port=int(os.getenv("PORT", "5000")))