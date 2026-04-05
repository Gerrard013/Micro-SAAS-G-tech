import os
import sys
import psycopg2

# ==================== CONEXÃO POSTGRESQL ====================
def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise Exception("❌ Variável de ambiente DATABASE_URL não definida.")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(database_url)

# ==================== ATUALIZAÇÃO DE FOTO ====================
def atualizar_foto():
    conn = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cur = conn.cursor()

        slug_empresa = input("Slug da barbearia (ex: barbearia): ").strip()
        nome_barbeiro = input("Nome do barbeiro: ").strip()
        nome_arquivo = input("Nome do arquivo da foto (ex: joao.jpeg): ").strip()

        if not slug_empresa or not nome_barbeiro or not nome_arquivo:
            print("❌ Todos os campos são obrigatórios.")
            return

        # Valida extensão
        extensoes_permitidas = (".jpg", ".jpeg", ".png", ".webp")
        if not nome_arquivo.lower().endswith(extensoes_permitidas):
            print("❌ Arquivo inválido. Use jpg, jpeg, png ou webp.")
            return

        # Verifica existência física do arquivo
        caminho_fisico = os.path.join("static", "uploads", "barbeiros", nome_arquivo)
        if not os.path.exists(caminho_fisico):
            print(f"❌ Arquivo não encontrado em: {caminho_fisico}")
            return

        caminho_banco = f"/static/uploads/barbeiros/{nome_arquivo}"

        # Busca barbeiro (case insensitive)
        cur.execute("""
            SELECT b.id, b.nome
            FROM barbeiros b
            JOIN empresas e ON b.empresa_id = e.id
            WHERE LOWER(TRIM(e.slug)) = LOWER(TRIM(%s))
              AND LOWER(TRIM(b.nome)) = LOWER(TRIM(%s))
        """, (slug_empresa, nome_barbeiro))
        barbeiro = cur.fetchone()

        if not barbeiro:
            print("❌ Barbeiro não encontrado para este slug e nome.")
            return

        # Atualiza foto_url
        cur.execute("""
            UPDATE barbeiros
            SET foto_url = %s
            WHERE id = %s
        """, (caminho_banco, barbeiro[0]))

        conn.commit()
        print("\n✅ Foto atualizada com sucesso!")
        print(f"Barbeiro: {barbeiro[1]}")
        print(f"Caminho salvo no banco: {caminho_banco}")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Erro ao atualizar foto: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    atualizar_foto()