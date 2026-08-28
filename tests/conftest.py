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
