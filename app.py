# -*- coding: utf-8 -*-
"""
App Flask - Gestão de Processos Jurídicos
"""

from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from models import db, Processo, Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR, PedidoCivel

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///processos.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()


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


def montar_processo_base(form):
    """Cria um objeto Processo com os campos comuns a todas as páginas de cadastro."""
    return Processo(
        numero_processo=form.get("numero_processo"),
        juizado=form.get("juizado"),
        comarca=form.get("comarca"),
        tipo_acao=form.get("tipo_acao"),
        data_distribuicao=texto_para_data(form.get("data_distribuicao")),
        valor_causa=texto_para_numero(form.get("valor_causa")),
        data_audiencia_1=texto_para_data(form.get("data_audiencia_1")),
        data_audiencia_2=texto_para_data(form.get("data_audiencia_2")),
        data_audiencia_3=texto_para_data(form.get("data_audiencia_3")),
        data_arquivamento=texto_para_data(form.get("data_arquivamento")),
        centro_resultado=form.get("centro_resultado"),
        escritorio=form.get("escritorio"),
        resultado=form.get("resultado"),
        sentenca=form.get("sentenca"),
        status=form.get("status", "ativo"),
        risco=form.get("risco"),
        grau_instancia=form.get("grau_instancia"),
        resumo=form.get("resumo"),
        valor_final=texto_para_numero(form.get("valor_final")),
        honorarios_advogado=texto_para_numero(form.get("honorarios_advogado")),
        honorarios_periciais=texto_para_numero(form.get("honorarios_periciais")),
        custas_processuais=texto_para_numero(form.get("custas_processuais")),
        valor_recursal=texto_para_numero(form.get("valor_recursal")),
        valor_alvara=texto_para_numero(form.get("valor_alvara")),
        data_alvara=texto_para_data(form.get("data_alvara")),
    )


def preencher_partes_e_advogados(processo, form):
    """Preenche autores, réus, advogados, movimentos e rateio - comum a todas as páginas."""
    for nome in form.getlist("autor_nome"):
        if nome.strip():
            processo.partes.append(Parte(tipo="autor", nome=nome.strip()))
    for nome in form.getlist("reu_nome"):
        if nome.strip():
            processo.partes.append(Parte(tipo="reu", nome=nome.strip()))

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


@app.route("/inicio")
def inicio():
    return render_template("inicio.html")


@app.route("/cadastro/civel", methods=["GET", "POST"])
def cadastro_civel():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_civel.html", sucesso=sucesso)

    form = request.form
    processo = montar_processo_base(form)
    processo.origem_cadastro = "civel"
    preencher_partes_e_advogados(processo, form)

    # Pedidos e requerimentos (cível) - checkboxes marcados
    for descricao in form.getlist("pedido_civel"):
        if descricao.strip():
            processo.pedidos_civeis.append(PedidoCivel(descricao=descricao.strip()))

    db.session.add(processo)
    db.session.commit()

    return redirect(url_for("cadastro_civel", sucesso=1))


@app.route("/cadastro/trabalhista", methods=["GET", "POST"])
def cadastro_trabalhista():
    if request.method == "GET":
        sucesso = request.args.get("sucesso") == "1"
        return render_template("cadastro_trabalhista.html", sucesso=sucesso)

    form = request.form
    processo = montar_processo_base(form)
    processo.origem_cadastro = "trabalhista"
    preencher_partes_e_advogados(processo, form)

    # Pedidos/verbas trabalhistas (verba + valor, pareados pela posição)
    verbas = form.getlist("pedido_verba")
    valores = form.getlist("pedido_valor")
    for verba, valor in zip(verbas, valores):
        if verba.strip():
            processo.pedidos_trabalhistas.append(
                PedidoTrabalhista(verba=verba.strip(), valor=texto_para_numero(valor))
            )

    db.session.add(processo)
    db.session.commit()

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


if __name__ == "__main__":
    app.run(debug=True)