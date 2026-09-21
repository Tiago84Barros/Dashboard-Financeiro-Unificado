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


# ── nenhum teste herda a leitura em voo de outro ─────────────────────────────
# `core.market_read._FII_SNAPSHOT_JOB` e um slot global do processo, e a leitura
# real o preenche sem esperar (`timeout_seconds=0`) quando o artefato local
# responde: o worker segue lendo o Supabase por mais de 30 s depois que o teste
# que o disparou ja terminou. O teste seguinte que chamasse o carregador recebia
# o resultado DELE -- 433 linhas reais ou um quadro vazio --, e seus
# `monkeypatch` de engine e `read_sql_query` viravam decoracao.
#
# Medido em 20/09/2026: `tests/test_market_read_failures.py` passava 38/38
# isolado e falhava de 2 a 5 testes dentro da suite, com o conjunto mudando
# entre execucoes do MESMO commit. O que decidia era se o worker ainda estava
# vivo -- ou seja, quanto tempo a suite levou --, e nao o que o teste pediu.
#
# Abandonar o worker e seguro: a thread e daemon e a fila e privada dela.
@pytest.fixture(autouse=True)
def _sem_leitura_de_snapshot_herdada():
    try:
        import core.market_read as mr
    except Exception:  # o modulo pode nao existir neste checkout
        yield
        return
    _limpar(mr)
    yield
    _limpar(mr)


def _limpar(mr) -> None:
    mr._reset_fii_snapshot_memory_cache()
    # O cache do Streamlit por cima e outro canal entre testes, e com TTL de
    # 900 s numa suite que leva entre 844 s e 906 s ele expira -- ou nao --
    # conforme a duracao da execucao, nao conforme o que o teste pediu.
    try:
        mr._load_fii_methodology_inputs_cached.clear()
    except Exception:
        pass


# ── libpq nao passa pelo socket do Python ────────────────────────────────────
# `socket.socket.connect` e `socket.create_connection` sao codigo Python, e todo
# cliente escrito em Python passa por eles. libpq NAO: psycopg2 abre o socket em
# C, e a chamada nunca toca o modulo `socket`. O guarda acima, portanto, cobria
# `requests`, `urllib`, `yfinance` -- e deixava passar justamente a conexao mais
# cara, a do banco de PRODUCAO.
#
# Medido em 21/09/2026, com o guarda de socket ativo: `psycopg2.connect` para o
# pooler do Supabase resolveu o DNS, abriu TCP para 54.232.77.43, completou o
# handshake TLS e recebeu um FATAL do servidor real. Nao falhou por estar
# bloqueado -- falhou por causa do usuario de mentira que o teste mandou. Com as
# credenciais boas teria conectado, e `load_dotenv` acha o `.env` subindo
# diretorios (ate a partir de um worktree), entao elas estao a mao.
#
# A regra e a MESMA (`_e_local`) e a recusa e a MESMA (`_recusar`), de proposito:
# duas copias da mesma regra divergem na primeira correcao feita de um lado so.
# O que muda e so a porta onde ela e aplicada.
def _enderecos_do_psycopg2(dsn, kwargs) -> list:
    """Para onde libpq vai discar de fato.

    Quem interpreta o DSN e `parse_dsn`, que e a funcao do proprio psycopg2:
    uma segunda interpretacao escrita aqui discordaria da real em algum formato
    (URL com varios hosts, keyword/value, `service=`) e o furo voltaria calado.

    `hostaddr` vem ANTES de `host` porque e o que libpq disca quando os dois
    existem -- e `core/database.py` usa exatamente esse par quando
    `SUPABASE_DB_HOSTADDR` esta configurada. Conferir so `host` deixaria
    `host=localhost hostaddr=<ip do supabase>` sair pela rede.
    """
    params: dict = {}
    if dsn:
        try:
            from psycopg2.extensions import parse_dsn
            params.update(parse_dsn(dsn))
        except Exception:
            # DSN que libpq nao entende nao chega a abrir socket nenhum: deixa o
            # erro nativo aparecer em vez de trocar por um nosso.
            return []
    params.update({k: v for k, v in kwargs.items() if v is not None})

    alvo = params.get("hostaddr") or params.get("host")
    if not alvo:
        return []  # socket unix ou default local
    porta = params.get("port")
    # `host=a,b` e valido em libpq: ele tenta um por um.
    return [(h.strip(), porta) for h in str(alvo).split(",") if h.strip()]


def _instalar_guarda_libpq() -> None:
    try:
        import psycopg2
    except Exception:  # o driver pode nao estar instalado neste checkout
        return

    original = psycopg2.connect

    def _connect_guardado(dsn=None, *args, **kwargs):
        for endereco in _enderecos_do_psycopg2(dsn, kwargs):
            if not _e_local(endereco):
                # So host e porta chegam a mensagem. O DSN carrega a senha, e
                # mensagem de teste vai parar em log de CI.
                _recusar(endereco)
        return original(dsn, *args, **kwargs)

    _connect_guardado._dfu_original = original  # noqa: SLF001 - usado no teste
    psycopg2.connect = _connect_guardado


if os.getenv("DFU_TESTES_PERMITEM_REDE", "").strip().lower() not in {"1", "true", "yes"}:
    _instalar_guarda_libpq()
