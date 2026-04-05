#!/usr/bin/env python3

import os
import re
import uuid
import secrets
import psycopg2
from psycopg2 import sql
from datetime import datetime

# ==================== CONEXÃO POSTGRESQL ====================
def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise Exception("❌ Variável de ambiente DATABASE_URL não definida.")
    # Railway usa 'postgres://', psycopg2 exige 'postgresql://'
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(database_url)

# ==================== UTILITÁRIOS ====================
def slugify(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = re.sub(r"[^a-z0-9\s-]", "", texto)
    texto = re.sub(r"[\s_]+", "-", texto)
    texto = re.sub(r"-+", "-", texto)
    return texto.strip("-")

def gerar_token_empresa() -> str:
    return secrets.token_urlsafe(32)

# ==================== CADASTRO PRINCIPAL ====================
def cadastrar():
    conn = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cur = conn.cursor()

        print("\n=== CADASTRO DE NOVA BARBEARIA (POSTGRESQL) ===\n")

        nome = input("Nome da barbearia: ").strip()
        slug_digitado = input("Slug (usado na URL, ex: barbearia-ze): ").strip()
        telefone = input("Telefone: ").strip()
        email = input("E-mail: ").strip()
        endereco = input("Endereço: ").strip()

        if not nome:
            print("❌ Nome é obrigatório.")
            return

        slug = slugify(slug_digitado if slug_digitado else nome)
        if not slug:
            print("❌ Slug inválido.")
            return

        # Verifica slug duplicado
        cur.execute("SELECT id FROM empresas WHERE slug = %s LIMIT 1", (slug,))
        if cur.fetchone():
            print(f"❌ Já existe uma barbearia com o slug '{slug}'.")
            return

        empresa_id = str(uuid.uuid4())
        token = gerar_token_empresa()
        agora = datetime.now().isoformat(timespec="seconds")

        # 1. Inserir empresa
        cur.execute("""
            INSERT INTO empresas (
                id, nome, slug, telefone, email, endereco, logo_url,
                ativo, criado_em, atualizado_em, token
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            empresa_id, nome, slug, telefone, email, endereco, None,
            1, agora, agora, token
        ))

        # 2. Configurações padrão
        cur.execute("""
            INSERT INTO configuracoes_empresa (
                empresa_id, hora_abertura, hora_fechamento, intervalo_min,
                antecedencia_max_dias, permite_encaixe, envia_whatsapp,
                criado_em, atualizado_em
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            empresa_id, "08:00", "20:00", 30, 30, 0, 1, agora, agora
        ))

        # 3. Barbeiros
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
                return

            barbeiro_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO barbeiros (
                    id, empresa_id, nome, whatsapp, email, foto_url, bio,
                    ativo, criado_em, atualizado_em
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                barbeiro_id, empresa_id, nome_b, whats or None, None, None, None,
                1, agora, agora
            ))
            barbeiros_ids.append(barbeiro_id)

        # 4. Serviços
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
                return

            try:
                preco = float(input(f"Preço {i+1} (ex: 35.00): ").strip())
                duracao = int(input(f"Duração em minutos {i+1}: ").strip())
            except ValueError:
                print("❌ Preço ou duração inválidos.")
                return

            emoji = input(f"Emoji {i+1} (opcional, ex: ✂️): ").strip()

            cur.execute("""
                INSERT INTO servicos (
                    empresa_id, nome, descricao, preco, duracao_min, emoji,
                    ativo, criado_em, atualizado_em
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                empresa_id, nome_s, None, preco, duracao, emoji or None,
                1, agora, agora
            ))
            servico_id = cur.fetchone()[0]
            servicos_ids.append(servico_id)

        # 5. Vincular todos os barbeiros a todos os serviços
        for b_id in barbeiros_ids:
            for s_id in servicos_ids:
                cur.execute("""
                    INSERT INTO barbeiro_servicos (barbeiro_id, servico_id, criado_em)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (b_id, s_id, agora))

        # Commit final
        conn.commit()

        # Links
        base_url = input("\n🌐 Base URL da aplicação (ex: https://seudominio.com ou deixe em branco para local): ").strip()
        if not base_url:
            base_url = "http://127.0.0.1:5000"

        print(f"\n✅ Barbearia '{nome}' cadastrada com sucesso!")
        print(f"🔗 Slug final: {slug}")
        print(f"🔐 Token interno: {token}")
        print(f"🌐 Link público: {base_url}/agendar/{slug}")
        print(f"📋 Link interno do dono: {base_url}/agenda/{slug}?token={token}\n")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Erro durante o cadastro: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    cadastrar()