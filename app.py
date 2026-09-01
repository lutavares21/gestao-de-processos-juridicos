# -*- coding: utf-8 -*-
"""
App Flask - Gestão de Processos Jurídicos
"""

from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from sqlalchemy.exc import IntegrityError
from models import db, Processo, Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR, PedidoCivel

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///processos.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()


@app.template_filter("moeda")
def formatar_moeda(valor):
    """Formata um número no padrão contábil brasileiro: R$ 1.234,56.
    Retorna '—' quando o valor é None."""
    if valor is None:
        return "—"
    texto = f"{float(valor):,.2f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


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
    processo.tipo_acao = form.get("tipo_acao")
    processo.data_distribuicao = texto_para_data(form.get("data_distribuicao"))
    processo.valor_causa = texto_para_numero(form.get("valor_causa"))
    processo.data_audiencia_1 = texto_para_data(form.get("data_audiencia_1"))
    processo.audiencia_1_horario = form.get("audiencia_1_horario") or None
    processo.audiencia_1_tipo = form.get("audiencia_1_tipo") or None
    processo.audiencia_1_link = form.get("audiencia_1_link") or None
    processo.data_audiencia_2 = texto_para_data(form.get("data_audiencia_2"))
    processo.data_audiencia_3 = texto_para_data(form.get("data_audiencia_3"))
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


def preencher_partes_e_advogados(processo, form):
    """Preenche partes, advogados, movimentos e rateio - comum a todas as páginas.

    O rótulo salvo em Parte.tipo depende da origem do cadastro: processos
    trabalhistas usam 'reclamante'/'reclamada'; os demais (cível) usam
    'autor'/'reu'."""
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


@app.route("/")
def index():
    return render_template("inicio.html")


def numero_processo_ja_existe(numero_processo):
    """Verifica se já existe algum processo (cível ou trabalhista) cadastrado
    com esse número. Usado para bloquear cadastros duplicados."""
    return Processo.query.filter_by(numero_processo=numero_processo).first() is not None


@app.route("/inicio")
def inicio():
    return render_template("inicio.html")


@app.route("/cadastro/civel", methods=["GET", "POST"])
def cadastro_civel():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_civel.html", sucesso=sucesso)

    form = request.form
    numero_processo = form.get("numero_processo")

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
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("cadastro_civel.html", erro_numero_duplicado=numero_processo)

    return redirect(url_for("cadastro_civel", sucesso=1))


@app.route("/cadastro/trabalhista", methods=["GET", "POST"])
def cadastro_trabalhista():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_trabalhista.html", sucesso=sucesso)

    form = request.form
    numero_processo = form.get("numero_processo")

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
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("cadastro_trabalhista.html", erro_numero_duplicado=numero_processo)

    return redirect(url_for("cadastro_trabalhista", sucesso=1))


@app.route("/processos/civel")
def processos_civel():
    processos = (
        Processo.query.filter_by(origem_cadastro="civel")
        .order_by(Processo.data_cadastro.desc())
        .all()
    )
    return render_template("processos_civel.html", processos=processos)


@app.route("/processos/trabalhista")
def processos_trabalhista():
    processos = (
        Processo.query.filter_by(origem_cadastro="trabalhista")
        .order_by(Processo.data_cadastro.desc())
        .all()
    )
    return render_template("processos_trabalhista.html", processos=processos)


@app.route("/processo/<int:processo_id>")
def processo_detalhe(processo_id):
    processo = Processo.query.get_or_404(processo_id)
    return render_template("processo_detalhe.html", p=processo)


@app.route("/processo/<int:processo_id>/editar", methods=["GET", "POST"])
def processo_editar(processo_id):
    processo = Processo.query.get_or_404(processo_id)

    if request.method == "GET":
        return render_template("processo_editar.html", p=processo)

    form = request.form
    numero_processo = form.get("numero_processo")

    # Bloqueia número duplicado, ignorando o próprio processo que está sendo editado
    duplicado = Processo.query.filter(
        Processo.numero_processo == numero_processo,
        Processo.id != processo_id,
    ).first()
    if duplicado:
        return render_template("processo_editar.html", p=processo, erro_numero_duplicado=numero_processo)

    preencher_campos_processo(processo, form)

    # Substitui as listas relacionadas (partes, advogados, movimentos, rateio,
    # pedidos) pelo que veio no formulário - mais simples e seguro do que
    # tentar casar item a item quais foram editados/removidos/adicionados.
    processo.partes = []
    processo.advogados = []
    processo.movimentos = []
    processo.rateio_crs = []
    preencher_partes_e_advogados(processo, form)

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
    else:
        processo.pedidos_civeis = []
        for descricao in form.getlist("pedido_civel"):
            if descricao.strip():
                processo.pedidos_civeis.append(PedidoCivel(descricao=descricao.strip()))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return render_template("processo_editar.html", p=processo, erro_numero_duplicado=numero_processo)

    return redirect(url_for("processo_detalhe", processo_id=processo.id))


if __name__ == "__main__":
    app.run(debug=True)