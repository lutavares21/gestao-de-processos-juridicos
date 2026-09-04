# -*- coding: utf-8 -*-
"""
App Flask - Gestão de Processos Jurídicos
"""

from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from werkzeug.security import check_password_hash
from models import db, Processo, Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR, PedidoCivel, Usuario

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///processos.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Chave usada para proteger a sessão de login. Em algum momento vale trocar
# por uma variável de ambiente, mas por enquanto um valor fixo já funciona.
app.config["SECRET_KEY"] = "troque-esta-chave-por-uma-string-aleatoria-depois"

db.init_app(app)

with app.app_context():
    db.create_all()

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para acessar o sistema."


@login_manager.user_loader
def carregar_usuario(usuario_id):
    return db.session.get(Usuario, int(usuario_id))


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

    usuario_login = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "")

    usuario = Usuario.query.filter_by(usuario=usuario_login).first()

    if usuario and check_password_hash(usuario.senha_hash, senha):
        login_user(usuario)
        proxima = request.args.get("next")
        return redirect(proxima or url_for("index"))

    return render_template("login.html", erro_login=True)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


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
    )


@app.route("/")
@login_required
def index():
    return render_template("inicio.html")


def numero_processo_ja_existe(numero_processo):
    """Verifica se já existe algum processo (cível ou trabalhista) cadastrado
    com esse número. Usado para bloquear cadastros duplicados."""
    return Processo.query.filter_by(numero_processo=numero_processo).first() is not None


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
@login_required
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
@login_required
def processos_civel():
    processos = (
        Processo.query.filter_by(origem_cadastro="civel")
        .order_by(Processo.data_cadastro.desc())
        .all()
    )
    return render_template("processos_civel.html", processos=processos)


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


@app.route("/registro-processos")
@login_required
def registro_processos():

    def contar(**filtros):
        """Conta processos que batem com os filtros dados.
        Ex: contar(origem_cadastro='civel', status='ativo')"""
        query = Processo.query
        for campo, valor in filtros.items():
            query = query.filter(getattr(Processo, campo) == valor)
        return query.count()

    def somar(coluna, **filtros):
        """Soma uma coluna numérica (ex: Processo.valor_causa) para os
        processos que batem com os filtros dados. Nunca retorna None."""
        query = db.session.query(func.coalesce(func.sum(coluna), 0))
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
    #                    depósito recursal.
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
    vitorias_qtd = Processo.query.filter(
        Processo.resultado == "ganhamos", Processo.sentenca != "procedente_parcial"
    ).count()
    parciais_qtd = Processo.query.filter(Processo.sentenca == "procedente_parcial").count()
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
    ativos = Processo.query.filter_by(status="ativo").all()
    for p in ativos:
        if p.risco in matriz:
            matriz[p.risco][faixa_impacto(p.valor_causa)] += 1
    dados["matriz_risco"] = matriz

    # ------------------------------------------------------------------
    # 4c. ATENÇÃO DA DIRETORIA - top 5 processos ativos de risco provável,
    #     ordenados pela maior exposição financeira.
    # ------------------------------------------------------------------
    alertas_processos = (
        Processo.query.filter_by(status="ativo", risco="provavel")
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
    top_cr = (
        db.session.query(
            Processo.centro_resultado,
            func.count(Processo.id),
            func.coalesce(func.sum(Processo.valor_causa), 0),
        )
        .filter(Processo.status == "ativo", Processo.centro_resultado.isnot(None))
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
        qtd = Processo.query.filter(
            func.extract("year", Processo.data_distribuicao) == ano,
            func.extract("month", Processo.data_distribuicao) == mes,
        ).count()
        evolucao.append({"label": f"{nomes_mes[mes - 1]}/{str(ano)[2:]}", "qtd": qtd})
    dados["evolucao_mensal"] = evolucao

    return render_template("registro_processos.html", dados=dados)


if __name__ == "__main__":
    app.run(debug=True)