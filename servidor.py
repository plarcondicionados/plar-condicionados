from flask import Flask, request, jsonify, send_from_directory
import sqlite3

app = Flask(__name__)

def conectar():
    conexao = sqlite3.connect("escala.db")
    conexao.row_factory = sqlite3.Row
    return conexao

def criar_tabelas():
    conexao = conectar()
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS atendimentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            local TEXT,
            data TEXT,
            horario TEXT
        )
    """)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE,
            senha TEXT,
            tipo TEXT,
            percentual REAL
        )
    """)

    colunas_existentes = [linha["name"] for linha in conexao.execute("PRAGMA table_info(atendimentos)")]
    colunas_novas = {
        "tipo_servico": "TEXT",
        "quantidade_aparelhos": "INTEGER",
        "valor_total": "REAL",
        "valor_comissao": "REAL"
    }
    for coluna, tipo in colunas_novas.items():
        if coluna not in colunas_existentes:
            conexao.execute(f"ALTER TABLE atendimentos ADD COLUMN {coluna} {tipo}")

    admin_existe = conexao.execute("SELECT * FROM usuarios WHERE usuario = ?", ("PLAR",)).fetchone()
    if not admin_existe:
        conexao.execute(
            "INSERT INTO usuarios (usuario, senha, tipo, percentual) VALUES (?, ?, ?, ?)",
            ("PLAR", "Edilson123", "admin", 0)
        )
    conexao.commit()
    conexao.close()

criar_tabelas()

def calcular_valores(tipo_servico, quantidade_aparelhos, percentual):
    if tipo_servico == "limpeza":
        valor_total = 200 * quantidade_aparelhos
    else:
        extras = max(0, quantidade_aparelhos - 2)
        valor_total = 950 + (extras * 100)
    valor_comissao = round(valor_total * (percentual / 100), 2)
    return valor_total, valor_comissao

@app.route("/login", methods=["POST"])
def login():
    dados = request.get_json()
    usuario = dados.get("usuario", "")
    senha = dados.get("senha", "")
    conexao = conectar()
    resultado = conexao.execute(
        "SELECT * FROM usuarios WHERE usuario = ? AND senha = ?",
        (usuario, senha)
    ).fetchone()
    conexao.close()
    if resultado:
        return jsonify({
            "sucesso": True,
            "tipo": resultado["tipo"],
            "id": resultado["id"],
            "usuario": resultado["usuario"]
        })
    return jsonify({"sucesso": False})

@app.route("/usuarios", methods=["GET"])
def listar_usuarios():
    conexao = conectar()
    resultado = conexao.execute(
        "SELECT id, usuario, tipo, percentual FROM usuarios WHERE tipo = 'funcionario'"
    ).fetchall()
    conexao.close()
    return jsonify([dict(linha) for linha in resultado])

@app.route("/usuarios", methods=["POST"])
def criar_usuario():
    dados = request.get_json()
    conexao = conectar()
    try:
        conexao.execute(
            "INSERT INTO usuarios (usuario, senha, tipo, percentual) VALUES (?, ?, ?, ?)",
            (dados["usuario"], dados["senha"], "funcionario", dados["percentual"])
        )
        conexao.commit()
        conexao.close()
        return jsonify({"mensagem": "criado"})
    except sqlite3.IntegrityError:
        conexao.close()
        return jsonify({"erro": "Já existe um usuário com esse nome."}), 400

@app.route("/usuarios/<int:id_usuario>", methods=["PUT"])
def editar_usuario(id_usuario):
    dados = request.get_json()
    conexao = conectar()
    conexao.execute(
        "UPDATE usuarios SET percentual = ? WHERE id = ?",
        (dados["percentual"], id_usuario)
    )
    conexao.commit()
    conexao.close()
    return jsonify({"mensagem": "atualizado"})

@app.route("/usuarios/<int:id_usuario>", methods=["DELETE"])
def excluir_usuario(id_usuario):
    conexao = conectar()
    conexao.execute("DELETE FROM usuarios WHERE id = ?", (id_usuario,))
    conexao.commit()
    conexao.close()
    return jsonify({"mensagem": "excluido"})

@app.route("/atendimentos", methods=["GET"])
def listar_atendimentos():
    conexao = conectar()
    resultado = conexao.execute("SELECT * FROM atendimentos").fetchall()
    conexao.close()
    return jsonify([dict(linha) for linha in resultado])

@app.route("/atendimentos", methods=["POST"])
def adicionar_atendimento():
    dados = request.get_json()
    nome = dados.get("nome", "")
    local = dados.get("local", "")
    data = dados.get("data", "")
    horario = dados.get("horario", "")
    tipo_servico = dados.get("tipo_servico", "")
    quantidade_aparelhos_bruto = dados.get("quantidade_aparelhos", "")

    if not nome or not local or not data or not horario or not tipo_servico or not quantidade_aparelhos_bruto:
        return jsonify({"erro": "Preencha todos os campos antes de adicionar."}), 400

    try:
        quantidade_aparelhos = int(quantidade_aparelhos_bruto)
    except (ValueError, TypeError):
        return jsonify({"erro": "Quantidade de aparelhos inválida."}), 400

    conexao = conectar()
    funcionario = conexao.execute(
        "SELECT percentual FROM usuarios WHERE usuario = ?", (nome,)
    ).fetchone()
    percentual = funcionario["percentual"] if funcionario else 0

    valor_total, valor_comissao = calcular_valores(tipo_servico, quantidade_aparelhos, percentual)

    conexao.execute("""
        INSERT INTO atendimentos
        (nome, local, data, horario, tipo_servico, quantidade_aparelhos, valor_total, valor_comissao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (nome, local, data, horario, tipo_servico, quantidade_aparelhos, valor_total, valor_comissao))
    conexao.commit()
    conexao.close()
    return jsonify({"mensagem": "salvo"})

@app.route("/atendimentos/<int:id_atendimento>", methods=["DELETE"])
def excluir_atendimento(id_atendimento):
    conexao = conectar()
    conexao.execute("DELETE FROM atendimentos WHERE id = ?", (id_atendimento,))
    conexao.commit()
    conexao.close()
    return jsonify({"mensagem": "excluido"})

@app.route("/")
def pagina_login():
    return send_from_directory(".", "login.html")

@app.route("/teste2.html")
def pagina_escala():
    return send_from_directory(".", "teste2.html")

@app.route("/<path:nome_arquivo>")
def servir_arquivo(nome_arquivo):
    return send_from_directory(".", nome_arquivo)

if __name__ == "__main__":
    app.run(debug=True)
