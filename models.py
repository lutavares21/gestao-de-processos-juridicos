# -*- coding: utf-8 -*-
"""
Modelo de dados do sistema de gestão de processos jurídicos.

Cada classe abaixo representa uma tabela do banco de dados.
"""

from datetime import date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Processo(db.Model):
    """Tabela principal - um registro por processo jurídico."""
    __tablename__ = "processos"

    id = db.Column(db.Integer, primary_key=True)

    # De qual página/formulário este processo foi cadastrado.
    # Usado para separar as listagens (Processos Cadastrados > Cível / Trabalhista).
    origem_cadastro = db.Column(db.String(30))  # 'civel', 'trabalhista'

    # --- Identificação ---
    numero_processo = db.Column(db.String(50), nullable=False, unique=True)
    juizado = db.Column(db.String(120))
    comarca = db.Column(db.String(120))
    tipo_acao = db.Column(db.String(50), nullable=False)
    # Valores possíveis para tipo_acao:
    # 'trabalhista', 'civel', 'juizado_especial_civel', 'tributario',
    # 'procon', 'acao_monitoria', 'acao_execucao'

    # --- Datas e audiências ---
    data_distribuicao = db.Column(db.Date, nullable=False)
    valor_causa = db.Column(db.Numeric(14, 2))
    data_audiencia_1 = db.Column(db.Date)
    data_audiencia_2 = db.Column(db.Date)
    data_audiencia_3 = db.Column(db.Date)
    data_arquivamento = db.Column(db.Date)

    # --- Organização interna ---
    centro_resultado = db.Column(db.String(120))  # CR
    escritorio = db.Column(db.String(120))

    # --- Resultado e status ---
    resultado = db.Column(db.String(30))
    # 'procedente', 'procedente_parcial', 'improcedente'
    sentenca = db.Column(db.String(20))
    # 'ganhamos', 'perdemos'
    status = db.Column(db.String(20), default="ativo")
    # 'ativo', 'arquivado', 'suspenso'
    risco = db.Column(db.String(20))
    # 'possivel', 'provavel', 'remoto'
    grau_instancia = db.Column(db.String(30))
    # 'primeira_instancia', 'recurso', 'stj', 'stf'

    # --- Acompanhamento ---
    resumo = db.Column(db.Text)
    arquivo_relatorio = db.Column(db.String(255))  # caminho do arquivo importado

    # --- Valores financeiros ---
    valor_final = db.Column(db.Numeric(14, 2))
    honorarios_advogado = db.Column(db.Numeric(14, 2))
    honorarios_periciais = db.Column(db.Numeric(14, 2))
    custas_processuais = db.Column(db.Numeric(14, 2))
    valor_recursal = db.Column(db.Numeric(14, 2))
    valor_alvara = db.Column(db.Numeric(14, 2))
    data_alvara = db.Column(db.Date)

    # --- Controle interno ---
    data_cadastro = db.Column(db.DateTime, default=db.func.now())

    # --- Relacionamentos (dados que se repetem) ---
    partes = db.relationship(
        "Parte", backref="processo", cascade="all, delete-orphan"
    )
    advogados = db.relationship(
        "Advogado", backref="processo", cascade="all, delete-orphan"
    )
    movimentos = db.relationship(
        "Movimento", backref="processo", cascade="all, delete-orphan"
    )
    pedidos_trabalhistas = db.relationship(
        "PedidoTrabalhista", backref="processo", cascade="all, delete-orphan"
    )
    rateio_crs = db.relationship(
        "RateioCR", backref="processo", cascade="all, delete-orphan"
    )
    pedidos_civeis = db.relationship(
        "PedidoCivel", backref="processo", cascade="all, delete-orphan"
    )

    @property
    def dias_ativos(self):
        """Calcula os dias ativos a partir da data de distribuição.
        Não é salvo no banco - é calculado sempre que consultado."""
        if not self.data_distribuicao:
            return None
        fim = self.data_arquivamento or date.today()
        return (fim - self.data_distribuicao).days


class Parte(db.Model):
    """Nome do autor ou réu. Um processo pode ter vários de cada lado."""
    __tablename__ = "partes"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)

    tipo = db.Column(db.String(10), nullable=False)  # 'autor' ou 'reu'
    nome = db.Column(db.String(200), nullable=False)


class Advogado(db.Model):
    """Advogado que representa um dos lados do processo (autor ou réu).
    Até 5 por lado. Fica ligado ao processo, não a um nome de parte
    específico, porque na prática o(s) advogado(s) representa(m) o
    lado inteiro."""
    __tablename__ = "advogados"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)

    lado = db.Column(db.String(10), nullable=False)  # 'autor' ou 'reu'
    nome = db.Column(db.String(200), nullable=False)
    oab = db.Column(db.String(30))


class Movimento(db.Model):
    """Histórico de movimentações do processo (lista que vai crescendo)."""
    __tablename__ = "movimentos"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)

    data_movimento = db.Column(db.Date, nullable=False)
    tipo_movimento = db.Column(db.String(200), nullable=False)


class PedidoTrabalhista(db.Model):
    """Pedidos/verbas de processos trabalhistas, com seus valores."""
    __tablename__ = "pedidos_trabalhistas"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)

    verba = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Numeric(14, 2))


class RateioCR(db.Model):
    """Quando o Centro de Resultados é 'rateio', lista os CRs que
    entram nesse rateio (um processo pode ratear entre vários CRs)."""
    __tablename__ = "rateio_crs"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)

    centro_resultado = db.Column(db.String(120), nullable=False)


class PedidoCivel(db.Model):
    """Pedidos/requerimentos marcados no processo cível (ex.: cancelamento
    da compra, indenização por danos morais, tutela antecipada etc.)."""
    __tablename__ = "pedidos_civeis"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)

    descricao = db.Column(db.String(120), nullable=False)