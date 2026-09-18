"""Configuração inicial interativa, somente no terminal local do operador.

Não recebe senhas por argumentos, não imprime credenciais e não substitui uma
senha individual existente. Não é uma rota web de recuperação de senha.
"""
from __future__ import annotations

import getpass
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from core.config import settings
from core.database import get_engine
from core.user_accounts import hash_password


def configure(password: str) -> None:
    encoded = hash_password(password)
    if not settings.ADMIN_USER_ID:
        raise ValueError("Administrador não configurado em OWNER_USER_ID.")
    engine = get_engine()
    if engine is None:
        raise ValueError("Banco não configurado.")
    with engine.begin() as conn:
        result = conn.execute(text(
            "UPDATE profiles SET password_hash = :pw "
            "WHERE id = CAST(:uid AS uuid) AND active = TRUE "
            "AND password_hash NOT LIKE 'scrypt-v1$%'"
        ), {"pw": encoded, "uid": settings.ADMIN_USER_ID})
        if result.rowcount != 1:
            raise ValueError("Configuração recusada: perfil ausente, inativo ou senha individual já definida.")


def main() -> int:
    if not sys.stdin.isatty():
        print("Abra este script em um terminal interativo local.")
        return 1
    print("Configuração inicial do administrador do App4.")
    print("Será gravada uma senha individual no banco configurado, sem alterar dados financeiros.")
    print("A senha terá exatamente 8 caracteres. Ela não aparecerá enquanto você digita.")
    if input("Digite CONFIGURAR para continuar: ").strip() != "CONFIGURAR":
        print("Cancelado. Nenhuma alteração realizada.")
        return 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Nova senha: ")
            confirmation = getpass.getpass("Confirme a senha: ")
        if password != confirmation:
            raise ValueError("As senhas não coincidem.")
        configure(password)
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("Não foi possível configurar. Nenhuma credencial será exibida; verifique a conexão e o terminal.")
        return 1
    print("Senha configurada. Volte ao App4 e entre como administrador.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
