from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    session,
    redirect
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from functools import wraps
from datetime import datetime
from pathlib import Path
import sqlite3
import os


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
BANCO_DADOS = BASE_DIR / "escala.db"

app = Flask(__name__)

app.secret_key = os.environ.get(
    "PLAR_SECRET_KEY",
    "ALTERE-ESTA-CHAVE-SECRETA"
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8
)


# ============================================================
# BANCO DE DADOS
# ============================================================

def conectar():
    conexao = sqlite3.connect(str(BANCO_DADOS))
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def coluna_existe(conexao, tabela, coluna):
    colunas = conexao.execute(
        f"PRAGMA table_info({tabela})"
    ).fetchall()

    return any(linha["name"] == coluna for linha in colunas)


def criar_tabelas():
    conexao = conectar()

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'funcionario',
            percentual REAL NOT NULL DEFAULT 0
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS atendimentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            local TEXT,
            data TEXT,
            horario TEXT
        )
    """)

    colunas_novas = {
        "tipo_servico": "TEXT",
        "quantidade_aparelhos": "INTEGER",
        "valor_total": "REAL",
        "valor_comissao": "REAL",
        "funcionario_id": "INTEGER",
        "status_resposta": "TEXT DEFAULT 'pendente'"
    }

    for coluna, tipo in colunas_novas.items():
        if not coluna_existe(conexao, "atendimentos", coluna):
            conexao.execute(
                f"ALTER TABLE atendimentos ADD COLUMN {coluna} {tipo}"
            )

    conexao.execute("""
        UPDATE atendimentos
        SET status_resposta = 'pendente'
        WHERE status_resposta IS NULL
        OR status_resposta = ''
    """)

    admin = conexao.execute(
        """
        SELECT id, senha
        FROM usuarios
        WHERE usuario = ?
        """,
        ("PLAR",)
    ).fetchone()

    if not admin:
        senha_hash = generate_password_hash("Edilson123")

        conexao.execute(
            """
            INSERT INTO usuarios
            (usuario, senha, tipo, percentual)
            VALUES (?, ?, ?, ?)
            """,
            ("PLAR", senha_hash, "admin", 0)
        )

        print("Administrador inicial criado.")
        print("Usuário: PLAR")
        print("Senha: Edilson123")
    else:
        tipo_admin = conexao.execute(
            """
            SELECT tipo
            FROM usuarios
            WHERE usuario = ?
            """,
            ("PLAR",)
        ).fetchone()

        if tipo_admin and tipo_admin["tipo"] != "admin":
            conexao.execute(
                """
                UPDATE usuarios
                SET tipo = 'admin'
                WHERE usuario = ?
                """,
                ("PLAR",)
            )

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_atendimentos_funcionario
        ON atendimentos(funcionario_id)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_atendimentos_data
        ON atendimentos(data)
    """)

    conexao.commit()
    conexao.close()


criar_tabelas()


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def usuario_da_sessao():
    usuario_id = session.get("usuario_id")

    if not usuario_id:
        return None

    conexao = conectar()

    usuario = conexao.execute(
        """
        SELECT id, usuario, tipo, percentual
        FROM usuarios
        WHERE id = ?
        """,
        (usuario_id,)
    ).fetchone()

    conexao.close()

    return usuario


def exigir_login(funcao):
    @wraps(funcao)
    def protegida(*args, **kwargs):
        usuario = usuario_da_sessao()

        if not usuario:
            if request.path.endswith(".html"):
                return redirect("/")

            return jsonify({
                "erro": "Não autenticado."
            }), 401

        return funcao(*args, **kwargs)

    return protegida


def exigir_admin(funcao):
    @wraps(funcao)
    def protegida(*args, **kwargs):
        usuario = usuario_da_sessao()

        if not usuario:
            if request.path.endswith(".html"):
                return redirect("/")

            return jsonify({
                "erro": "Não autenticado."
            }), 401

        if usuario["tipo"] != "admin":
            if request.path.endswith(".html"):
                return redirect("/area-funcionario.html")

            return jsonify({
                "erro": "Acesso permitido somente ao administrador."
            }), 403

        return funcao(*args, **kwargs)

    return protegida


def senha_valida(senha_salva, senha_digitada):
    """
    Aceita senhas antigas em texto puro apenas para permitir
    a migração automática para hash no primeiro login.
    """

    if not senha_salva:
        return False

    senha_salva = str(senha_salva)

    e_hash = (
        senha_salva.startswith("pbkdf2:")
        or senha_salva.startswith("scrypt:")
        or senha_salva.startswith("argon2:")
    )

    if e_hash:
        try:
            return check_password_hash(
                senha_salva,
                senha_digitada
            )
        except (ValueError, TypeError):
            return False

    return senha_salva == senha_digitada


def horario_valido(horario):
    try:
        horario_objeto = datetime.strptime(
            horario,
            "%H:%M"
        )
    except (ValueError, TypeError):
        return False

    minutos = (
        horario_objeto.hour * 60
        + horario_objeto.minute
    )

    inicio = 8 * 60
    fim = 17 * 60 + 30

    if minutos < inicio or minutos > fim:
        return False

    if horario_objeto.minute not in (0, 30):
        return False

    return True


def data_valida(data):
    try:
        datetime.strptime(data, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def calcular_valores(
    tipo_servico,
    quantidade_aparelhos,
    percentual
):
    tipo_servico = str(
        tipo_servico or ""
    ).lower()

    if tipo_servico == "limpeza":
        valor_total = 200 * quantidade_aparelhos
    else:
        extras = max(
            0,
            quantidade_aparelhos - 2
        )

        valor_total = 950 + (extras * 100)

    valor_comissao = round(
        valor_total * (percentual / 100),
        2
    )

    return valor_total, valor_comissao


# ============================================================
# LOGIN E LOGOUT
# ============================================================

@app.route("/login", methods=["POST"])
def login():
    dados = request.get_json(silent=True) or {}

    usuario_digitado = str(
        dados.get("usuario", "")
    ).strip()

    senha_digitada = str(
        dados.get("senha", "")
    )

    if not usuario_digitado or not senha_digitada:
        return jsonify({
            "sucesso": False,
            "erro": "Informe usuário e senha."
        }), 400

    conexao = conectar()

    usuario = conexao.execute(
        """
        SELECT *
        FROM usuarios
        WHERE usuario = ?
        """,
        (usuario_digitado,)
    ).fetchone()

    if not usuario:
        conexao.close()

        return jsonify({
            "sucesso": False,
            "erro": "Usuário ou senha inválidos."
        }), 401

    if not senha_valida(
        usuario["senha"],
        senha_digitada
    ):
        conexao.close()

        return jsonify({
            "sucesso": False,
            "erro": "Usuário ou senha inválidos."
        }), 401

    senha_salva = str(usuario["senha"])

    senha_em_hash = (
        senha_salva.startswith("pbkdf2:")
        or senha_salva.startswith("scrypt:")
        or senha_salva.startswith("argon2:")
    )

    if not senha_em_hash:
        nova_senha_hash = generate_password_hash(
            senha_digitada
        )

        conexao.execute(
            """
            UPDATE usuarios
            SET senha = ?
            WHERE id = ?
            """,
            (
                nova_senha_hash,
                usuario["id"]
            )
        )

        conexao.commit()

    conexao.close()

    session.clear()
    session.permanent = True

    session["usuario_id"] = usuario["id"]
    session["usuario"] = usuario["usuario"]
    session["tipo_usuario"] = usuario["tipo"]

    return jsonify({
        "sucesso": True,
        "id": usuario["id"],
        "usuario": usuario["usuario"],
        "tipo": usuario["tipo"]
    })


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()

    return jsonify({
        "sucesso": True
    })


# ============================================================
# PÁGINAS
# ============================================================

@app.route("/")
def pagina_login():
    return send_from_directory(
        str(BASE_DIR),
        "login.html"
    )


@app.route("/login.html")
def pagina_login_html():
    return send_from_directory(
        str(BASE_DIR),
        "login.html"
    )


@app.route("/teste2.html")
@exigir_admin
def pagina_admin():
    return send_from_directory(
        str(BASE_DIR),
        "teste2.html"
    )


@app.route("/area-funcionario.html")
@exigir_login
def pagina_funcionario():
    return send_from_directory(
        str(BASE_DIR),
        "area-funcionario.html"
    )


@app.route("/fundo-plar.png")
def imagem_fundo():
    return send_from_directory(
        str(BASE_DIR),
        "fundo-plar.png"
    )


@app.route("/logo-plar.png.png")
def imagem_logo():
    return send_from_directory(
        str(BASE_DIR),
        "logo-plar.png.png"
    )


@app.route("/logoplarpng.png")
def imagem_logo_fallback():
    return send_from_directory(
        str(BASE_DIR),
        "logoplarpng.png"
    )


# ============================================================
# PERFIL DO FUNCIONÁRIO
# ============================================================

@app.route("/meu-perfil", methods=["GET"])
@exigir_login
def meu_perfil():
    usuario = usuario_da_sessao()

    return jsonify({
        "id": usuario["id"],
        "usuario": usuario["usuario"],
        "tipo": usuario["tipo"]
    })


# ============================================================
# USUÁRIOS — ADMINISTRADOR
# ============================================================

@app.route("/usuarios", methods=["GET"])
@exigir_admin
def listar_usuarios():
    conexao = conectar()

    resultado = conexao.execute(
        """
        SELECT id, usuario, tipo, percentual
        FROM usuarios
        WHERE tipo = 'funcionario'
        ORDER BY usuario
        """
    ).fetchall()

    conexao.close()

    return jsonify([
        dict(linha)
        for linha in resultado
    ])


@app.route("/usuarios", methods=["POST"])
@exigir_admin
def criar_usuario():
    dados = request.get_json(silent=True) or {}

    usuario = str(
        dados.get("usuario", "")
    ).strip()

    senha = str(
        dados.get("senha", "")
    )

    try:
        percentual = float(
            dados.get("percentual", 0)
        )
    except (ValueError, TypeError):
        return jsonify({
            "erro": "Percentual inválido."
        }), 400

    if not usuario or not senha:
        return jsonify({
            "erro": "Usuário e senha são obrigatórios."
        }), 400

    if percentual < 0 or percentual > 100:
        return jsonify({
            "erro": "O percentual deve estar entre 0 e 100."
        }), 400

    senha_hash = generate_password_hash(senha)

    conexao = conectar()

    try:
        cursor = conexao.execute(
            """
            INSERT INTO usuarios
            (usuario, senha, tipo, percentual)
            VALUES (?, ?, ?, ?)
            """,
            (
                usuario,
                senha_hash,
                "funcionario",
                percentual
            )
        )

        conexao.commit()
        novo_id = cursor.lastrowid

    except sqlite3.IntegrityError:
        conexao.close()

        return jsonify({
            "erro": "Já existe um usuário com esse nome."
        }), 400

    conexao.close()

    return jsonify({
        "mensagem": "Funcionário criado.",
        "id": novo_id
    }), 201


@app.route("/usuarios/<int:id_usuario>", methods=["PUT"])
@exigir_admin
def editar_usuario(id_usuario):
    dados = request.get_json(silent=True) or {}

    try:
        percentual = float(
            dados.get("percentual", 0)
        )
    except (ValueError, TypeError):
        return jsonify({
            "erro": "Percentual inválido."
        }), 400

    if percentual < 0 or percentual > 100:
        return jsonify({
            "erro": "O percentual deve estar entre 0 e 100."
        }), 400

    conexao = conectar()

    conexao.execute(
        """
        UPDATE usuarios
        SET percentual = ?
        WHERE id = ?
        AND tipo = 'funcionario'
        """,
        (
            percentual,
            id_usuario
        )
    )

    conexao.commit()
    conexao.close()

    return jsonify({
        "mensagem": "Percentual atualizado."
    })


@app.route("/usuarios/<int:id_usuario>", methods=["DELETE"])
@exigir_admin
def excluir_usuario(id_usuario):
    conexao = conectar()

    usuario = conexao.execute(
        """
        SELECT id
        FROM usuarios
        WHERE id = ?
        AND tipo = 'funcionario'
        """,
        (id_usuario,)
    ).fetchone()

    if not usuario:
        conexao.close()

        return jsonify({
            "erro": "Funcionário não encontrado."
        }), 404

    conexao.execute(
        """
        UPDATE atendimentos
        SET funcionario_id = NULL
        WHERE funcionario_id = ?
        """,
        (id_usuario,)
    )

    conexao.execute(
        """
        DELETE FROM usuarios
        WHERE id = ?
        AND tipo = 'funcionario'
        """,
        (id_usuario,)
    )

    conexao.commit()
    conexao.close()

    return jsonify({
        "mensagem": "Funcionário excluído."
    })


# ============================================================
# ATENDIMENTOS — ADMINISTRADOR
# ============================================================

@app.route("/atendimentos", methods=["GET"])
@exigir_admin
def listar_atendimentos():
    conexao = conectar()

    resultado = conexao.execute(
        """
        SELECT *
        FROM atendimentos
        ORDER BY data, horario
        """
    ).fetchall()

    conexao.close()

    return jsonify([
        dict(linha)
        for linha in resultado
    ])


@app.route("/atendimentos", methods=["POST"])
@exigir_admin
def adicionar_atendimento():
    dados = request.get_json(silent=True) or {}

    nome = str(
        dados.get("nome", "")
    ).strip()

    local = str(
        dados.get("local", "")
    ).strip()

    data = str(
        dados.get("data", "")
    ).strip()

    horario = str(
        dados.get("horario", "")
    ).strip()

    tipo_servico = str(
        dados.get("tipo_servico", "")
    ).strip().lower()

    quantidade_bruta = dados.get(
        "quantidade_aparelhos",
        ""
    )

    if not nome or not local or not data or not horario:
        return jsonify({
            "erro": (
                "Preencha funcionário, local, data e horário."
            )
        }), 400

    if not tipo_servico:
        return jsonify({
            "erro": "Informe o tipo de serviço."
        }), 400

    if not data_valida(data):
        return jsonify({
            "erro": "Data inválida."
        }), 400

    if not horario_valido(horario):
        return jsonify({
            "erro": (
                "O horário deve estar entre 08:00 e 17:30, "
                "em intervalos de 30 minutos."
            )
        }), 400

    try:
        quantidade_aparelhos = int(
            quantidade_bruta
        )
    except (ValueError, TypeError):
        return jsonify({
            "erro": "Quantidade de aparelhos inválida."
        }), 400

    if quantidade_aparelhos <= 0:
        return jsonify({
            "erro": (
                "A quantidade deve ser maior que zero."
            )
        }), 400

    conexao = conectar()

    funcionario = conexao.execute(
        """
        SELECT id, usuario, percentual
        FROM usuarios
        WHERE usuario = ?
        AND tipo = 'funcionario'
        """,
        (nome,)
    ).fetchone()

    if not funcionario:
        conexao.close()

        return jsonify({
            "erro": (
                "Funcionário não encontrado. "
                "Use exatamente o usuário cadastrado."
            )
        }), 400

    conflito = conexao.execute(
        """
        SELECT id
        FROM atendimentos
        WHERE funcionario_id = ?
        AND data = ?
        AND horario = ?
        AND COALESCE(status_resposta, 'pendente') != 'recusado'
        """,
        (
            funcionario["id"],
            data,
            horario
        )
    ).fetchone()

    if conflito:
        conexao.close()

        return jsonify({
            "erro": (
                "Esse funcionário já possui um atendimento "
                "nesse dia e horário."
            )
        }), 400

    valor_total, valor_comissao = calcular_valores(
        tipo_servico,
        quantidade_aparelhos,
        funcionario["percentual"]
    )

    conexao.execute(
        """
        INSERT INTO atendimentos
        (
            nome,
            local,
            data,
            horario,
            tipo_servico,
            quantidade_aparelhos,
            valor_total,
            valor_comissao,
            funcionario_id,
            status_resposta
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            funcionario["usuario"],
            local,
            data,
            horario,
            tipo_servico,
            quantidade_aparelhos,
            valor_total,
            valor_comissao,
            funcionario["id"],
            "pendente"
        )
    )

    conexao.commit()
    conexao.close()

    return jsonify({
        "mensagem": "Atendimento criado.",
        "valor_total": valor_total,
        "valor_comissao": valor_comissao
    }), 201


@app.route(
    "/atendimentos/<int:id_atendimento>",
    methods=["DELETE"]
)
@exigir_admin
def excluir_atendimento(id_atendimento):
    conexao = conectar()

    atendimento = conexao.execute(
        """
        SELECT id
        FROM atendimentos
        WHERE id = ?
        """,
        (id_atendimento,)
    ).fetchone()

    if not atendimento:
        conexao.close()

        return jsonify({
            "erro": "Atendimento não encontrado."
        }), 404

    conexao.execute(
        """
        DELETE FROM atendimentos
        WHERE id = ?
        """,
        (id_atendimento,)
    )

    conexao.commit()
    conexao.close()

    return jsonify({
        "mensagem": "Atendimento excluído."
    })


@app.route(
    "/atendimentos/<int:id_atendimento>/concluir",
    methods=["PUT"]
)
@exigir_admin
def concluir_atendimento(id_atendimento):
    conexao = conectar()

    atendimento = conexao.execute(
        """
        SELECT id, status_resposta
        FROM atendimentos
        WHERE id = ?
        """,
        (id_atendimento,)
    ).fetchone()

    if not atendimento:
        conexao.close()

        return jsonify({
            "erro": "Atendimento não encontrado."
        }), 404

    status_atual = (
        atendimento["status_resposta"]
        or "pendente"
    )

    if status_atual != "aceito":
        conexao.close()

        return jsonify({
            "erro": (
                "Somente atendimentos aceitos "
                "podem ser concluídos."
            )
        }), 400

    conexao.execute(
        """
        UPDATE atendimentos
        SET status_resposta = 'concluido'
        WHERE id = ?
        """,
        (id_atendimento,)
    )

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "mensagem": "Atendimento concluído."
    })


# ============================================================
# ÁREA DO FUNCIONÁRIO
# ============================================================

@app.route("/me/agendamentos", methods=["GET"])
@exigir_login
def meus_agendamentos():
    usuario = usuario_da_sessao()

    data = request.args.get(
        "data",
        ""
    ).strip()

    conexao = conectar()

    consulta = """
        SELECT
            id,
            nome,
            local,
            data,
            horario,
            tipo_servico,
            quantidade_aparelhos,
            valor_comissao,
            status_resposta
        FROM atendimentos
        WHERE funcionario_id = ?
    """

    parametros = [
        usuario["id"]
    ]

    if data:
        if not data_valida(data):
            conexao.close()

            return jsonify({
                "erro": "Data inválida."
            }), 400

        consulta += " AND data = ?"
        parametros.append(data)

    consulta += """
        ORDER BY data, horario
    """

    resultado = conexao.execute(
        consulta,
        parametros
    ).fetchall()

    conexao.close()

    return jsonify([
        dict(linha)
        for linha in resultado
    ])


@app.route(
    "/me/agendamentos/<int:id_atendimento>/resposta",
    methods=["PUT"]
)
@exigir_login
def responder_agendamento(id_atendimento):
    usuario = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    resposta = str(
        dados.get("resposta", "")
    ).strip().lower()

    if resposta not in (
        "aceito",
        "recusado"
    ):
        return jsonify({
            "erro": "Resposta inválida."
        }), 400

    conexao = conectar()

    atendimento = conexao.execute(
        """
        SELECT id, status_resposta
        FROM atendimentos
        WHERE id = ?
        AND funcionario_id = ?
        """,
        (
            id_atendimento,
            usuario["id"]
        )
    ).fetchone()

    if not atendimento:
        conexao.close()

        return jsonify({
            "erro": "Atendimento não encontrado."
        }), 404

    status_atual = (
        atendimento["status_resposta"]
        or "pendente"
    )

    if status_atual != "pendente":
        conexao.close()

        return jsonify({
            "erro": (
                "Este atendimento já recebeu "
                "uma resposta."
            )
        }), 400

    conexao.execute(
        """
        UPDATE atendimentos
        SET status_resposta = ?
        WHERE id = ?
        AND funcionario_id = ?
        """,
        (
            resposta,
            id_atendimento,
            usuario["id"]
        )
    )

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "status_resposta": resposta
    })


# ============================================================
# STATUS DO SERVIDOR
# ============================================================

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "online": True,
        "aplicacao": "PLAR ERP"
    })


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
