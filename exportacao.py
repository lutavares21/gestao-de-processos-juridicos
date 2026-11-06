"""Exportação de processos para Excel (.xlsx) e PDF.

Dois usos:
  * gerar_excel_processo()  - UM processo, com uma aba por quadro escolhido.
  * gerar_excel_processos() - VÁRIOS processos, uma linha por processo, com as
                              colunas agrupadas pelos quadros escolhidos.

Os quadros (seções) espelham a página "Visualizar processo". Para incluir ou
tirar um dado da exportação, basta mexer na função do quadro correspondente.

O usuário também escolhe, dentro de cada quadro, quais campos/tabelas entram
no arquivo. Essa escolha chega como `escolha`: {id_do_quadro: {chaves}}, onde
as chaves são as dos campos (ex.: "uf") e "t0", "t1"... para as tabelas do
quadro. Com `escolha=None`, tudo é exportado.

Este módulo não depende do Flask. O openpyxl só é importado na hora de gerar o
arquivo, para o sistema continuar subindo mesmo se ele não estiver instalado.
"""
import io
from datetime import date, datetime
from decimal import Decimal

# Formatos de célula do Excel
FORMATO_DATA = "DD/MM/YYYY"
FORMATO_MOEDA = '"R$" #,##0.00'

# ----------------------------------------------------------------------------
# Quadros disponíveis: (id, título na tela, descrição, título da aba/coluna)
# ----------------------------------------------------------------------------
SECOES_EXPORTACAO = [
    ("identificacao", "Identificação",
     "Número, vara/juizado, comarca, UF, tipo de ação e parte principal", "Identificação"),
    ("datas", "Datas de Distribuição, Audiências, Arquivamento e Valor da Causa",
     "Distribuição, audiências, arquivamento, valor da causa/provisão e tempo de tramitação",
     "Datas e valor da causa"),
    ("partes", "Partes envolvidas",
     "Partes e advogados de cada lado", "Partes envolvidas"),
    ("organizacao", "Organização interna",
     "Centro de resultados, escritório e rateio", "Organização interna"),
    ("resultado", "Resultado, status e risco",
     "Resultado, sentença, grau/instância, status e risco", "Resultado, status e risco"),
    ("pedidos", "Pedidos e requerimentos",
     "Pedidos/verbas (trabalhista), pedidos marcados (cível) ou títulos e acordos (recuperação)",
     "Pedidos e requerimentos"),
    ("acompanhamento", "Acompanhamento",
     "Movimentos, resumo do processo e relatório importado", "Acompanhamento"),
    ("financeiro", "Valores financeiros",
     "Valores pagos, honorários, custas, depósito recursal e alvará", "Valores financeiros"),
]
IDS_SECOES = [s[0] for s in SECOES_EXPORTACAO]
TITULO_ABA = {s[0]: s[3] for s in SECOES_EXPORTACAO}

# ----------------------------------------------------------------------------
# Rótulos (iguais aos da página de detalhes)
# ----------------------------------------------------------------------------
ROTULOS_TIPO_ACAO = {
    "acao_anulatoria": "Ação Anulatória", "acao_civil_publica": "Ação Civil Pública",
    "acao_consignacao_pagamento": "Ação de Consignação em Pagamento",
    "acao_cumprimento": "Ação de Cumprimento", "acao_rescisoria": "Ação Rescisória",
    "dissidio_coletivo": "Dissídio Coletivo",
    "inquerito_falta_grave": "Inquérito para Apuração de Falta Grave",
    "mandado_seguranca": "Mandado de Segurança", "reclamacao_trabalhista": "Reclamação Trabalhista",
    "acao_civel": "Ação Cível", "acao_trabalhista": "Ação Trabalhista",
    "cobranca_extrajudicial": "Cobrança Extrajudicial", "cobranca_judicial": "Cobrança Judicial",
    "outros": "Outros",
}
ROTULOS_MODALIDADE = {
    "hibrida": "Híbrida", "presencial": "Presencial",
    "telepresencial": "Telepresencial", "videoconferencia": "Videoconferência",
}
ROTULOS_STATUS = {
    "ativo": "Ativo", "ativo_sem_exposicao": "Ativo - Sem exposição",
    "arquivado": "Arquivado", "suspenso": "Suspenso",
}
ROTULOS_RISCO = {"provavel": "Provável", "possivel": "Possível", "remoto": "Remoto"}
ROTULOS_STATUS_PEDIDO = {"em_analise": "Em análise", "deferido": "Deferido", "indeferido": "Indeferido"}
ROTULOS_SITUACAO_PARCELA = {"pago": "Recebido", "parcial": "Recebido parcial", "pendente": "Pendente"}


def rotulos_grau(origem):
    return {
        "primeira_instancia": "1ª Instância",
        "segunda_instancia_trt": "2ª Instância - Recurso TRT",
        "segunda_instancia_tj": "2ª Instância - TJ",
        "segunda_instancia_trf": "2ª Instância - TRF",
        "execucao": "Execução",
        "stj": "Tribunal Superior - STJ",
        "stf": "Instância Extraordinária - STF" if origem == "trabalhista" else "Tribunal Superior - STF",
        "tst": "Instância Extraordinária - TST",
        "nao_informado": "Não informado",
    }


def rotulos_do_tipo(origem):
    """Rótulos que mudam conforme o tipo do processo (trabalhista, cível...)."""
    trabalhista = origem == "trabalhista"
    recuperacao = origem == "civel_recuperacao"
    return {
        "juizado": "Vara do Trabalho" if trabalhista else "Juizado",
        "classe": "Classe processual" if trabalhista else "Tipo de ação",
        "ativo": "Reclamante(s)" if trabalhista else "Autor(es)",
        "passivo": "Reclamada" if trabalhista else "Réu",
        "adv_ativo": "Advogado(s) do reclamante" if trabalhista else "Advogado(s) do autor",
        "adv_passivo": "Advogado(s) da reclamada" if trabalhista else "Advogado(s) do réu",
        "tipo_parte_ativa": "reclamante" if trabalhista else "autor",
        "tipo_parte_passiva": "reclamada" if trabalhista else "reu",
        "valor_pago": ("Valor Recuperado (R$)" if recuperacao
                       else ("Valor pago ao reclamante (R$)" if trabalhista else "Valor pago ao autor (R$)")),
        "economia": "Saldo Não Recuperado (R$)" if recuperacao else "Economia Gerada (R$)",
    }


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def _num(valor):
    """Decimal/int -> float (ou None)."""
    if valor is None:
        return None
    return float(valor) if isinstance(valor, Decimal) else valor


def _limpa_underscore(texto):
    return texto.replace("_", " ").title() if texto else ""


def _texto(valor, formato):
    """Valor como texto (usado quando uma tabela vira uma célula só)."""
    if valor is None or valor == "":
        return "—"
    if formato == "data":
        return valor.strftime("%d/%m/%Y")
    if formato == "moeda":
        inteiro, _, decimal = f"{float(valor):,.2f}".partition(".")
        return "R$ " + inteiro.replace(",", ".") + "," + decimal
    return str(valor)


def _nomes_advogados(advogados, lado):
    return "; ".join(
        a.nome + (f" (OAB {a.oab})" if a.oab else "")
        for a in advogados if a.lado == lado
    )


# ----------------------------------------------------------------------------
# Quadros. Cada função devolve:
#   {"campos":  [(chave, rótulo, valor, formato), ...],
#    "tabelas": [{"titulo", "cabecalhos", "formatos", "linhas"}, ...]}
# formato: None | "data" | "moeda" | "inteiro" | "longo" (texto com quebra de linha)
# ----------------------------------------------------------------------------
def _identificacao(p, r, origem, centros):
    nomes = "; ".join(x.nome for x in p.partes if x.tipo == r["tipo_parte_ativa"])
    return {"campos": [
        ("numero", "Número do processo", p.numero_processo, None),
        ("juizado", r["juizado"], p.juizado, None),
        ("comarca", "Comarca", p.comarca, None),
        ("uf", "UF", p.uf, None),
        ("classe", r["classe"], ROTULOS_TIPO_ACAO.get(p.tipo_acao, p.tipo_acao), None),
        ("parte_ativa", r["ativo"], nomes, None),
    ], "tabelas": []}


def _datas(p, r, origem, centros):
    campos = [
        ("dist", "Data de distribuição", p.data_distribuicao, "data"),
        ("causa", "Valor da causa (R$)", _num(p.valor_causa), "moeda"),
    ]
    if origem == "trabalhista":
        provisao = p.valor_provisao if p.valor_provisao is not None else p.valor_causa
        campos.append(("provisao", "Valor da provisão estimada (R$)", _num(provisao), "moeda"))
    campos += [
        ("prox_aud", "Próxima audiência", p.proxima_audiencia, "data"),
        ("prox_hora", "Horário da próxima audiência", p.proxima_audiencia_horario, None),
        ("prox_mod", "Modalidade da audiência",
         ROTULOS_MODALIDADE.get(p.proxima_audiencia_tipo, p.proxima_audiencia_tipo), None),
        ("prox_link", "Link da audiência", p.proxima_audiencia_link, None),
        ("ult_aud", "Última audiência", p.ultima_audiencia, "data"),
        ("aud_ini", "Audiência inicial", p.audiencia_inicial, "data"),
        ("arquiv", "Data do arquivamento", p.data_arquivamento, "data"),
        ("tramit", "Tempo de tramitação (dias)", p.dias_ativos, "inteiro"),
    ]
    return {"campos": campos, "tabelas": []}


def _partes(p, r, origem, centros):
    ativas = "; ".join(x.nome for x in p.partes if x.tipo == r["tipo_parte_ativa"])
    passivas = "; ".join(x.nome for x in p.partes if x.tipo == r["tipo_parte_passiva"])
    return {"campos": [
        ("p_ativa", r["ativo"], ativas, None),
        ("adv_ativo", r["adv_ativo"], _nomes_advogados(p.advogados, "autor"), None),
        ("p_passiva", r["passivo"], passivas, None),
        ("adv_passivo", r["adv_passivo"], _nomes_advogados(p.advogados, "reu"), None),
    ], "tabelas": []}


def _organizacao(p, r, origem, centros):
    if p.centro_resultado == "rateio":
        cr = "Rateio"
    elif p.centro_resultado:
        cr = centros.get(p.centro_resultado, p.centro_resultado)
    else:
        cr = ""
    rateio = "; ".join(centros.get(x.centro_resultado, x.centro_resultado) for x in p.rateio_crs)
    return {"campos": [
        ("cr", "Centro de Resultados (CR)", cr, None),
        ("escritorio", "Escritório", p.escritorio, None),
        ("rateio", "CRs no rateio", rateio, None),
    ], "tabelas": []}


def _resultado(p, r, origem, centros):
    return {"campos": [
        ("resultado", "Resultado", _limpa_underscore(p.resultado), None),
        ("sentenca", "Sentença", _limpa_underscore(p.sentenca), None),
        ("grau", "Grau/Instância atual", rotulos_grau(origem).get(p.grau_instancia, p.grau_instancia), None),
        ("status", "Status", ROTULOS_STATUS.get(p.status, p.status), None),
        ("risco", "Risco", ROTULOS_RISCO.get(p.risco, p.risco), None),
    ], "tabelas": []}


def _pedidos(p, r, origem, centros):
    if origem == "trabalhista":
        linhas = [
            [ped.verba, _num(ped.valor), ROTULOS_STATUS_PEDIDO.get(ped.status, "Em análise")]
            for ped in p.pedidos_trabalhistas
        ]
        valores = [ped.valor for ped in p.pedidos_trabalhistas if ped.valor is not None]
        return {
            "campos": [("total_pedidos", "Total dos pedidos (R$)", _num(sum(valores)) if valores else None, "moeda")],
            "tabelas": [{"titulo": "Pedidos/Verbas", "cabecalhos": ["Pedido/Verba", "Valor", "Situação"],
                         "formatos": [None, "moeda", None], "linhas": linhas}],
        }

    if origem == "civel_recuperacao":
        titulos = sorted(p.titulos_recuperacao, key=lambda t: t.ordem or 0)
        acordos = sorted(p.acordos_recebimento, key=lambda a: a.ordem or 0)
        linhas_titulos = [[
            t.company, t.cliente, t.nota_fiscal, t.emissao, t.vencimento,
            _num(t.valor), _num(t.saldo), _num(t.juros), _num(t.correcao), _num(t.outros), _num(t.total),
            ROTULOS_STATUS_PEDIDO.get(t.status, "Em análise"),
        ] for t in titulos]
        linhas_acordos = [[
            a.parcela, a.vencimento, _num(a.valor_parcela), _num(a.honorarios_exito), _num(a.sucumbencia),
            _num(a.valor_recebido), a.data_recebimento, _num(a.valor_pago_adv), a.data_pagto_adv,
            ROTULOS_SITUACAO_PARCELA.get(a.situacao or ("pago" if a.valor_recebido else "pendente"), "Pendente"),
        ] for a in acordos]
        return {
            "campos": [
                ("total_titulos", "Total dos títulos (R$)", _num(p.total_titulos_recuperacao), "moeda"),
                ("total_acordo", "Valor do acordo (R$)", _num(p.total_acordo), "moeda"),
                ("total_recebido", "Total recebido do acordo (R$)", _num(p.total_recebido_acordo), "moeda"),
                ("a_receber", "Saldo a receber do acordo (R$)", _num(p.sentenca_a_receber), "moeda"),
            ],
            "tabelas": [
                {"titulo": "Títulos",
                 "cabecalhos": ["Company", "Cliente", "Nota Fiscal", "Emissão", "Vencimento", "Valor", "Saldo",
                                "Juros", "Correção", "Outros", "Total", "Situação"],
                 "formatos": [None, None, None, "data", "data", "moeda", "moeda", "moeda", "moeda", "moeda",
                              "moeda", None],
                 "linhas": linhas_titulos},
                {"titulo": "Acordos e Recebimentos",
                 "cabecalhos": ["Parcela", "Vencimento", "Valor da Parcela", "Honorários Êxito", "Sucumbência",
                                "Valor Recebido", "Data Recebimento", "Valor Pago Adv", "Data Pagto Adv",
                                "Situação"],
                 "formatos": [None, "data", "moeda", "moeda", "moeda", "moeda", "data", "moeda", "data", None],
                 "linhas": linhas_acordos},
            ],
        }

    # Cível
    return {"campos": [
        ("pedidos_civeis", "Pedidos marcados", ", ".join(x.descricao for x in p.pedidos_civeis), None),
    ], "tabelas": []}


def _acompanhamento(p, r, origem, centros):
    movimentos = sorted(p.movimentos, key=lambda m: m.data_movimento or date.min)
    return {"campos": [
        ("resumo", "Resumo do processo", p.resumo, "longo"),
        ("relatorio", "Relatório importado", p.arquivo_relatorio, None),
    ], "tabelas": [{
        "titulo": "Últimos movimentos", "cabecalhos": ["Data", "Movimento"], "formatos": ["data", None],
        "linhas": [[m.data_movimento, m.tipo_movimento] for m in movimentos],
    }]}


def _financeiro(p, r, origem, centros):
    return {"campos": [
        ("valor_pago", r["valor_pago"], _num(p.valor_final), "moeda"),
        ("honor_adv", "Honorários Advocatícios (R$)", _num(p.honorarios_advogado), "moeda"),
        ("economia", r["economia"], _num(p.economia_gerada), "moeda"),
        ("honor_per", "Honorários periciais (R$)", _num(p.honorarios_periciais), "moeda"),
        ("custas", "Custas processuais (R$)", _num(p.custas_processuais), "moeda"),
        ("deposito", "Depósito recursal (R$)", _num(p.deposito_recursal), "moeda"),
        ("alvara", "Alvará (R$)", _num(p.valor_alvara), "moeda"),
        ("data_alvara", "Data do alvará", p.data_alvara, "data"),
    ], "tabelas": []}


_CONSTRUTORES = {
    "identificacao": _identificacao, "datas": _datas, "partes": _partes,
    "organizacao": _organizacao, "resultado": _resultado, "pedidos": _pedidos,
    "acompanhamento": _acompanhamento, "financeiro": _financeiro,
}


def _secao(id_secao, processo, centros):
    origem = processo.origem_cadastro
    return _CONSTRUTORES[id_secao](processo, rotulos_do_tipo(origem), origem, centros)



# ----------------------------------------------------------------------------
# Licitatório: quadros espelham o formulário de cadastro
# ----------------------------------------------------------------------------
SECOES_LICITACAO = [
    ("tipo", "Tipo de processo e participação",
     "Modalidade, ano da licitação, participação e justificativa", "Tipo e participação"),
    ("orgao", "Dados do Órgão Licitante",
     "Edital, processo administrativo, UASG, órgão, contatos e endereço", "Órgão licitante"),
    ("objeto", "Objeto", "Objeto da licitação", "Objeto"),
    ("fase", "Fase Inicial – Acompanhamento",
     "Data da licitação, plataforma, tipo de disputa e datas de acompanhamento", "Fase inicial"),
    ("juridico", "Esclarecimento – Impugnação – Recursos – Contrarrazão",
     "O que foi apresentado e o resultado de cada um", "Impugnação e recursos"),
    ("resumo", "Resumo do Processo e Informação sobre Obrigatoriedade",
     "Resumo do processo", "Resumo"),
    ("custo", "Custo Obrigatório e Garantia",
     "Pilhas, desumidificador, receptor, massa dreve, molde, adaptação, apoio local e garantia",
     "Custo obrigatório"),
    ("contratacao", "Dados da Contratação",
     "Homologação, contrato, ata, vigência, prazo e aditivos", "Contratação"),
    ("faturamento", "Dados Faturamento",
     "Cliente, CNPJ e endereços de faturamento e entrega", "Faturamento"),
    ("responsaveis", "Responsáveis",
     "Nome, telefone e e-mail dos responsáveis", "Responsáveis"),
    ("planilha_itens", "Planilha Processo Licitatório",
     "Itens do edital, nossos lances e dados do vencedor", "Planilha do processo"),
    ("planilha_resumo", "Planilha Resumo Resultado Processo Licitatório",
     "Quantidade, produto, valor total, ticket médio e empresa vencedora", "Planilha resumo"),
]
IDS_SECOES_LICITACAO = [s[0] for s in SECOES_LICITACAO]
TITULO_ABA_LICITACAO = {s[0]: s[3] for s in SECOES_LICITACAO}


def _sim_nao(valor):
    return {"sim": "Sim", "nao": "Não"}.get(valor, "")


def _plataforma(l):
    return (l.plataforma_outra if l.plataforma == "outros" else l.plataforma) or ""


def _lic_tipo(l, c):
    return {"campos": [
        ("modalidade", "Modalidade", c["tipos"].get(l.tipo_processo, l.tipo_processo), None),
        ("ano", "Ano da licitação", l.ano_licitacao, None),
        ("participacao", "Participação", _sim_nao(l.participacao), None),
        ("justificativa", "Justificativa da não participação",
         c["justificativas"].get(l.justificativa_nao_participacao, l.justificativa_nao_participacao), None),
    ], "tabelas": []}


def _lic_orgao(l, c):
    return {"campos": [
        ("edital", "Edital", l.edital, None),
        ("proc_adm", "Processo administrativo", l.processo_administrativo, None),
        ("uasg", "UASG", l.uasg, None),
        ("orgao", "Órgão licitante", l.orgao_licitante, None),
        ("email", "E-mail", l.email, None),
        ("telefone", "Telefone", l.telefone, None),
        ("telefone_2", "Telefone 2", l.telefone_2, None),
        ("endereco", "Endereço", l.endereco, None),
        ("municipio", "Município", l.municipio, None),
        ("estado", "Estado", l.estado, None),
        ("uf", "UF", l.uf, None),
    ], "tabelas": []}


def _lic_objeto(l, c):
    return {"campos": [("objeto", "Objeto da licitação", c["objetos"].get(l.objeto, l.objeto), None)], "tabelas": []}


def _lic_fase(l, c):
    return {"campos": [
        ("data_lic", "Data da licitação", l.data_licitacao, "data"),
        ("hora_lic", "Horário da licitação", l.horario_licitacao, None),
        ("plataforma", "Plataforma", _plataforma(l), None),
        ("disputa", "Tipo de disputa", c["disputas"].get(l.tipo_disputa, l.tipo_disputa), None),
        ("prox_data", "Próxima data de acompanhamento", l.proxima_data_acompanhamento, "data"),
        ("prox_hora", "Horário da próxima data", l.proximo_horario_acompanhamento, None),
        ("ult_data", "Última data de acompanhamento", l.ultima_data_acompanhamento, "data"),
        ("ult_hora", "Horário da última data", l.ultimo_horario_acompanhamento, None),
        ("pen_data", "Penúltima data de acompanhamento", l.penultima_data_acompanhamento, "data"),
        ("pen_hora", "Horário da penúltima data", l.penultimo_horario_acompanhamento, None),
    ], "tabelas": []}


def _lic_juridico(l, c):
    def res(chave):
        return c["resultados"].get(chave, chave) if chave else ""
    return {"campos": [
        ("esclarecimento", "Esclarecimento", _sim_nao(l.esclarecimento), None),
        ("impugnacao", "Impugnação", _sim_nao(l.impugnacao), None),
        ("impugnacao_res", "Resultado da impugnação", res(l.impugnacao_resultado), None),
        ("recursos", "Recursos", _sim_nao(l.recursos), None),
        ("recursos_res", "Resultado dos recursos", res(l.recursos_resultado), None),
        ("contrarrazao", "Contrarrazão", _sim_nao(l.contrarrazao), None),
        ("contrarrazao_res", "Resultado da contrarrazão", res(l.contrarrazao_resultado), None),
    ], "tabelas": []}


def _lic_resumo(l, c):
    return {"campos": [("resumo", "Resumo do processo", l.resumo_processo, "longo")], "tabelas": []}


def _lic_custo(l, c):
    return {"campos": [
        ("pilhas", "Pilhas (cartelas) para cada AASI", l.pilhas_por_aasi, None),
        ("desumid", "Desumidificador para cada AASI", l.desumidificador_por_aasi, None),
        ("receptor", "Receptor para cada AASI", l.receptor_por_aasi, None),
        ("massa", "Massa dreve", l.massa_dreve, None),
        ("molde", "Molde", _sim_nao(l.molde), None),
        ("adaptacao", "Adaptação aberta", _sim_nao(l.adaptacao_aberta), None),
        ("apoio", "Apoio local", _sim_nao(l.apoio_local), None),
        ("garantia", "Garantia da licitação", _sim_nao(l.garantia_licitacao), None),
        ("garantia_qual", "Qual garantia", l.garantia_qual, None),
    ], "tabelas": []}


def _lic_contratacao(l, c):
    return {"campos": [
        ("homologacao", "Data de homologação", l.data_homologacao, "data"),
        ("adjudicacao", "Data de adjudicação", l.data_adjudicacao, "data"),
        ("valor_contrato", "Valor total do contrato (R$)", _num(l.valor_total_contrato), "moeda"),
        ("n_contrato", "Número do contrato", l.numero_contrato, None),
        ("n_ata", "Número da ata", l.numero_ata, None),
        ("contrato_proc", "Número do processo (contrato)", l.contrato_numero_processo, None),
        ("assinatura", "Data de assinatura do contrato", l.data_assinatura_contrato, "data"),
        ("vig_ini", "Vigência inicial", l.vigencia_inicial, "data"),
        ("vig_fim", "Vigência final", l.vigencia_final, "data"),
        ("prazo", "Prazo contratual", l.prazo_contratual, None),
    ], "tabelas": [{
        "titulo": "Aditivos", "cabecalhos": ["Aditivo", "Data do aditivo", "Data final"],
        "formatos": [None, "data", "data"],
        "linhas": [[f"{i}º aditivo", a.data_aditivo, a.data_final] for i, a in enumerate(l.aditivos, start=1)],
    }]}


def _lic_faturamento(l, c):
    return {"campos": [
        ("id_cliente", "ID do cliente", l.id_cliente, None),
        ("cliente", "Cliente", l.cliente, None),
        ("cnpj", "CNPJ", l.cnpj, None),
        ("end_fat", "Endereço de faturamento", l.endereco_faturamento, None),
        ("end_ent", "Endereço de entrega", l.endereco_entrega, None),
    ], "tabelas": []}


def _lic_responsaveis(l, c):
    campos = []
    for prefixo, rotulo in (("resp_fono", "Fonoaudiólogo(a)"), ("resp_orgao", "Responsável pelo órgão"),
                            ("resp_envio", "Responsável pelo envio do pedido")):
        campos += [
            (prefixo + "_nome", f"{rotulo} - nome", getattr(l, prefixo + "_nome"), None),
            (prefixo + "_tel", f"{rotulo} - telefone", getattr(l, prefixo + "_telefone"), None),
            (prefixo + "_email", f"{rotulo} - e-mail", getattr(l, prefixo + "_email"), None),
        ]
    return {"campos": campos, "tabelas": []}


def _lic_planilha_itens(l, c):
    empresas, participacoes = c["empresas"], c["participacao_item"]
    linhas = [[
        i.item, i.quantidade, i.produto, i.marca, i.modelo, i.codigo_produto,
        _num(i.unitario_inicial), _num(i.ultimo_lance_atomed), _num(i.valor_vencedor),
        empresas.get(i.empresa_vencedora, i.empresa_vencedora), i.marca_vencedora, i.modelo_vencedor,
        participacoes.get(i.tipo_participacao, i.tipo_participacao),
    ] for i in l.itens]
    return {"campos": [], "tabelas": [{
        "titulo": "Itens",
        "cabecalhos": ["Itens", "Quant.", "Produto", "Marca", "Modelo", "Código produto", "Unitário inicial",
                       "Último lance Atomed", "Valor vencedor", "Empresa vencedora", "Marca vencedora",
                       "Modelo vencedor", "Ampla participação / Exclusivo ME/EPP"],
        "formatos": [None, "inteiro", None, None, None, None, "moeda", "moeda", "moeda", None, None, None, None],
        "linhas": linhas,
    }]}


def _lic_planilha_resumo(l, c):
    linhas = [[
        r.quantidade, c["produtos"].get(r.produto, r.produto), _num(r.valor_total), _num(r.ticket_medio),
        c["empresas"].get(r.empresa_vencedora, r.empresa_vencedora),
    ] for r in l.resumos]
    return {"campos": [], "tabelas": [{
        "titulo": "Resumo do resultado",
        "cabecalhos": ["Quantidade", "Produto", "Valor total", "Ticket médio", "Empresa vencedora"],
        "formatos": ["inteiro", None, "moeda", "moeda", None],
        "linhas": linhas,
    }]}


_CONSTRUTORES_LICITACAO = {
    "tipo": _lic_tipo, "orgao": _lic_orgao, "objeto": _lic_objeto, "fase": _lic_fase,
    "juridico": _lic_juridico, "resumo": _lic_resumo, "custo": _lic_custo,
    "contratacao": _lic_contratacao, "faturamento": _lic_faturamento,
    "responsaveis": _lic_responsaveis, "planilha_itens": _lic_planilha_itens,
    "planilha_resumo": _lic_planilha_resumo,
}


def _e_licitacao(objeto):
    return hasattr(objeto, "orgao_licitante")


def catalogo(tipo):
    """(lista de quadros, ids) do tipo: 'licitatorio' ou qualquer outro (processo)."""
    return SECOES_LICITACAO if tipo == "licitatorio" else SECOES_EXPORTACAO


def _dados_secao(id_secao, objeto, contexto, escolha=None):
    """Dados de um quadro, seja de um processo ou de uma licitação.

    `escolha` ({quadro: {chaves}}) limita os campos e tabelas devolvidos.
    """
    if _e_licitacao(objeto):
        dados = _CONSTRUTORES_LICITACAO[id_secao](objeto, contexto)
    else:
        dados = _secao(id_secao, objeto, contexto)
    # Cada tabela ganha uma chave ("t0", "t1"...) para poder ser escolhida
    for i, tabela in enumerate(dados["tabelas"]):
        tabela["chave"] = f"t{i}"
    if escolha is None:
        return dados
    permitidas = escolha.get(id_secao, set())
    return {
        "campos": [c for c in dados["campos"] if c[0] in permitidas],
        "tabelas": [t for t in dados["tabelas"] if t["chave"] in permitidas],
    }


class _Nada:
    """Objeto vazio usado só para descobrir QUAIS campos cada quadro tem
    (sem precisar de um processo real): qualquer atributo é vazio, e qualquer
    lista é uma lista vazia."""
    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        return _Nada()
    def __iter__(self):
        return iter(())
    def __bool__(self):
        return False
    def __eq__(self, outro):
        return False
    def __hash__(self):
        return 0


def catalogo_campos(tipo, contexto):
    """Campos e tabelas de cada quadro do tipo, para a tela de exportação.

    Devolve {id_do_quadro: {"campos": [(chave, rótulo)], "tabelas": [(chave, título)]}}.
    Sai das mesmas funções que montam o arquivo, então nunca fica desatualizado.
    """
    modelo = _Nada()
    catalogo_quadros = {}
    for id_secao in [s[0] for s in catalogo(tipo)]:
        if tipo == "licitatorio":
            dados = _CONSTRUTORES_LICITACAO[id_secao](modelo, contexto)
        else:
            dados = _CONSTRUTORES[id_secao](modelo, rotulos_do_tipo(tipo), tipo, contexto)
        catalogo_quadros[id_secao] = {
            "campos": [(c[0], c[1]) for c in dados["campos"]],
            "tabelas": [(f"t{i}", t["titulo"]) for i, t in enumerate(dados["tabelas"])],
        }
    return catalogo_quadros


def chaves_validas(tipo, contexto):
    """Conjunto de "quadro:chave" aceitos para o tipo (para validar o formulário)."""
    catalogo_quadros = catalogo_campos(tipo, contexto)
    return {
        f"{id_secao}:{chave}"
        for id_secao, itens in catalogo_quadros.items()
        for chave, _rotulo in itens["campos"] + itens["tabelas"]
    }


def _titulos(objeto):
    if _e_licitacao(objeto):
        return SECOES_LICITACAO, TITULO_ABA_LICITACAO
    return SECOES_EXPORTACAO, TITULO_ABA


# ----------------------------------------------------------------------------
# Excel
# ----------------------------------------------------------------------------
def _estilos():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    lado = Side(style="thin", color="D9D4C7")
    return {
        "titulo": Font(name="Calibri", size=14, bold=True, color="16233D"),
        "cabecalho_fonte": Font(name="Calibri", bold=True, color="FFFFFF"),
        "cabecalho_fundo": PatternFill("solid", start_color="16233D"),
        "faixa_fonte": Font(name="Calibri", bold=True, color="FFFFFF"),
        "faixa_fundo": PatternFill("solid", start_color="B7924B"),
        "rotulo_fonte": Font(name="Calibri", bold=True, color="3A3A3A"),
        "rotulo_fundo": PatternFill("solid", start_color="F2F0EB"),
        "borda": Border(left=lado, right=lado, top=lado, bottom=lado),
        "topo_quebra": Alignment(vertical="top", wrap_text=True),
        "centro": Alignment(horizontal="center", vertical="center", wrap_text=True),
        # Alinhamento por tipo de dado (usado na planilha de vários processos)
        "alin_texto": Alignment(horizontal="left", vertical="center", wrap_text=True),
        "alin_longo": Alignment(horizontal="left", vertical="top", wrap_text=True),
        "alin_data": Alignment(horizontal="center", vertical="center"),
        "alin_numero": Alignment(horizontal="right", vertical="center"),
        "zebra": PatternFill("solid", start_color="FAF8F4"),
    }


def _grava(celula, valor, formato, est, tabular=True, zebra=False):
    """Escreve o valor na célula com o formato certo (data, moeda...).

    tabular=True  -> alinha conforme o tipo (data centralizada, valores à direita,
                     texto à esquerda). tabular=False -> texto no topo, à esquerda
                     (usado na aba "Campo / Valor" de um processo só).
    zebra=True    -> aplica o fundo claro das linhas alternadas."""
    celula.border = est["borda"]
    if zebra:
        celula.fill = est["zebra"]
    if tabular:
        celula.alignment = {
            "data": est["alin_data"],
            "moeda": est["alin_numero"],
            "inteiro": est["alin_numero"],
            "longo": est["alin_longo"],
        }.get(formato, est["alin_texto"])
    else:
        celula.alignment = est["topo_quebra"]
    if valor is None or valor == "":
        celula.value = None
        return
    celula.value = valor
    if formato == "data":
        celula.number_format = FORMATO_DATA
    elif formato == "moeda":
        celula.number_format = FORMATO_MOEDA
    elif formato == "inteiro":
        celula.number_format = "0"


def _tamanho_texto(valor, formato):
    """Quantos caracteres a célula ocupa (para calcular a largura da coluna)."""
    if valor is None or valor == "":
        return 0
    if formato == "data":
        return 10
    if formato == "moeda":
        return len(_texto(valor, formato))
    texto = str(valor)
    return max(len(linha) for linha in texto.split("\n"))


def _largura_coluna(rotulo, conteudo, minimo=12, maximo=55):
    """Largura da coluna: cabe o conteúdo e o cabeçalho (em até 2 linhas),
    com folga para o botão de filtro do Excel, que cobre o fim do título."""
    cab = len(rotulo)
    cab_largura = cab + 7 if cab <= 18 else cab // 2 + 10
    return max(minimo, min(maximo, max(cab_largura, conteudo + 2)))


def _linhas_necessarias(texto, largura):
    """Quantas linhas de texto a célula ocupa com a quebra automática."""
    total = 0
    for parte in str(texto).split("\n"):
        total += max(1, -(-len(parte) // max(1, int(largura) - 1)))
    return total


def _largura(valores, minimo=12, maximo=60):
    maior = max((len(str(v)) for v in valores if v is not None), default=0)
    return max(minimo, min(maximo, maior + 2))


def _arquivo(wb):
    saida = io.BytesIO()
    wb.save(saida)
    saida.seek(0)
    return saida


def gerar_excel_processo(processo, secoes, centros, escolha=None):
    """Excel de UM processo (ou uma licitação): uma aba por quadro escolhido
    (na ordem da página). Para licitação, `centros` é o dicionário de rótulos
    do licitatório (tipos, objetos, empresas...)."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    est = _estilos()
    wb = Workbook()
    wb.remove(wb.active)

    lista_secoes, titulos_aba = _titulos(processo)
    for id_secao in [s[0] for s in lista_secoes if s[0] in secoes]:
        dados = _dados_secao(id_secao, processo, centros, escolha)
        if not dados["campos"] and not dados["tabelas"]:
            continue  # nada marcado neste quadro
        ws = wb.create_sheet(titulos_aba[id_secao][:31])
        if _e_licitacao(processo):
            ws["A1"] = f"Licitação {processo.edital} - {processo.orgao_licitante}"
        else:
            ws["A1"] = f"Processo {processo.numero_processo}"
        ws["A1"].font = est["titulo"]
        ws["A2"] = next(s[1] for s in lista_secoes if s[0] == id_secao)
        ws["A2"].font = est["rotulo_fonte"]

        larguras = {}
        linha = 4

        def acumula(coluna, valor):
            larguras.setdefault(coluna, []).append(valor)

        if dados["campos"]:
            for coluna, texto in enumerate(("Campo", "Valor"), start=1):
                c = ws.cell(row=linha, column=coluna, value=texto)
                c.font, c.fill, c.alignment, c.border = (
                    est["cabecalho_fonte"], est["cabecalho_fundo"], est["centro"], est["borda"])
            linha += 1
            for _chave, rotulo, valor, formato in dados["campos"]:
                c_rotulo = ws.cell(row=linha, column=1, value=rotulo)
                c_rotulo.font, c_rotulo.fill, c_rotulo.alignment, c_rotulo.border = (
                    est["rotulo_fonte"], est["rotulo_fundo"], est["topo_quebra"], est["borda"])
                _grava(ws.cell(row=linha, column=2), valor, formato, est, tabular=False)
                acumula(1, rotulo)
                acumula(2, valor if formato is None else None)
                linha += 1

        for tabela in dados["tabelas"]:
            linha += 1
            ws.cell(row=linha, column=1, value=tabela["titulo"]).font = est["titulo"]
            linha += 1
            for coluna, texto in enumerate(tabela["cabecalhos"], start=1):
                c = ws.cell(row=linha, column=coluna, value=texto)
                c.font, c.fill, c.alignment, c.border = (
                    est["cabecalho_fonte"], est["cabecalho_fundo"], est["centro"], est["borda"])
                acumula(coluna, texto)
            linha += 1
            if not tabela["linhas"]:
                ws.cell(row=linha, column=1, value="Nenhum registro.").font = est["rotulo_fonte"]
                linha += 1
            for registro in tabela["linhas"]:
                for coluna, (valor, formato) in enumerate(zip(registro, tabela["formatos"]), start=1):
                    _grava(ws.cell(row=linha, column=coluna), valor, formato, est)
                    acumula(coluna, valor if formato is None else None)
                linha += 1

        for coluna, valores in larguras.items():
            ws.column_dimensions[get_column_letter(coluna)].width = _largura(valores, minimo=14, maximo=70)
        ws.sheet_view.showGridLines = False

    return _arquivo(wb)


def _tabela_como_texto(tabela):
    """Uma tabela inteira em uma célula: uma linha de texto por registro."""
    if not tabela["linhas"]:
        return ""
    return "\n".join(
        " | ".join(_texto(v, f) for v, f in zip(registro, tabela["formatos"]))
        for registro in tabela["linhas"]
    )


def gerar_excel_processos(processos, origem, secoes, centros, escolha=None):
    """Excel de VÁRIOS processos: uma linha por processo. A primeira coluna é
    sempre o número do processo; depois vêm as colunas dos quadros escolhidos,
    com uma faixa dourada em cima indicando o quadro."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    est = _estilos()
    wb = Workbook()
    ws = wb.active
    ws.title = "Processos"

    licitacao = origem == "licitatorio"
    lista_secoes = SECOES_LICITACAO if licitacao else SECOES_EXPORTACAO
    titulos_aba = TITULO_ABA_LICITACAO if licitacao else TITULO_ABA
    ids = [s[0] for s in lista_secoes if s[0] in secoes]
    # A primeira coluna é sempre fixa (número do processo / edital); a mesma
    # informação não se repete dentro do quadro de identificação.
    rotulo_fixo, chave_fixa = ("Edital", "edital") if licitacao else ("Número do processo", "numero")

    # Estrutura das colunas (usa o primeiro item só para obter os rótulos)
    colunas = [("Processo", rotulo_fixo, None, ("fixo", chave_fixa))]
    for id_secao in ids:
        if processos:
            modelo = _dados_secao(id_secao, processos[0], centros, escolha)
        else:
            modelo = {"campos": [], "tabelas": []}
        for chave, rotulo, _valor, formato in modelo["campos"]:
            if chave == chave_fixa:
                continue  # já é a primeira coluna
            colunas.append((titulos_aba[id_secao], rotulo, formato, (id_secao, "campo", chave)))
        for indice, tabela in enumerate(modelo["tabelas"]):
            colunas.append((titulos_aba[id_secao], tabela["titulo"], "longo", (id_secao, "tabela", indice)))

    # Cabeçalhos: faixa do quadro (linha 1) e nome da coluna (linha 2)
    inicio = 1
    while inicio <= len(colunas):
        quadro = colunas[inicio - 1][0]
        fim = inicio
        while fim < len(colunas) and colunas[fim][0] == quadro:
            fim += 1
        faixa = ws.cell(row=1, column=inicio, value=quadro)
        faixa.font, faixa.fill, faixa.alignment = est["faixa_fonte"], est["faixa_fundo"], est["centro"]
        for c in range(inicio, fim + 1):
            ws.cell(row=1, column=c).fill = est["faixa_fundo"]
        if fim > inicio:
            ws.merge_cells(start_row=1, start_column=inicio, end_row=1, end_column=fim)
        inicio = fim + 1
    for numero, (_quadro, rotulo, _formato, _ref) in enumerate(colunas, start=1):
        c = ws.cell(row=2, column=numero, value=rotulo)
        c.font, c.fill, c.alignment, c.border = (
            est["cabecalho_fonte"], est["cabecalho_fundo"], est["centro"], est["borda"])

    # Linhas
    larguras = [[] for _ in colunas]      # tamanho do conteúdo de cada coluna
    textos_quebra = {}                    # {linha: [(coluna, texto)]} para a altura
    for n_linha, processo in enumerate(processos, start=3):
        cache = {i: _dados_secao(i, processo, centros, escolha) for i in ids}
        for n_coluna, (_quadro, _rotulo, formato, ref) in enumerate(colunas, start=1):
            if ref[0] == "fixo":
                valor = processo.edital if licitacao else processo.numero_processo
                formato = None
            else:
                dados = cache[ref[0]]
                if ref[1] == "campo":
                    valor = next(v for (k, _r, v, _f) in dados["campos"] if k == ref[2])
                else:
                    valor = _tabela_como_texto(dados["tabelas"][ref[2]])
            _grava(ws.cell(row=n_linha, column=n_coluna), valor, formato, est,
                   zebra=(n_linha % 2 == 1))
            larguras[n_coluna - 1].append(_tamanho_texto(valor, formato))
            if formato in (None, "longo") and valor:
                textos_quebra.setdefault(n_linha, []).append((n_coluna, valor))

    # Largura de cada coluna
    largura_final = []
    for n_coluna, (_quadro, rotulo, _formato, _ref) in enumerate(colunas, start=1):
        largura = _largura_coluna(rotulo, max(larguras[n_coluna - 1], default=0))
        largura_final.append(largura)
        ws.column_dimensions[get_column_letter(n_coluna)].width = largura

    # Altura do cabeçalho (título pode quebrar em até 3 linhas)
    linhas_cab = max(
        min(3, -(-int(len(rotulo) * 1.15) // max(1, int(largura_final[i]) - 5)))
        for i, (_q, rotulo, _f, _r) in enumerate(colunas)
    )
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = max(32, linhas_cab * 16 + 8)

    # Altura de cada linha: acompanha o texto, com um limite para um campo muito
    # longo não deixar a planilha enorme (a célula continua com o texto completo).
    altura_linha, altura_maxima = 20, 105
    for n_linha in range(3, len(processos) + 3):
        linhas = max(
            (_linhas_necessarias(t, largura_final[c - 1]) for c, t in textos_quebra.get(n_linha, [])),
            default=1,
        )
        ws.row_dimensions[n_linha].height = min(altura_maxima, max(altura_linha, linhas * 15 + 5))
    ws.freeze_panes = "B3"
    if processos:
        ws.auto_filter.ref = f"A2:{get_column_letter(len(colunas))}{len(processos) + 2}"

    return _arquivo(wb)


# ----------------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------------
def gerar_pdf(registros, origem, secoes, centros, escolha=None):
    """PDF de um ou VÁRIOS processos (ou licitações): cada processo começa em
    uma página nova, com os quadros escolhidos na ordem da página e, dentro de
    cada quadro, só os campos/tabelas marcados.

    O reportlab só é importado aqui, para o sistema continuar subindo mesmo se
    ele não estiver instalado (mesmo cuidado do openpyxl).
    """
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    azul, dourado = colors.HexColor("#16233D"), colors.HexColor("#B7924B")
    cinza, borda = colors.HexColor("#F2F0EB"), colors.HexColor("#D9D4C7")

    est_titulo = ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=azul)
    est_faixa = ParagraphStyle("faixa", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                               textColor=colors.white, backColor=dourado, borderPadding=(3, 5, 3, 5),
                               spaceAfter=8, keepWithNext=1)
    est_subtitulo = ParagraphStyle("subtitulo", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=azul,
                                   spaceAfter=3, keepWithNext=1)
    est_rotulo = ParagraphStyle("rotulo", fontName="Helvetica-Bold", fontSize=8.5, leading=11)
    est_valor = ParagraphStyle("valor", fontName="Helvetica", fontSize=8.5, leading=11)
    est_vazio = ParagraphStyle("vazio", fontName="Helvetica-Oblique", fontSize=8.5, leading=11)

    def par(valor, formato, estilo):
        texto = escape(_texto(valor, formato)).replace("\n", "<br/>")
        return Paragraph(texto, estilo)

    pagina = landscape(A4)
    margem = 14 * mm
    largura = pagina[0] - 2 * margem

    licitacao = origem == "licitatorio"
    lista_secoes = SECOES_LICITACAO if licitacao else SECOES_EXPORTACAO
    ids = [s[0] for s in lista_secoes if s[0] in secoes]
    titulo_quadro = {s[0]: s[1] for s in lista_secoes}

    elementos = []
    for n, registro in enumerate(registros):
        if n:
            elementos.append(PageBreak())
        if licitacao:
            elementos.append(Paragraph(escape(f"Licitação {registro.edital} - {registro.orgao_licitante}"), est_titulo))
        else:
            elementos.append(Paragraph(escape(f"Processo {registro.numero_processo}"), est_titulo))
        elementos.append(Spacer(1, 6))

        for id_secao in ids:
            dados = _dados_secao(id_secao, registro, centros, escolha)
            if not dados["campos"] and not dados["tabelas"]:
                continue
            elementos.append(Paragraph(escape(titulo_quadro[id_secao]), est_faixa))

            if dados["campos"]:
                linhas = [[Paragraph(escape(rotulo), est_rotulo), par(valor, formato, est_valor)]
                          for _chave, rotulo, valor, formato in dados["campos"]]
                tabela = Table(linhas, colWidths=[largura * 0.32, largura * 0.68])
                tabela.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, -1), cinza),
                    ("GRID", (0, 0), (-1, -1), 0.4, borda),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                elementos.append(tabela)
                elementos.append(Spacer(1, 8))

            for sub in dados["tabelas"]:
                elementos.append(Paragraph(escape(sub["titulo"]), est_subtitulo))
                if not sub["linhas"]:
                    elementos.append(Paragraph("Nenhum registro.", est_vazio))
                    elementos.append(Spacer(1, 8))
                    continue
                colunas = len(sub["cabecalhos"])
                tamanho = 8 if colunas <= 6 else (7 if colunas <= 9 else 6.2)
                est_cab = ParagraphStyle("cab", fontName="Helvetica-Bold", fontSize=tamanho,
                                         leading=tamanho + 2, textColor=colors.white, alignment=1)
                est_cel = ParagraphStyle("cel", fontName="Helvetica", fontSize=tamanho, leading=tamanho + 2)
                corpo = [[Paragraph(escape(c), est_cab) for c in sub["cabecalhos"]]]
                for reg in sub["linhas"]:
                    corpo.append([par(v, f, est_cel) for v, f in zip(reg, sub["formatos"])])
                tabela = Table(corpo, colWidths=[largura / colunas] * colunas, repeatRows=1)
                tabela.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), azul),
                    ("GRID", (0, 0), (-1, -1), 0.4, borda),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, cinza]),
                ]))
                elementos.append(tabela)
                elementos.append(Spacer(1, 8))
            elementos.append(Spacer(1, 6))

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#6B6B6B"))
        canvas.drawString(margem, 8 * mm, f"Gerado em {datetime.now():%d/%m/%Y %H:%M}")
        canvas.drawRightString(pagina[0] - margem, 8 * mm, f"Página {doc.page}")
        canvas.restoreState()

    saida = io.BytesIO()
    SimpleDocTemplate(
        saida, pagesize=pagina, leftMargin=margem, rightMargin=margem,
        topMargin=margem, bottomMargin=16 * mm, title="Exportação de processos",
    ).build(elementos, onFirstPage=rodape, onLaterPages=rodape)
    saida.seek(0)
    return saida