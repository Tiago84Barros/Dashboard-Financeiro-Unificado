"""O guarda de rede tambem precisa ser guardado.

Um `conftest.py` que para de aplicar o patch nao quebra nada: a suite continua
verde e volta a sair na rede em silencio -- exatamente o modo de falha que ele
existe para impedir. Estes testes cobram que o bloqueio esteja de pe e que ele
nao esteja bloqueando de mais.
"""
import socket

import pytest


def test_conexao_para_fora_e_recusada():
    """Sem isto, "a suite e offline" volta a ser so uma frase no YAML."""
    with pytest.raises(RuntimeError, match="Rede bloqueada"):
        socket.create_connection(("142.250.0.1", 443), timeout=0.1)


def test_socket_connect_tambem_e_coberto():
    """`create_connection` nao e o unico caminho: httpx e psycopg2 chegam ao
    `socket.connect` cru."""
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="Rede bloqueada"):
            s.connect(("142.250.0.1", 443))
    finally:
        s.close()


def test_loopback_continua_liberado():
    """O armazem local (5433) e servidores de teste vivem em loopback. Bloquear
    localhost trocaria um falso negativo por um falso positivo."""
    with pytest.raises(OSError):  # ninguem escutando: erro de rede, nao do guarda
        socket.create_connection(("127.0.0.1", 1), timeout=0.2)
