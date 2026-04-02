import sqlite3

DB_PATH = "database.db"

def criar_banco():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # EMPRESA
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS empresas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        logo_url TEXT,
        token TEXT
    )
    """)

    # BARBEIROS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS barbeiros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        empresa_id INTEGER
    )
    """)

    # SERVIÇOS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS servicos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        duracao INTEGER,
        preco REAL,
        empresa_id INTEGER
    )
    """)

    # RELAÇÃO
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS barbeiro_servicos (
        barbeiro_id INTEGER,
        servico_id INTEGER
    )
    """)

    # CLIENTES
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        telefone TEXT,
        email TEXT
    )
    """)

    # AGENDAMENTOS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agendamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER,
        barbeiro_id INTEGER,
        servico_id INTEGER,
        data TEXT,
        hora_inicio TEXT,
        hora_fim TEXT,
        status TEXT,
        observacao TEXT
    )
    """)

    conn.commit()

    print("✅ Banco criado com sucesso!")

    # -----------------------
    # DADOS INICIAIS (TESTE)
    # -----------------------

    cursor.execute("SELECT * FROM empresas WHERE slug='barbearia'")
    if cursor.fetchone():
        print("⚠️ Já existe dados, pulando seed...")
        conn.close()
        return

    # EMPRESA
    cursor.execute("""
    INSERT INTO empresas (nome, slug, logo_url, token)
    VALUES (?, ?, ?, ?)
    """, (
        "Barbearia do Paulo",
        "barbearia",
        "/static/uploads/empresas/gtech.jpeg",
        "123456"
    ))

    empresa_id = cursor.lastrowid

    # BARBEIROS
    cursor.execute("INSERT INTO barbeiros (nome, empresa_id) VALUES (?, ?)", ("João", empresa_id))
    cursor.execute("INSERT INTO barbeiros (nome, empresa_id) VALUES (?, ?)", ("Carlos", empresa_id))

    # SERVIÇOS
    cursor.execute("INSERT INTO servicos (nome, duracao, preco, empresa_id) VALUES (?, ?, ?, ?)", ("Corte", 30, 25, empresa_id))
    cursor.execute("INSERT INTO servicos (nome, duracao, preco, empresa_id) VALUES (?, ?, ?, ?)", ("Barba", 20, 20, empresa_id))

    # VÍNCULO
    barbeiros = cursor.execute("SELECT id FROM barbeiros").fetchall()
    servicos = cursor.execute("SELECT id FROM servicos").fetchall()

    for b in barbeiros:
        for s in servicos:
            cursor.execute("INSERT INTO barbeiro_servicos (barbeiro_id, servico_id) VALUES (?, ?)", (b[0], s[0]))

    conn.commit()
    conn.close()

    print("🔥 Dados de teste inseridos!")
    print("👉 Empresa: barbearia")
    print("👉 Token: 123456")


if __name__ == "__main__":
    criar_banco()