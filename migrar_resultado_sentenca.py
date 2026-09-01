# -*- coding: utf-8 -*-
"""
Script de migração ÚNICA: troca os valores já salvos nas colunas
'resultado' e 'sentenca', em TODOS os processos existentes
(Cível e Trabalhista), corrigindo a inversão histórica.

Antes da correção do formulário:
  - coluna resultado guardava valores de Sentença (procedente/improcedente/procedente_parcial)
  - coluna sentenca  guardava valores de Resultado (ganhamos/perdemos)

Depois da correção:
  - coluna resultado passa a guardar Resultado (ganhamos/perdemos/acordo)
  - coluna sentenca  passa a guardar Sentença (improcedente/procedente/procedente_parcial)

Este script troca (swap) o conteúdo das duas colunas em TODOS os
processos já existentes (Cível e Trabalhista), para que fiquem
consistentes com os novos cadastros.

COMO RODAR (uma vez só, no console do PythonAnywhere):
  cd gestao-de-processos-juridicos
  python3 migrar_resultado_sentenca.py
"""

from app import app
from models import db, Processo

with app.app_context():
    processos = Processo.query.all()

    total_atualizados = 0
    for processo in processos:
        if processo.resultado or processo.sentenca:
            processo.resultado, processo.sentenca = processo.sentenca, processo.resultado
            total_atualizados += 1

    db.session.commit()
    print(f"Migração concluída. {total_atualizados} processo(s) atualizado(s) "
          f"de um total de {len(processos)} (Cível e Trabalhista).")
