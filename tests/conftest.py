"""Faz valer, em codigo, o contrato que `.github/workflows/tests.yml` ja escreve:
"Sem banco e sem chaves: a suite e offline por construcao. Qualquer teste que
precise de rede deve isolar-se com fixture/monkeypatch."

Ate aqui o contrato era so prosa, e prosa nao falha. Dois defeitos passaram por
baixo dele no mesmo dia:

* `tests/test_llm_provider_fallback.py` neutralizava provedores um a um pelo
  nome. Quando o OpenRouter entrou na cadeia, o teste nao quebrou -- ele vazou, e
  a suite passou a chamar a API de verdade com a chave de verdade.
* `test_pesos_continuam_somando_um_com_o_banco_fora` injetava um engine morto,
  mas `validacao_fii()` e `validacao_us()` abrem o de producao por conta propria.
  Um teste chamado "com o banco fora" saia pela rede.

Nenhum dos dois aparecia como erro na minha maquina, que tem chave e tem banco.
Apareciam no CI, que nao tem -- e la o sintoma nao e falha, e job pendurado ate o
runner cancelar. Bloquear o socket transforma esse silencio caro em excecao
imediata, com o endereco de quem tentou sair.

Localhost continua liberado: o armazem local (porta 5433) e servidores de teste
sobem em loopback e sao parte legitima do ambiente.

Escape para investigacao pontual, nunca para o CI:

    DFU_TESTES_PERMITEM_REDE=1 pytest tests/...
"""
import os
import socket

import pytest

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CREATE_CONNECTION = socket.create_connection

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _e_local(endereco) -> bool:
    """Endereco de familia nao-IP (unix socket, por exemplo) passa: o que este
    guarda persegue e trafego para fora da maquina, nao IPC."""
    if not isinstance(endereco, (tuple, list)) or not endereco:
        return True
    host = endereco[0]
    if not isinstance(host, str):
        return True
    return host in _LOOPBACK or host.startswith("127.")


def _recusar(endereco):
    raise RuntimeError(
        f"Rede bloqueada na suite: tentativa de conexao para {endereco!r}. "
        "A suite e offline por construcao -- isole a dependencia com "
        "monkeypatch/fixture. Para investigar, rode com "
        "DFU_TESTES_PERMITEM_REDE=1."
    )


def _connect(self, endereco):
    if not _e_local(endereco):
        _recusar(endereco)
    return _ORIGINAL_CONNECT(self, endereco)


def _create_connection(endereco, *args, **kwargs):
    if not _e_local(endereco):
        _recusar(endereco)
    return _ORIGINAL_CREATE_CONNECTION(endereco, *args, **kwargs)


if os.getenv("DFU_TESTES_PERMITEM_REDE", "").strip().lower() not in {"1", "true", "yes"}:
    socket.socket.connect = _connect
    socket.create_connection = _create_connection


# ── O armazem local e loopback, e loopback estava liberado ───────────────────
# O guarda acima persegue trafego para fora da maquina. O contexto macro sai
# pela porta 5433, que e loopback, e por isso passava.
#
# Medido em 03/09/2026: com `MACRO_LOCAL_DB_URL` configurada no .env,
# `contexto_segregado` de um painel de teste passou de 956 para 4.167 caracteres
# e de 7 para 68 numeros -- lidos do banco, dentro de um teste cujo docstring diz
# que nenhum cenario toca banco. E o efeito nao era so de higiene: com 68 numeros
# no lastro, a aritmetica de ancoragem passou a "derivar" 37,4 e o cenario C13
# (guarda do A-148) ficou verde sem guardar nada.
#
# A fixture zera a fonte, nao o resultado: quem quiser exercitar o contexto macro
# passa `macro_facts=` explicitamente, que e o caminho que a producao usa quando
# ja tem os fatos em maos.
@pytest.fixture(autouse=True)
def _sem_armazem_macro(monkeypatch):
    try:
        from core.macro_data import database as macro_db
    except Exception:  # o modulo pode nao existir neste checkout
        return
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: None)
