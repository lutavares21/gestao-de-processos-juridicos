# -*- coding: utf-8 -*-
"""
Script para criar (ou redefinir a senha de) um usuário do sistema.

Rode com: py seed_usuario.py
Depois pode apagar ou guardar em lugar seguro - não precisa rodar de novo,
a menos que queira criar outro usuário ou trocar uma senha.

IMPORTANTE: não suba este arquivo para o GitHub com a senha real escrita
nele. Ele já está listado no .gitignore.
"""

from app import app
from models import db, Usuario
from werkzeug.security import generate_password_hash

# --- Preencha aqui os dados do usuário que quer criar/atualizar ---
NOME = "Sheila Tavares"
LOGIN = "Sheila_Tavares"
SENHA = "tavares123"
# --------------------------------------------------------------

with app.app_context():
    usuario = Usuario.query.filter_by(usuario=LOGIN).first()

    if usuario:
        usuario.senha_hash = generate_password_hash(SENHA)
        print(f"Senha do usuário '{LOGIN}' atualizada.")
    else:
        usuario = Usuario(
            nome=NOME,
            usuario=LOGIN,
            senha_hash=generate_password_hash(SENHA),
        )
        db.session.add(usuario)
        print(f"Usuário '{LOGIN}' ({NOME}) criado com sucesso.")

    db.session.commit()
