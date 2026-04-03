import sqlite3
import csv
import io
import uuid
import os
import re
import time
import secrets
from datetime import datetime, date
from urllib.parse import quote
from collections import defaultdict, deque

from flask import (
    Flask, render_template, request, jsonify,
    send_file, redirect, url_for, abort
)

app = Flask(__name__)

# ==========================================================
# CONFIGURACOES - CORRIGIDO PARA RAILWAY
# ==========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "database.db")

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", secrets.token_hex(32))
DEFAULT_EMPRESA_SLUG = os.getenv("DEFAULT_EMPRESA_SLUG", "barbearia")
MAX_AGENDAMENTO_POR_MINUTO = int(os.getenv("MAX_AGENDAMENTO_POR_MINUTO", "10"))
MAX_DISPONIBILIDADE_POR_MINUTO = int(os.getenv("MAX_DISPONIBILIDADE_POR_MINUTO", "60"))

app.config["SECRET_KEY"] = APP_SECRET_KEY
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

RATE_LIMIT_STORAGE = defaultdict(deque)

PHONE_RE = re.compile(r"^\d{10,13}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATUS_VALIDOS = ["marcado", "confirmado", "concluido", "cancelado"]


# ==========================================================
# UTILITARIOS
# ==========================================================
def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limit_check(key: str, limit: int, window_seconds: int = 60) -> bool:
    current = time.time()
    queue = RATE_LIMIT_STORAGE[key]

    while queue and (current - queue[0]) > window_seconds:
        queue.popleft()

    if len(queue) >= limit:
        return False

    queue.append(current)
    return True


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
    h, m = map(int, hora_str.split(":"))
    return h * 60 + m


def minutos_para_hora_str(minutos: int) -> str:
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def intervalo_sobrepoe(inicio_a: str, fim_a: str, inicio_b: str, fim_b: str) -> bool:
    a1 = hora_str_para_minutos(inicio_a)
    a2 = hora_str_para_minutos(fim_a)
    b1 = hora_str_para_minutos(inicio_b)
    b2 = hora_str_para_minutos(fim_b)
    return a1 < b2 and a2 > b1


def validar_data_yyyy_mm_dd(data_str: str) -> bool:
    try:
        datetime.strptime(data_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validar_hora_hh_mm(hora_str: str) -> bool:
    try:
        if len(hora_str) != 5 or hora_str[2] != ":":
            return False
        h, m = map(int, hora_str.split(':'))
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


# ==========================================================
# BANCO DE DADOS
# ==========================================================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_column_exists(conn, table_name, column_name, alter_sql):
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    nomes = [c["name"] for c in cols]
    if column_name not in nomes:
        conn.execute(alter_sql)
        conn.commit()


def init_db():
    conn = get_conn()

    conn.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS empresas (
        id TEXT PRIMARY KEY,
        nome TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        telefone TEXT,
        email TEXT,
        endereco TEXT,
        logo_url TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        token TEXT,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT
    );

    CREATE TABLE IF NOT EXISTS barbeiros (
        id TEXT PRIMARY KEY,
        empresa_id TEXT NOT NULL,
        nome TEXT NOT NULL,
        whatsapp TEXT,
        email TEXT,
        foto_url TEXT,
        bio TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS servicos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id TEXT NOT NULL,
        nome TEXT NOT NULL,
        descricao TEXT,
        preco REAL NOT NULL DEFAULT 0,
        duracao_min INTEGER NOT NULL DEFAULT 30,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS barbeiro_servicos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barbeiro_id TEXT NOT NULL,
        servico_id INTEGER NOT NULL,
        criado_em TEXT NOT NULL,
        UNIQUE(barbeiro_id, servico_id),
        FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id) ON DELETE CASCADE,
        FOREIGN KEY (servico_id) REFERENCES servicos(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS clientes (
        id TEXT PRIMARY KEY,
        empresa_id TEXT NOT NULL,
        nome TEXT NOT NULL,
        telefone TEXT NOT NULL,
        email TEXT,
        observacoes TEXT,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT,
        UNIQUE(empresa_id, telefone),
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS agendamentos (
        id TEXT PRIMARY KEY,
        empresa_id TEXT NOT NULL,
        cliente_id TEXT,
        barbeiro_id TEXT NOT NULL,
        servico_id INTEGER NOT NULL,
        data TEXT NOT NULL,
        hora_inicio TEXT NOT NULL,
        hora_fim TEXT NOT NULL,
        cliente_nome TEXT NOT NULL,
        cliente_telefone TEXT NOT NULL,
        cliente_email TEXT,
        preco REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        status TEXT NOT NULL DEFAULT 'marcado',
        origem TEXT NOT NULL DEFAULT 'site',
        criado_em TEXT NOT NULL,
        atualizado_em TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL,
        FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id) ON DELETE CASCADE,
        FOREIGN KEY (servico_id) REFERENCES servicos(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS bloqueios_agenda (
        id TEXT PRIMARY KEY,
        empresa_id TEXT NOT NULL,
        barbeiro_id TEXT,
        data TEXT NOT NULL,
        hora_inicio TEXT,
        hora_fim TEXT,
        tipo TEXT NOT NULL DEFAULT 'bloqueio',
        motivo TEXT,
        criado_em TEXT NOT NULL,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
        FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS configuracoes_empresa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id TEXT NOT NULL UNIQUE,
        hora_abertura TEXT NOT NULL DEFAULT '08:00',
        hora_fechamento TEXT NOT NULL DEFAULT '20:00',
        intervalo_min INTEGER NOT NULL DEFAULT 30,
        antecedencia_max_dias INTEGER NOT NULL DEFAULT 30,
        permite_encaixe INTEGER NOT NULL DEFAULT 0,
        envia_whatsapp INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_barbeiros_empresa ON barbeiros (empresa_id);
    CREATE INDEX IF NOT EXISTS idx_servicos_empresa ON servicos (empresa_id);
    CREATE INDEX IF NOT EXISTS idx_clientes_empresa_telefone ON clientes (empresa_id, telefone);
    CREATE INDEX IF NOT EXISTS idx_agendamentos_empresa_data ON agendamentos (empresa_id, data);
    CREATE INDEX IF NOT EXISTS idx_agendamentos_barbeiro_data ON agendamentos (barbeiro_id, data);
    CREATE INDEX IF NOT EXISTS idx_bloqueios_empresa_data ON bloqueios_agenda (empresa_id, data);
    """)

    ensure_column_exists(conn, "empresas", "token",
                         "ALTER TABLE empresas ADD COLUMN token TEXT")

    empresas_sem_token = conn.execute(
        "SELECT id FROM empresas WHERE token IS NULL OR TRIM(token) = ''"
    ).fetchall()
    for emp in empresas_sem_token:
        conn.execute(
            "UPDATE empresas SET token = ? WHERE id = ?",
            (gerar_token_empresa(), emp["id"])
        )
    conn.commit()

    empresa = conn.execute(
        "SELECT id, token FROM empresas WHERE LOWER(TRIM(slug)) = LOWER(TRIM(?)) LIMIT 1",
        (DEFAULT_EMPRESA_SLUG,)
    ).fetchone()

    if not empresa:
        agora = now_iso()
        empresa_id = str(uuid.uuid4())
        token_padrao = gerar_token_empresa()

        conn.execute(
            """
            INSERT INTO empresas (
                id, nome, slug, telefone, email, endereco, logo_url,
                ativo, criado_em, atualizado_em, token
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                empresa_id,
                "Barbearia do Ze",
                DEFAULT_EMPRESA_SLUG,
                "11999999999",
                "contato@barbearia.com",
                "Sao Paulo - SP",
                "/static/uploads/empresas/logo_padrao.jpg",
                1,
                agora,
                agora,
                token_padrao,
            ),
        )

        conn.execute(
            """
            INSERT INTO configuracoes_empresa (
                empresa_id, hora_abertura, hora_fechamento, intervalo_min,
                antecedencia_max_dias, permite_encaixe, envia_whatsapp,
                criado_em, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (empresa_id, "08:00", "20:00", 30, 30, 0, 1, agora, agora),
        )

        barbeiros = [
            (str(uuid.uuid4()), empresa_id, "Joao Silva", "11988888888", None, None, None, 1, agora, agora),
            (str(uuid.uuid4()), empresa_id, "Carlos Souza", "11999999999", None, None, None, 1, agora, agora),
        ]
        conn.executemany(
            """
            INSERT INTO barbeiros (
                id, empresa_id, nome, whatsapp, email, foto_url, bio,
                ativo, criado_em, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            barbeiros,
        )

        servicos = [
            (empresa_id, "Corte classico", "Corte tradicional", 35.00, 30, 1, agora, agora),
            (empresa_id, "Barba", "Barba completa", 25.00, 20, 1, agora, agora),
            (empresa_id, "Corte + Barba", "Pacote completo", 55.00, 50, 1, agora, agora),
        ]
        conn.executemany(
            """
            INSERT INTO servicos (
                empresa_id, nome, descricao, preco, duracao_min,
                ativo, criado_em, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            servicos,
        )

        servicos_ids = conn.execute(
            "SELECT id FROM servicos WHERE empresa_id = ?",
            (empresa_id,),
        ).fetchall()
        for barbeiro in barbeiros:
            for servico in servicos_ids:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO barbeiro_servicos (barbeiro_id, servico_id, criado_em)
                    VALUES (?, ?, ?)
                    """,
                    (barbeiro[0], servico["id"], agora),
                )

        conn.commit()
        print(f"Empresa padrao criada: slug={DEFAULT_EMPRESA_SLUG}, token={token_padrao}")

    conn.close()


# ==========================================================
# INICIALIZACAO DO BANCO (GUNICORN)
# ==========================================================
init_db()


# ==========================================================
# SEGURANCA (HEADERS)
# ==========================================================
@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https: data: 'unsafe-inline' 'unsafe-eval'; "
        "img-src 'self' https: data:; "
        "font-src 'self' https: data:;"
    )
    return response


# ==========================================================
# FUNCOES DE NEGOCIO (BANCO)
# ==========================================================
def get_empresa_por_slug(slug):
    conn = get_conn()
    empresa = conn.execute(
        "SELECT * FROM empresas WHERE LOWER(TRIM(slug)) = LOWER(TRIM(?)) AND ativo = 1",
        (slug,),
    ).fetchone()
    conn.close()
    return empresa


def validar_token_empresa(empresa_slug, token):
    conn = get_conn()
    empresa = conn.execute(
        "SELECT * FROM empresas WHERE LOWER(TRIM(slug)) = LOWER(TRIM(?)) AND token = ? AND ativo = 1",
        (empresa_slug, token),
    ).fetchone()
    conn.close()
    return empresa


def get_config_empresa(empresa_id):
    conn = get_conn()
    config = conn.execute(
        """
        SELECT hora_abertura, hora_fechamento, intervalo_min, antecedencia_max_dias, permite_encaixe
        FROM configuracoes_empresa
        WHERE empresa_id = ?
        """,
        (empresa_id,),
    ).fetchone()
    conn.close()
    return config


def listar_barbeiros(empresa_id):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, nome, whatsapp, foto_url
        FROM barbeiros
        WHERE empresa_id = ? AND ativo = 1
        ORDER BY nome
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def listar_servicos(empresa_id):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, nome, preco, duracao_min, descricao
        FROM servicos
        WHERE empresa_id = ? AND ativo = 1
        ORDER BY nome
        """,
        (empresa_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def gerar_horarios(empresa_id):
    config = get_config_empresa(empresa_id)
    if not config:
        abertura = "08:00"
        fechamento = "20:00"
        intervalo = 30
    else:
        abertura = config["hora_abertura"]
        fechamento = config["hora_fechamento"]
        intervalo = config["intervalo_min"]

    inicio = hora_str_para_minutos(abertura)
    fim = hora_str_para_minutos(fechamento)
    return [minutos_para_hora_str(m) for m in range(inicio, fim, intervalo)]


def profissional_pertence_empresa(conn, profissional_id, empresa_id):
    return conn.execute(
        """
        SELECT id, nome, whatsapp
        FROM barbeiros
        WHERE id = ? AND empresa_id = ? AND ativo = 1
        """,
        (profissional_id, empresa_id),
    ).fetchone()


def servico_pertence_empresa(conn, servico_id, empresa_id):
    return conn.execute(
        """
        SELECT id, nome, descricao, preco, duracao_min
        FROM servicos
        WHERE id = ? AND empresa_id = ? AND ativo = 1
        """,
        (servico_id, empresa_id),
    ).fetchone()


def servico_vinculado_ao_barbeiro(conn, profissional_id, servico_id):
    return conn.execute(
        """
        SELECT 1 FROM barbeiro_servicos
        WHERE barbeiro_id = ? AND servico_id = ?
        LIMIT 1
        """,
        (profissional_id, servico_id),
    ).fetchone() is not None


def existe_bloqueio(conn, empresa_id, barbeiro_id, data_agendamento, hora_inicio, hora_fim):
    rows = conn.execute(
        """
        SELECT hora_inicio, hora_fim
        FROM bloqueios_agenda
        WHERE empresa_id = ?
          AND data = ?
          AND (barbeiro_id IS NULL OR barbeiro_id = ?)
        """,
        (empresa_id, data_agendamento, barbeiro_id),
    ).fetchall()

    for r in rows:
        b_ini = r["hora_inicio"] or "00:00"
        b_fim = r["hora_fim"] or "23:59"
        if intervalo_sobrepoe(hora_inicio, hora_fim, b_ini, b_fim):
            return True
    return False


def existe_conflito_agendamento(conn, barbeiro_id, data_agendamento, hora_inicio, hora_fim):
    rows = conn.execute(
        """
        SELECT hora_inicio, hora_fim
        FROM agendamentos
        WHERE barbeiro_id = ?
          AND data = ?
          AND status IN ('marcado', 'confirmado')
        """,
        (barbeiro_id, data_agendamento),
    ).fetchall()

    for r in rows:
        if intervalo_sobrepoe(hora_inicio, hora_fim, r["hora_inicio"], r["hora_fim"]):
            return True
    return False


def buscar_ou_criar_cliente(conn, empresa_id, nome, telefone, email, observacoes=""):
    row = conn.execute(
        "SELECT id FROM clientes WHERE empresa_id = ? AND telefone = ? LIMIT 1",
        (empresa_id, telefone),
    ).fetchone()
    agora = now_iso()

    if row:
        conn.execute(
            """
            UPDATE clientes
            SET nome = ?, email = ?, observacoes = ?, atualizado_em = ?
            WHERE id = ?
            """,
            (nome, email or None, observacoes or None, agora, row["id"]),
        )
        return row["id"]

    cliente_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO clientes (
            id, empresa_id, nome, telefone, email, observacoes, criado_em, atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (cliente_id, empresa_id, nome, telefone, email or None, observacoes or None, agora, agora),
    )
    return cliente_id


def validar_regras_de_data(empresa_id, data_agendamento):
    config = get_config_empresa(empresa_id)
    antecedencia_max_dias = 30 if not config else int(config["antecedencia_max_dias"])
    data_escolhida = parse_data(data_agendamento)
    hoje = date.today()

    if data_escolhida < hoje:
        return False, "Nao e permitido agendar em data passada."

    diferenca = (data_escolhida - hoje).days
    if diferenca > antecedencia_max_dias:
        return False, f"Agendamento permitido apenas ate {antecedencia_max_dias} dias a frente."

    return True, ""


def obter_agendamentos_do_dia(conn, empresa_id, data_ref, barbeiro_id=""):
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
            s.nome AS servico_nome,
            b.nome AS barbeiro_nome,
            b.id AS barbeiro_id
        FROM agendamentos a
        JOIN servicos s ON s.id = a.servico_id
        JOIN barbeiros b ON b.id = a.barbeiro_id
        WHERE a.empresa_id = ? AND a.data = ?
    """
    params = [empresa_id, data_ref]

    if barbeiro_id:
        query += " AND a.barbeiro_id = ?"
        params.append(barbeiro_id)

    query += " ORDER BY a.hora_inicio ASC, a.criado_em ASC"
    return conn.execute(query, params).fetchall()


def gerar_resumo_agendamentos(agendamentos):
    resumo = {
        "total": 0,
        "marcado": 0,
        "confirmado": 0,
        "concluido": 0,
        "cancelado": 0,
    }
    for item in agendamentos:
        resumo["total"] += 1
        status = (item["status"] or "").lower()
        if status in resumo:
            resumo[status] += 1
    return resumo


# ==========================================================
# ROTAS PUBLICAS (AGENDAMENTO)
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
        return "Empresa nao encontrada.", 404

    barbeiros = listar_barbeiros(empresa["id"])
    servicos = listar_servicos(empresa["id"])

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Agendamento - {{ empresa.nome }}</title>
        <style>
            body { font-family: Arial; margin: 20px; }
            .container { max-width: 500px; margin: auto; }
            label, select, input { display: block; width: 100%; margin-bottom: 10px; }
            button { margin-top: 10px; padding: 10px; background: #28a745; color: white; border: none; }
            .horarios { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
            .horario { padding: 8px; background: #f0f0f0; cursor: pointer; border-radius: 4px; }
            .horario.disponivel { background: #d4edda; }
            .horario.ocupado { background: #f8d7da; text-decoration: line-through; cursor: not-allowed; }
            .horario.selecionado { background: #007bff; color: white; }
            #resultado { margin-top: 20px; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>{{ empresa.nome }}</h2>
            <form id="agendamentoForm">
                <label>Nome completo:</label>
                <input type="text" id="cliente" required>
                <label>Telefone (com DDD, apenas numeros):</label>
                <input type="tel" id="telefone" required pattern="\\d{10,13}">
                <label>Email (opcional):</label>
                <input type="email" id="email">
                <label>Servico:</label>
                <select id="servico_id" required>
                    <option value="">Selecione</option>
                    {% for s in servicos %}
                    <option value="{{ s.id }}" data-duracao="{{ s.duracao_min }}">{{ s.nome }} - R$ {{ s.preco }}</option>
                    {% endfor %}
                </select>
                <label>Profissional:</label>
                <select id="profissional_id" required>
                    <option value="">Selecione</option>
                    {% for b in barbeiros %}
                    <option value="{{ b.id }}">{{ b.nome }}</option>
                    {% endfor %}
                </select>
                <label>Data:</label>
                <input type="date" id="data" required>
                <label>Horarios disponiveis:</label>
                <div id="horarios" class="horarios"></div>
                <input type="hidden" id="hora_selecionada">
                <button type="submit">Agendar</button>
            </form>
            <div id="resultado"></div>
        </div>
        <script>
            const servicoSelect = document.getElementById('servico_id');
            const profissionalSelect = document.getElementById('profissional_id');
            const dataInput = document.getElementById('data');
            const horariosDiv = document.getElementById('horarios');
            const horaHidden = document.getElementById('hora_selecionada');
            const form = document.getElementById('agendamentoForm');
            const resultado = document.getElementById('resultado');

            let currentDisponiveis = [];

            function carregarHorarios() {
                const servicoId = servicoSelect.value;
                const profissionalId = profissionalSelect.value;
                const data = dataInput.value;
                if (!servicoId || !profissionalId || !data) {
                    horariosDiv.innerHTML = '<p>Preencha servico, profissional e data.</p>';
                    return;
                }
                fetch(`/api/agendamentos/disponibilidade?empresa_slug={{ empresa.slug }}&profissional_id=${profissionalId}&data=${data}&servico_id=${servicoId}`)
                    .then(res => res.json())
                    .then(data => {
                        if (data.disponiveis) {
                            currentDisponiveis = data.disponiveis;
                            renderHorarios(data.disponiveis, data.ocupados || []);
                        } else {
                            horariosDiv.innerHTML = '<p>Erro ao carregar horarios.</p>';
                        }
                    })
                    .catch(err => {
                        console.error(err);
                        horariosDiv.innerHTML = '<p>Erro ao carregar horarios.</p>';
                    });
            }

            function renderHorarios(disponiveis, ocupados) {
                if (disponiveis.length === 0 && ocupados.length === 0) {
                    horariosDiv.innerHTML = '<p>Nenhum horario disponivel.</p>';
                    return;
                }
                let html = '';
                disponiveis.forEach(h => {
                    html += `<div class="horario disponivel" data-hora="${h}">${h}</div>`;
                });
                ocupados.forEach(h => {
                    html += `<div class="horario ocupado" data-hora="${h}">${h}</div>`;
                });
                horariosDiv.innerHTML = html;
                document.querySelectorAll('.horario.disponivel').forEach(el => {
                    el.addEventListener('click', () => {
                        document.querySelectorAll('.horario').forEach(h => h.classList.remove('selecionado'));
                        el.classList.add('selecionado');
                        horaHidden.value = el.dataset.hora;
                    });
                });
            }

            servicoSelect.addEventListener('change', carregarHorarios);
            profissionalSelect.addEventListener('change', carregarHorarios);
            dataInput.addEventListener('change', carregarHorarios);

            form.addEventListener('submit', (e) => {
                e.preventDefault();
                const cliente = document.getElementById('cliente').value.trim();
                const telefone = document.getElementById('telefone').value.trim();
                const email = document.getElementById('email').value.trim();
                const servicoId = servicoSelect.value;
                const profissionalId = profissionalSelect.value;
                const data = dataInput.value;
                const hora = horaHidden.value;
                const servicoNome = servicoSelect.options[servicoSelect.selectedIndex]?.text.split(' -')[0] || '';
                const profissionalNome = profissionalSelect.options[profissionalSelect.selectedIndex]?.text || '';

                if (!cliente || !telefone || !servicoId || !profissionalId || !data || !hora) {
                    resultado.innerText = 'Preencha todos os campos e selecione um horario.';
                    return;
                }
                if (!/^\\d{10,13}$/.test(telefone)) {
                    resultado.innerText = 'Telefone invalido (10 a 13 digitos).';
                    return;
                }

                const payload = {
                    servico: servicoNome,
                    profissional: profissionalNome,
                    data: data,
                    hora: hora,
                    cliente: cliente,
                    telefone: telefone,
                    email: email,
                    observacao: '',
                    profissional_id: profissionalId,
                    servico_id: servicoId
                };

                fetch(`/agendar/{{ empresa.slug }}/confirmar-json`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                })
                .then(res => res.json())
                .then(data => {
                    if (data.message) {
                        resultado.innerText = data.message;
                        if (data.whatsapp_url_cliente) {
                            window.location.href = data.whatsapp_url_cliente;
                        }
                        form.reset();
                        horariosDiv.innerHTML = '';
                        horaHidden.value = '';
                    } else {
                        resultado.innerText = 'Erro ao agendar.';
                    }
                })
                .catch(err => {
                    console.error(err);
                    resultado.innerText = 'Erro ao agendar.';
                });
            });
        </script>
    </body>
    </html>
    """
    from flask import render_template_string
    return render_template_string(html, empresa=empresa, barbeiros=barbeiros, servicos=servicos)


@app.route("/api/agendamentos/disponibilidade")
def disponibilidade():
    ip = get_client_ip()
    if not rate_limit_check(f"disponibilidade:{ip}", MAX_DISPONIBILIDADE_POR_MINUTO, 60):
        return jsonify({"message": "Muitas consultas. Tente novamente em instantes."}), 429

    empresa_slug = limpar_texto(request.args.get("empresa_slug", ""), 80)
    profissional_id = limpar_texto(request.args.get("profissional_id", ""), 80)
    data_agendamento = limpar_texto(request.args.get("data", ""), 10)
    servico_id = limpar_texto(request.args.get("servico_id", ""), 20)

    if not empresa_slug or not profissional_id or not data_agendamento or not servico_id:
        return jsonify({"message": "Parametros incompletos."}), 400
    if not validar_data_yyyy_mm_dd(data_agendamento):
        return jsonify({"message": "Data invalida."}), 400

    empresa = get_empresa_por_slug(empresa_slug)
    if not empresa:
        return jsonify({"message": "Empresa nao encontrada."}), 404

    ok, msg = validar_regras_de_data(empresa["id"], data_agendamento)
    if not ok:
        return jsonify({"message": msg}), 400
    if not validar_uuid(profissional_id):
        return jsonify({"message": "ID profissional invalido."}), 400

    try:
        servico_id_int = int(servico_id)
    except ValueError:
        return jsonify({"message": "ID servico invalido."}), 400

    conn = get_conn()
    try:
        barbeiro = profissional_pertence_empresa(conn, profissional_id, empresa["id"])
        if not barbeiro:
            return jsonify({"message": "Profissional nao encontrado."}), 404

        servico = servico_pertence_empresa(conn, servico_id_int, empresa["id"])
        if not servico:
            return jsonify({"message": "Servico nao encontrado."}), 404

        if not servico_vinculado_ao_barbeiro(conn, profissional_id, servico_id_int):
            return jsonify({"message": "Profissional nao atende este servico."}), 400

        horarios_base = gerar_horarios(empresa["id"])
        duracao = int(servico["duracao_min"])
        disponiveis = []
        ocupados = []

        config = get_config_empresa(empresa["id"])
        hora_fechamento = "20:00" if not config else config["hora_fechamento"]
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
    finally:
        conn.close()


@app.route("/agendar/<empresa_slug>/confirmar-json", methods=["POST"])
def confirmar_agendamento(empresa_slug):
    ip = get_client_ip()
    if not rate_limit_check(f"confirmar:{ip}", MAX_AGENDAMENTO_POR_MINUTO, 60):
        return jsonify({"message": "Muitas tentativas. Aguarde."}), 429

    empresa = get_empresa_por_slug(empresa_slug)
    if not empresa:
        return jsonify({"message": "Empresa nao encontrada."}), 404

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
        return jsonify({"message": "Campos obrigatorios faltando."}), 400
    if not validar_data_yyyy_mm_dd(data_agendamento):
        return jsonify({"message": "Data invalida."}), 400

    ok, msg = validar_regras_de_data(empresa["id"], data_agendamento)
    if not ok:
        return jsonify({"message": msg}), 400
    if not validar_hora_hh_mm(hora):
        return jsonify({"message": "Hora invalida."}), 400
    if not validar_uuid(profissional_id):
        return jsonify({"message": "ID profissional invalido."}), 400

    try:
        servico_id = int(servico_id)
    except (TypeError, ValueError):
        return jsonify({"message": "ID servico invalido."}), 400

    if not telefone_valido(telefone):
        return jsonify({"message": "Telefone invalido (10 a 13 digitos)."}), 400
    if email and not email_valido(email):
        return jsonify({"message": "Email invalido."}), 400

    conn = get_conn()
    try:
        barbeiro = profissional_pertence_empresa(conn, profissional_id, empresa["id"])
        if not barbeiro:
            return jsonify({"message": "Barbeiro nao encontrado."}), 404

        servico_valido = servico_pertence_empresa(conn, servico_id, empresa["id"])
        if not servico_valido:
            return jsonify({"message": "Servico nao encontrado."}), 404

        if not servico_vinculado_ao_barbeiro(conn, profissional_id, servico_id):
            return jsonify({"message": "Profissional nao atende este servico."}), 400

        hora_inicio = hora
        duracao = int(servico_valido["duracao_min"])
        total_min = hora_str_para_minutos(hora_inicio) + duracao
        hora_fim = minutos_para_hora_str(total_min)

        horarios_permitidos = gerar_horarios(empresa["id"])
        if hora_inicio not in horarios_permitidos:
            return jsonify({"message": "Horario nao permitido."}), 400

        config = get_config_empresa(empresa["id"])
        hora_fechamento = "20:00" if not config else config["hora_fechamento"]
        if hora_str_para_minutos(hora_fim) > hora_str_para_minutos(hora_fechamento):
            return jsonify({"message": "Servico ultrapassa horario de funcionamento."}), 400

        if existe_bloqueio(conn, empresa["id"], profissional_id, data_agendamento, hora_inicio, hora_fim):
            return jsonify({"message": "Horario bloqueado."}), 409
        if existe_conflito_agendamento(conn, profissional_id, data_agendamento, hora_inicio, hora_fim):
            return jsonify({"message": "Horario ja ocupado."}), 409

        cliente_id = buscar_ou_criar_cliente(
            conn=conn,
            empresa_id=empresa["id"],
            nome=cliente,
            telefone=telefone,
            email=email,
            observacoes=observacao,
        )

        agendamento_id = str(uuid.uuid4())
        agora = now_iso()
        conn.execute(
            """
            INSERT INTO agendamentos (
                id, empresa_id, cliente_id, barbeiro_id, servico_id,
                data, hora_inicio, hora_fim,
                cliente_nome, cliente_telefone, cliente_email,
                preco, observacao, status, origem, criado_em, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agendamento_id,
                empresa["id"],
                cliente_id,
                profissional_id,
                servico_id,
                data_agendamento,
                hora_inicio,
                hora_fim,
                cliente,
                telefone,
                email or None,
                float(servico_valido["preco"]),
                observacao or None,
                "marcado",
                "site",
                agora,
                agora,
            ),
        )
        conn.commit()

        mensagem = (
            f"Ola, tudo bem? Gostaria de confirmar um agendamento.\n\n"
            f"Nome: {cliente}\n"
            f"Servico: {servico_valido['nome']}\n"
            f"Profissional: {barbeiro['nome']}\n"
            f"Data: {data_agendamento}\n"
            f"Hora: {hora_inicio}\n"
        )
        if observacao:
            mensagem += f"Observacao: {observacao}\n"
        mensagem += "\nFico no aguardo da confirmacao. Obrigado."

        whatsapp_url = ""
        telefone_empresa = normalizar_telefone(empresa["telefone"] or "")
        if telefone_empresa:
            whatsapp_url = f"https://api.whatsapp.com/send?phone=55{telefone_empresa}&text={quote(mensagem)}"

        return jsonify(
            {
                "message": "Agendamento realizado com sucesso.",
                "agendamento_id": agendamento_id,
                "cliente_telefone_mascarado": mascarar_telefone(telefone),
                "whatsapp_url_cliente": whatsapp_url,
            }
        ), 200
    finally:
        conn.close()


# ==========================================================
# ROTAS INTERNAS (DONO)
# ==========================================================
@app.route("/agenda/<empresa_slug>")
def agenda_dia(empresa_slug):
    token = request.args.get("token", "").strip()
    empresa = validar_token_empresa(empresa_slug, token)
    if not empresa:
        abort(401, description="Token invalido ou empresa nao encontrada.")

    data_ref = limpar_texto(request.args.get("data", date.today().strftime("%Y-%m-%d")), 10)
    barbeiro_id = limpar_texto(request.args.get("barbeiro_id", ""), 80)
    if not validar_data_yyyy_mm_dd(data_ref):
        data_ref = date.today().strftime("%Y-%m-%d")

    conn = get_conn()
    try:
        if barbeiro_id:
            barbeiro_valido = conn.execute(
                "SELECT id FROM barbeiros WHERE id = ? AND empresa_id = ? AND ativo = 1 LIMIT 1",
                (barbeiro_id, empresa["id"]),
            ).fetchone()
            if not barbeiro_valido:
                barbeiro_id = ""

        agendamentos = obter_agendamentos_do_dia(conn, empresa["id"], data_ref, barbeiro_id)
        resumo = gerar_resumo_agendamentos(agendamentos)
        barbeiros = listar_barbeiros(empresa["id"])
    finally:
        conn.close()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Agenda - {{ empresa.nome }}</title>
        <style>
            body { font-family: Arial; margin: 20px; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
            .status-select { padding: 4px; }
            .btn-csv { margin-bottom: 20px; padding: 8px 16px; background: #007bff; color: white; text-decoration: none; display: inline-block; }
            .resumo { margin-bottom: 20px; }
        </style>
    </head>
    <body>
        <h2>Agenda - {{ empresa.nome }}</h2>
        <div class="resumo">
            <strong>Resumo:</strong> Total: {{ resumo.total }} |
            Marcado: {{ resumo.marcado }} |
            Confirmado: {{ resumo.confirmado }} |
            Concluido: {{ resumo.concluido }} |
            Cancelado: {{ resumo.cancelado }}
        </div>
        <form method="get" action="">
            <label>Data: <input type="date" name="data" value="{{ data_ref }}"></label>
            <label>Barbeiro:
                <select name="barbeiro_id">
                    <option value="">Todos</option>
                    {% for b in barbeiros %}
                    <option value="{{ b.id }}" {% if barbeiro_selecionado == b.id %}selected{% endif %}>{{ b.nome }}</option>
                    {% endfor %}
                </select>
            </label>
            <input type="hidden" name="token" value="{{ token }}">
            <button type="submit">Filtrar</button>
        </form>
        <a class="btn-csv" href="{{ url_for('exportar_csv_interno', empresa_slug=empresa.slug, token=token, data=data_ref, barbeiro_id=barbeiro_selecionado) }}">Exportar CSV</a>
        <table>
            <thead>
                <tr><th>Horario</th><th>Cliente</th><th>Telefone</th><th>Servico</th><th>Barbeiro</th><th>Preco</th><th>Status</th><th>Acao</th></tr>
            </thead>
            <tbody>
                {% for a in agendamentos %}
                <tr>
                    <td>{{ a.hora_inicio }} - {{ a.hora_fim }}</td>
                    <td>{{ a.cliente_nome }}</td>
                    <td>{{ a.cliente_telefone }}</td>
                    <td>{{ a.servico_nome }}</td>
                    <td>{{ a.barbeiro_nome }}</td>
                    <td>R$ {{ a.preco }}</td>
                    <td>
                        <select class="status-select" data-id="{{ a.id }}">
                            <option value="marcado" {% if a.status == 'marcado' %}selected{% endif %}>Marcado</option>
                            <option value="confirmado" {% if a.status == 'confirmado' %}selected{% endif %}>Confirmado</option>
                            <option value="concluido" {% if a.status == 'concluido' %}selected{% endif %}>Concluido</option>
                            <option value="cancelado" {% if a.status == 'cancelado' %}selected{% endif %}>Cancelado</option>
                        </select>
                    </td>
                    <td><button onclick="alterarStatus('{{ a.id }}', this)">Atualizar</button></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <script>
            function alterarStatus(id, btn) {
                const select = btn.parentElement.previousElementSibling.querySelector('.status-select');
                const novoStatus = select.value;
                fetch(`/api/agendamentos/${id}/status?token={{ token }}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: novoStatus })
                })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        alert('Status atualizado para ' + novoStatus);
                        location.reload();
                    } else {
                        alert('Erro ao atualizar');
                    }
                })
                .catch(err => alert('Erro'));
            }
        </script>
    </body>
    </html>
    """
    from flask import render_template_string
    return render_template_string(
        html,
        empresa=empresa,
        token=token,
        data_ref=data_ref,
        barbeiros=barbeiros,
        barbeiro_selecionado=barbeiro_id,
        resumo=resumo,
        agendamentos=agendamentos
    )


@app.route("/api/agendamentos/<agendamento_id>/status", methods=["POST"])
def atualizar_status(agendamento_id):
    token = request.args.get("token") or request.headers.get("X-Auth-Token")
    if not token:
        abort(401, description="Token nao fornecido.")

    conn = get_conn()
    try:
        empresa = conn.execute(
            "SELECT id FROM empresas WHERE token = ? AND ativo = 1",
            (token,),
        ).fetchone()
        if not empresa:
            abort(401, description="Token invalido.")

        agendamento = conn.execute(
            "SELECT id FROM agendamentos WHERE id = ? AND empresa_id = ?",
            (agendamento_id, empresa["id"]),
        ).fetchone()
        if not agendamento:
            abort(404, description="Agendamento nao encontrado.")

        data = request.get_json(silent=True) or {}
        novo_status = limpar_texto(data.get("status", ""), 20).lower()
        if novo_status not in STATUS_VALIDOS:
            return jsonify({"error": "Status invalido."}), 400

        agora = now_iso()
        conn.execute(
            "UPDATE agendamentos SET status = ?, atualizado_em = ? WHERE id = ?",
            (novo_status, agora, agendamento_id),
        )
        conn.commit()

        return jsonify({"success": True, "status": novo_status}), 200
    finally:
        conn.close()


@app.route("/exportar-csv")
def exportar_csv():
    token = request.args.get("token", "").strip()
    empresa_slug = limpar_texto(request.args.get("empresa", ""), 80)
    data_ref = limpar_texto(request.args.get("data", ""), 10)
    barbeiro_id = limpar_texto(request.args.get("barbeiro_id", ""), 80)

    if not token:
        abort(401, description="Token nao fornecido.")
    empresa = validar_token_empresa(empresa_slug, token)
    if not empresa:
        abort(401, description="Token invalido.")
    if not empresa_slug:
        return jsonify({"message": "Parametro 'empresa' e obrigatorio."}), 400
    if data_ref and not validar_data_yyyy_mm_dd(data_ref):
        return jsonify({"message": "Data invalida."}), 400

    data_exportacao = data_ref or date.today().strftime("%Y-%m-%d")

    conn = get_conn()
    try:
        rows = obter_agendamentos_do_dia(conn, empresa["id"], data_exportacao, barbeiro_id)
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Data", "Hora Inicio", "Hora Fim", "Cliente", "Telefone", "Email",
        "Servico", "Barbeiro", "Preco", "Status", "Observacao"
    ])
    for r in rows:
        writer.writerow([
            r["data"], r["hora_inicio"], r["hora_fim"], r["cliente_nome"],
            r["cliente_telefone"], r["cliente_email"], r["servico_nome"],
            r["barbeiro_nome"], r["preco"], r["status"], r["observacao"],
        ])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    output.close()

    nome_arquivo = f"agendamentos_{empresa_slug}_{data_exportacao}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=nome_arquivo)


@app.route("/agenda/<empresa_slug>/exportar-csv")
def exportar_csv_interno(empresa_slug):
    token = request.args.get("token", "").strip()
    if not token:
        abort(401, description="Token nao fornecido.")

    empresa = validar_token_empresa(empresa_slug, token)
    if not empresa:
        abort(401, description="Token invalido.")

    data_ref = limpar_texto(request.args.get("data", ""), 10)
    barbeiro_id = limpar_texto(request.args.get("barbeiro_id", ""), 80)
    args = {
        "empresa": empresa_slug,
        "data": data_ref,
        "barbeiro_id": barbeiro_id,
        "token": token,
    }
    return redirect(url_for("exportar_csv", **args))


# ==========================================================
# ERROS
# ==========================================================
@app.errorhandler(400)
def bad_request(err):
    return jsonify({"message": getattr(err, "description", "Requisicao invalida.")}), 400


@app.errorhandler(401)
def unauthorized(err):
    return jsonify({"message": getattr(err, "description", "Nao autorizado.")}), 401


@app.errorhandler(404)
def not_found(err):
    return jsonify({"message": "Recurso nao encontrado."}), 404


@app.errorhandler(405)
def method_not_allowed(err):
    return jsonify({"message": "Metodo nao permitido."}), 405


@app.errorhandler(413)
def payload_too_large(err):
    return jsonify({"message": "Payload muito grande."}), 413


@app.errorhandler(429)
def too_many_requests(err):
    return jsonify({"message": "Muitas requisicoes. Tente novamente."}), 429


@app.errorhandler(500)
def internal_error(err):
    return jsonify({"message": "Erro interno no servidor."}), 500


# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(
        debug=debug_mode,
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
    )