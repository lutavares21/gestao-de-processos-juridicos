# -*- coding: utf-8 -*-
"""
Exclui TODOS os processos cadastrados (cível, recuperação de crédito e
trabalhista) e tudo que está ligado a eles. Operadores e o histórico de
atividades NÃO são apagados.

Como usar (na pasta do projeto, com o ambiente do app ativo):
    python3 limpar_processos.py

Antes de apagar, o script faz uma cópia de segurança do banco de dados e
pede que você digite uma frase de confirmação.
"""
import os
import shutil
from datetime import datetime

from app import app
from models import (
    db, Processo, Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR,
    PedidoCivel, TituloRecuperacao, AcordoRecebimento, RegistroAtividade,
)

FRASE = "EXCLUIR TODOS"

with app.app_context():
    total = Processo.query.count()
    print(f"\nProcessos cadastrados: {total}")
    for origem, qtd in (
        db.session.query(Processo.origem_cadastro, db.func.count(Processo.id))
        .group_by(Processo.origem_cadastro).all()
    ):
        print(f"  - {origem}: {qtd}")

    if total == 0:
        print("\nNão há processos para excluir.")
        raise SystemExit(0)

    caminho_banco = db.engine.url.database
    if not caminho_banco or not os.path.exists(caminho_banco):
        print("\nNão encontrei o arquivo do banco de dados para fazer a cópia de segurança.")
        print("Nada foi apagado.")
        raise SystemExit(1)

    print("\nATENÇÃO: esta ação é IRREVERSÍVEL (exceto pela cópia de segurança).")
    resposta = input(f'Para confirmar, digite exatamente "{FRASE}": ').strip()
    if resposta != FRASE:
        print("Confirmação não recebida. Nada foi apagado.")
        raise SystemExit(0)

    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{caminho_banco}.backup_{carimbo}"
    shutil.copy2(caminho_banco, backup)
    print(f"\nCópia de segurança criada: {backup}")

    for modelo in (Parte, Advogado, Movimento, PedidoTrabalhista, RateioCR,
                   PedidoCivel, TituloRecuperacao, AcordoRecebimento):
        modelo.query.delete(synchronize_session=False)
    Processo.query.delete(synchronize_session=False)

    db.session.add(RegistroAtividade(
        operador_id=None,
        operador_nome="Script de limpeza",
        acao="processos_excluidos_em_massa",
        descricao=f"Excluiu todos os {total} processos cadastrados",
    ))
    db.session.commit()
    print(f"Pronto: {total} processo(s) excluído(s).")
