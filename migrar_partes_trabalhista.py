# -*- coding: utf-8 -*-
"""
Script de migração ÚNICA: atualiza os registros já existentes de
processos trabalhistas, trocando o tipo de parte de 'autor'/'reu'
para 'reclamante'/'reclamada'.

Processos cíveis (origem_cadastro='civel') NÃO são alterados.

COMO RODAR (uma vez só, no console do PythonAnywhere):
  cd gestao-de-processos-juridicos
  python3 migrar_partes_trabalhista.py
"""

from app import app
from models import db, Processo, Parte

with app.app_context():
    processos_trabalhistas = Processo.query.filter_by(origem_cadastro="trabalhista").all()

    total_atualizadas = 0
    for processo in processos_trabalhistas:
        for parte in processo.partes:
            if parte.tipo == "autor":
                parte.tipo = "reclamante"
                total_atualizadas += 1
            elif parte.tipo == "reu":
                parte.tipo = "reclamada"
                total_atualizadas += 1

    db.session.commit()
    print(f"Migração concluída. {total_atualizadas} parte(s) atualizada(s) em "
          f"{len(processos_trabalhistas)} processo(s) trabalhista(s).")
