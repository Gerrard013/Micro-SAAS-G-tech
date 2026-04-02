import sqlite3
import os

DB_PATH = "database.db"


def atualizar_foto():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    slug_empresa = input("Slug da barbearia (ex: barbearia): ").strip()
    nome_barbeiro = input("Nome do barbeiro: ").strip()
    nome_arquivo = input("Nome do arquivo da foto (ex: joao.jpeg): ").strip()

    if not slug_empresa:
        print("Slug da barbearia não informado.")
        conn.close()
        return

    if not nome_barbeiro:
        print("Nome do barbeiro não informado.")
        conn.close()
        return

    if not nome_arquivo:
        print("Nome do arquivo não informado.")
        conn.close()
        return

    extensoes_permitidas = (".jpg", ".jpeg", ".png", ".webp")
    if not nome_arquivo.lower().endswith(extensoes_permitidas):
        print("Arquivo inválido. Use jpg, jpeg, png ou webp.")
        conn.close()
        return

    caminho_fisico = os.path.join("static", "uploads", "barbeiros", nome_arquivo)
    if not os.path.exists(caminho_fisico):
        print(f"Arquivo não encontrado em: {caminho_fisico}")
        conn.close()
        return

    caminho_banco = f"/static/uploads/barbeiros/{nome_arquivo}"

    cursor.execute("""
        SELECT b.id, b.nome
        FROM barbeiros b
        JOIN empresas e ON b.empresa_id = e.id
        WHERE LOWER(TRIM(e.slug)) = LOWER(TRIM(?))
          AND LOWER(TRIM(b.nome)) = LOWER(TRIM(?))
    """, (slug_empresa, nome_barbeiro))
    barbeiro = cursor.fetchone()

    if not barbeiro:
        print("Barbeiro não encontrado.")
        conn.close()
        return

    cursor.execute("""
        UPDATE barbeiros
        SET foto_url = ?
        WHERE id = ?
    """, (caminho_banco, barbeiro["id"]))

    conn.commit()
    conn.close()

    print("Foto atualizada com sucesso!")
    print(f"Barbeiro: {barbeiro['nome']}")
    print(f"Caminho salvo no banco: {caminho_banco}")


if __name__ == "__main__":
    atualizar_foto()