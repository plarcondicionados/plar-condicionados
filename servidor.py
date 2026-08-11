from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    send_file,
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
from io import BytesIO, StringIO
import sqlite3
import csv
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
# CONSTANTES
# ============================================================

STATUS_VALIDOS = {
    "pendente",
    "aceito",
    "recusado",
    "em_andamento",
    "concluido",
    "cancelado"
}

TRANSICOES = {
    "pendente": {
        "aceito",
        "recusado",
        "cancelado"
    },
    "aceito": {
        "em_andamento",
        "concluido",
        "cancelado"
    },
    "em_andamento": {
        "concluido",
        "cancelado"
    },
    "recusado": set(),
    "concluido": set(),
    "cancelado": set()
}


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

    return any(
        coluna_atual["name"] == coluna
        for coluna_atual in colunas
    )


def adicionar_coluna(conexao, tabela, coluna, tipo):
    if not coluna_existe(conexao, tabela, coluna):
        conexao.execute(
            f"""
            ALTER TABLE {tabela}
            ADD COLUMN {coluna} {tipo}
            """
        )


def criar_tabelas():
    conexao = conectar()

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'funcionario',
            percentual REAL NOT NULL DEFAULT 0,
            criado_em TEXT
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

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER,
            acao TEXT NOT NULL,
            tabela TEXT NOT NULL,
            registro_id INTEGER,
            detalhes TEXT,
            criado_em TEXT NOT NULL
        )
    """)

    colunas_atendimento = {
        "cliente": "TEXT",
        "tipo_servico": "TEXT",
        "quantidade_aparelhos": "INTEGER DEFAULT 0",
        "valor_total": "REAL DEFAULT 0",
        "custo": "REAL DEFAULT 0",
        "valor_comissao": "REAL DEFAULT 0",
        "funcionario_id": "INTEGER",
        "status_resposta": "TEXT DEFAULT 'pendente'",
        "criado_em": "TEXT",
        "atualizado_em": "TEXT",
        "concluido_em": "TEXT"
    }

    for coluna, tipo in colunas_atendimento.items():
        adicionar_coluna(
            conexao,
            "atendimentos",
            coluna,
            tipo
        )

    agora = datetime.now().isoformat(timespec="seconds")

    conexao.execute(
        """
        UPDATE atendimentos
        SET status_resposta = 'pendente'
        WHERE status_resposta IS NULL
        OR status_resposta = ''
        """
    )

    conexao.execute(
        """
        UPDATE atendimentos
        SET cliente = COALESCE(cliente, local, 'Não informado')
        WHERE cliente IS NULL
        """
    )

    conexao.execute(
        """
        UPDATE atendimentos
        SET criado_em = ?
        WHERE criado_em IS NULL
        """,
        (agora,)
    )

    conexao.execute(
        """
        UPDATE atendimentos
        SET atualizado_em = criado_em
        WHERE atualizado_em IS NULL
        """
    )

    conexao.execute(
        """
        UPDATE atendimentos
        SET custo = 0
        WHERE custo IS NULL
        """
    )

    admin = conexao.execute(
        """
        SELECT id
        FROM usuarios
        WHERE usuario = ?
        """,
        ("PLAR",)
    ).fetchone()

    if not admin:
        conexao.execute(
            """
            INSERT INTO usuarios
            (
                usuario,
                senha,
                tipo,
                percentual,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "PLAR",
                generate_password_hash("Edilson123"),
                "admin",
                0,
                agora
            )
        )

        print("Administrador inicial criado.")
        print("Usuário: PLAR")
        print("Senha: Edilson123")
    else:
        conexao.execute(
            """
            UPDATE usuarios
            SET tipo = 'admin'
            WHERE usuario = 'PLAR'
            """
        )

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_atendimentos_funcionario
        ON atendimentos(funcionario_id)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_atendimentos_data
        ON atendimentos(data)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS idx_atendimentos_status
        ON atendimentos(status_resposta)
    """)

    conexao.commit()
    conexao.close()


criar_tabelas()


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def agora():
    return datetime.now().isoformat(timespec="seconds")


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
    if not senha_salva:
        return False

    senha_salva = str(senha_salva)

    hash_valido = (
        senha_salva.startswith("pbkdf2:")
        or senha_salva.startswith("scrypt:")
        or senha_salva.startswith("argon2:")
    )

    if hash_valido:
        try:
            return check_password_hash(
                senha_salva,
                senha_digitada
            )
        except (ValueError, TypeError):
            return False

    return senha_salva == senha_digitada


def data_valida(data):
    try:
        datetime.strptime(data, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


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

    return horario_objeto.minute in (0, 30)


def numero(dado, padrao=0):
    try:
        return float(dado)
    except (ValueError, TypeError):
        return padrao


def inteiro(dado, padrao=0):
    try:
        return int(dado)
    except (ValueError, TypeError):
        return padrao


def calcular_valores(
    tipo_servico,
    quantidade_aparelhos,
    percentual
):
    tipo_servico = str(
        tipo_servico or ""
    ).strip().lower()

    quantidade_aparelhos = max(
        1,
        int(quantidade_aparelhos)
    )

    if tipo_servico == "limpeza":
        valor_total = 200 * quantidade_aparelhos
    else:
        extras = max(
            0,
            quantidade_aparelhos - 2
        )

        valor_total = 950 + (extras * 100)

    valor_comissao = round(
        valor_total * float(percentual) / 100,
        2
    )

    return round(valor_total, 2), valor_comissao


def transicao_permitida(status_atual, novo_status):
    return novo_status in TRANSICOES.get(
        status_atual,
        set()
    )


def registrar_auditoria(
    usuario_id,
    acao,
    tabela,
    registro_id=None,
    detalhes=""
):
    conexao = conectar()

    conexao.execute(
        """
        INSERT INTO auditoria
        (
            usuario_id,
            acao,
            tabela,
            registro_id,
            detalhes,
            criado_em
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            usuario_id,
            acao,
            tabela,
            registro_id,
            detalhes,
            agora()
        )
    )

    conexao.commit()
    conexao.close()


def dados_atendimento(linha):
    dados = dict(linha)

    dados["status"] = (
        dados.get("status_resposta")
        or "pendente"
    )

    dados["lucro_estimado"] = round(
        numero(dados.get("valor_total"))
        - numero(dados.get("custo"))
        - numero(dados.get("valor_comissao")),
        2
    )

    return dados


# ============================================================
# LOGIN
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

    hash_valido = (
        senha_salva.startswith("pbkdf2:")
        or senha_salva.startswith("scrypt:")
        or senha_salva.startswith("argon2:")
    )

    if not hash_valido:
        conexao.execute(
            """
            UPDATE usuarios
            SET senha = ?
            WHERE id = ?
            """,
            (
                generate_password_hash(senha_digitada),
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
# PERFIL
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
# FUNCIONÁRIOS
# ============================================================

@app.route("/usuarios", methods=["GET"])
@exigir_admin
def listar_usuarios():
    conexao = conectar()

    usuarios = conexao.execute(
        """
        SELECT id, usuario, tipo, percentual, criado_em
        FROM usuarios
        WHERE tipo = 'funcionario'
        ORDER BY usuario
        """
    ).fetchall()

    conexao.close()

    return jsonify([
        dict(usuario)
        for usuario in usuarios
    ])


@app.route("/usuarios", methods=["POST"])
@exigir_admin
def criar_usuario():
    administrador = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    usuario = str(
        dados.get("usuario", "")
    ).strip()

    senha = str(
        dados.get("senha", "")
    )

    percentual = numero(
        dados.get("percentual"),
        -1
    )

    if not usuario or not senha:
        return jsonify({
            "erro": "Usuário e senha são obrigatórios."
        }), 400

    if percentual < 0 or percentual > 100:
        return jsonify({
            "erro": "O percentual deve estar entre 0 e 100."
        }), 400

    conexao = conectar()

    try:
        cursor = conexao.execute(
            """
            INSERT INTO usuarios
            (
                usuario,
                senha,
                tipo,
                percentual,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                usuario,
                generate_password_hash(senha),
                "funcionario",
                percentual,
                agora()
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

    registrar_auditoria(
        administrador["id"],
        "FUNCIONARIO_CRIADO",
        "usuarios",
        novo_id,
        f"Usuário criado: {usuario}"
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Funcionário criado.",
        "id": novo_id
    }), 201


@app.route("/usuarios/<int:id_usuario>", methods=["PUT"])
@exigir_admin
def editar_usuario(id_usuario):
    administrador = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    percentual = numero(
        dados.get("percentual"),
        -1
    )

    if percentual < 0 or percentual > 100:
        return jsonify({
            "erro": "O percentual deve estar entre 0 e 100."
        }), 400

    conexao = conectar()

    funcionario = conexao.execute(
        """
        SELECT id, usuario
        FROM usuarios
        WHERE id = ?
        AND tipo = 'funcionario'
        """,
        (id_usuario,)
    ).fetchone()

    if not funcionario:
        conexao.close()

        return jsonify({
            "erro": "Funcionário não encontrado."
        }), 404

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

    registrar_auditoria(
        administrador["id"],
        "PERCENTUAL_ATUALIZADO",
        "usuarios",
        id_usuario,
        f"Novo percentual: {percentual}"
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Percentual atualizado."
    })


@app.route("/usuarios/<int:id_usuario>", methods=["DELETE"])
@exigir_admin
def excluir_usuario(id_usuario):
    administrador = usuario_da_sessao()
    conexao = conectar()

    funcionario = conexao.execute(
        """
        SELECT id, usuario
        FROM usuarios
        WHERE id = ?
        AND tipo = 'funcionario'
        """,
        (id_usuario,)
    ).fetchone()

    if not funcionario:
        conexao.close()

        return jsonify({
            "erro": "Funcionário não encontrado."
        }), 404

    conexao.execute(
        """
        UPDATE atendimentos
        SET funcionario_id = NULL,
            atualizado_em = ?
        WHERE funcionario_id = ?
        """,
        (
            agora(),
            id_usuario
        )
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

    registrar_auditoria(
        administrador["id"],
        "FUNCIONARIO_EXCLUIDO",
        "usuarios",
        id_usuario,
        f"Usuário excluído: {funcionario['usuario']}"
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Funcionário excluído."
    })


# ============================================================
# ATENDIMENTOS
# ============================================================

@app.route("/atendimentos", methods=["GET"])
@exigir_admin
def listar_atendimentos():
    conexao = conectar()

    atendimentos = conexao.execute(
        """
        SELECT
            a.*,
            u.usuario AS funcionario_usuario
        FROM atendimentos a
        LEFT JOIN usuarios u
            ON u.id = a.funcionario_id
        ORDER BY a.data, a.horario
        """
    ).fetchall()

    conexao.close()

    return jsonify([
        dados_atendimento(atendimento)
        for atendimento in atendimentos
    ])


@app.route("/atendimentos", methods=["POST"])
@exigir_admin
def adicionar_atendimento():
    administrador = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    nome = str(
        dados.get("nome", "")
    ).strip()

    cliente = str(
        dados.get("cliente", dados.get("local", ""))
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

    quantidade = inteiro(
        dados.get("quantidade_aparelhos"),
        0
    )

    custo = numero(
        dados.get("custo"),
        0
    )

    funcionario_usuario = nome

    if not cliente:
        return jsonify({
            "erro": "Informe o cliente ou local."
        }), 400

    if not local:
        return jsonify({
            "erro": "Informe o local do serviço."
        }), 400

    if not data or not data_valida(data):
        return jsonify({
            "erro": "Informe uma data válida."
        }), 400

    if not horario_valido(horario):
        return jsonify({
            "erro": (
                "O horário deve estar entre 08:00 e 17:30 "
                "em intervalos de 30 minutos."
            )
        }), 400

    if not tipo_servico:
        return jsonify({
            "erro": "Informe o tipo de serviço."
        }), 400

    if quantidade <= 0:
        return jsonify({
            "erro": "A quantidade deve ser maior que zero."
        }), 400

    if custo < 0:
        return jsonify({
            "erro": "O custo não pode ser negativo."
        }), 400

    conexao = conectar()

    funcionario = conexao.execute(
        """
        SELECT id, usuario, percentual
        FROM usuarios
        WHERE usuario = ?
        AND tipo = 'funcionario'
        """,
        (funcionario_usuario,)
    ).fetchone()

    if not funcionario:
        conexao.close()

        return jsonify({
            "erro": (
                "Funcionário não encontrado. "
                "Informe exatamente o usuário cadastrado."
            )
        }), 400

    conflito = conexao.execute(
        """
        SELECT id
        FROM atendimentos
        WHERE funcionario_id = ?
        AND data = ?
        AND horario = ?
        AND COALESCE(status_resposta, 'pendente')
            NOT IN ('recusado', 'cancelado')
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
        }), 409

    valor_total, valor_comissao = calcular_valores(
        tipo_servico,
        quantidade,
        funcionario["percentual"]
    )

    momento = agora()

    cursor = conexao.execute(
        """
        INSERT INTO atendimentos
        (
            nome,
            cliente,
            local,
            data,
            horario,
            tipo_servico,
            quantidade_aparelhos,
            valor_total,
            custo,
            valor_comissao,
            funcionario_id,
            status_resposta,
            criado_em,
            atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            funcionario["usuario"],
            cliente,
            local,
            data,
            horario,
            tipo_servico,
            quantidade,
            valor_total,
            custo,
            valor_comissao,
            funcionario["id"],
            "pendente",
            momento,
            momento
        )
    )

    conexao.commit()
    novo_id = cursor.lastrowid
    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "ATENDIMENTO_CRIADO",
        "atendimentos",
        novo_id,
        f"Cliente: {cliente}; Local: {local}"
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Atendimento criado.",
        "id": novo_id,
        "valor_total": valor_total,
        "valor_comissao": valor_comissao
    }), 201


@app.route(
    "/atendimentos/<int:id_atendimento>",
    methods=["PUT"]
)
@exigir_admin
def editar_atendimento(id_atendimento):
    administrador = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    conexao = conectar()

    atendimento = conexao.execute(
        """
        SELECT *
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

    cliente = str(
        dados.get(
            "cliente",
            atendimento["cliente"] or atendimento["local"] or ""
        )
    ).strip()

    local = str(
        dados.get(
            "local",
            atendimento["local"] or ""
        )
    ).strip()

    data = str(
        dados.get(
            "data",
            atendimento["data"] or ""
        )
    ).strip()

    horario = str(
        dados.get(
            "horario",
            atendimento["horario"] or ""
        )
    ).strip()

    tipo_servico = str(
        dados.get(
            "tipo_servico",
            atendimento["tipo_servico"] or ""
        )
    ).strip().lower()

    quantidade = inteiro(
        dados.get(
            "quantidade_aparelhos",
            atendimento["quantidade_aparelhos"]
        ),
        0
    )

    custo = numero(
        dados.get(
            "custo",
            atendimento["custo"]
        ),
        0
    )

    funcionario_id = atendimento["funcionario_id"]

    if not data_valida(data):
        conexao.close()

        return jsonify({
            "erro": "Data inválida."
        }), 400

    if not horario_valido(horario):
        conexao.close()

        return jsonify({
            "erro": "Horário inválido."
        }), 400

    if quantidade <= 0:
        conexao.close()

        return jsonify({
            "erro": "Quantidade inválida."
        }), 400

    funcionario = conexao.execute(
        """
        SELECT percentual
        FROM usuarios
        WHERE id = ?
        """,
        (funcionario_id,)
    ).fetchone()

    percentual = (
        funcionario["percentual"]
        if funcionario
        else 0
    )

    valor_total, valor_comissao = calcular_valores(
        tipo_servico,
        quantidade,
        percentual
    )

    conexao.execute(
        """
        UPDATE atendimentos
        SET cliente = ?,
            local = ?,
            data = ?,
            horario = ?,
            tipo_servico = ?,
            quantidade_aparelhos = ?,
            valor_total = ?,
            custo = ?,
            valor_comissao = ?,
            atualizado_em = ?
        WHERE id = ?
        """,
        (
            cliente,
            local,
            data,
            horario,
            tipo_servico,
            quantidade,
            valor_total,
            custo,
            valor_comissao,
            agora(),
            id_atendimento
        )
    )

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "ATENDIMENTO_EDITADO",
        "atendimentos",
        id_atendimento,
        "Dados do atendimento atualizados."
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Atendimento atualizado.",
        "valor_total": valor_total,
        "valor_comissao": valor_comissao
    })


@app.route(
    "/atendimentos/<int:id_atendimento>",
    methods=["DELETE"]
)
@exigir_admin
def excluir_atendimento(id_atendimento):
    administrador = usuario_da_sessao()
    conexao = conectar()

    atendimento = conexao.execute(
        """
        SELECT id, cliente, local
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

    registrar_auditoria(
        administrador["id"],
        "ATENDIMENTO_EXCLUIDO",
        "atendimentos",
        id_atendimento,
        f"Cliente: {atendimento['cliente'] or atendimento['local']}"
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Atendimento excluído."
    })


# ============================================================
# ALTERAÇÃO DE STATUS
# ============================================================

@app.route(
    "/atendimentos/<int:id_atendimento>/status",
    methods=["PUT"]
)
@exigir_admin
def alterar_status(id_atendimento):
    administrador = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    novo_status = str(
        dados.get("status", "")
    ).strip().lower()

    if novo_status not in STATUS_VALIDOS:
        return jsonify({
            "erro": "Status inválido."
        }), 400

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

    if not transicao_permitida(
        status_atual,
        novo_status
    ):
        conexao.close()

        return jsonify({
            "erro": (
                f"Não é permitido alterar de "
                f"{status_atual} para {novo_status}."
            )
        }), 400

    concluido_em = (
        agora()
        if novo_status == "concluido"
        else None
    )

    conexao.execute(
        """
        UPDATE atendimentos
        SET status_resposta = ?,
            atualizado_em = ?,
            concluido_em = COALESCE(?, concluido_em)
        WHERE id = ?
        """,
        (
            novo_status,
            agora(),
            concluido_em,
            id_atendimento
        )
    )

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "STATUS_ALTERADO",
        "atendimentos",
        id_atendimento,
        f"{status_atual} -> {novo_status}"
    )

    return jsonify({
        "sucesso": True,
        "status": novo_status
    })


@app.route(
    "/atendimentos/<int:id_atendimento>/concluir",
    methods=["PUT"]
)
@exigir_admin
def concluir_atendimento(id_atendimento):
    administrador = usuario_da_sessao()
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

    if status_atual not in (
        "aceito",
        "em_andamento"
    ):
        conexao.close()

        return jsonify({
            "erro": (
                "O atendimento precisa estar aceito "
                "ou em andamento."
            )
        }), 400

    conexao.execute(
        """
        UPDATE atendimentos
        SET status_resposta = 'concluido',
            concluido_em = ?,
            atualizado_em = ?
        WHERE id = ?
        """,
        (
            agora(),
            agora(),
            id_atendimento
        )
    )

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "ATENDIMENTO_CONCLUIDO",
        "atendimentos",
        id_atendimento,
        "Comissão liberada após conclusão."
    )

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
            cliente,
            nome,
            local,
            data,
            horario,
            tipo_servico,
            quantidade_aparelhos,
            valor_comissao,
            status_resposta,
            criado_em,
            atualizado_em,
            concluido_em
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

    consulta += " ORDER BY data, horario"

    atendimentos = conexao.execute(
        consulta,
        parametros
    ).fetchall()

    conexao.close()

    resposta = []

    for atendimento in atendimentos:
        item = dict(atendimento)

        # Nunca envia faturamento, custo ou percentual ao funcionário.
        item.pop("valor_total", None)
        item.pop("custo", None)
        item.pop("percentual", None)

        resposta.append(item)

    return jsonify(resposta)


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
        SET status_resposta = ?,
            atualizado_em = ?
        WHERE id = ?
        AND funcionario_id = ?
        """,
        (
            resposta,
            agora(),
            id_atendimento,
            usuario["id"]
        )
    )

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        usuario["id"],
        "ATENDIMENTO_RESPONDIDO",
        "atendimentos",
        id_atendimento,
        f"Resposta do funcionário: {resposta}"
    )

    return jsonify({
        "sucesso": True,
        "status_resposta": resposta
    })


# ============================================================
# AUDITORIA
# ============================================================

@app.route("/auditoria", methods=["GET"])
@exigir_admin
def listar_auditoria():
    conexao = conectar()

    registros = conexao.execute(
        """
        SELECT
            a.*,
            u.usuario
        FROM auditoria a
        LEFT JOIN usuarios u
            ON u.id = a.usuario_id
        ORDER BY a.id DESC
        LIMIT 500
        """
    ).fetchall()

    conexao.close()

    return jsonify([
        dict(registro)
        for registro in registros
    ])


# ============================================================
# RELATÓRIOS FINANCEIROS
# ============================================================

def obter_periodo():
    inicio = request.args.get(
        "inicio",
        ""
    ).strip()

    fim = request.args.get(
        "fim",
        ""
    ).strip()

    if inicio and not data_valida(inicio):
        return None, None, "Data inicial inválida."

    if fim and not data_valida(fim):
        return None, None, "Data final inválida."

    if inicio and fim and inicio > fim:
        return None, None, (
            "A data inicial não pode ser maior que a final."
        )

    return inicio, fim, None


@app.route("/relatorios/financeiro", methods=["GET"])
@exigir_admin
def relatorio_financeiro():
    inicio, fim, erro = obter_periodo()

    if erro:
        return jsonify({
            "erro": erro
        }), 400

    conexao = conectar()

    consulta = """
        SELECT *
        FROM atendimentos
        WHERE 1 = 1
    """

    parametros = []

    if inicio:
        consulta += " AND data >= ?"
        parametros.append(inicio)

    if fim:
        consulta += " AND data <= ?"
        parametros.append(fim)

    consulta += " ORDER BY data, horario"

    atendimentos = conexao.execute(
        consulta,
        parametros
    ).fetchall()

    conexao.close()

    total_faturamento = 0
    total_custos = 0
    total_comissoes = 0
    total_lucro = 0
    comissoes_pendentes = 0
    comissoes_concluidas = 0

    por_status = {}
    por_servico = {}
    por_funcionario = {}

    for atendimento in atendimentos:
        item = dados_atendimento(atendimento)

        faturamento = numero(item.get("valor_total"))
        custo = numero(item.get("custo"))
        comissao = numero(item.get("valor_comissao"))
        lucro = faturamento - custo - comissao
        status = item["status"]

        total_faturamento += faturamento
        total_custos += custo
        total_comissoes += comissao
        total_lucro += lucro

        if status == "concluido":
            comissoes_concluidas += comissao
        elif status not in ("recusado", "cancelado"):
            comissoes_pendentes += comissao

        por_status[status] = (
            por_status.get(status, 0) + 1
        )

        servico = item.get("tipo_servico") or "outros"

        if servico not in por_servico:
            por_servico[servico] = {
                "atendimentos": 0,
                "faturamento": 0,
                "comissoes": 0,
                "lucro": 0
            }

        por_servico[servico]["atendimentos"] += 1
        por_servico[servico]["faturamento"] += faturamento
        por_servico[servico]["comissoes"] += comissao
        por_servico[servico]["lucro"] += lucro

        funcionario = (
            item.get("nome")
            or "Não atribuído"
        )

        if funcionario not in por_funcionario:
            por_funcionario[funcionario] = {
                "atendimentos": 0,
                "faturamento": 0,
                "comissoes": 0
            }

        por_funcionario[funcionario]["atendimentos"] += 1
        por_funcionario[funcionario]["faturamento"] += faturamento
        por_funcionario[funcionario]["comissoes"] += comissao

    return jsonify({
        "periodo": {
            "inicio": inicio,
            "fim": fim
        },
        "resumo": {
            "atendimentos": len(atendimentos),
            "faturamento": round(total_faturamento, 2),
            "custos": round(total_custos, 2),
            "comissoes": round(total_comissoes, 2),
            "lucro": round(total_lucro, 2),
            "comissoes_pendentes": round(
                comissoes_pendentes,
                2
            ),
            "comissoes_concluidas": round(
                comissoes_concluidas,
                2
            )
        },
        "por_status": por_status,
        "por_servico": por_servico,
        "por_funcionario": por_funcionario
    })


# ============================================================
# EXPORTAÇÃO CSV
# ============================================================

@app.route("/exportar/atendimentos.csv", methods=["GET"])
@exigir_admin
def exportar_atendimentos():
    inicio, fim, erro = obter_periodo()

    if erro:
        return jsonify({
            "erro": erro
        }), 400

    conexao = conectar()

    consulta = """
        SELECT *
        FROM atendimentos
        WHERE 1 = 1
    """

    parametros = []

    if inicio:
        consulta += " AND data >= ?"
        parametros.append(inicio)

    if fim:
        consulta += " AND data <= ?"
        parametros.append(fim)

    consulta += " ORDER BY data, horario"

    atendimentos = conexao.execute(
        consulta,
        parametros
    ).fetchall()

    conexao.close()

    texto = StringIO()
    texto.write("\ufeff")

    escritor = csv.writer(
        texto,
        delimiter=";"
    )

    escritor.writerow([
        "ID",
        "Cliente",
        "Local",
        "Funcionário",
        "Data",
        "Horário",
        "Serviço",
        "Aparelhos",
        "Valor total",
        "Custo",
        "Comissão",
        "Lucro",
        "Status"
    ])

    for atendimento in atendimentos:
        item = dados_atendimento(atendimento)

        escritor.writerow([
            item.get("id", ""),
            item.get("cliente", ""),
            item.get("local", ""),
            item.get("nome", ""),
            item.get("data", ""),
            item.get("horario", ""),
            item.get("tipo_servico", ""),
            item.get("quantidade_aparelhos", ""),
            item.get("valor_total", 0),
            item.get("custo", 0),
            item.get("valor_comissao", 0),
            item.get("lucro_estimado", 0),
            item.get("status", "")
        ])

    arquivo = BytesIO(
        texto.getvalue().encode("utf-8")
    )

    return send_file(
        arquivo,
        mimetype="text/csv",
        as_attachment=True,
        download_name="atendimentos-plar.csv"
    )


@app.route("/exportar/auditoria.csv", methods=["GET"])
@exigir_admin
def exportar_auditoria():
    conexao = conectar()

    registros = conexao.execute(
        """
        SELECT
            a.id,
            u.usuario,
            a.acao,
            a.tabela,
            a.registro_id,
            a.detalhes,
            a.criado_em
        FROM auditoria a
        LEFT JOIN usuarios u
            ON u.id = a.usuario_id
        ORDER BY a.id DESC
        """
    ).fetchall()

    conexao.close()

    texto = StringIO()
    texto.write("\ufeff")

    escritor = csv.writer(
        texto,
        delimiter=";"
    )

    escritor.writerow([
        "ID",
        "Usuário",
        "Ação",
        "Tabela",
        "Registro",
        "Detalhes",
        "Data"
    ])

    for registro in registros:
        escritor.writerow([
            registro["id"],
            registro["usuario"] or "",
            registro["acao"],
            registro["tabela"],
            registro["registro_id"] or "",
            registro["detalhes"] or "",
            registro["criado_em"]
        ])

    arquivo = BytesIO(
        texto.getvalue().encode("utf-8")
    )

    return send_file(
        arquivo,
        mimetype="text/csv",
        as_attachment=True,
        download_name="auditoria-plar.csv"
    )


# ============================================================
# STATUS
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
