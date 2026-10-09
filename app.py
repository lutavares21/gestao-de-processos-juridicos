# -*- coding: utf-8 -*-
"""
App Flask - Gestão de Processos Jurídicos
"""

import csv
import io
import os
import re
import unicodedata
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case
from sqlalchemy.orm import selectinload
from werkzeug.security import check_password_hash, generate_password_hash
from models import (
    db, Processo, Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR,
    PedidoCivel, TituloRecuperacao, AcordoRecebimento, Operador, RegistroAtividade,
)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///processos.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Chave usada para proteger a sessão de login. Em algum momento vale trocar
# por uma variável de ambiente, mas por enquanto um valor fixo já funciona.
app.config["SECRET_KEY"] = "troque-esta-chave-por-uma-string-aleatoria-depois"

db.init_app(app)

with app.app_context():
    db.create_all()

    # create_all() só cria tabelas novas - não acrescenta colunas novas em
    # tabelas que já existem. Então, se o banco é anterior à coluna
    # "situacao" das parcelas do acordo, ela é adicionada aqui (uma vez só).
    from sqlalchemy import inspect as sa_inspect, text as sa_text
    colunas_acordo = [c["name"] for c in sa_inspect(db.engine).get_columns("acordos_recebimento")]
    if "situacao" not in colunas_acordo:
        db.session.execute(sa_text(
            "ALTER TABLE acordos_recebimento ADD COLUMN situacao VARCHAR(20) DEFAULT 'pendente'"
        ))
        # Parcelas antigas que já têm valor recebido passam a constar como pagas.
        db.session.execute(sa_text(
            "UPDATE acordos_recebimento SET situacao = 'pago' "
            "WHERE valor_recebido IS NOT NULL AND valor_recebido > 0"
        ))
        db.session.commit()

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para acessar o sistema."


@login_manager.user_loader
def carregar_operador(operador_id):
    return db.session.get(Operador, int(operador_id))


@app.route("/logout-beacon", methods=["POST"])
def logout_beacon():
    """Chamada pelo navegador (via navigator.sendBeacon) só quando a página
    detectou que está sendo fechada de verdade - o próprio JavaScript
    (static/js/sessao.js) já filtrou navegações internas, F5, etc, então
    aqui é só deslogar direto, sem prazo de espera."""
    if current_user.is_authenticated:
        logout_user()
    return ("", 204)


def admin_required(f):
    """Só deixa passar se o operador logado for administrador.
    Quem não for recebe 403 (acesso negado)."""
    @wraps(f)
    @login_required
    def decorado(*args, **kwargs):
        if not current_user.eh_administrador:
            abort(403)
        return f(*args, **kwargs)
    return decorado


def registrar_atividade(acao, descricao):
    """Grava uma linha no log de atividades, associada ao operador
    logado no momento. Não faz commit sozinha - a chamada que já vai
    salvar o processo/operador salva essa linha junto."""
    db.session.add(RegistroAtividade(
        operador_id=current_user.id if current_user.is_authenticated else None,
        operador_nome=current_user.nome if current_user.is_authenticated else "Desconhecido",
        acao=acao,
        descricao=descricao,
    ))



@app.template_filter("moeda")
def formatar_moeda(valor):
    """Formata um número no padrão contábil brasileiro: R$ 1.234,56.
    Retorna '—' quando o valor é None."""
    if valor is None:
        return "—"
    texto = f"{float(valor):,.2f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("login.html")

    login_informado = request.form.get("login", "").strip()
    senha = request.form.get("senha", "")

    operador = Operador.query.filter(
        func.lower(Operador.login) == login_informado.lower()
    ).first()

    if operador and check_password_hash(operador.senha_hash, senha):
        login_user(operador)
        proxima = request.args.get("next")
        return redirect(proxima or url_for("index"))

    return render_template("login.html", erro_login=True)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/operadores")
@admin_required
def operadores():
    lista = Operador.query.order_by(Operador.nome).all()
    return render_template("operadores.html", operadores=lista)


@app.route("/operadores/novo", methods=["GET", "POST"])
@admin_required
def operador_novo():
    if request.method == "GET":
        return render_template("novo_operador.html")

    form = request.form
    nome = form.get("nome", "").strip()
    login_novo = form.get("login", "").strip()
    email = form.get("email", "").strip()
    senha = form.get("senha", "")
    confirmar_senha = form.get("confirmar_senha", "")
    nivel = form.get("nivel", "comum")
    if nivel not in ("administrador", "comum"):
        nivel = "comum"

    erros = []
    if not nome:
        erros.append("Informe o nome.")
    if not login_novo:
        erros.append("Informe o login.")
    if not email:
        erros.append("Informe o e-mail.")
    if not senha:
        erros.append("Informe a senha.")
    if senha != confirmar_senha:
        erros.append("A senha e a confirmação de senha não coincidem.")
    if login_novo and Operador.query.filter(func.lower(Operador.login) == login_novo.lower()).first():
        erros.append("Já existe um operador com esse login.")

    if erros:
        return render_template("novo_operador.html", erros=erros, valores=form)

    novo_operador = Operador(
        nome=nome,
        login=login_novo,
        email=email,
        senha_hash=generate_password_hash(senha),
        nivel=nivel,
    )
    db.session.add(novo_operador)
    registrar_atividade("operador_criado", f"Criou o operador {nome} ({login_novo})")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        erros.append("Já existe um operador com esse login.")
        return render_template("novo_operador.html", erros=erros, valores=form)

    return redirect(url_for("operadores"))


@app.route("/operadores/<int:operador_id>/editar", methods=["GET", "POST"])
@admin_required
def operador_editar(operador_id):
    operador = db.session.get(Operador, operador_id)
    if operador is None:
        abort(404)

    if request.method == "GET":
        return render_template("operador_editar.html", operador=operador)

    form = request.form
    nome = form.get("nome", "").strip()
    login_novo = form.get("login", "").strip()
    email = form.get("email", "").strip()
    senha = form.get("senha", "")
    confirmar_senha = form.get("confirmar_senha", "")
    nivel = form.get("nivel", "comum")
    if nivel not in ("administrador", "comum"):
        nivel = "comum"

    erros = []
    if not nome:
        erros.append("Informe o nome.")
    if not login_novo:
        erros.append("Informe o login.")
    if not email:
        erros.append("Informe o e-mail.")
    # A senha é opcional na edição - só troca se o campo for preenchido.
    if senha and senha != confirmar_senha:
        erros.append("A senha e a confirmação de senha não coincidem.")

    login_em_uso = Operador.query.filter(
        func.lower(Operador.login) == login_novo.lower(),
        Operador.id != operador.id,
    ).first()
    if login_novo and login_em_uso:
        erros.append("Já existe outro operador com esse login.")

    if erros:
        return render_template("operador_editar.html", operador=operador, erros=erros, valores=form)

    operador.nome = nome
    operador.login = login_novo
    operador.email = email
    operador.nivel = nivel
    if senha:
        operador.senha_hash = generate_password_hash(senha)

    try:
        registrar_atividade("operador_editado", f"Editou o operador {operador.nome} ({operador.login})")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        erros.append("Já existe outro operador com esse login.")
        return render_template("operador_editar.html", operador=operador, erros=erros, valores=form)

    return redirect(url_for("operadores"))


@app.route("/operadores/<int:operador_id>/excluir", methods=["POST"])
@admin_required
def operador_excluir(operador_id):
    operador = db.session.get(Operador, operador_id)
    if operador is None:
        abort(404)

    # Não deixa o administrador excluir a própria conta enquanto está
    # logado com ela - evitaria ele mesmo se trancar fora do sistema.
    if operador.id == current_user.id:
        erros = ["Você não pode excluir o próprio usuário enquanto estiver logado com ele."]
        return render_template("operador_editar.html", operador=operador, erros=erros)

    registrar_atividade("operador_excluido", f"Excluiu o operador {operador.nome} ({operador.login})")
    db.session.delete(operador)
    db.session.commit()
    return redirect(url_for("operadores"))


@app.route("/atividades")
@admin_required
def atividades():
    registros = (
        RegistroAtividade.query
        .order_by(RegistroAtividade.data_hora.desc())
        .limit(300)
        .all()
    )
    return render_template("atividades.html", registros=registros)


@app.route("/atividades/<int:registro_id>/excluir", methods=["POST"])
@admin_required
def atividade_excluir(registro_id):
    """Exclui uma única linha do registro de atividades."""
    registro = db.session.get(RegistroAtividade, registro_id)
    if registro is None:
        abort(404)
    db.session.delete(registro)
    db.session.commit()
    return redirect(url_for("atividades"))


@app.route("/atividades/excluir-todas", methods=["POST"])
@admin_required
def atividades_excluir_todas():
    """Apaga todo o registro de atividades. Para não perder o rastro de quem
    fez a limpeza, fica gravada uma única linha informando a exclusão."""
    RegistroAtividade.query.delete(synchronize_session=False)
    registrar_atividade("atividades_excluidas", "Excluiu todo o registro de atividades")
    db.session.commit()
    return redirect(url_for("atividades"))


def texto_para_data(valor):
    """Converte string 'AAAA-MM-DD' do formulário em objeto date. Retorna None se vazio."""
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def texto_para_numero(valor):
    """Converte string do formulário em número decimal. Retorna None se vazio."""
    if not valor:
        return None
    return float(valor)


def preencher_campos_processo(processo, form):
    """Preenche (ou atualiza) os campos comuns do processo a partir do
    formulário. Usado tanto no cadastro novo quanto na edição."""
    processo.numero_processo = form.get("numero_processo")
    processo.juizado = form.get("juizado")
    processo.comarca = form.get("comarca")
    processo.uf = form.get("uf")
    processo.tipo_acao = form.get("tipo_acao")
    processo.data_distribuicao = texto_para_data(form.get("data_distribuicao"))
    processo.valor_causa = texto_para_numero(form.get("valor_causa"))
    # Aceita os nomes novos dos campos do formulário e, por enquanto, também
    # os antigos (data_audiencia_1 etc.), para o cadastro não quebrar enquanto
    # os templates não forem atualizados.
    def campo(novo, antigo):
        return form.get(novo) or form.get(antigo)

    processo.proxima_audiencia = texto_para_data(campo("proxima_audiencia", "data_audiencia_1"))
    processo.proxima_audiencia_horario = campo("proxima_audiencia_horario", "audiencia_1_horario") or None
    processo.proxima_audiencia_tipo = campo("proxima_audiencia_tipo", "audiencia_1_tipo") or None
    processo.proxima_audiencia_link = campo("proxima_audiencia_link", "audiencia_1_link") or None
    processo.ultima_audiencia = texto_para_data(campo("ultima_audiencia", "data_audiencia_2"))
    processo.audiencia_inicial = texto_para_data(campo("audiencia_inicial", "data_audiencia_3"))
    processo.data_arquivamento = texto_para_data(form.get("data_arquivamento"))
    processo.centro_resultado = form.get("centro_resultado")
    processo.escritorio = form.get("escritorio")
    processo.resultado = form.get("resultado")
    processo.sentenca = form.get("sentenca")
    processo.status = form.get("status", "ativo")
    processo.risco = form.get("risco")
    processo.grau_instancia = form.get("grau_instancia")
    processo.resumo = form.get("resumo")
    processo.valor_final = texto_para_numero(form.get("valor_final"))
    processo.honorarios_advogado = texto_para_numero(form.get("honorarios_advogado"))
    processo.honorarios_periciais = texto_para_numero(form.get("honorarios_periciais"))
    processo.custas_processuais = texto_para_numero(form.get("custas_processuais"))
    processo.deposito_recursal = texto_para_numero(form.get("deposito_recursal"))
    processo.valor_alvara = texto_para_numero(form.get("valor_alvara"))
    processo.data_alvara = texto_para_data(form.get("data_alvara"))
    processo.economia_gerada = texto_para_numero(form.get("economia_gerada"))


def montar_processo_base(form):
    """Cria um objeto Processo novo com os campos comuns já preenchidos."""
    processo = Processo()
    preencher_campos_processo(processo, form)
    return processo


def preencher_partes_e_advogados(processo, form, incluir_partes=True):
    """Preenche partes, advogados, movimentos e rateio - comum a todas as páginas.

    O rótulo salvo em Parte.tipo depende da origem do cadastro: processos
    trabalhistas usam 'reclamante'/'reclamada'; os demais (cível) usam
    'autor'/'reu'.

    incluir_partes=False é usado na edição: o reclamante/autor e a
    reclamada/réu não podem ser alterados depois do cadastro, então as
    partes existentes do processo são preservadas e o conteúdo enviado
    pelo formulário para autor_nome/reu_nome é ignorado."""
    if incluir_partes:
        if processo.origem_cadastro == "trabalhista":
            tipo_polo_ativo, tipo_polo_passivo = "reclamante", "reclamada"
        else:
            tipo_polo_ativo, tipo_polo_passivo = "autor", "reu"

        for nome in form.getlist("autor_nome"):
            if nome.strip():
                processo.partes.append(Parte(tipo=tipo_polo_ativo, nome=nome.strip()))
        for nome in form.getlist("reu_nome"):
            if nome.strip():
                processo.partes.append(Parte(tipo=tipo_polo_passivo, nome=nome.strip()))

    nomes_adv_autor = form.getlist("advogado_autor_nome")
    oabs_adv_autor = form.getlist("advogado_autor_oab")
    for nome, oab in zip(nomes_adv_autor, oabs_adv_autor):
        if nome.strip():
            processo.advogados.append(Advogado(lado="autor", nome=nome.strip(), oab=oab.strip()))

    nomes_adv_reu = form.getlist("advogado_reu_nome")
    oabs_adv_reu = form.getlist("advogado_reu_oab")
    for nome, oab in zip(nomes_adv_reu, oabs_adv_reu):
        if nome.strip():
            processo.advogados.append(Advogado(lado="reu", nome=nome.strip(), oab=oab.strip()))

    datas_mov = form.getlist("movimento_data")
    tipos_mov = form.getlist("movimento_tipo")
    for data_str, tipo in zip(datas_mov, tipos_mov):
        if tipo.strip():
            processo.movimentos.append(
                Movimento(data_movimento=texto_para_data(data_str), tipo_movimento=tipo.strip())
            )

    if form.get("centro_resultado") == "rateio":
        crs_marcados = form.getlist("rateio_cr")
        crs_digitados = form.getlist("rateio_cr_manual")
        for cr in crs_marcados + crs_digitados:
            if cr.strip():
                processo.rateio_crs.append(RateioCR(centro_resultado=cr.strip()))


@app.context_processor
def injetar_contadores_menu():
    """Disponibiliza as contagens de processos ativos (cível e trabalhista)
    para o menu lateral, em todas as páginas, sem precisar passar isso
    manualmente em cada rota."""
    return dict(
        contagem_civel_ativos=Processo.query.filter_by(origem_cadastro="civel", status="ativo").count(),
        contagem_trabalhista_ativos=Processo.query.filter_by(origem_cadastro="trabalhista", status="ativo").count(),
        contagem_recuperacao_ativos=Processo.query.filter_by(origem_cadastro="civel_recuperacao", status="ativo").count(),
    )


@app.route("/")
@login_required
def index():
    return render_template("inicio.html")


def numero_processo_ja_existe(numero_processo):
    """Verifica se já existe algum processo (cível ou trabalhista) cadastrado
    com esse número. Usado para bloquear cadastros duplicados."""
    return Processo.query.filter_by(numero_processo=numero_processo).first() is not None


def campos_obrigatorios_faltando(form, trabalhista=False):
    """Confere os campos que nenhum cadastro pode ficar sem: número do
    processo e pelo menos um nome de cada lado. Devolve a lista dos que
    faltam (vazia = pode salvar).

    O formulário sempre manda os nomes em autor_nome/reu_nome, mesmo no
    trabalhista - só os rótulos mostrados ao operador mudam.

    Essa checagem repete a do JavaScript de propósito: o navegador pode
    estar com script desativado, ou o POST pode vir de fora da tela."""
    rotulo_autor = "Reclamante" if trabalhista else "Autor"
    rotulo_reu = "Reclamada" if trabalhista else "Réu"

    faltando = []
    if not (form.get("numero_processo") or "").strip():
        faltando.append("Número do processo")
    if not any(nome.strip() for nome in form.getlist("autor_nome")):
        faltando.append(rotulo_autor)
    if not any(nome.strip() for nome in form.getlist("reu_nome")):
        faltando.append(rotulo_reu)
    return faltando


@app.route("/inicio")
@login_required
def inicio():
    return render_template("inicio.html")


@app.route("/cadastro/civel", methods=["GET", "POST"])
@login_required
def cadastro_civel():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_civel.html", sucesso=sucesso)

    form = request.form
    numero_processo = form.get("numero_processo")

    faltando = campos_obrigatorios_faltando(form)
    if faltando:
        return render_template("cadastro_civel.html", erro_campos_obrigatorios=faltando)

    if numero_processo_ja_existe(numero_processo):
        return render_template(
            "cadastro_civel.html",
            erro_numero_duplicado=numero_processo,
        )

    processo = montar_processo_base(form)
    processo.origem_cadastro = "civel"
    preencher_partes_e_advogados(processo, form)

    # Pedidos e requerimentos (cível) - checkboxes marcados
    for descricao in form.getlist("pedido_civel"):
        if descricao.strip():
            processo.pedidos_civeis.append(PedidoCivel(descricao=descricao.strip()))

    db.session.add(processo)
    registrar_atividade("processo_criado", f"Cadastrou o processo cível {numero_processo}")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("cadastro_civel.html", erro_numero_duplicado=numero_processo)

    return redirect(url_for("cadastro_civel", sucesso=1))


# ---------------------------------------------------------------------------
# Recuperação de Crédito - leitura da planilha de títulos
# ---------------------------------------------------------------------------
#
# A planilha é lida por uma rota separada (/recuperacao/ler-planilha), chamada
# por JavaScript. Ela devolve as linhas em JSON e o navegador monta a tabela
# editável na tela. Assim o operador pode corrigir qualquer célula antes de
# salvar, e a página do cadastro não recarrega (não se perde o que já foi
# digitado nas outras seções).


class PlanilhaErro(Exception):
    """Erro de leitura que já tem uma mensagem pronta para mostrar ao operador."""


# Nomes de coluna aceitos para cada campo. A comparação ignora acentos,
# maiúsculas e espaços sobrando, então "Correção", "correcao" e "CORREÇÃO "
# caem todos no mesmo lugar.
COLUNAS_RECUPERACAO = {
    "company": ["company", "companhia", "empresa", "filial"],
    "cliente": ["cliente", "sacado", "devedor", "razao social", "id cliente"],
    "nota_fiscal": ["nota fiscal", "notafiscal", "nf", "nfe", "nf-e",
                    "numero da nota", "numero nf", "no nf", "documento", "titulo"],
    "emissao": ["emissao", "data emissao", "data de emissao", "dt emissao"],
    "vencimento": ["vencimento", "data vencimento", "data de vencimento", "dt vencimento"],
    "valor": ["valor", "valor original", "valor da nota", "valor nf", "valor principal"],
    "saldo": ["saldo", "saldo devedor", "saldo em aberto"],
    "juros": ["juros", "juros de mora", "mora"],
    "correcao": ["correcao", "correcao monetaria", "atualizacao", "atualizacao monetaria"],
    "outros": ["outros", "outras despesas", "despesas", "acrescimos"],
    "total": ["total", "valor total", "total geral", "total atualizado"],
}

CAMPOS_NUMERICOS_RECUPERACAO = ["valor", "saldo", "juros", "correcao", "outros", "total"]
CAMPOS_DATA_RECUPERACAO = ["emissao", "vencimento"]

# Planilha de acordo/recebimento (parcelas), preenchida aos poucos ao longo
# do processo - mesma lógica de importação da planilha de títulos acima.
COLUNAS_ACORDO = {
    "parcela": ["parcela", "parcelas", "n parcela", "numero parcela", "num parcela"],
    "vencimento": ["vencimento", "data vencimento", "data de vencimento", "dt vencimento"],
    "valor_parcela": ["valor da parcela", "valor parcela"],
    "honorarios_exito": ["honorarios exito", "honorarios de exito", "honorario exito",
                          "honorarios de exito adv"],
    "sucumbencia": ["sucumbencia", "honorarios sucumbencia", "honorarios de sucumbencia"],
    "valor_recebido": ["valor recebido"],
    "data_recebimento": ["data recebimento", "data do recebimento", "dt recebimento"],
    "valor_pago_adv": ["valor pago adv", "valor pago advogado", "valor pg adv"],
    "data_pagto_adv": ["data pagto adv", "data pagamento adv", "data pagamento advogado"],
}

CAMPOS_NUMERICOS_ACORDO = [
    "valor_parcela", "honorarios_exito", "sucumbencia", "valor_recebido", "valor_pago_adv",
]
CAMPOS_DATA_ACORDO = ["vencimento", "data_recebimento", "data_pagto_adv"]

EXTENSOES_PLANILHA = (
    ".xlsx", ".xlsm", ".xltx", ".xltm",   # Excel moderno (openpyxl)
    ".xls",                                # Excel antigo (xlrd)
    ".csv", ".txt", ".tsv",                # texto separado (módulo csv)
    ".ods",                                # LibreOffice / OpenOffice (odfpy)
)


def normalizar_rotulo(texto):
    """Deixa um nome de coluna comparável: sem acento, minúsculo, sem
    pontuação e sem espaços repetidos."""
    if texto is None:
        return ""
    texto = str(texto)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower().replace("º", "").replace("°", "")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return texto.strip()


def valor_para_numero_planilha(valor):
    """Converte o conteúdo de uma célula em número (float) ou None.

    Aceita número puro vindo do Excel, e também texto em formato brasileiro
    ('1.234,56', 'R$ 1.234,56', '(1.234,56)' para negativo) ou americano
    ('1,234.56')."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)

    texto = str(valor).strip()
    if not texto:
        return None

    negativo = texto.startswith("(") and texto.endswith(")")
    texto = texto.strip("()")
    texto = re.sub(r"[^\d,.\-]", "", texto)  # tira "R$", espaços, etc.
    if not texto or texto in {"-", ".", ","}:
        return None

    if "," in texto and "." in texto:
        # O separador decimal é o que aparece por último.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    else:
        # Só pontos: se houver mais de um, ou se o último grupo tiver 3
        # dígitos, é separador de milhar (1.234 / 1.234.567).
        partes = texto.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            texto = "".join(partes)

    try:
        numero = float(texto)
    except ValueError:
        return None
    return -numero if negativo else numero


def valor_para_data_planilha(valor):
    """Converte o conteúdo de uma célula em date, ou None se não der.

    Aceita datetime/date vindos do Excel, texto em vários formatos e também
    o número serial de data do Excel (ex.: 45000)."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    # Número serial do Excel (contado a partir de 30/12/1899).
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        try:
            from datetime import timedelta
            return (datetime(1899, 12, 30) + timedelta(days=float(valor))).date()
        except (ValueError, OverflowError):
            return None

    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.split(" ")[0].split("T")[0]  # descarta a hora, se vier junto

    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y",
                    "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _linhas_xlsx(stream):
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise PlanilhaErro(
            "A biblioteca openpyxl não está instalada no servidor. "
            "Rode: pip3.10 install --user openpyxl"
        )
    # data_only=True traz o resultado das fórmulas, não a fórmula em si.
    planilha = load_workbook(stream, data_only=True, read_only=True)
    aba = planilha.active
    return [list(linha) for linha in aba.iter_rows(values_only=True)]


def _linhas_xls(conteudo):
    try:
        import xlrd
    except ImportError:
        raise PlanilhaErro(
            "Este é um Excel antigo (.xls) e a biblioteca xlrd não está "
            "instalada no servidor. Rode: pip3.10 install --user xlrd  "
            "(ou salve a planilha como .xlsx e anexe de novo)."
        )
    livro = xlrd.open_workbook(file_contents=conteudo)
    aba = livro.sheet_by_index(0)
    linhas = []
    for indice in range(aba.nrows):
        celulas = []
        for celula in aba.row(indice):
            if celula.ctype == xlrd.XL_CELL_DATE:
                ano, mes, dia, *_ = xlrd.xldate_as_tuple(celula.value, livro.datemode)
                celulas.append(date(ano, mes, dia) if ano else None)
            else:
                celulas.append(celula.value)
        linhas.append(celulas)
    return linhas


def _linhas_csv(conteudo):
    texto = None
    for codificacao in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise PlanilhaErro("Não foi possível identificar a codificação do arquivo de texto.")

    amostra = texto[:4000]
    try:
        separador = csv.Sniffer().sniff(amostra, delimiters=";,\t|").delimiter
    except csv.Error:
        # Chute razoável: no Brasil o Excel exporta CSV com ponto e vírgula.
        separador = ";" if amostra.count(";") >= amostra.count(",") else ","
    return [linha for linha in csv.reader(io.StringIO(texto), delimiter=separador)]


def _linhas_ods(stream):
    try:
        from odf.opendocument import load
        from odf.table import Table, TableRow, TableCell
        from odf.text import P
    except ImportError:
        raise PlanilhaErro(
            "Este arquivo é .ods e a biblioteca odfpy não está instalada no "
            "servidor. Rode: pip3.10 install --user odfpy  (ou salve a "
            "planilha como .xlsx e anexe de novo)."
        )
    documento = load(stream)
    tabelas = documento.spreadsheet.getElementsByType(Table)
    if not tabelas:
        raise PlanilhaErro("O arquivo .ods não tem nenhuma aba com dados.")

    linhas = []
    for linha_ods in tabelas[0].getElementsByType(TableRow):
        celulas = []
        for celula in linha_ods.getElementsByType(TableCell):
            repeticoes = int(celula.getAttribute("numbercolumnsrepeated") or 1)
            bruto = (celula.getAttribute("value")
                     or celula.getAttribute("datevalue")
                     or "".join(str(paragrafo) for paragrafo in celula.getElementsByType(P)))
            celulas.extend([bruto or None] * min(repeticoes, 60))
        linhas.append(celulas)
    return linhas


def ler_linhas_brutas(arquivo):
    """Abre o arquivo enviado e devolve uma lista de listas com o conteúdo
    cru das células, escolhendo o leitor certo pela extensão."""
    nome = (arquivo.filename or "").strip()
    extensao = os.path.splitext(nome)[1].lower()

    if extensao not in EXTENSOES_PLANILHA:
        raise PlanilhaErro(
            f"Formato não suportado ({extensao or 'sem extensão'}). "
            "Aceitos: " + ", ".join(EXTENSOES_PLANILHA)
        )

    conteudo = arquivo.read()
    if not conteudo:
        raise PlanilhaErro("O arquivo enviado está vazio.")

    if extensao in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return _linhas_xlsx(io.BytesIO(conteudo))
    if extensao == ".xls":
        return _linhas_xls(conteudo)
    if extensao == ".ods":
        return _linhas_ods(io.BytesIO(conteudo))
    return _linhas_csv(conteudo)


def localizar_cabecalho(linhas, colunas_aceitas, exemplo_colunas):
    """Procura a linha de cabeçalho e devolve (índice da linha, mapa de
    coluna -> posição). Percorre as 15 primeiras linhas porque muita planilha
    vem com título, logotipo ou linhas em branco antes da tabela.

    colunas_aceitas: dict campo -> lista de rótulos aceitos (ex.: COLUNAS_RECUPERACAO).
    exemplo_colunas: texto com os nomes de coluna esperados, usado só na
    mensagem de erro quando o cabeçalho não é encontrado."""
    melhor_indice = None
    melhor_mapa = {}

    for indice, linha in enumerate(linhas[:15]):
        rotulos = [normalizar_rotulo(celula) for celula in linha]
        mapa = {}
        for campo, aceitos in colunas_aceitas.items():
            for posicao, rotulo in enumerate(rotulos):
                if not rotulo or posicao in mapa.values():
                    continue
                if rotulo in aceitos:
                    mapa[campo] = posicao
                    break
        if len(mapa) > len(melhor_mapa):
            melhor_indice, melhor_mapa = indice, mapa

    # Com menos de 3 colunas reconhecidas provavelmente não achamos a tabela.
    if melhor_indice is None or len(melhor_mapa) < 3:
        raise PlanilhaErro(
            "Não encontrei a linha de cabeçalho na planilha. Ela precisa ter "
            "uma linha com os nomes das colunas (" + exemplo_colunas + ")."
        )
    return melhor_indice, melhor_mapa


def ler_planilha_recuperacao(arquivo):
    """Lê a planilha de títulos e devolve (linhas, avisos, colunas_faltando).

    Cada linha é um dicionário pronto para virar JSON: textos como string,
    datas em 'AAAA-MM-DD' (formato que o <input type=date> entende) e números
    como float. Nada é gravado no banco aqui - isso só acontece quando o
    operador salva o processo."""
    linhas_brutas = ler_linhas_brutas(arquivo)
    if not linhas_brutas:
        raise PlanilhaErro("A planilha não tem nenhuma linha.")

    indice_cabecalho, mapa = localizar_cabecalho(
        linhas_brutas, COLUNAS_RECUPERACAO,
        "Company, Cliente, Nota Fiscal, Emissão, Vencimento, Valor, Saldo, "
        "Juros, Correção, Outros, Total",
    )
    colunas_faltando = [campo for campo in COLUNAS_RECUPERACAO if campo not in mapa]

    linhas = []
    avisos = []

    for deslocamento, linha in enumerate(linhas_brutas[indice_cabecalho + 1:], start=1):
        numero_linha = indice_cabecalho + 1 + deslocamento  # como o operador vê no Excel

        # Pula linhas totalmente vazias.
        if not any(str(celula).strip() for celula in linha if celula is not None):
            continue

        registro = {}
        for campo, posicao in mapa.items():
            registro[campo] = linha[posicao] if posicao < len(linha) else None

        # Pula a linha de totais que costuma vir no rodapé da planilha
        # (sem Company/Cliente/NF, só com números).
        identificacao = " ".join(
            str(registro.get(campo) or "") for campo in ("company", "cliente", "nota_fiscal")
        ).strip()
        if not identificacao:
            continue
        if normalizar_rotulo(identificacao) in {"total", "total geral", "totais", "soma"}:
            continue

        saida = {"status": "em_analise"}
        for campo in ("company", "cliente", "nota_fiscal"):
            bruto = registro.get(campo)
            if isinstance(bruto, float) and bruto.is_integer():
                bruto = int(bruto)  # nota fiscal 1234.0 vira 1234
            saida[campo] = str(bruto).strip() if bruto is not None else ""

        for campo in CAMPOS_DATA_RECUPERACAO:
            bruto = registro.get(campo)
            convertida = valor_para_data_planilha(bruto)
            saida[campo] = convertida.isoformat() if convertida else ""
            if convertida is None and bruto not in (None, ""):
                avisos.append(
                    f"Linha {numero_linha}: não consegui entender a data de "
                    f"{'emissão' if campo == 'emissao' else 'vencimento'} "
                    f"(\"{str(bruto).strip()}\") - preencha na tela."
                )

        for campo in CAMPOS_NUMERICOS_RECUPERACAO:
            saida[campo] = valor_para_numero_planilha(registro.get(campo))

        linhas.append(saida)

    if not linhas:
        raise PlanilhaErro(
            "Encontrei o cabeçalho, mas nenhuma linha de título abaixo dele."
        )

    # Só os 12 primeiros avisos, para não virar uma parede de texto.
    if len(avisos) > 12:
        restantes = len(avisos) - 12
        avisos = avisos[:12] + [f"...e mais {restantes} aviso(s) do mesmo tipo."]

    return linhas, avisos, colunas_faltando


@app.route("/recuperacao/ler-planilha", methods=["POST"])
@login_required
def recuperacao_ler_planilha():
    """Recebe só o arquivo da planilha (via JavaScript), devolve as linhas em
    JSON. Não grava nada - quem grava é o submit do formulário."""
    arquivo = request.files.get("planilha")
    if not arquivo or not arquivo.filename:
        return jsonify({"ok": False, "erro": "Nenhum arquivo foi selecionado."}), 400

    try:
        linhas, avisos, colunas_faltando = ler_planilha_recuperacao(arquivo)
    except PlanilhaErro as erro:
        return jsonify({"ok": False, "erro": str(erro)}), 400
    except Exception:
        app.logger.exception("Falha ao ler planilha de recuperação de crédito")
        return jsonify({
            "ok": False,
            "erro": "Não consegui ler este arquivo. Confira se ele abre normalmente "
                    "no Excel e se a primeira aba é a que tem a tabela de títulos.",
        }), 400

    if colunas_faltando:
        nomes = {
            "company": "Company", "cliente": "Cliente", "nota_fiscal": "Nota Fiscal",
            "emissao": "Emissão", "vencimento": "Vencimento", "valor": "Valor",
            "saldo": "Saldo", "juros": "Juros", "correcao": "Correção",
            "outros": "Outros", "total": "Total",
        }
        avisos.insert(0, "Colunas não encontradas na planilha (ficaram em branco): "
                         + ", ".join(nomes[campo] for campo in colunas_faltando) + ".")

    return jsonify({"ok": True, "linhas": linhas, "avisos": avisos})


def ler_planilha_acordo(arquivo):
    """Lê a planilha de acordo/recebimento (parcelas) e devolve (linhas,
    avisos, colunas_faltando). Mesma lógica de ler_planilha_recuperacao,
    só que com o mapa de colunas do acordo."""
    linhas_brutas = ler_linhas_brutas(arquivo)
    if not linhas_brutas:
        raise PlanilhaErro("A planilha não tem nenhuma linha.")

    indice_cabecalho, mapa = localizar_cabecalho(
        linhas_brutas, COLUNAS_ACORDO,
        "Parcela, Vencimento, Valor da Parcela, Honorários Êxito, "
        "Sucumbência, Valor Recebido, Data Recebimento, Valor Pago Adv, "
        "Data Pagto Adv",
    )
    colunas_faltando = [campo for campo in COLUNAS_ACORDO if campo not in mapa]

    linhas = []
    avisos = []

    for deslocamento, linha in enumerate(linhas_brutas[indice_cabecalho + 1:], start=1):
        numero_linha = indice_cabecalho + 1 + deslocamento  # como o operador vê no Excel

        # Pula linhas totalmente vazias.
        if not any(str(celula).strip() for celula in linha if celula is not None):
            continue

        registro = {}
        for campo, posicao in mapa.items():
            registro[campo] = linha[posicao] if posicao < len(linha) else None

        # Pula a linha de totais que costuma vir no rodapé da planilha
        # (sem Parcela/Vencimento, só com números).
        identificacao = " ".join(
            str(registro.get(campo) or "") for campo in ("parcela", "vencimento")
        ).strip()
        if not identificacao:
            continue
        if normalizar_rotulo(identificacao) in {"total", "total geral", "totais", "soma"}:
            continue

        saida = {}
        bruto_parcela = registro.get("parcela")
        if isinstance(bruto_parcela, float) and bruto_parcela.is_integer():
            bruto_parcela = int(bruto_parcela)  # parcela 1.0 vira 1
        saida["parcela"] = str(bruto_parcela).strip() if bruto_parcela is not None else ""

        for campo in CAMPOS_DATA_ACORDO:
            bruto = registro.get(campo)
            convertida = valor_para_data_planilha(bruto)
            saida[campo] = convertida.isoformat() if convertida else ""
            if convertida is None and bruto not in (None, "") and campo == "vencimento":
                avisos.append(
                    f"Linha {numero_linha}: não consegui entender a data de "
                    f"vencimento (\"{str(bruto).strip()}\") - preencha na tela."
                )

        for campo in CAMPOS_NUMERICOS_ACORDO:
            saida[campo] = valor_para_numero_planilha(registro.get(campo))

        linhas.append(saida)

    if not linhas:
        raise PlanilhaErro(
            "Encontrei o cabeçalho, mas nenhuma linha de parcela abaixo dele."
        )

    if len(avisos) > 12:
        restantes = len(avisos) - 12
        avisos = avisos[:12] + [f"...e mais {restantes} aviso(s) do mesmo tipo."]

    return linhas, avisos, colunas_faltando


@app.route("/acordo/ler-planilha", methods=["POST"])
@login_required
def acordo_ler_planilha():
    """Igual a /recuperacao/ler-planilha, mas para a planilha de parcelas
    do acordo/recebimento."""
    arquivo = request.files.get("planilha")
    if not arquivo or not arquivo.filename:
        return jsonify({"ok": False, "erro": "Nenhum arquivo foi selecionado."}), 400

    try:
        linhas, avisos, colunas_faltando = ler_planilha_acordo(arquivo)
    except PlanilhaErro as erro:
        return jsonify({"ok": False, "erro": str(erro)}), 400
    except Exception:
        app.logger.exception("Falha ao ler planilha de acordo/recebimento")
        return jsonify({
            "ok": False,
            "erro": "Não consegui ler este arquivo. Confira se ele abre normalmente "
                    "no Excel e se a primeira aba é a que tem a tabela de parcelas.",
        }), 400

    if colunas_faltando:
        nomes = {
            "parcela": "Parcela", "vencimento": "Vencimento",
            "valor_parcela": "Valor da Parcela", "honorarios_exito": "Honorários Êxito",
            "sucumbencia": "Sucumbência", "valor_recebido": "Valor Recebido",
            "data_recebimento": "Data Recebimento", "valor_pago_adv": "Valor Pago Adv",
            "data_pagto_adv": "Data Pagto Adv",
        }
        avisos.insert(0, "Colunas não encontradas na planilha (ficaram em branco): "
                         + ", ".join(nomes[campo] for campo in colunas_faltando) + ".")

    return jsonify({"ok": True, "linhas": linhas, "avisos": avisos})


def preencher_acordo_recebimento(processo, form):
    """Lê os campos repetidos da tabela de parcelas do acordo (pareados pela
    posição, igual à tabela de títulos) e monta os objetos AcordoRecebimento."""
    colunas = {
        "parcela": form.getlist("acordo_parcela"),
        "vencimento": form.getlist("acordo_vencimento"),
        "valor_parcela": form.getlist("acordo_valor_parcela"),
        "honorarios_exito": form.getlist("acordo_honorarios_exito"),
        "sucumbencia": form.getlist("acordo_sucumbencia"),
        "valor_recebido": form.getlist("acordo_valor_recebido"),
        "data_recebimento": form.getlist("acordo_data_recebimento"),
        "valor_pago_adv": form.getlist("acordo_valor_pago_adv"),
        "data_pagto_adv": form.getlist("acordo_data_pagto_adv"),
        "situacao": form.getlist("acordo_situacao"),
    }
    quantidade = max((len(lista) for lista in colunas.values()), default=0)

    def pegar(campo, indice):
        lista = colunas[campo]
        return lista[indice].strip() if indice < len(lista) and lista[indice] else ""

    for indice in range(quantidade):
        parcela = pegar("parcela", indice)
        vencimento = pegar("vencimento", indice)
        numeros = [valor_para_numero_planilha(pegar(campo, indice))
                   for campo in CAMPOS_NUMERICOS_ACORDO]
        data_recebimento = pegar("data_recebimento", indice)
        data_pagto_adv = pegar("data_pagto_adv", indice)

        # Situação escolhida na tela ('pendente' ou 'pago'). Se por algum
        # motivo não vier, deduz pelo Valor Recebido.
        situacao = pegar("situacao", indice)
        if situacao not in ("pendente", "parcial", "pago"):
            situacao = "pago" if numeros[3] else "pendente"

        # Linha completamente em branco: ignora.
        if not (parcela or vencimento or data_recebimento or data_pagto_adv) \
                and not any(n is not None for n in numeros):
            continue

        processo.acordos_recebimento.append(AcordoRecebimento(
            parcela=parcela or None,
            vencimento=valor_para_data_planilha(vencimento),
            valor_parcela=numeros[0],
            honorarios_exito=numeros[1],
            sucumbencia=numeros[2],
            valor_recebido=numeros[3],
            data_recebimento=valor_para_data_planilha(data_recebimento),
            valor_pago_adv=numeros[4],
            data_pagto_adv=valor_para_data_planilha(data_pagto_adv),
            situacao=situacao,
            ordem=indice,
        ))


def preencher_titulos_recuperacao(processo, form):
    """Lê os campos repetidos da tabela de títulos (pareados pela posição,
    igual aos pedidos/verbas do trabalhista) e monta os objetos
    TituloRecuperacao."""
    colunas = {
        "company": form.getlist("rec_company"),
        "cliente": form.getlist("rec_cliente"),
        "nota_fiscal": form.getlist("rec_nota_fiscal"),
        "emissao": form.getlist("rec_emissao"),
        "vencimento": form.getlist("rec_vencimento"),
        "valor": form.getlist("rec_valor"),
        "saldo": form.getlist("rec_saldo"),
        "juros": form.getlist("rec_juros"),
        "correcao": form.getlist("rec_correcao"),
        "outros": form.getlist("rec_outros"),
        "total": form.getlist("rec_total"),
        "status": form.getlist("rec_status"),
    }
    quantidade = max((len(lista) for lista in colunas.values()), default=0)

    def pegar(campo, indice):
        lista = colunas[campo]
        return lista[indice].strip() if indice < len(lista) and lista[indice] else ""

    for indice in range(quantidade):
        textos = [pegar(campo, indice) for campo in
                  ("company", "cliente", "nota_fiscal", "emissao", "vencimento")]
        numeros = [valor_para_numero_planilha(pegar(campo, indice))
                   for campo in CAMPOS_NUMERICOS_RECUPERACAO]

        # Linha completamente em branco: ignora.
        if not any(textos) and not any(n is not None for n in numeros):
            continue

        processo.titulos_recuperacao.append(TituloRecuperacao(
            company=textos[0] or None,
            cliente=textos[1] or None,
            nota_fiscal=textos[2] or None,
            emissao=valor_para_data_planilha(textos[3]),
            vencimento=valor_para_data_planilha(textos[4]),
            valor=numeros[0],
            saldo=numeros[1],
            juros=numeros[2],
            correcao=numeros[3],
            outros=numeros[4],
            total=numeros[5],
            status=pegar("status", indice) or "em_analise",
            ordem=indice,
        ))


@app.route("/cadastro/civel-recuperacao", methods=["GET", "POST"])
@login_required
def cadastro_civel_recuperacao():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_civel_recuperacao.html", sucesso=sucesso)

    form = request.form
    numero_processo = form.get("numero_processo")

    faltando = campos_obrigatorios_faltando(form)
    if faltando:
        return render_template("cadastro_civel_recuperacao.html", erro_campos_obrigatorios=faltando)

    if numero_processo_ja_existe(numero_processo):
        return render_template(
            "cadastro_civel_recuperacao.html",
            erro_numero_duplicado=numero_processo,
        )

    processo = montar_processo_base(form)
    processo.origem_cadastro = "civel_recuperacao"
    preencher_partes_e_advogados(processo, form)

    # Títulos em recuperação (linhas da tabela editável)
    preencher_titulos_recuperacao(processo, form)
    # Parcelas do acordo/recebimento (linhas da tabela editável)
    preencher_acordo_recebimento(processo, form)

    db.session.add(processo)
    quantidade_titulos = len(processo.titulos_recuperacao)
    registrar_atividade(
        "processo_criado",
        f"Cadastrou o processo cível - recuperação de crédito {numero_processo}"
        + (f" ({quantidade_titulos} título(s))" if quantidade_titulos else "")
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("cadastro_civel_recuperacao.html", erro_numero_duplicado=numero_processo)

    return redirect(url_for("cadastro_civel_recuperacao", sucesso=1))


@app.route("/cadastro/trabalhista", methods=["GET", "POST"])
@login_required
def cadastro_trabalhista():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_trabalhista.html", sucesso=sucesso)

    form = request.form
    numero_processo = form.get("numero_processo")

    faltando = campos_obrigatorios_faltando(form, trabalhista=True)
    if faltando:
        return render_template("cadastro_trabalhista.html", erro_campos_obrigatorios=faltando)

    if numero_processo_ja_existe(numero_processo):
        return render_template(
            "cadastro_trabalhista.html",
            erro_numero_duplicado=numero_processo,
        )

    processo = montar_processo_base(form)
    processo.origem_cadastro = "trabalhista"
    preencher_partes_e_advogados(processo, form)

    # Pedidos/verbas trabalhistas (verba + valor, pareados pela posição)
    verbas = form.getlist("pedido_verba")
    valores = form.getlist("pedido_valor")
    statuses = form.getlist("pedido_status")
    for verba, valor, status in zip(verbas, valores, statuses):
        if verba.strip():
            processo.pedidos_trabalhistas.append(
                PedidoTrabalhista(verba=verba.strip(), valor=texto_para_numero(valor), status=status or "em_analise")
            )

    db.session.add(processo)
    registrar_atividade("processo_criado", f"Cadastrou o processo trabalhista {numero_processo}")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("cadastro_trabalhista.html", erro_numero_duplicado=numero_processo)

    return redirect(url_for("cadastro_trabalhista", sucesso=1))


@app.route("/processos/civel")
@login_required
def processos_civel():
    query = Processo.query.filter_by(origem_cadastro="civel")
    f = request.args

    numero_processo = f.get("numero_processo", "").strip()
    parte = f.get("parte", "").strip()
    juizado = f.get("juizado", "").strip()
    comarca = f.get("comarca", "").strip()
    uf = f.get("uf", "").strip()
    tipo_acao = f.get("tipo_acao", "").strip()
    centro_resultado = f.get("centro_resultado", "").strip()
    escritorio = f.get("escritorio", "").strip()
    resultado = f.get("resultado", "").strip()
    status = f.get("status", "").strip()
    risco = f.get("risco", "").strip()
    grau_instancia = f.get("grau_instancia", "").strip()
    data_distribuicao_de = f.get("data_distribuicao_de", "").strip()
    data_distribuicao_ate = f.get("data_distribuicao_ate", "").strip()

    if numero_processo:
        query = query.filter(Processo.numero_processo.ilike(f"%{numero_processo}%"))
    if parte:
        query = query.filter(Processo.partes.any(Parte.nome.ilike(f"%{parte}%")))
    if juizado:
        query = query.filter(Processo.juizado.ilike(f"%{juizado}%"))
    if comarca:
        query = query.filter(Processo.comarca.ilike(f"%{comarca}%"))
    if uf:
        query = query.filter(Processo.uf == uf)
    if tipo_acao:
        query = query.filter(Processo.tipo_acao == tipo_acao)
    if centro_resultado:
        query = query.filter(Processo.centro_resultado == centro_resultado)
    if escritorio:
        query = query.filter(Processo.escritorio == escritorio)
    if resultado:
        query = query.filter(Processo.resultado == resultado)
    if status:
        query = query.filter(Processo.status == status)
    if risco:
        query = query.filter(Processo.risco == risco)
    if grau_instancia:
        query = query.filter(Processo.grau_instancia == grau_instancia)
    if data_distribuicao_de:
        data_de = texto_para_data(data_distribuicao_de)
        if data_de:
            query = query.filter(Processo.data_distribuicao >= data_de)
    if data_distribuicao_ate:
        data_ate = texto_para_data(data_distribuicao_ate)
        if data_ate:
            query = query.filter(Processo.data_distribuicao <= data_ate)

    processos = query.order_by(Processo.data_cadastro.desc()).all()

    campos_filtro = [
        "numero_processo", "parte", "juizado", "comarca", "uf", "tipo_acao",
        "centro_resultado", "escritorio", "resultado", "status",
        "risco", "grau_instancia",
        "data_distribuicao_de", "data_distribuicao_ate",
    ]
    filtros_ativos = any(f.get(c, "").strip() for c in campos_filtro)

    return render_template(
        "processos_civel.html",
        processos=processos,
        filtros=f,
        filtros_ativos=filtros_ativos,
    )


@app.route("/processos/trabalhista")
@login_required
def processos_trabalhista():
    query = Processo.query.filter_by(origem_cadastro="trabalhista")
    f = request.args

    numero_processo = f.get("numero_processo", "").strip()
    parte = f.get("parte", "").strip()
    juizado = f.get("juizado", "").strip()
    comarca = f.get("comarca", "").strip()
    uf = f.get("uf", "").strip()
    tipo_acao = f.get("tipo_acao", "").strip()
    centro_resultado = f.get("centro_resultado", "").strip()
    escritorio = f.get("escritorio", "").strip()
    resultado = f.get("resultado", "").strip()
    sentenca = f.get("sentenca", "").strip()
    status = f.get("status", "").strip()
    risco = f.get("risco", "").strip()
    grau_instancia = f.get("grau_instancia", "").strip()
    pedido_status = f.get("pedido_status", "").strip()
    data_distribuicao_de = f.get("data_distribuicao_de", "").strip()
    data_distribuicao_ate = f.get("data_distribuicao_ate", "").strip()

    if numero_processo:
        query = query.filter(Processo.numero_processo.ilike(f"%{numero_processo}%"))
    if parte:
        query = query.filter(Processo.partes.any(Parte.nome.ilike(f"%{parte}%")))
    if juizado:
        query = query.filter(Processo.juizado.ilike(f"%{juizado}%"))
    if comarca:
        query = query.filter(Processo.comarca.ilike(f"%{comarca}%"))
    if uf:
        query = query.filter(Processo.uf == uf)
    if tipo_acao:
        query = query.filter(Processo.tipo_acao == tipo_acao)
    if centro_resultado:
        query = query.filter(Processo.centro_resultado == centro_resultado)
    if escritorio:
        query = query.filter(Processo.escritorio == escritorio)
    if resultado:
        query = query.filter(Processo.resultado == resultado)
    if sentenca:
        query = query.filter(Processo.sentenca == sentenca)
    if status:
        query = query.filter(Processo.status == status)
    if risco:
        query = query.filter(Processo.risco == risco)
    if grau_instancia:
        query = query.filter(Processo.grau_instancia == grau_instancia)
    if pedido_status:
        query = query.filter(Processo.pedidos_trabalhistas.any(PedidoTrabalhista.status == pedido_status))
    if data_distribuicao_de:
        data_de = texto_para_data(data_distribuicao_de)
        if data_de:
            query = query.filter(Processo.data_distribuicao >= data_de)
    if data_distribuicao_ate:
        data_ate = texto_para_data(data_distribuicao_ate)
        if data_ate:
            query = query.filter(Processo.data_distribuicao <= data_ate)

    processos = query.order_by(Processo.data_cadastro.desc()).all()

    campos_filtro = [
        "numero_processo", "parte", "juizado", "comarca", "uf", "tipo_acao",
        "centro_resultado", "escritorio", "resultado", "sentenca", "status",
        "risco", "grau_instancia", "pedido_status",
        "data_distribuicao_de", "data_distribuicao_ate",
    ]
    filtros_ativos = any(f.get(c, "").strip() for c in campos_filtro)

    return render_template(
        "processos_trabalhista.html",
        processos=processos,
        filtros=f,
        filtros_ativos=filtros_ativos,
    )


@app.route("/processos/civel-recuperacao")
@login_required
def processos_civel_recuperacao():
    """Listagem dos processos de Recuperação de Crédito, com a mesma
    pesquisa processual da trabalhista + os filtros próprios dos títulos
    (Company, Cliente, Nota Fiscal, vencimento e situação)."""
    query = Processo.query.filter_by(origem_cadastro="civel_recuperacao")
    f = request.args

    def texto(chave):
        return f.get(chave, "").strip()

    numero_processo = texto("numero_processo")
    parte = texto("parte")
    company = texto("company")
    cliente = texto("cliente")
    nota_fiscal = texto("nota_fiscal")
    juizado = texto("juizado")
    comarca = texto("comarca")
    uf = texto("uf")
    tipo_acao = texto("tipo_acao")
    centro_resultado = texto("centro_resultado")
    escritorio = texto("escritorio")
    resultado = texto("resultado")
    status = texto("status")
    risco = texto("risco")
    grau_instancia = texto("grau_instancia")
    titulo_status = texto("titulo_status")
    data_distribuicao_de = texto("data_distribuicao_de")
    data_distribuicao_ate = texto("data_distribuicao_ate")
    vencimento_de = texto("vencimento_de")
    vencimento_ate = texto("vencimento_ate")

    if numero_processo:
        query = query.filter(Processo.numero_processo.ilike(f"%{numero_processo}%"))
    if parte:
        query = query.filter(Processo.partes.any(Parte.nome.ilike(f"%{parte}%")))
    if company:
        query = query.filter(Processo.titulos_recuperacao.any(
            TituloRecuperacao.company.ilike(f"%{company}%")))
    if cliente:
        query = query.filter(Processo.titulos_recuperacao.any(
            TituloRecuperacao.cliente.ilike(f"%{cliente}%")))
    if nota_fiscal:
        query = query.filter(Processo.titulos_recuperacao.any(
            TituloRecuperacao.nota_fiscal.ilike(f"%{nota_fiscal}%")))
    if juizado:
        query = query.filter(Processo.juizado.ilike(f"%{juizado}%"))
    if comarca:
        query = query.filter(Processo.comarca.ilike(f"%{comarca}%"))
    if uf:
        query = query.filter(Processo.uf == uf)
    if tipo_acao:
        query = query.filter(Processo.tipo_acao == tipo_acao)
    if centro_resultado:
        query = query.filter(Processo.centro_resultado == centro_resultado)
    if escritorio:
        query = query.filter(Processo.escritorio == escritorio)
    if resultado:
        query = query.filter(Processo.resultado == resultado)
    if status:
        query = query.filter(Processo.status == status)
    if risco:
        query = query.filter(Processo.risco == risco)
    if grau_instancia:
        query = query.filter(Processo.grau_instancia == grau_instancia)
    if titulo_status:
        query = query.filter(Processo.titulos_recuperacao.any(
            TituloRecuperacao.status == titulo_status))
    if data_distribuicao_de:
        data_de = texto_para_data(data_distribuicao_de)
        if data_de:
            query = query.filter(Processo.data_distribuicao >= data_de)
    if data_distribuicao_ate:
        data_ate = texto_para_data(data_distribuicao_ate)
        if data_ate:
            query = query.filter(Processo.data_distribuicao <= data_ate)
    if vencimento_de:
        data_de = texto_para_data(vencimento_de)
        if data_de:
            query = query.filter(Processo.titulos_recuperacao.any(
                TituloRecuperacao.vencimento >= data_de))
    if vencimento_ate:
        data_ate = texto_para_data(vencimento_ate)
        if data_ate:
            query = query.filter(Processo.titulos_recuperacao.any(
                TituloRecuperacao.vencimento <= data_ate))

    # Filtro por lista de ids - usado pelos links do Panorama de Recuperação
    # (ex.: processos sem movimentação, parcelas sem valor informado).
    ids_param = texto("ids")
    if ids_param:
        ids_lista = [int(x) for x in ids_param.split(",") if x.strip().isdigit()]
        query = query.filter(Processo.id.in_(ids_lista))

    processos = query.order_by(Processo.data_cadastro.desc()).all()

    campos_filtro = [
        "numero_processo", "parte", "company", "cliente", "nota_fiscal",
        "juizado", "comarca", "uf", "tipo_acao", "centro_resultado",
        "escritorio", "resultado", "status", "risco", "grau_instancia",
        "titulo_status", "data_distribuicao_de", "data_distribuicao_ate",
        "vencimento_de", "vencimento_ate", "ids",
    ]
    filtros_ativos = any(f.get(c, "").strip() for c in campos_filtro)

    # Totais do rodapé da listagem (soma de todos os títulos exibidos).
    total_titulos = sum(len(processo.titulos_recuperacao) for processo in processos)
    total_geral = sum(
        float(titulo.total or 0)
        for processo in processos
        for titulo in processo.titulos_recuperacao
    )

    return render_template(
        "processos_civel_recuperacao.html",
        processos=processos,
        filtros=f,
        filtros_ativos=filtros_ativos,
        total_titulos=total_titulos,
        total_geral=total_geral,
    )


@app.route("/agenda")
@login_required
def agenda():
    """Agenda de audiências: mostra a "Próxima Audiência" cadastrada em cada
    um dos processos cíveis, cíveis-recuperação, trabalhistas, tributários
    (estes últimos ainda são processos cíveis com tipo_acao='tributario',
    já que não existe cadastro próprio para tributário) e licitatórios
    (origem_cadastro='licitatorio' - o cadastro ainda não existe, então
    por enquanto essa opção da agenda não retorna nada)."""
    f = request.args

    def texto(chave):
        return f.get(chave, "").strip()

    tipo = texto("tipo") or "todos"
    numero_processo = texto("numero_processo")
    parte = texto("parte")
    advogado = texto("advogado")
    comarca = texto("comarca")
    uf = texto("uf")
    tipo_audiencia = texto("tipo_audiencia")
    data_de = texto("data_de")
    data_ate = texto("data_ate")

    query = Processo.query

    if tipo == "civel":
        query = query.filter(
            Processo.origem_cadastro == "civel", Processo.tipo_acao != "tributario"
        )
    elif tipo == "civel_recuperacao":
        query = query.filter(Processo.origem_cadastro == "civel_recuperacao")
    elif tipo == "trabalhista":
        query = query.filter(Processo.origem_cadastro == "trabalhista")
    elif tipo == "tributario":
        query = query.filter(
            Processo.origem_cadastro == "civel", Processo.tipo_acao == "tributario"
        )
    elif tipo == "licitatorio":
        query = query.filter(Processo.origem_cadastro == "licitatorio")
    # tipo == "todos" -> sem filtro de origem

    if numero_processo:
        query = query.filter(Processo.numero_processo.ilike(f"%{numero_processo}%"))
    if parte:
        query = query.filter(Processo.partes.any(Parte.nome.ilike(f"%{parte}%")))
    if advogado:
        # Mesmo lado que aparece na coluna "Advogado" da agenda (lado "reu").
        query = query.filter(Processo.advogados.any(
            db.and_(Advogado.lado == "reu", Advogado.nome.ilike(f"%{advogado}%"))
        ))
    if comarca:
        query = query.filter(Processo.comarca.ilike(f"%{comarca}%"))
    if uf:
        query = query.filter(Processo.uf == uf)

    query = query.filter(Processo.proxima_audiencia.isnot(None))

    processos = query.all()

    # Sem filtro de data explícito, considera "próxima audiência" a partir de
    # hoje - não interessa mostrar audiências que já aconteceram.
    data_de_obj = texto_para_data(data_de) if data_de else date.today()
    data_ate_obj = texto_para_data(data_ate) if data_ate else None

    audiencias = []
    for p in processos:
        tipo_parte = "reclamante" if p.origem_cadastro == "trabalhista" else "autor"
        nomes_partes = [parte_obj.nome for parte_obj in p.partes if parte_obj.tipo == tipo_parte]
        nome_parte = ", ".join(nomes_partes) if nomes_partes else "—"

        if p.origem_cadastro == "trabalhista":
            tipo_processo = "trabalhista"
        elif p.origem_cadastro == "civel_recuperacao":
            tipo_processo = "civel_recuperacao"
        elif p.origem_cadastro == "licitatorio":
            tipo_processo = "licitatorio"
        elif p.tipo_acao == "tributario":
            tipo_processo = "tributario"
        else:
            tipo_processo = "civel"

        # A data da agenda é a "Próxima Audiência" cadastrada no processo,
        # junto com o horário, a modalidade e o link dela.
        data_aud = p.proxima_audiencia
        horario = p.proxima_audiencia_horario
        tipo_aud = p.proxima_audiencia_tipo
        link_audiencia = p.proxima_audiencia_link

        if data_aud < data_de_obj:
            continue
        if data_ate_obj and data_aud > data_ate_obj:
            continue
        if tipo_audiencia and tipo_aud != tipo_audiencia:
            continue

        # Advogado(s) da empresa (lado "reu").
        nomes_advogados = [adv.nome for adv in p.advogados if adv.lado == "reu"]
        advogados = ", ".join(nomes_advogados) if nomes_advogados else "—"

        audiencias.append({
            "processo_id": p.id,
            "numero_processo": p.numero_processo,
            "nome_parte": nome_parte,
            "tipo_processo": tipo_processo,
            "data": data_aud,
            "horario": horario,
            "tipo_audiencia": tipo_aud,
            "juizado": p.juizado,
            "comarca": p.comarca,
            "uf": p.uf,
            "link": link_audiencia,
            "advogados": advogados,
        })

    audiencias.sort(key=lambda a: (a["data"], a["horario"] or ""))

    campos_filtro = [
        "numero_processo", "parte", "advogado", "comarca", "uf",
        "tipo_audiencia", "data_de", "data_ate",
    ]
    filtros_ativos = tipo != "todos" or any(f.get(c, "").strip() for c in campos_filtro)

    return render_template(
        "agenda.html",
        audiencias=audiencias,
        filtros=f,
        filtros_ativos=filtros_ativos,
        tipo_selecionado=tipo,
    )


@app.route("/processo/<int:processo_id>")
@login_required
def processo_detalhe(processo_id):
    processo = Processo.query.get_or_404(processo_id)
    return render_template("processo_detalhe.html", p=processo)


@app.route("/processo/<int:processo_id>/editar", methods=["GET", "POST"])
@login_required
def processo_editar(processo_id):
    processo = Processo.query.get_or_404(processo_id)

    if request.method == "GET":
        return render_template("processo_editar.html", p=processo)

    form = request.form
    numero_processo = form.get("numero_processo")
    numero_processo_original = processo.numero_processo

    # Bloqueia número duplicado, ignorando o próprio processo que está sendo editado
    duplicado = Processo.query.filter(
        Processo.numero_processo == numero_processo,
        Processo.id != processo_id,
    ).first()
    if duplicado:
        return render_template("processo_editar.html", p=processo, erro_numero_duplicado=numero_processo)

    preencher_campos_processo(processo, form)
    processo.numero_processo = numero_processo_original  # não pode ser alterado

    # Substitui advogados, movimentos e rateio pelo que veio no formulário -
    # mais simples e seguro do que tentar casar item a item quais foram
    # editados/removidos/adicionados. As partes (reclamante/autor e
    # reclamada/réu) NÃO são substituídas: não podem ser alteradas depois
    # do cadastro, então ficam como já estavam.
    processo.advogados = []
    processo.movimentos = []
    processo.rateio_crs = []
    preencher_partes_e_advogados(processo, form, incluir_partes=False)

    if processo.origem_cadastro == "trabalhista":
        processo.pedidos_trabalhistas = []
        verbas = form.getlist("pedido_verba")
        valores = form.getlist("pedido_valor")
        statuses = form.getlist("pedido_status")
        for verba, valor, status in zip(verbas, valores, statuses):
            if verba.strip():
                processo.pedidos_trabalhistas.append(
                    PedidoTrabalhista(verba=verba.strip(), valor=texto_para_numero(valor), status=status or "em_analise")
                )
    elif processo.origem_cadastro == "civel_recuperacao":
        # Mesma estratégia dos demais: troca a lista inteira pelo que veio
        # da tabela editável.
        processo.titulos_recuperacao = []
        preencher_titulos_recuperacao(processo, form)
        processo.acordos_recebimento = []
        preencher_acordo_recebimento(processo, form)
    else:
        processo.pedidos_civeis = []
        for descricao in form.getlist("pedido_civel"):
            if descricao.strip():
                processo.pedidos_civeis.append(PedidoCivel(descricao=descricao.strip()))

    try:
        registrar_atividade("processo_editado", f"Editou o processo {numero_processo_original}")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("processo_editar.html", p=processo, erro_numero_duplicado=numero_processo)

    return redirect(url_for("processo_detalhe", processo_id=processo.id))


@app.route("/processo/<int:processo_id>/excluir", methods=["POST"])
@login_required
def processo_excluir(processo_id):
    """Exclui o processo e tudo que está ligado a ele (partes, advogados,
    movimentos, pedidos, rateio, títulos e acordo). A confirmação formal é
    feita no navegador (processo_editar.html) antes de chegar aqui."""
    processo = Processo.query.get_or_404(processo_id)
    numero = processo.numero_processo
    origem = processo.origem_cadastro

    # Apaga os registros filhos explicitamente, para não sobrar lixo no banco
    # mesmo que os relacionamentos do modelo não tenham cascade configurado.
    for modelo in (Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR,
                   PedidoCivel, TituloRecuperacao, AcordoRecebimento):
        modelo.query.filter_by(processo_id=processo.id).delete(synchronize_session=False)

    registrar_atividade("processo_excluido", f"Excluiu o processo {numero}")
    db.session.delete(processo)
    db.session.commit()

    if origem == "trabalhista":
        return redirect(url_for("processos_trabalhista"))
    if origem == "civel_recuperacao":
        return redirect(url_for("processos_civel_recuperacao"))
    return redirect(url_for("processos_civel"))


# ---------------------------------------------------------------------------
# PANORAMA - CÍVEL / RECUPERAÇÃO DE CRÉDITO
# ---------------------------------------------------------------------------
# Aqui a empresa é CREDORA (cobra o que lhe devem), então a lógica é o oposto
# do panorama cível/trabalhista (onde é ré e se mede exposição/risco de perda).
# Em vez de provisionado/gasto/economizado, o painel mede:
#   carteira em cobrança -> recuperado -> inadimplência dos acordos -> custo.
#
# Regras de negócio adotadas (ajuste aqui se a diretoria pensar diferente):
#   * Cobrado (por processo) = soma do "Total" dos títulos; se o processo não
#     tem títulos, cai para o Valor da causa.
#   * Recuperado (por processo) = o maior entre o campo "Valor Recuperado"
#     (valor_final) e a soma dos Valores Recebidos das parcelas do acordo.
#     Usa-se o maior (e não a soma) para não contar duas vezes o mesmo
#     dinheiro quando o operador preenche os dois lugares.
#   * Carteira exigível = títulos de processos ATIVOS que não estão
#     "indeferidos" (título indeferido não é cobrável).
#   * Parcela em atraso = vencida e com situação diferente de "Recebido".
#   * Custo de recuperação = honorários advocatícios + periciais + custas.
#   * Taxa de êxito = (ganhamos + acordo) / todos os processos com resultado.

FASES_PROCESSUAIS = {
    "primeira_instancia": "1ª Instância",
    "segunda_instancia_tj": "2ª Instância - TJ",
    "segunda_instancia_trf": "2ª Instância - TRF",
    "execucao": "Execução",
    "stj": "Tribunal Superior - STJ",
    "stf": "Tribunal Superior - STF",
    "nao_informado": "Não informado",
}

# Centros de Resultado do cadastro (valor gravado = código).
CENTROS_RESULTADO = {
    "271": "271 - Audiologia",
    "290": "290 - Bernafon",
    "245": "245 - Call Center",
    "220": "220 - Departamento Financeiro",
    "273": "273 - Diatec",
    "211": "211 - Diretoria Executiva",
    "501": "501 - Distribuidores",
    "302": "302 - Expansão",
    "800": "800 - Expatriados",
    "246": "246 - Hub Teleconsulta",
    "253": "253 - Interacoustics - Distribuidores",
    "1111": "1111 - Laboratório",
    "237": "237 - Licitação e Legalização",
    "234": "234 - Logística",
    "242": "242 - Marketing",
    "601": "601 - Marketing B2B",
    "700": "700 - México",
    "402": "402 - Neurelec",
    "254": "254 - Oticon Governo",
    "400": "400 - Oticon Medical",
    "301": "301 - Philips",
    "281": "281 - Produtos e Regulatórios",
    "249": "249 - Programa Parceria",
    "251": "251 - RH/Depto.Pessoal",
    "258": "258 - Sonic",
    "502": "502 - Sonic Distribuidores",
    "236": "236 - Suprimentos",
    "213": "213 - Telex Licença",
    "261": "261 - TI - Informática",
}

TIPOS_ACAO_RECUPERACAO = {
    "acao_civel": "Ação Cível",
    "acao_trabalhista": "Ação Trabalhista",
    "cobranca_extrajudicial": "Cobrança Extrajudicial",
    "cobranca_judicial": "Cobrança Judicial",
}


def nome_centro_resultado(codigo):
    """"271" -> "271 - Audiologia"; "rateio" -> "Rateio entre CRs"."""
    if codigo == "rateio":
        return "Rateio entre CRs"
    return CENTROS_RESULTADO.get(str(codigo), str(codigo))


def montar_panorama_recuperacao():
    hoje = date.today()
    em_30_dias = hoje + timedelta(days=30)
    nomes_mes = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    def num(valor):
        return float(valor or 0)

    def total_titulo(t):
        if t.total is not None:
            return float(t.total)
        base = t.saldo if t.saldo is not None else t.valor
        return num(base) + num(t.juros) + num(t.correcao) + num(t.outros)

    def deslocar_mes(ano, mes, delta):
        indice = ano * 12 + (mes - 1) + delta
        return indice // 12, indice % 12 + 1

    processos = (
        Processo.query.filter_by(origem_cadastro="civel_recuperacao")
        .options(
            selectinload(Processo.titulos_recuperacao),
            selectinload(Processo.acordos_recebimento),
            selectinload(Processo.movimentos),
            selectinload(Processo.partes),
            selectinload(Processo.rateio_crs),
        )
        .all()
    )
    mapa_processos = {p.id: p for p in processos}

    # ---- Resumo por processo (base para quase tudo abaixo) ----------------
    resumo = []
    for p in processos:
        titulos_total = sum(total_titulo(t) for t in p.titulos_recuperacao)
        cobrado = titulos_total or num(p.valor_causa)
        recebido_acordo = sum(num(a.valor_recebido) for a in p.acordos_recebimento)
        recuperado = max(num(p.valor_final), recebido_acordo)
        custo = num(p.honorarios_advogado) + num(p.honorarios_periciais) + num(p.custas_processuais)
        resumo.append({"p": p, "cobrado": cobrado, "recuperado": recuperado, "custo": custo})
    ativos = [r for r in resumo if r["p"].status == "ativo"]

    dados = {"total": len(processos)}

    # ---- 1. Visão geral ---------------------------------------------------
    for status in ("ativo", "arquivado", "suspenso"):
        dados[status] = sum(1 for p in processos if p.status == status)
    dados["total_titulos"] = sum(len(p.titulos_recuperacao) for p in processos)

    # ---- 2. Financeiro ----------------------------------------------------
    cobrado_total = sum(r["cobrado"] for r in resumo)
    recuperado_total = sum(r["recuperado"] for r in resumo)
    custo_total = sum(r["custo"] for r in resumo)

    titulos_ativos = [t for r in ativos for t in r["p"].titulos_recuperacao]
    exigiveis = [t for t in titulos_ativos if t.status != "indeferido"]

    dados["financeiro"] = {
        "carteira_exigivel": sum(total_titulo(t) for t in exigiveis),
        "cobrado_total": cobrado_total,
        "recuperado": recuperado_total,
        "taxa_recuperacao": round(100 * recuperado_total / cobrado_total, 1) if cobrado_total else None,
        "custo": custo_total,
        "custo_pct": round(100 * custo_total / recuperado_total, 1) if recuperado_total else None,
        "liquido": recuperado_total - custo_total,
    }

    # ---- 3. Composição da carteira exigível -------------------------------
    saldo = sum(num(t.saldo if t.saldo is not None else t.valor) for t in exigiveis)
    juros = sum(num(t.juros) for t in exigiveis)
    correcao = sum(num(t.correcao) for t in exigiveis)
    outros = sum(num(t.outros) for t in exigiveis)
    dados["composicao"] = {
        "saldo": saldo, "juros": juros, "correcao": correcao, "outros": outros,
        "acrescimo_pct": round(100 * (juros + correcao + outros) / saldo, 1) if saldo else None,
    }

    # ---- 4. Situação dos títulos (carteira dos processos ativos) ----------
    dados["titulos_status"] = {
        chave: {
            "qtd": sum(1 for t in titulos_ativos if (t.status or "em_analise") == chave),
            "valor": sum(total_titulo(t) for t in titulos_ativos if (t.status or "em_analise") == chave),
        }
        for chave in ("deferido", "em_analise", "indeferido")
    }

    # ---- 5. Aging (vencimento da carteira exigível) -----------------------
    faixas = [("A vencer", None, 0), ("1-90 dias", 1, 90), ("91-180 dias", 91, 180),
              ("181-365 dias", 181, 365), ("+ de 1 ano", 366, None)]
    aging = {nome: {"qtd": 0, "valor": 0.0} for nome, _, _ in faixas}
    aging["Sem vencimento"] = {"qtd": 0, "valor": 0.0}
    vencidos_mais_1ano = 0
    ids_venc_1ano = set()
    for t in exigiveis:
        if not t.vencimento:
            nome = "Sem vencimento"
        else:
            dias = (hoje - t.vencimento).days
            nome = next(n for n, ini, fim in faixas
                        if (ini is None or dias >= ini) and (fim is None or dias <= fim))
            if dias > 365:
                vencidos_mais_1ano += 1
                ids_venc_1ano.add(t.processo_id)
        aging[nome]["qtd"] += 1
        aging[nome]["valor"] += total_titulo(t)
    dados["aging"] = [{"faixa": k, **v} for k, v in aging.items()]

    # ---- 6. Devedores e companies (carteira exigível) ---------------------
    # O valor recebido é registrado por processo (acordos/valor final), não por
    # título. Para chegar ao valor recebido de cada devedor, o recebido do
    # processo é dividido proporcionalmente ao peso dos títulos dele no processo.
    recuperado_por_processo = {r["p"].id: r["recuperado"] for r in resumo}
    total_exigivel_por_processo = {}
    for t in exigiveis:
        total_exigivel_por_processo[t.processo_id] = (
            total_exigivel_por_processo.get(t.processo_id, 0.0) + total_titulo(t)
        )

    def agrupar(chave_fn):
        grupos = {}
        for t in exigiveis:
            nome = (chave_fn(t) or "Não informado").strip().upper()
            g = grupos.setdefault(nome, {"nome": nome, "titulos": 0, "valor": 0.0, "processos": set(),
                                         "valor_por_processo": {}})
            g["titulos"] += 1
            g["valor"] += total_titulo(t)
            g["processos"].add(t.processo_id)
            g["valor_por_processo"][t.processo_id] = (
                g["valor_por_processo"].get(t.processo_id, 0.0) + total_titulo(t)
            )
        lista = sorted(grupos.values(), key=lambda g: g["valor"], reverse=True)
        for g in lista:
            g["recebido"] = sum(
                recuperado_por_processo.get(pid, 0.0) * valor / total_exigivel_por_processo[pid]
                for pid, valor in g["valor_por_processo"].items()
                if total_exigivel_por_processo.get(pid)
            )
            g["saldo"] = max(g["valor"] - g["recebido"], 0.0)
            # O campo Cliente da planilha é um ID; o(s) réu(s) do processo ajudam a identificar o devedor.
            nomes_reus = []
            for pid in g["processos"]:
                proc = mapa_processos.get(pid)
                if proc:
                    nomes_reus.extend(x.nome for x in proc.partes if x.tipo == "reu" and x.nome)
            unicos = list(dict.fromkeys(nomes_reus))
            g["reus"] = ", ".join(unicos[:2]) + (f" (+{len(unicos) - 2})" if len(unicos) > 2 else "")
            g["processos"] = len(g["processos"])
        return lista

    devedores = agrupar(lambda t: t.cliente)
    total_exigivel = dados["financeiro"]["carteira_exigivel"]
    dados["top_devedores"] = devedores[:8]
    dados["concentracao_top5"] = (
        round(100 * sum(g["valor"] for g in devedores[:5]) / total_exigivel, 1) if total_exigivel else None
    )
    dados["top_companies"] = agrupar(lambda t: t.company)[:6]

    # ---- 7. Acordos / parcelas --------------------------------------------
    ac = {"valor_acordado": 0.0, "recebido": 0.0, "em_aberto": 0.0, "em_atraso": 0.0,
          "a_vencer_30d": 0.0, "vencido_total": 0.0, "honorarios_exito": 0.0,
          "sucumbencia": 0.0, "pago_adv": 0.0, "repasses_pendentes": 0, "sem_valor": 0,
          "situacao": {"pendente": 0, "parcial": 0, "pago": 0}}
    processos_com_acordo = 0
    atrasadas = []
    ids_repasse, ids_sem_valor = set(), set()
    previsto_mes, recebido_mes = {}, {}
    for r in resumo:
        parcelas = r["p"].acordos_recebimento
        if parcelas:
            processos_com_acordo += 1
        for a in parcelas:
            valor_parcela, recebido = num(a.valor_parcela), num(a.valor_recebido)
            situacao = a.situacao if a.situacao in ac["situacao"] else ("pago" if recebido else "pendente")
            aberto = 0.0 if situacao == "pago" else max(valor_parcela - recebido, 0.0)
            ac["situacao"][situacao] += 1
            # Parcela vencida, não quitada e sem valor informado: não entra no
            # cálculo de inadimplência (saldo em aberto = 0), então é sinalizada.
            if (situacao != "pago" and a.valor_parcela is None
                    and a.vencimento and a.vencimento < hoje):
                ac["sem_valor"] += 1
                ids_sem_valor.add(r["p"].id)
            ac["valor_acordado"] += valor_parcela
            ac["recebido"] += recebido
            ac["em_aberto"] += aberto
            ac["honorarios_exito"] += num(a.honorarios_exito)
            ac["sucumbencia"] += num(a.sucumbencia)
            ac["pago_adv"] += num(a.valor_pago_adv)
            if recebido > 0 and not a.data_pagto_adv and not a.valor_pago_adv:
                ac["repasses_pendentes"] += 1
                ids_repasse.add(r["p"].id)
            if a.vencimento:
                chave = (a.vencimento.year, a.vencimento.month)
                previsto_mes[chave] = previsto_mes.get(chave, 0.0) + valor_parcela
                if a.vencimento < hoje:
                    ac["vencido_total"] += valor_parcela
                    if aberto > 0:
                        ac["em_atraso"] += aberto
                        atrasadas.append({
                            "processo_id": r["p"].id,
                            "numero_processo": r["p"].numero_processo, "valor": aberto,
                            "parcela": a.parcela or "—", "dias": (hoje - a.vencimento).days,
                        })
                elif a.vencimento <= em_30_dias:
                    ac["a_vencer_30d"] += aberto
            if a.data_recebimento and recebido:
                chave = (a.data_recebimento.year, a.data_recebimento.month)
                recebido_mes[chave] = recebido_mes.get(chave, 0.0) + recebido
    ac["inadimplencia_pct"] = (
        round(100 * ac["em_atraso"] / ac["vencido_total"], 1) if ac["vencido_total"] else None
    )
    ac["processos_com_acordo"] = processos_com_acordo
    ac["pct_processos_com_acordo"] = round(100 * processos_com_acordo / len(processos), 1) if processos else None
    dados["acordos"] = ac

    # Fluxo mês a mês: 6 meses para trás + mês atual + 6 para frente
    fluxo = []
    for delta in range(-6, 7):
        ano, mes = deslocar_mes(hoje.year, hoje.month, delta)
        fluxo.append({
            "label": f"{nomes_mes[mes - 1]}/{str(ano)[2:]}",
            "previsto": previsto_mes.get((ano, mes), 0.0),
            "recebido": recebido_mes.get((ano, mes), 0.0),
        })
    dados["fluxo_mensal"] = fluxo

    # ---- 8. Resultado dos processos ---------------------------------------
    resultados = {k: sum(1 for p in processos if p.resultado == k)
                  for k in ("ganhamos", "acordo", "perdemos", "extinto_sem_resolucao_merito")}
    dados["resultado"] = resultados
    com_resultado = sum(resultados.values())
    dados["taxa_exito"] = (
        round(100 * (resultados["ganhamos"] + resultados["acordo"]) / com_resultado, 1)
        if com_resultado else None
    )

    # ---- 9. Desempenho por escritório -------------------------------------
    escritorios = {}
    for r in resumo:
        nome = r["p"].escritorio or "Não informado"
        e = escritorios.setdefault(nome, {"nome": nome, "processos": 0, "cobrado": 0.0, "recuperado": 0.0})
        e["processos"] += 1
        e["cobrado"] += r["cobrado"]
        e["recuperado"] += r["recuperado"]
    lista_esc = sorted(escritorios.values(), key=lambda e: e["cobrado"], reverse=True)[:8]
    for e in lista_esc:
        e["taxa"] = round(100 * e["recuperado"] / e["cobrado"], 1) if e["cobrado"] else None
    dados["escritorios"] = lista_esc

    # ---- 10. Fase processual e Centro de Resultado (só ativos) ------------
    def em_aberto(r):
        return max(r["cobrado"] - r["recuperado"], 0.0)

    fases = {}
    crs = {}
    for r in ativos:
        chave = FASES_PROCESSUAIS.get(r["p"].grau_instancia, "Não informado")
        f = fases.setdefault(chave, {"nome": chave, "qtd": 0, "valor": 0.0})
        f["qtd"] += 1
        f["valor"] += em_aberto(r)
        if r["p"].centro_resultado:
            nome_cr = nome_centro_resultado(r["p"].centro_resultado)
            c = crs.setdefault(nome_cr, {"nome": nome_cr, "qtd": 0, "valor": 0.0})
            c["qtd"] += 1
            c["valor"] += em_aberto(r)
    dados["fases"] = sorted(fases.values(), key=lambda f: f["valor"], reverse=True)
    dados["top_centros_resultado"] = sorted(crs.values(), key=lambda c: c["valor"], reverse=True)[:6]

    # ---- 10b. Processos com êxito por Centro de Resultado (CR) ------------
    # Êxito = resultado "acordo" ou "ganhamos" (sentença favorável), em qualquer
    # status do processo. Falta recuperar = cobrado - recuperado (nunca negativo).
    # Processos com rateio entre CRs têm os valores divididos igualmente entre os
    # CRs do rateio; a quantidade de processos conta 1 em cada CR do rateio, mas
    # o total geral conta cada processo uma única vez.
    vias_exito = {"acordo": "qtd_acordo", "ganhamos": "qtd_sentenca"}
    exito_crs = {}
    exito_total = {"qtd": 0, "qtd_acordo": 0, "qtd_sentenca": 0,
                   "cobrado": 0.0, "recuperado": 0.0, "falta": 0.0}
    for r in resumo:
        p = r["p"]
        campo_via = vias_exito.get(p.resultado)
        if not campo_via:
            continue
        falta = max(r["cobrado"] - r["recuperado"], 0.0)
        if p.centro_resultado == "rateio":
            codigos = [x.centro_resultado.strip() for x in p.rateio_crs
                       if x.centro_resultado and x.centro_resultado.strip()]
            codigos = list(dict.fromkeys(codigos)) or ["rateio"]
        elif p.centro_resultado:
            codigos = [p.centro_resultado]
        else:
            codigos = [None]
        for codigo in codigos:
            nome_cr = nome_centro_resultado(codigo) if codigo else "Sem centro de resultado"
            c = exito_crs.setdefault(nome_cr, {"nome": nome_cr, "qtd": 0, "qtd_acordo": 0, "qtd_sentenca": 0,
                                               "cobrado": 0.0, "recuperado": 0.0, "falta": 0.0})
            c["qtd"] += 1
            c[campo_via] += 1
            c["cobrado"] += r["cobrado"] / len(codigos)
            c["recuperado"] += r["recuperado"] / len(codigos)
            c["falta"] += falta / len(codigos)
        exito_total["qtd"] += 1
        exito_total[campo_via] += 1
        exito_total["cobrado"] += r["cobrado"]
        exito_total["recuperado"] += r["recuperado"]
        exito_total["falta"] += falta
    linhas_exito = sorted(exito_crs.values(), key=lambda c: c["cobrado"], reverse=True)
    for c in linhas_exito + [exito_total]:
        c["pct"] = round(100 * c["recuperado"] / c["cobrado"], 1) if c["cobrado"] else None
    dados["exito_cr"] = {"linhas": linhas_exito, "total": exito_total}

    # ---- 11. Idade, tempo de tramitação e processos parados ---------------
    idade = {"Até 1 ano": [0, 0.0], "1 a 2 anos": [0, 0.0], "2 a 3 anos": [0, 0.0],
             "+ de 3 anos": [0, 0.0], "Sem data": [0, 0.0]}
    parados, valor_parados = 0, 0.0
    ids_parados = []
    for r in ativos:
        p = r["p"]
        if p.data_distribuicao:
            anos = (hoje - p.data_distribuicao).days / 365.25
            faixa = ("Até 1 ano" if anos < 1 else "1 a 2 anos" if anos < 2
                     else "2 a 3 anos" if anos < 3 else "+ de 3 anos")
        else:
            faixa = "Sem data"
        idade[faixa][0] += 1
        idade[faixa][1] += em_aberto(r)

        datas_mov = [m.data_movimento for m in p.movimentos if m.data_movimento]
        referencia = max(datas_mov) if datas_mov else p.data_distribuicao
        if referencia and (hoje - referencia).days > 90:
            parados += 1
            valor_parados += em_aberto(r)
            ids_parados.append(p.id)
    dados["idade_ativos"] = [{"faixa": k, "qtd": v[0], "valor": v[1]} for k, v in idade.items()]
    dados["parados"] = {"qtd": parados, "valor": valor_parados}

    duracoes = [(p.data_arquivamento - p.data_distribuicao).days
                for p in processos if p.status == "arquivado" and p.data_arquivamento and p.data_distribuicao]
    dados["tempo_medio_meses"] = round(sum(duracoes) / len(duracoes) / 30.4, 1) if duracoes else None

    # ---- 11b. Desempenho por tipo de ação ----------------------------------
    tipos = {}
    for r in resumo:
        chave = TIPOS_ACAO_RECUPERACAO.get(r["p"].tipo_acao, "Não informado")
        x = tipos.setdefault(chave, {"nome": chave, "processos": 0, "cobrado": 0.0, "recuperado": 0.0})
        x["processos"] += 1
        x["cobrado"] += r["cobrado"]
        x["recuperado"] += r["recuperado"]
    lista_tipos = sorted(tipos.values(), key=lambda x: x["cobrado"], reverse=True)
    for x in lista_tipos:
        x["taxa"] = round(100 * x["recuperado"] / x["cobrado"], 1) if x["cobrado"] else None
    dados["tipos_acao"] = lista_tipos

    # ---- 11c. Processos arquivados (encerrados) ----------------------------
    arquivados = [r for r in resumo if r["p"].status == "arquivado"]
    cobrado_arq = sum(r["cobrado"] for r in arquivados)
    recuperado_arq = sum(r["recuperado"] for r in arquivados)
    dados["encerrados"] = {
        "qtd": len(arquivados),
        "cobrado": cobrado_arq,
        "recuperado": recuperado_arq,
        "taxa": round(100 * recuperado_arq / cobrado_arq, 1) if cobrado_arq else None,
        "nao_recuperado": sum(num(r["p"].economia_gerada) for r in resumo),
    }

    # ---- 12. Atenção da diretoria -----------------------------------------
    atrasadas.sort(key=lambda x: x["valor"], reverse=True)
    dados["atencao_parcelas"] = atrasadas[:5]
    def link_lista(ids):
        """Link para a listagem de recuperação filtrada pelos processos citados."""
        ids = sorted(set(ids))
        if len(ids) > 400:  # evita URL gigante
            return url_for("processos_civel_recuperacao", status="ativo")
        return url_for("processos_civel_recuperacao", ids=",".join(str(i) for i in ids))

    def refs(ids):
        """Processos (id + número) citados em um aviso, em ordem de número."""
        validos = [i for i in set(ids) if i in mapa_processos]
        validos.sort(key=lambda i: str(mapa_processos[i].numero_processo))
        return [{"id": i, "numero": mapa_processos[i].numero_processo} for i in validos]

    avisos = []
    if parados:
        avisos.append({
            "texto": f"{parados} processo(s) ativo(s) sem movimentação há mais de 90 dias.",
            "url": link_lista(ids_parados),
            "processos": refs(ids_parados),
        })
    if vencidos_mais_1ano:
        avisos.append({
            "texto": f"{vencidos_mais_1ano} título(s) exigível(is) vencido(s) há mais de 1 ano.",
            "url": link_lista(ids_venc_1ano),
            "processos": refs(ids_venc_1ano),
        })
    if ac["repasses_pendentes"]:
        avisos.append({
            "texto": f"{ac['repasses_pendentes']} parcela(s) recebida(s) sem pagamento ao advogado registrado.",
            "url": link_lista(ids_repasse),
            "processos": refs(ids_repasse),
        })
    if ac["sem_valor"]:
        avisos.append({
            "texto": f"{ac['sem_valor']} parcela(s) vencida(s) sem valor informado, não computada(s) como inadimplência.",
            "url": link_lista(ids_sem_valor),
            "processos": refs(ids_sem_valor),
        })
    proximas = [
        r["p"] for r in ativos
        if r["p"].proxima_audiencia and hoje <= r["p"].proxima_audiencia <= em_30_dias
    ]
    if proximas:
        avisos.append({
            "texto": f"{len(proximas)} processo(s) ativo(s) com audiência nos próximos 30 dias.",
            "url": url_for("agenda", tipo="civel_recuperacao",
                           data_de=hoje.isoformat(), data_ate=em_30_dias.isoformat()),
            "processos": refs([p_.id for p_ in proximas]),
        })
    dados["avisos"] = avisos

    return dados


@app.route("/panorama-juridico")
@login_required
def panorama_juridico():

    # Sem "tipo" na URL (ex.: acabou de clicar em "Panorama Jurídico" no
    # menu lateral) -> mostra a tela de escolha do tipo de processo, sem
    # gastar tempo calculando os dados do painel. Só depois que o
    # operador escolhe um dos cartões (que já vêm com ?tipo=... no link)
    # é que a lógica abaixo roda e o painel de verdade é exibido.
    #
    # "civel_trabalhista" é o único tipo que junta mais de uma origem de
    # cadastro (Cível + Trabalhista) - não existe um "todos" genérico que
    # some literalmente todos os tipos de processo do sistema.
    tipos_processo_validos = {"civel", "trabalhista", "civel_trabalhista", "civel_recuperacao"}
    tipo_param = request.args.get("tipo")
    if tipo_param not in tipos_processo_validos:
        return render_template("panorama_juridico.html", mostrar_menu=True)
    tipo_selecionado = tipo_param

    # Recuperação de Crédito tem análise própria (empresa credora: carteira,
    # recuperado, acordos, inadimplência) - ver montar_panorama_recuperacao().
    if tipo_selecionado == "civel_recuperacao":
        return render_template(
            "panorama_recuperacao.html", dados=montar_panorama_recuperacao(),
            tipo_selecionado=tipo_selecionado, mostrar_menu=False,
        )

    # Filtro por tipo de processo. "civel_trabalhista" não aplica nenhum
    # filtro extra de origem_cadastro, pois já é a junção dos dois.
    #
    # Cível - Recuperação de Crédito fica de fora deste panorama: a análise
    # de recuperação de crédito é muito diferente (não fala em risco/
    # sentença/pedidos como os outros tipos) e tem panorama próprio
    # (montar_panorama_recuperacao). Nenhum processo com origem_cadastro=
    # "civel_recuperacao" deve aparecer aqui - nem nas somas/contagens de
    # "Cível + Trabalhista", nem como opção selecionável no filtro.

    def query_base():
        """Ponto de partida de toda consulta nesta página - já vem com o
        filtro de tipo de processo aplicado (se houver um selecionado) e
        sempre exclui Cível - Recuperação de Crédito (ver comentário acima)."""
        query = Processo.query.filter(Processo.origem_cadastro != "civel_recuperacao")
        if tipo_selecionado != "civel_trabalhista":
            query = query.filter(Processo.origem_cadastro == tipo_selecionado)
        return query

    def contar(**filtros):
        """Conta processos que batem com os filtros dados (além do filtro
        de tipo de processo já aplicado por query_base).
        Ex: contar(origem_cadastro='civel', status='ativo')"""
        query = query_base()
        for campo, valor in filtros.items():
            query = query.filter(getattr(Processo, campo) == valor)
        return query.count()

    def somar(coluna, **filtros):
        """Soma uma coluna numérica (ex: Processo.valor_causa) para os
        processos que batem com os filtros dados (além do filtro de tipo
        de processo já aplicado). Nunca retorna None."""
        query = db.session.query(func.coalesce(func.sum(coluna), 0)).select_from(Processo)
        query = query.filter(Processo.origem_cadastro != "civel_recuperacao")
        if tipo_selecionado != "civel_trabalhista":
            query = query.filter(Processo.origem_cadastro == tipo_selecionado)
        for campo, valor in filtros.items():
            query = query.filter(getattr(Processo, campo) == valor)
        return float(query.scalar() or 0)

    # ------------------------------------------------------------------
    # 1. VISÃO GERAL (cível x trabalhista x status)
    # ------------------------------------------------------------------
    dados = {
        "total": contar(),
        "civel": {
            "total": contar(origem_cadastro="civel"),
            "ativo": contar(origem_cadastro="civel", status="ativo"),
            "arquivado": contar(origem_cadastro="civel", status="arquivado"),
            "suspenso": contar(origem_cadastro="civel", status="suspenso"),
        },
        "trabalhista": {
            "total": contar(origem_cadastro="trabalhista"),
            "ativo": contar(origem_cadastro="trabalhista", status="ativo"),
            "arquivado": contar(origem_cadastro="trabalhista", status="arquivado"),
            "suspenso": contar(origem_cadastro="trabalhista", status="suspenso"),
        },
    }

    # ------------------------------------------------------------------
    # 2. FINANCEIRO
    #    Provisionado = valor da causa dos processos ATIVOS (exposição em
    #                    aberto - o que ainda pode ser desembolsado).
    #    Gasto         = tudo que já efetivamente saiu do caixa: valor final
    #                    pago + honorários (advogado/perícia) + custas +
    #                    depósito recursal - valor do alvará (o que voltou
    #                    para o caixa).
    #    Economizado   = soma do campo "economia_gerada".
    #    Ajuste essa régua livremente se a definição da diretoria for outra.
    # ------------------------------------------------------------------
    provisionado = somar(Processo.valor_causa, status="ativo")
    valor_pago_total = somar(Processo.valor_final)
    gasto = (
        valor_pago_total
        + somar(Processo.honorarios_advogado)
        + somar(Processo.honorarios_periciais)
        + somar(Processo.custas_processuais)
        + somar(Processo.deposito_recursal)
        - somar(Processo.valor_alvara)
    )
    economizado = somar(Processo.economia_gerada)

    dados["financeiro"] = {
        "provisionado": provisionado,
        "gasto": gasto,
        "economizado": economizado,
        "valor_causa_total": somar(Processo.valor_causa),
        "valor_pago_total": valor_pago_total,
    }

    # ------------------------------------------------------------------
    # 3. RISCO - exposição em aberto (só processos ativos) por nível de risco
    # ------------------------------------------------------------------
    dados["risco"] = {
        nivel: {
            "qtd": contar(status="ativo", risco=nivel),
            "valor": somar(Processo.valor_causa, status="ativo", risco=nivel),
        }
        for nivel in ["possivel", "provavel", "remoto"]
    }
    dados["risco_sem_classificacao"] = contar(status="ativo", risco=None)

    # ------------------------------------------------------------------
    # 4. RESULTADO - processos já com desfecho, em 4 categorias:
    #    Vitórias = resultado "ganhamos" e sentença não é parcial
    #    Parciais = sentença "procedente_parcial"
    #    Acordos  = resultado "acordo"
    #    Derrotas = resultado "perdemos"
    #    Ajuste essa régua se a definição da diretoria for outra.
    # ------------------------------------------------------------------
    vitorias_qtd = query_base().filter(
        Processo.resultado == "ganhamos", Processo.sentenca != "procedente_parcial"
    ).count()
    parciais_qtd = query_base().filter(Processo.sentenca == "procedente_parcial").count()
    acordos_qtd = contar(resultado="acordo")
    derrotas_qtd = contar(resultado="perdemos")

    dados["resultado_detalhado"] = {
        "vitorias": vitorias_qtd,
        "parciais": parciais_qtd,
        "acordos": acordos_qtd,
        "derrotas": derrotas_qtd,
    }

    # Mantido para o gráfico financeiro por resultado (usa o campo "resultado" bruto)
    dados["resultado"] = {
        chave: {
            "qtd": contar(resultado=chave),
            "valor": somar(Processo.valor_final, resultado=chave),
        }
        for chave in ["ganhamos", "perdemos", "acordo"]
    }
    total_com_desfecho = vitorias_qtd + parciais_qtd + acordos_qtd + derrotas_qtd
    dados["taxa_exito"] = (
        round(100 * (vitorias_qtd + parciais_qtd) / total_com_desfecho, 1)
        if total_com_desfecho
        else None
    )

    # Quadro "Resultado dos encerrados" do painel - usa o campo SENTENÇA
    # (não o campo "resultado" acima), em 7 categorias definidas pela
    # diretoria.
    dados["resultado_sentenca"] = {
        chave: contar(sentenca=chave)
        for chave in [
            "procedente",
            "procedente_parcial",
            "acordo",
            "extinto_com_resolucao_merito",
            "extinto_sem_resolucao_merito",
            "desistencia",
            "improcedente",
        ]
    }


    # ------------------------------------------------------------------
    # 4b. MATRIZ DE RISCO - Provável/Possível/Remoto x Baixo/Médio/Alto
    #     impacto, calculado pelo valor da causa (só processos ativos).
    #     Faixas: Baixo < R$50k · Médio R$50k-200k · Alto > R$200k
    # ------------------------------------------------------------------
    def faixa_impacto(valor):
        valor = float(valor or 0)
        if valor < 50_000:
            return "baixo"
        if valor < 200_000:
            return "medio"
        return "alto"

    matriz = {nivel: {"baixo": 0, "medio": 0, "alto": 0} for nivel in ["provavel", "possivel", "remoto"]}
    ativos = query_base().filter_by(status="ativo").all()
    for p in ativos:
        if p.risco in matriz:
            matriz[p.risco][faixa_impacto(p.valor_causa)] += 1
    dados["matriz_risco"] = matriz

    # ------------------------------------------------------------------
    # 4c. ATENÇÃO DA DIRETORIA - top 5 processos ativos de risco provável,
    #     ordenados pela maior exposição financeira.
    # ------------------------------------------------------------------
    alertas_processos = (
        query_base().filter_by(status="ativo", risco="provavel")
        .order_by(Processo.valor_causa.desc())
        .limit(5)
        .all()
    )
    alertas = []
    for p in alertas_processos:
        motivo = None
        if p.pedidos_trabalhistas:
            motivo = max(p.pedidos_trabalhistas, key=lambda x: float(x.valor or 0)).verba
        elif p.pedidos_civeis:
            motivo = p.pedidos_civeis[0].descricao
        if not motivo:
            motivo = (p.tipo_acao or "").replace("_", " ").title() or "—"
        alertas.append({
            "numero_processo": p.numero_processo,
            "valor_causa": float(p.valor_causa or 0),
            "motivo": motivo,
        })
    dados["atencao_diretoria"] = alertas

    # ------------------------------------------------------------------
    # 5. TOP CENTROS DE RESULTADO por exposição em aberto
    # ------------------------------------------------------------------
    top_cr_query = db.session.query(
        Processo.centro_resultado,
        func.count(Processo.id),
        func.coalesce(func.sum(Processo.valor_causa), 0),
    ).filter(
        Processo.status == "ativo",
        Processo.centro_resultado.isnot(None),
        Processo.origem_cadastro != "civel_recuperacao",
    )
    if tipo_selecionado != "civel_trabalhista":
        top_cr_query = top_cr_query.filter(Processo.origem_cadastro == tipo_selecionado)
    top_cr = (
        top_cr_query
        .group_by(Processo.centro_resultado)
        .order_by(func.sum(Processo.valor_causa).desc())
        .limit(6)
        .all()
    )
    dados["top_centros_resultado"] = [
        {"nome": nome, "qtd": qtd, "valor": float(valor or 0)}
        for nome, qtd, valor in top_cr
    ]

    # ------------------------------------------------------------------
    # 6. EVOLUÇÃO MENSAL - novos processos distribuídos nos últimos 12 meses
    # ------------------------------------------------------------------
    hoje = date.today()
    meses_alvo = []
    ano, mes = hoje.year, hoje.month
    for _ in range(12):
        meses_alvo.append((ano, mes))
        mes -= 1
        if mes == 0:
            mes = 12
            ano -= 1
    meses_alvo.reverse()

    nomes_mes = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    evolucao = []
    for ano, mes in meses_alvo:
        qtd = query_base().filter(
            func.extract("year", Processo.data_distribuicao) == ano,
            func.extract("month", Processo.data_distribuicao) == mes,
        ).count()
        evolucao.append({"label": f"{nomes_mes[mes - 1]}/{str(ano)[2:]}", "qtd": qtd})
    dados["evolucao_mensal"] = evolucao

    # ------------------------------------------------------------------
    # 7. PEDIDOS E REQUERIMENTOS MAIS RECORRENTES - aponta quais itens
    #    aparecem com mais frequência e onde a tese da empresa está mais
    #    frágil. Os pedidos são diferentes conforme o tipo de processo:
    #    - Trabalhista: cada verba tem status próprio (deferido/
    #      indeferido/em análise) - "% deferido" = perda para a empresa.
    #    - Cível: o pedido em si não tem status - usamos a SENTENÇA do
    #      processo como um todo (procedente/procedente_parcial = ponto
    #      frágil) para saber o quanto aquele pedido pesa no resultado.
    # ------------------------------------------------------------------
    pedidos_trab_query = (
        db.session.query(
            PedidoTrabalhista.verba,
            func.count(PedidoTrabalhista.id),
            func.sum(case((PedidoTrabalhista.status == "deferido", 1), else_=0)),
            func.sum(case((PedidoTrabalhista.status == "indeferido", 1), else_=0)),
            func.sum(case((PedidoTrabalhista.status == "em_analise", 1), else_=0)),
        )
        .join(Processo, PedidoTrabalhista.processo_id == Processo.id)
    )
    if tipo_selecionado != "civel_trabalhista":
        pedidos_trab_query = pedidos_trab_query.filter(Processo.origem_cadastro == tipo_selecionado)
    pedidos_trab_query = (
        pedidos_trab_query.group_by(PedidoTrabalhista.verba)
        .order_by(func.count(PedidoTrabalhista.id).desc())
        .limit(8)
        .all()
    )
    dados["pedidos_recorrentes_trabalhista"] = [
        {
            "item": verba,
            "total": total,
            "deferidos": deferidos,
            "indeferidos": indeferidos,
            "em_analise": em_analise,
            "pct_deferido": round(100 * deferidos / total, 1) if total else 0,
        }
        for verba, total, deferidos, indeferidos, em_analise in pedidos_trab_query
    ]

    pedidos_civel_query = (
        db.session.query(
            PedidoCivel.descricao,
            func.count(PedidoCivel.id),
            func.sum(case((Processo.sentenca.in_(["procedente", "procedente_parcial"]), 1), else_=0)),
        )
        .join(Processo, PedidoCivel.processo_id == Processo.id)
    )
    if tipo_selecionado != "civel_trabalhista":
        pedidos_civel_query = pedidos_civel_query.filter(Processo.origem_cadastro == tipo_selecionado)
    pedidos_civel_query = (
        pedidos_civel_query.group_by(PedidoCivel.descricao)
        .order_by(func.count(PedidoCivel.id).desc())
        .limit(8)
        .all()
    )
    dados["pedidos_recorrentes_civel"] = [
        {
            "item": descricao,
            "total": total,
            "desfavoraveis": desfavoraveis,
            "pct_desfavoravel": round(100 * desfavoraveis / total, 1) if total else 0,
        }
        for descricao, total, desfavoraveis in pedidos_civel_query
    ]

    return render_template(
        "panorama_juridico.html", dados=dados, tipo_selecionado=tipo_selecionado,
        mostrar_menu=False,
    )


if __name__ == "__main__":
    app.run(debug=True)