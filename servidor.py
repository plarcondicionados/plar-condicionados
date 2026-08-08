from flask import Flask, request, jsonify
import sqlite3

app = Flask(__name__)

def conectar():
    conexao = sqlite3.connect("escala.db")
    conexao.row_factory = sqlite3.Row
    return conexao

def criar_tabela():
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
    conexao.commit()
    conexao.close()

criar_tabela()

@app.route("/atendimentos", methods=["GET"])
def listar_atendimentos():
    conexao = conectar()
    resultado = conexao.execute("SELECT * FROM atendimentos").fetchall()
    conexao.close()
    return jsonify([dict(linha) for linha in resultado])

@app.route("/atendimentos", methods=["POST"])
def adicionar_atendimento():
    dados = request.get_json()
    conexao = conectar()
    conexao.execute(
        "INSERT INTO atendimentos (nome, local, data, horario) VALUES (?, ?, ?, ?)",
        (dados["nome"], dados["local"], dados["data"], dados["horario"])
    )
    conexao.commit()
    conexao.close()
    return jsonify({"mensagem": "salvo"})

if __name__ == "__main__":
    app.run(debug=True)