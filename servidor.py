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
from datetime import datetime, timedelta
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
# CONFIGURAÇÃO DA EMPRESA
# ============================================================

NOME_EMPRESA = "PLAR Condicionados"
CIDADE_EMPRESA = "Ribeirão Preto - SP"

SERVICOS = {
    "manutencao_preventiva": (
        "Manutenção preventiva"
    ),
    "limpeza_higienizacao": (
        "Limpeza e higienização de evaporadora"
    ),
    "automacao": (
        "Automação de ar-condicionado"
    )
}

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


def agora():
    return datetime.now().isoformat(timespec="seconds")


def coluna_existe(conexao, tabela, coluna):
    colunas = conexao.execute(
        f"PRAGMA table_info({tabela})"
    ).fetchall()

    return any(
        item["name"] == coluna
        for item in colunas
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
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT NOT NULL,
            email TEXT,
            endereco TEXT,
            cidade TEXT DEFAULT 'Ribeirão Preto - SP',
            consentimento_lgpd INTEGER NOT NULL DEFAULT 0,
            criado_em TEXT NOT NULL,
            atualizado_em TEXT NOT NULL
        )
    """)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS atendimentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            cliente TEXT,
            cliente_id INTEGER,
            telefone_cliente TEXT,
            local TEXT,
            data TEXT,
            horario TEXT,
            duracao_minutos INTEGER DEFAULT 30,
            tipo_servico TEXT,
            quantidade_aparelhos INTEGER DEFAULT 1,
            valor_total REAL DEFAULT 0,
            custo REAL DEFAULT 0,
            valor_comissao REAL DEFAULT 0,
            funcionario_id INTEGER,
            status_resposta TEXT DEFAULT 'pendente',
            origem TEXT DEFAULT 'admin',
            consentimento_lgpd INTEGER DEFAULT 0,
            observacoes TEXT,
            criado_em TEXT,
            atualizado_em TEXT,
            concluido_em TEXT,
            FOREIGN KEY (cliente_id)
                REFERENCES clientes(id)
                ON DELETE SET NULL,
            FOREIGN KEY (funcionario_id)
                REFERENCES usuarios(id)
                ON DELETE SET NULL
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

    colunas_atendimentos = {
        "cliente": "TEXT",
        "cliente_id": "INTEGER",
        "telefone_cliente": "TEXT",
        "tipo_servico": "TEXT",
        "quantidade_aparelhos": (
            "INTEGER DEFAULT 1"
        ),
        "duracao_minutos": (
            "INTEGER DEFAULT 30"
        ),
        "valor_total": "REAL DEFAULT 0",
        "custo": "REAL DEFAULT 0",
        "valor_comissao": "REAL DEFAULT 0",
        "funcionario_id": "INTEGER",
        "status_resposta": (
            "TEXT DEFAULT 'pendente'"
        ),
        "origem": "TEXT DEFAULT 'admin'",
        "consentimento_lgpd": (
            "INTEGER DEFAULT 0"
        ),
        "observacoes": "TEXT",
        "criado_em": "TEXT",
        "atualizado_em": "TEXT",
        "concluido_em": "TEXT"
    }

    for coluna, tipo in colunas_atendimentos.items():
        adicionar_coluna(
            conexao,
            "atendimentos",
            coluna,
            tipo
        )

    momento = agora()

    conexao.execute("""
        UPDATE atendimentos
        SET status_resposta = 'pendente'
        WHERE status_resposta IS NULL
        OR status_resposta = ''
    """)

    conexao.execute("""
        UPDATE atendimentos
        SET quantidade_aparelhos = 1
        WHERE quantidade_aparelhos IS NULL
        OR quantidade_aparelhos <= 0
    """)

    conexao.execute("""
        UPDATE atendimentos
        SET duracao_minutos =
            quantidade_aparelhos * 30
        WHERE duracao_minutos IS NULL
        OR duracao_minutos <= 0
    """)

    conexao.execute("""
        UPDATE atendimentos
        SET cliente = COALESCE(
            cliente,
            local,
            'Não informado'
        )
        WHERE cliente IS NULL
    """)

    conexao.execute("""
        UPDATE atendimentos
        SET criado_em = ?
        WHERE criado_em IS NULL
    """, (momento,))

    conexao.execute("""
        UPDATE atendimentos
        SET atualizado_em = criado_em
        WHERE atualizado_em IS NULL
    """)

    admin = conexao.execute("""
        SELECT id
        FROM usuarios
        WHERE usuario = 'PLAR'
    """).fetchone()

    if not admin:
        senha_inicial = os.environ.get(
            "PLAR_ADMIN_PASSWORD",
            "Edilson123"
        )

        conexao.execute("""
            INSERT INTO usuarios
            (
                usuario,
                senha,
                tipo,
                percentual,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            "PLAR",
            generate_password_hash(senha_inicial),
            "admin",
            0,
            momento
        ))

        print("Administrador inicial criado.")
        print("Usuário: PLAR")
        print("Altere a senha após o primeiro acesso.")

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_atendimentos_data_horario
        ON atendimentos(data, horario)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_atendimentos_funcionario
        ON atendimentos(funcionario_id)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_atendimentos_status
        ON atendimentos(status_resposta)
    """)

    conexao.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_clientes_telefone
        ON clientes(telefone)
    """)

    conexao.commit()
    conexao.close()


criar_tabelas()


# ============================================================
# UTILITÁRIOS
# ============================================================

def numero(valor, padrao=0):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def inteiro(valor, padrao=0):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def data_valida(data):
    try:
        datetime.strptime(
            str(data),
            "%Y-%m-%d"
        )

        return True
    except (TypeError, ValueError):
        return False


def hora_em_minutos(horario):
    try:
        objeto = datetime.strptime(
            str(horario),
            "%H:%M"
        )

        return objeto.hour * 60 + objeto.minute
    except (TypeError, ValueError):
        return None


def limite_expediente(data):
    objeto = datetime.strptime(
        data,
        "%Y-%m-%d"
    )

    dia_semana = objeto.weekday()

    if dia_semana == 6:
        return None

    if dia_semana == 5:
        return 8 * 60, 14 * 60

    return 8 * 60, 17 * 60 + 30


def horario_valido(data, horario, duracao=30):
    if not data_valida(data):
        return False, "Data inválida."

    minutos = hora_em_minutos(horario)

    if minutos is None:
        return False, "Horário inválido."

    if minutos % 30 != 0:
        return False, (
            "O horário deve ser em intervalos de 30 minutos."
        )

    limites = limite_expediente(data)

    if limites is None:
        return False, (
            "A empresa não atende aos domingos."
        )

    inicio, fim = limites
    final = minutos + int(duracao)

    if minutos < inicio or final > fim:
        return False, (
            "O horário está fora do expediente."
        )

    return True, ""


def data_hora_agendamento(data, horario):
    return datetime.strptime(
        f"{data} {horario}",
        "%Y-%m-%d %H:%M"
    )


def antecedencia_valida(data, horario):
    agendamento = data_hora_agendamento(
        data,
        horario
    )

    limite = datetime.now() + timedelta(hours=2)

    return agendamento >= limite


def normalizar_telefone(telefone):
    return "".join(
        caractere
        for caractere in str(telefone or "")
        if caractere.isdigit()
    )


def servico_valido(servico):
    return str(servico or "").strip().lower() in SERVICOS


def duracao_por_aparelhos(quantidade):
    return max(1, int(quantidade)) * 30


def calcular_valores(
    tipo_servico,
    quantidade_aparelhos,
    percentual
):
    quantidade = max(
        1,
        int(quantidade_aparelhos)
    )

    valores = {
        "manutencao_preventiva": 200,
        "limpeza_higienizacao": 180,
        "automacao": 950
    }

    valor_unitario = valores.get(
        tipo_servico,
        200
    )

    valor_total = valor_unitario * quantidade

    valor_comissao = round(
        valor_total * float(percentual) / 100,
        2
    )

    return round(valor_total, 2), valor_comissao


def usuario_da_sessao():
    usuario_id = session.get("usuario_id")

    if not usuario_id:
        return None

    conexao = conectar()

    usuario = conexao.execute("""
        SELECT id, usuario, tipo, percentual
        FROM usuarios
        WHERE id = ?
    """, (usuario_id,)).fetchone()

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
                return redirect(
                    "/area-funcionario.html"
                )

            return jsonify({
                "erro": (
                    "Acesso permitido somente "
                    "ao administrador."
                )
            }), 403

        return funcao(*args, **kwargs)

    return protegida


def senha_valida(senha_salva, senha_digitada):
    if not senha_salva:
        return False

    valor = str(senha_salva)

    eh_hash = (
        valor.startswith("pbkdf2:")
        or valor.startswith("scrypt:")
        or valor.startswith("argon2:")
    )

    if eh_hash:
        try:
            return check_password_hash(
                valor,
                senha_digitada
            )
        except (ValueError, TypeError):
            return False

    return valor == senha_digitada


def transicao_permitida(
    status_atual,
    novo_status
):
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

    conexao.execute("""
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
    """, (
        usuario_id,
        acao,
        tabela,
        registro_id,
        detalhes,
        agora()
    ))

    conexao.commit()
    conexao.close()


def conflito_horario(
    conexao,
    data,
    horario,
    duracao,
    funcionario_id=None,
    ignorar_id=None
):
    inicio_novo = hora_em_minutos(horario)
    fim_novo = inicio_novo + duracao

    consulta = """
        SELECT id, horario, duracao_minutos
        FROM atendimentos
        WHERE data = ?
        AND COALESCE(status_resposta, 'pendente')
        NOT IN ('recusado', 'cancelado')
    """

    parametros = [data]

    if funcionario_id:
        consulta += " AND funcionario_id = ?"
        parametros.append(funcionario_id)

    if ignorar_id:
        consulta += " AND id != ?"
        parametros.append(ignorar_id)

    registros = conexao.execute(
        consulta,
        parametros
    ).fetchall()

    for registro in registros:
        inicio_existente = hora_em_minutos(
            registro["horario"]
        )

        duracao_existente = (
            registro["duracao_minutos"] or 30
        )

        fim_existente = (
            inicio_existente + duracao_existente
        )

        existe_intersecao = (
            inicio_novo < fim_existente
            and fim_novo > inicio_existente
        )

        if existe_intersecao:
            return True

    return False


def dados_atendimento(linha):
    dados = dict(linha)

    dados["status"] = (
        dados.get("status_resposta")
        or "pendente"
    )

    dados["duracao_minutos"] = (
        dados.get("duracao_minutos")
        or duracao_por_aparelhos(
            dados.get("quantidade_aparelhos") or 1
        )
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

    usuario_nome = str(
        dados.get("usuario", "")
    ).strip()

    senha = str(
        dados.get("senha", "")
    )

    if not usuario_nome or not senha:
        return jsonify({
            "sucesso": False,
            "erro": "Informe usuário e senha."
        }), 400

    conexao = conectar()

    usuario = conexao.execute("""
        SELECT *
        FROM usuarios
        WHERE usuario = ?
    """, (usuario_nome,)).fetchone()

    if not usuario or not senha_valida(
        usuario["senha"],
        senha
    ):
        conexao.close()

        return jsonify({
            "sucesso": False,
            "erro": "Usuário ou senha inválidos."
        }), 401

    senha_salva = str(usuario["senha"])

    eh_hash = (
        senha_salva.startswith("pbkdf2:")
        or senha_salva.startswith("scrypt:")
        or senha_salva.startswith("argon2:")
    )

    if not eh_hash:
        conexao.execute("""
            UPDATE usuarios
            SET senha = ?
            WHERE id = ?
        """, (
            generate_password_hash(senha),
            usuario["id"]
        ))

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


@app.route("/agendar")
def pagina_agendamento():
    return send_from_directory(
        str(BASE_DIR),
        "agendar.html"
    )


@app.route("/agendar.html")
def pagina_agendamento_html():
    return send_from_directory(
        str(BASE_DIR),
        "agendar.html"
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

@app.route("/meu-perfil")
@exigir_login
def meu_perfil():
    usuario = usuario_da_sessao()

    return jsonify({
        "id": usuario["id"],
        "usuario": usuario["usuario"],
        "tipo": usuario["tipo"]
    })


# ============================================================
# SERVIÇOS E HORÁRIOS PÚBLICOS
# ============================================================

@app.route("/api/servicos", methods=["GET"])
def listar_servicos():
    return jsonify([
        {
            "codigo": codigo,
            "nome": nome
        }
        for codigo, nome in SERVICOS.items()
    ])


@app.route(
    "/api/horarios-disponiveis",
    methods=["GET"]
)
def horarios_disponiveis():
    data = request.args.get(
        "data",
        ""
    ).strip()

    quantidade = inteiro(
        request.args.get(
            "quantidade",
            1
        ),
        1
    )

    quantidade = max(1, quantidade)
    duracao = duracao_por_aparelhos(quantidade)

    if not data_valida(data):
        return jsonify({
            "erro": "Informe uma data válida."
        }), 400

    limites = limite_expediente(data)

    if limites is None:
        return jsonify({
            "data": data,
            "duracao_minutos": duracao,
            "horarios": []
        })

    inicio, fim = limites
    conexao = conectar()

    horarios = []

    minuto_atual = inicio

    while minuto_atual + duracao <= fim:
        horario = (
            f"{minuto_atual // 60:02d}:"
            f"{minuto_atual % 60:02d}"
        )

        disponivel = (
            antecedencia_valida(data, horario)
            and not conflito_horario(
                conexao,
                data,
                horario,
                duracao
            )
        )

        if disponivel:
            horarios.append(horario)

        minuto_atual += 30

    conexao.close()

    return jsonify({
        "data": data,
        "duracao_minutos": duracao,
        "horarios": horarios
    })


# ============================================================
# AGENDAMENTO PÚBLICO
# ============================================================

@app.route(
    "/api/agendamentos-publicos",
    methods=["POST"]
)
def criar_agendamento_publico():
    dados = request.get_json(silent=True) or {}

    nome = str(
        dados.get("nome", "")
    ).strip()

    telefone = normalizar_telefone(
        dados.get("telefone")
    )

    endereco = str(
        dados.get("endereco", "")
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

    observacoes = str(
        dados.get("observacoes", "")
    ).strip()

    consentimento = bool(
        dados.get("consentimento_lgpd")
    )

    if len(nome) < 2:
        return jsonify({
            "erro": "Informe seu nome completo."
        }), 400

    if len(telefone) < 10:
        return jsonify({
            "erro": "Informe um WhatsApp válido."
        }), 400

    if not endereco:
        return jsonify({
            "erro": "Informe o endereço do atendimento."
        }), 400

    if quantidade <= 0:
        return jsonify({
            "erro": (
                "Informe a quantidade de aparelhos."
            )
        }), 400

    if not servico_valido(tipo_servico):
        return jsonify({
            "erro": "Escolha um serviço válido."
        }), 400

    duracao = duracao_por_aparelhos(quantidade)

    valido, mensagem = horario_valido(
        data,
        horario,
        duracao
    )

    if not valido:
        return jsonify({
            "erro": mensagem
        }), 400

    if not antecedencia_valida(data, horario):
        return jsonify({
            "erro": (
                "O agendamento precisa ser feito "
                "com pelo menos 2 horas de antecedência."
            )
        }), 400

    if not consentimento:
        return jsonify({
            "erro": (
                "É necessário aceitar a política "
                "de privacidade."
            )
        }), 400

    conexao = conectar()

    if conflito_horario(
        conexao,
        data,
        horario,
        duracao
    ):
        conexao.close()

        return jsonify({
            "erro": (
                "Esse horário não está mais disponível."
            )
        }), 409

    momento = agora()

    cliente = conexao.execute("""
        SELECT id
        FROM clientes
        WHERE telefone = ?
        ORDER BY id DESC
        LIMIT 1
    """, (telefone,)).fetchone()

    if cliente:
        cliente_id = cliente["id"]

        conexao.execute("""
            UPDATE clientes
            SET nome = ?,
                endereco = ?,
                consentimento_lgpd = 1,
                atualizado_em = ?
            WHERE id = ?
        """, (
            nome,
            endereco,
            momento,
            cliente_id
        ))
    else:
        cursor_cliente = conexao.execute("""
            INSERT INTO clientes
            (
                nome,
                telefone,
                endereco,
                cidade,
                consentimento_lgpd,
                criado_em,
                atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            nome,
            telefone,
            endereco,
            CIDADE_EMPRESA,
            1,
            momento,
            momento
        ))

        cliente_id = cursor_cliente.lastrowid

    cursor = conexao.execute("""
        INSERT INTO atendimentos
        (
            cliente,
            cliente_id,
            telefone_cliente,
            local,
            data,
            horario,
            duracao_minutos,
            tipo_servico,
            quantidade_aparelhos,
            valor_total,
            custo,
            valor_comissao,
            funcionario_id,
            status_resposta,
            origem,
            consentimento_lgpd,
            observacoes,
            criado_em,
            atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nome,
        cliente_id,
        telefone,
        endereco,
        data,
        horario,
        duracao,
        tipo_servico,
        quantidade,
        0,
        0,
        0,
        None,
        "pendente",
        "publico",
        1,
        observacoes,
        momento,
        momento
    ))

    atendimento_id = cursor.lastrowid

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "mensagem": (
            "Solicitação enviada. "
            "Aguarde a confirmação da PLAR."
        ),
        "id": atendimento_id,
        "status": "pendente"
    }), 201


# ============================================================
# CLIENTES — ADMINISTRADOR
# ============================================================

@app.route("/clientes", methods=["GET"])
@exigir_admin
def listar_clientes():
    conexao = conectar()

    clientes = conexao.execute("""
        SELECT *
        FROM clientes
        ORDER BY nome
    """).fetchall()

    conexao.close()

    return jsonify([
        dict(cliente)
        for cliente in clientes
    ])


@app.route("/clientes/<int:cliente_id>", methods=["PUT"])
@exigir_admin
def editar_cliente(cliente_id):
    dados = request.get_json(silent=True) or {}

    nome = str(
        dados.get("nome", "")
    ).strip()

    telefone = normalizar_telefone(
        dados.get("telefone")
    )

    endereco = str(
        dados.get("endereco", "")
    ).strip()

    if not nome or len(telefone) < 10:
        return jsonify({
            "erro": "Nome e telefone são obrigatórios."
        }), 400

    conexao = conectar()

    resultado = conexao.execute("""
        UPDATE clientes
        SET nome = ?,
            telefone = ?,
            endereco = ?,
            atualizado_em = ?
        WHERE id = ?
    """, (
        nome,
        telefone,
        endereco,
        agora(),
        cliente_id
    ))

    conexao.commit()
    alterado = resultado.rowcount
    conexao.close()

    if not alterado:
        return jsonify({
            "erro": "Cliente não encontrado."
        }), 404

    return jsonify({
        "sucesso": True,
        "mensagem": "Cliente atualizado."
    })


# ============================================================
# FUNCIONÁRIOS
# ============================================================

@app.route("/usuarios", methods=["GET"])
@exigir_admin
def listar_usuarios():
    conexao = conectar()

    usuarios = conexao.execute("""
        SELECT id, usuario, tipo, percentual, criado_em
        FROM usuarios
        WHERE tipo = 'funcionario'
        ORDER BY usuario
    """).fetchall()

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
            "erro": "Percentual deve estar entre 0 e 100."
        }), 400

    conexao = conectar()

    try:
        cursor = conexao.execute("""
            INSERT INTO usuarios
            (
                usuario,
                senha,
                tipo,
                percentual,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            usuario,
            generate_password_hash(senha),
            "funcionario",
            percentual,
            agora()
        ))

        conexao.commit()
        novo_id = cursor.lastrowid

    except sqlite3.IntegrityError:
        conexao.close()

        return jsonify({
            "erro": "Esse usuário já existe."
        }), 400

    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "FUNCIONARIO_CRIADO",
        "usuarios",
        novo_id,
        usuario
    )

    return jsonify({
        "sucesso": True,
        "id": novo_id
    }), 201


@app.route(
    "/usuarios/<int:id_usuario>",
    methods=["PUT"]
)
@exigir_admin
def editar_usuario(id_usuario):
    dados = request.get_json(silent=True) or {}

    percentual = numero(
        dados.get("percentual"),
        -1
    )

    if percentual < 0 or percentual > 100:
        return jsonify({
            "erro": "Percentual deve estar entre 0 e 100."
        }), 400

    conexao = conectar()

    resultado = conexao.execute("""
        UPDATE usuarios
        SET percentual = ?
        WHERE id = ?
        AND tipo = 'funcionario'
    """, (
        percentual,
        id_usuario
    ))

    conexao.commit()
    conexao.close()

    if not resultado.rowcount:
        return jsonify({
            "erro": "Funcionário não encontrado."
        }), 404

    return jsonify({
        "sucesso": True,
        "mensagem": "Percentual atualizado."
    })


@app.route(
    "/usuarios/<int:id_usuario>",
    methods=["DELETE"]
)
@exigir_admin
def excluir_usuario(id_usuario):
    conexao = conectar()

    funcionario = conexao.execute("""
        SELECT usuario
        FROM usuarios
        WHERE id = ?
        AND tipo = 'funcionario'
    """, (id_usuario,)).fetchone()

    if not funcionario:
        conexao.close()

        return jsonify({
            "erro": "Funcionário não encontrado."
        }), 404

    conexao.execute("""
        UPDATE atendimentos
        SET funcionario_id = NULL,
            atualizado_em = ?
        WHERE funcionario_id = ?
    """, (
        agora(),
        id_usuario
    ))

    conexao.execute("""
        DELETE FROM usuarios
        WHERE id = ?
        AND tipo = 'funcionario'
    """, (id_usuario,))

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "mensagem": "Funcionário excluído."
    })


# ============================================================
# ATENDIMENTOS ADMINISTRATIVOS
# ============================================================

@app.route("/atendimentos", methods=["GET"])
@exigir_admin
def listar_atendimentos():
    conexao = conectar()

    atendimentos = conexao.execute("""
        SELECT
            a.*,
            u.usuario AS funcionario_usuario
        FROM atendimentos a
        LEFT JOIN usuarios u
            ON u.id = a.funcionario_id
        ORDER BY a.data, a.horario
    """).fetchall()

    conexao.close()

    return jsonify([
        dados_atendimento(item)
        for item in atendimentos
    ])


@app.route("/atendimentos", methods=["POST"])
@exigir_admin
def adicionar_atendimento():
    administrador = usuario_da_sessao()
    dados = request.get_json(silent=True) or {}

    nome_funcionario = str(
        dados.get("nome", "")
    ).strip()

    cliente = str(
        dados.get("cliente", "")
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

    quantidade = max(
        1,
        inteiro(
            dados.get("quantidade_aparelhos"),
            0
        )
    )

    custo = numero(
        dados.get("custo"),
        0
    )

    if not cliente:
        return jsonify({
            "erro": "Informe o cliente."
        }), 400

    if not local:
        return jsonify({
            "erro": "Informe o local."
        }), 400

    if not servico_valido(tipo_servico):
        return jsonify({
            "erro": "Informe um serviço válido."
        }), 400

    duracao = duracao_por_aparelhos(quantidade)

    valido, mensagem = horario_valido(
        data,
        horario,
        duracao
    )

    if not valido:
        return jsonify({
            "erro": mensagem
        }), 400

    if custo < 0:
        return jsonify({
            "erro": "O custo não pode ser negativo."
        }), 400

    conexao = conectar()

    funcionario = conexao.execute("""
        SELECT id, usuario, percentual
        FROM usuarios
        WHERE usuario = ?
        AND tipo = 'funcionario'
    """, (
        nome_funcionario,
    )).fetchone()

    if not funcionario:
        conexao.close()

        return jsonify({
            "erro": "Funcionário não encontrado."
        }), 400

    if conflito_horario(
        conexao,
        data,
        horario,
        duracao,
        funcionario["id"]
    ):
        conexao.close()

        return jsonify({
            "erro": (
                "O funcionário já possui conflito "
                "nesse período."
            )
        }), 409

    valor_total, valor_comissao = calcular_valores(
        tipo_servico,
        quantidade,
        funcionario["percentual"]
    )

    momento = agora()

    cursor = conexao.execute("""
        INSERT INTO atendimentos
        (
            nome,
            cliente,
            local,
            data,
            horario,
            duracao_minutos,
            tipo_servico,
            quantidade_aparelhos,
            valor_total,
            custo,
            valor_comissao,
            funcionario_id,
            status_resposta,
            origem,
            criado_em,
            atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        funcionario["usuario"],
        cliente,
        local,
        data,
        horario,
        duracao,
        tipo_servico,
        quantidade,
        valor_total,
        custo,
        valor_comissao,
        funcionario["id"],
        "pendente",
        "admin",
        momento,
        momento
    ))

    atendimento_id = cursor.lastrowid

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "ATENDIMENTO_CRIADO",
        "atendimentos",
        atendimento_id,
        f"Cliente: {cliente}"
    )

    return jsonify({
        "sucesso": True,
        "id": atendimento_id,
        "valor_total": valor_total,
        "valor_comissao": valor_comissao
    }), 201


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

    atendimento = conexao.execute("""
        SELECT id, status_resposta
        FROM atendimentos
        WHERE id = ?
    """, (id_atendimento,)).fetchone()

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

    momento = agora()

    conexao.execute("""
        UPDATE atendimentos
        SET status_resposta = ?,
            atualizado_em = ?,
            concluido_em = CASE
                WHEN ? = 'concluido' THEN ?
                ELSE concluido_em
            END
        WHERE id = ?
    """, (
        novo_status,
        momento,
        novo_status,
        momento,
        id_atendimento
    ))

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
    dados = request.get_json(silent=True) or {}

    return alterar_status_interno(
        id_atendimento,
        "concluido",
        dados
    )


def alterar_status_interno(
    id_atendimento,
    novo_status,
    dados=None
):
    administrador = usuario_da_sessao()
    conexao = conectar()

    atendimento = conexao.execute("""
        SELECT id, status_resposta
        FROM atendimentos
        WHERE id = ?
    """, (id_atendimento,)).fetchone()

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

    momento = agora()

    conexao.execute("""
        UPDATE atendimentos
        SET status_resposta = ?,
            atualizado_em = ?,
            concluido_em = ?
        WHERE id = ?
    """, (
        novo_status,
        momento,
        momento if novo_status == "concluido" else None,
        id_atendimento
    ))

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        administrador["id"],
        "ATENDIMENTO_CONCLUIDO",
        "atendimentos",
        id_atendimento,
        "Serviço concluído."
    )

    return jsonify({
        "sucesso": True,
        "mensagem": "Atendimento concluído."
    })


@app.route(
    "/atendimentos/<int:id_atendimento>",
    methods=["DELETE"]
)
@exigir_admin
def excluir_atendimento(id_atendimento):
    conexao = conectar()

    atendimento = conexao.execute("""
        SELECT id
        FROM atendimentos
        WHERE id = ?
    """, (id_atendimento,)).fetchone()

    if not atendimento:
        conexao.close()

        return jsonify({
            "erro": "Atendimento não encontrado."
        }), 404

    conexao.execute("""
        DELETE FROM atendimentos
        WHERE id = ?
    """, (id_atendimento,))

    conexao.commit()
    conexao.close()

    return jsonify({
        "sucesso": True,
        "mensagem": "Atendimento excluído."
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
            telefone_cliente,
            local,
            data,
            horario,
            duracao_minutos,
            tipo_servico,
            quantidade_aparelhos,
            valor_comissao,
            status_resposta
        FROM atendimentos
        WHERE funcionario_id = ?
    """

    parametros = [usuario["id"]]

    if data:
        if not data_valida(data):
            conexao.close()

            return jsonify({
                "erro": "Data inválida."
            }), 400

        consulta += " AND data = ?"
        parametros.append(data)

    consulta += " ORDER BY data, horario"

    registros = conexao.execute(
        consulta,
        parametros
    ).fetchall()

    conexao.close()

    return jsonify([
        dict(registro)
        for registro in registros
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

    if resposta not in {
        "aceito",
        "recusado"
    }:
        return jsonify({
            "erro": "Resposta inválida."
        }), 400

    conexao = conectar()

    atendimento = conexao.execute("""
        SELECT id, status_resposta
        FROM atendimentos
        WHERE id = ?
        AND funcionario_id = ?
    """, (
        id_atendimento,
        usuario["id"]
    )).fetchone()

    if not atendimento:
        conexao.close()

        return jsonify({
            "erro": "Atendimento não encontrado."
        }), 404

    if atendimento["status_resposta"] != "pendente":
        conexao.close()

        return jsonify({
            "erro": (
                "Este atendimento já recebeu uma resposta."
            )
        }), 400

    conexao.execute("""
        UPDATE atendimentos
        SET status_resposta = ?,
            atualizado_em = ?
        WHERE id = ?
        AND funcionario_id = ?
    """, (
        resposta,
        agora(),
        id_atendimento,
        usuario["id"]
    ))

    conexao.commit()
    conexao.close()

    registrar_auditoria(
        usuario["id"],
        "RESPOSTA_FUNCIONARIO",
        "atendimentos",
        id_atendimento,
        resposta
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

    registros = conexao.execute("""
        SELECT
            a.*,
            u.usuario
        FROM auditoria a
        LEFT JOIN usuarios u
            ON u.id = a.usuario_id
        ORDER BY a.id DESC
        LIMIT 500
    """).fetchall()

    conexao.close()

    return jsonify([
        dict(registro)
        for registro in registros
    ])


# ============================================================
# RELATÓRIO FINANCEIRO
# ============================================================

@app.route(
    "/relatorios/financeiro",
    methods=["GET"]
)
@exigir_admin
def relatorio_financeiro():
    inicio = request.args.get(
        "inicio",
        ""
    ).strip()

    fim = request.args.get(
        "fim",
        ""
    ).strip()

    if inicio and not data_valida(inicio):
        return jsonify({
            "erro": "Data inicial inválida."
        }), 400

    if fim and not data_valida(fim):
        return jsonify({
            "erro": "Data final inválida."
        }), 400

    if inicio and fim and inicio > fim:
        return jsonify({
            "erro": (
                "A data inicial não pode ser maior "
                "que a final."
            )
        }), 400

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

    conexao = conectar()

    registros = conexao.execute(
        consulta + " ORDER BY data, horario",
        parametros
    ).fetchall()

    conexao.close()

    faturamento = 0
    custos = 0
    comissoes = 0
    lucro = 0
    comissoes_pendentes = 0
    comissoes_concluidas = 0

    por_status = {}

    for registro in registros:
        item = dados_atendimento(registro)

        valor_total = numero(
            item.get("valor_total")
        )

        custo = numero(
            item.get("custo")
        )

        comissao = numero(
            item.get("valor_comissao")
        )

        status = item["status"]

        faturamento += valor_total
        custos += custo
        comissoes += comissao
        lucro += valor_total - custo - comissao

        por_status[status] = (
            por_status.get(status, 0) + 1
        )

        if status == "concluido":
            comissoes_concluidas += comissao
        elif status not in {
            "recusado",
            "cancelado"
        }:
            comissoes_pendentes += comissao

    return jsonify({
        "periodo": {
            "inicio": inicio,
            "fim": fim
        },
        "resumo": {
            "atendimentos": len(registros),
            "faturamento": round(faturamento, 2),
            "custos": round(custos, 2),
            "comissoes": round(comissoes, 2),
            "lucro": round(lucro, 2),
            "comissoes_pendentes": round(
                comissoes_pendentes,
                2
            ),
            "comissoes_concluidas": round(
                comissoes_concluidas,
                2
            )
        },
        "por_status": por_status
    })


# ============================================================
# STATUS DO SERVIDOR
# ============================================================

@app.route("/status")
def status():
    return jsonify({
        "online": True,
        "aplicacao": "PLAR ERP",
        "empresa": NOME_EMPRESA,
        "cidade": CIDADE_EMPRESA
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
