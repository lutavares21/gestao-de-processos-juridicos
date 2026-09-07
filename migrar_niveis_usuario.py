# -*- coding: utf-8 -*-
"""
Migração: adiciona as colunas 'email' e 'nivel' na tabela de usuários,
sem apagar nada do banco de dados existente.

Rode com: py migrar_niveis_usuario.py
Só precisa rodar uma vez.
"""

import sqlite3
import os

CAMINHO_BANCO = os.path.join("instance", "processos.db")

if not os.path.exists(CAMINHO_BANCO):
    print(f"Banco de dados não encontrado em '{CAMINHO_BANCO}'.")
    print("Rode 'py app.py' primeiro para criar o banco, depois rode esta migração.")
    raise SystemExit(1)

conexao = sqlite3.connect(CAMINHO_BANCO)
cursor = conexao.cursor()

cursor.execute("PRAGMA table_info(usuarios)")
colunas_existentes = {linha[1] for linha in cursor.fetchall()}

if "email" not in colunas_existentes:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN email VARCHAR(150)")
    print("Coluna 'email' adicionada.")
else:
    print("Coluna 'email' já existia.")

if "nivel" not in colunas_existentes:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN nivel VARCHAR(20) DEFAULT 'comum'")
    print("Coluna 'nivel' adicionada.")
else:
    print("Coluna 'nivel' já existia.")

# Marca a Sheila_Tavares como administradora
cursor.execute(
    "UPDATE usuarios SET nivel = 'administrador' WHERE LOWER(usuario) = LOWER(?)",
    ("Sheila_Tavares",),
)
if cursor.rowcount:
    print("Sheila_Tavares marcada como administradora.")
else:
    print("Aviso: não encontrei um usuário chamado Sheila_Tavares para marcar como administradora.")

conexao.commit()
conexao.close()
print("Migração concluída.")
