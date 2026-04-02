#!/usr/bin/env python3

import sqlite3
import uuid
import secrets
import re
from datetime import datetime

DB_PATH = "database.db"


def slugify(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = re.sub(r"[^a-z0-9\s-]", "", texto)
    texto = re.sub(r"[\s_]+", "-", texto)
    texto = re.sub(r"-+", "-", texto)
    return texto.strip("-")


def gerar_token_empresa() -> str:
    return secrets.token_urlsafe(32)


def cadastrar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    print("\n=== CADASTRO DE NOVA BARBEARIA ===\n")

    nome = input("Nome da barbearia: ").strip()
    slug_digitado = input("Slug (usado na URL, ex: barbearia-ze): ").strip()
    telefone = input("Telefone: ").strip()
    email = input("E-mail: ").strip()
    endereco = input("Endereço: ").strip()

    if not nome:
      print("❌ Nome é obrigatório.")
      conn.close()
      return

    slug = slugify(slug_digitado if slug_digitado else nome)
    if not slug:
        print("❌ Slug inválido.")
        conn.close()
        return

    existe = conn.execute("SELECT id FROM empresas WHERE slug = ? LIMIT 1", (slug,)).fetchone()
    if existe:
        print(f"❌ Já existe uma barbearia com o slug '{slug}'.")
        conn.close()
        return

    empresa_id = str(uuid.uuid4())
    token = gerar_token_empresa()
    agora = datetime.now().isoformat(timespec="seconds")

    # Insere empresa com token exclusivo
    conn.execute("""
        INSERT INTO empresas (
            id, nome, slug, telefone, email, endereco, logo_url,
            ativo, criado_em, atualizado_em, token
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        empresa_id,
        nome,
        slug,
        telefone,
        email,
        endereco,
        None,
        1,
        agora,
        agora,
        token
    ))

    # Configurações padrão
    conn.execute("""
        INSERT INTO configuracoes_empresa (
            empresa_id, hora_abertura, hora_fechamento, intervalo_min,
            antecedencia_max_dias, permite_encaixe, envia_whatsapp,
            criado_em, atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        empresa_id,
        "08:00",
        "20:00",
        30,
        30,
        0,
        1,
        agora,
        agora
    ))

    # Barbeiros
    while True:
        try:
            qtd_barbeiros = int(input("\nQuantos barbeiros? ").strip())
            if qtd_barbeiros <= 0:
                print("Digite um número maior que zero.")
                continue
            break
        except ValueError:
            print("Digite um número válido.")

    barbeiros_ids = []
    for i in range(qtd_barbeiros):
        nome_b = input(f"Nome do barbeiro {i+1}: ").strip()
        whats = input(f"WhatsApp {i+1} (opcional): ").strip()

        if not nome_b:
            print("❌ Nome do barbeiro é obrigatório.")
            conn.rollback()
            conn.close()
            return

        barbeiro_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO barbeiros (
                id, empresa_id, nome, whatsapp, email, foto_url, bio,
                ativo, criado_em, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            barbeiro_id,
            empresa_id,
            nome_b,
            whats or None,
            None,
            None,
            None,
            1,
            agora,
            agora
        ))
        barbeiros_ids.append(barbeiro_id)

    # Serviços
    while True:
        try:
            qtd_servicos = int(input("\nQuantos serviços? ").strip())
            if qtd_servicos <= 0:
                print("Digite um número maior que zero.")
                continue
            break
        except ValueError:
            print("Digite um número válido.")

    servicos_ids = []
    for i in range(qtd_servicos):
        nome_s = input(f"Nome do serviço {i+1}: ").strip()
        if not nome_s:
            print("❌ Nome do serviço é obrigatório.")
            conn.rollback()
            conn.close()
            return

        try:
            preco = float(input(f"Preço {i+1} (ex: 35.00): ").strip())
            duracao = int(input(f"Duração em minutos {i+1}: ").strip())
        except ValueError:
            print("❌ Preço ou duração inválidos.")
            conn.rollback()
            conn.close()
            return

        emoji = input(f"Emoji {i+1} (opcional, ex: ✂️): ").strip()

        cur = conn.execute("""
            INSERT INTO servicos (
                empresa_id, nome, descricao, preco, duracao_min, emoji,
                ativo, criado_em, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, (
            empresa_id,
            nome_s,
            None,
            preco,
            duracao,
            emoji or None,
            1,
            agora,
            agora
        ))
        servico_id = cur.fetchone()["id"]
        servicos_ids.append(servico_id)

    # Vincula todos os barbeiros a todos os serviços
    for b_id in barbeiros_ids:
        for s_id in servicos_ids:
            conn.execute("""
                INSERT OR IGNORE INTO barbeiro_servicos (barbeiro_id, servico_id, criado_em)
                VALUES (?, ?, ?)
            """, (b_id, s_id, agora))

    conn.commit()
    conn.close()

    print(f"\n✅ Barbearia '{nome}' cadastrada com sucesso!")
    print(f"🔗 Slug final: {slug}")
    print(f"🔐 Token interno: {token}")
    print(f"🌐 Link público: http://127.0.0.1:5000/agendar/{slug}")
    print(f"📋 Link interno do dono: http://127.0.0.1:5000/agenda/{slug}?token={token}\n")


if __name__ == "__main__":
    cadastrar()