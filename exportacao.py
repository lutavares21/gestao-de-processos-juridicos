"""Exportação de processos para Excel (.xlsx).

Dois usos:
  * gerar_excel_processo()  - UM processo, com uma aba por quadro escolhido.
  * gerar_excel_processos() - VÁRIOS processos, uma linha por processo, com as
                              colunas agrupadas pelos quadros escolhidos.

Os quadros (seções) espelham a página "Visualizar processo". Para incluir ou
tirar um dado da exportação, basta mexer na função do quadro correspondente.

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


def _dados_secao(id_secao, objeto, contexto):
    """Dados de um quadro, seja de um processo ou de uma licitação."""
    if _e_licitacao(objeto):
        return _CONSTRUTORES_LICITACAO[id_secao](objeto, contexto)
    return _secao(id_secao, objeto, contexto)


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
    }


def _grava(celula, valor, formato, est):
    """Escreve o valor na célula com o formato certo (data, moeda...)."""
    celula.border = est["borda"]
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


def _largura(valores, minimo=12, maximo=60):
    maior = max((len(str(v)) for v in valores if v is not None), default=0)
    return max(minimo, min(maximo, maior + 2))


def _arquivo(wb):
    saida = io.BytesIO()
    wb.save(saida)
    saida.seek(0)
    return saida


def gerar_excel_processo(processo, secoes, centros):
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
        dados = _dados_secao(id_secao, processo, centros)
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
                _grava(ws.cell(row=linha, column=2), valor, formato, est)
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


def gerar_excel_processos(processos, origem, secoes, centros):
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
            modelo = _dados_secao(id_secao, processos[0], centros)
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
    larguras = [[rotulo] for (_q, rotulo, _f, _r) in colunas]
    for n_linha, processo in enumerate(processos, start=3):
        cache = {i: _dados_secao(i, processo, centros) for i in ids}
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
            _grava(ws.cell(row=n_linha, column=n_coluna), valor, formato, est)
            if formato in (None, "longo"):
                larguras[n_coluna - 1].append(str(valor).split("\n")[0] if valor else None)

    for n_coluna, valores in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(n_coluna)].width = _largura(valores, minimo=14, maximo=55)
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 32
    ws.freeze_panes = "B3"
    if processos:
        ws.auto_filter.ref = f"A2:{get_column_letter(len(colunas))}{len(processos) + 2}"

    return _arquivo(wb)
