"""O guarda de rede tambem precisa ser guardado, nas DUAS portas.

Um `conftest.py` que para de aplicar o patch nao quebra nada: a suite continua
verde e volta a sair na rede em silencio -- exatamente o modo de falha que ele
existe para impedir. Estes testes cobram que o bloqueio esteja de pe e que ele
nao esteja bloqueando de mais.

Sao duas portas porque `socket.socket.connect` e codigo Python e libpq nao passa
por la. Ate 21/09/2026 o bloqueio cobria `requests` e `yfinance` e deixava
passar a conexao mais cara que a suite podia fazer: a do Postgres de producao.
Um teste que dizia "com o banco fora" abria o banco de verdade, com as
credenciais do `.env`, e so nao gravava por sorte de caminho.

O caso que manda na parte do libpq e `test_hostaddr_nao_e_um_atalho_para_fora`:
e o unico formato que `core/database.py` produz sozinho, e e o que um guarda que
olha so `host` deixa passar.

Arquivo unico de proposito: duas copias do mesmo assunto sao duas copias que
divergem na primeira correcao feita de um lado so.
"""
from __future__ import annotations

import os
import socket

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DFU_TESTES_PERMITEM_REDE", "").strip().lower() in {"1", "true", "yes"},
    reason="valvula de escape ligada: o guarda esta desligado de proposito e "
           "cobrar o bloqueio aqui so produz ruido em quem investiga",
)

_REMOTO = "aws-1-sa-east-1.pooler.supabase.com"
_PORTA_FECHADA = 5599  # loopback, quase certamente sem ninguem escutando


# ── a porta do socket (Python puro: requests, httpx, yfinance) ───────────────

def test_conexao_para_fora_e_recusada():
    """Sem isto, "a suite e offline" volta a ser so uma frase no YAML."""
    with pytest.raises(RuntimeError, match="Rede bloqueada"):
        socket.create_connection(("142.250.0.1", 443), timeout=0.1)


def test_socket_connect_tambem_e_coberto():
    """`create_connection` nao e o unico caminho: httpx chega ao
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


# ── a porta do libpq (psycopg2: C, nao passa pelo socket do Python) ──────────

psycopg2 = pytest.importorskip("psycopg2")


def _conectar(**kwargs):
    kwargs.setdefault("connect_timeout", 5)
    return psycopg2.connect(**kwargs)


def test_kwargs_para_fora_sao_recusados():
    with pytest.raises(RuntimeError, match="Rede bloqueada"):
        _conectar(host=_REMOTO, port=5432, dbname="postgres", user="x")


def test_dsn_url_para_fora_e_recusado():
    with pytest.raises(RuntimeError, match="Rede bloqueada"):
        psycopg2.connect(f"postgresql://u:s3nha@{_REMOTO}:5432/postgres")


def test_a_recusa_nao_ecoa_a_senha():
    """A mensagem vai parar em log de CI, que e publico no repositorio."""
    with pytest.raises(RuntimeError) as erro:
        psycopg2.connect(f"postgresql://u:s3nha-secreta@{_REMOTO}:5432/postgres")
    assert "s3nha-secreta" not in str(erro.value)
    assert _REMOTO in str(erro.value)


def test_hostaddr_nao_e_um_atalho_para_fora():
    """`host` loopback + `hostaddr` remoto disca para o remoto.

    Nao e hipotese: `core/database.py` monta exatamente esse par quando
    `SUPABASE_DB_HOSTADDR` esta configurada, para contornar falha de DNS do
    pooler. Um guarda que lesse so `host` aprovaria a conexao lendo a string
    'localhost' enquanto libpq abre o socket para o IP do Supabase.
    """
    with pytest.raises(RuntimeError, match="Rede bloqueada"):
        _conectar(host="localhost", hostaddr="54.232.77.43", port=5432,
                  dbname="postgres", user="x")


def test_lista_de_hosts_e_conferida_inteira():
    """`host=a,b` e valido em libpq: ele tenta um por um."""
    with pytest.raises(RuntimeError, match="Rede bloqueada"):
        _conectar(host=f"127.0.0.1,{_REMOTO}", port=5432, dbname="postgres", user="x")


def test_loopback_chega_ao_driver_de_verdade():
    """O armazem local (5433) e parte legitima do ambiente.

    Um guarda que bloqueasse loopback junto quebraria a suite inteira sem
    parecer um guarda -- pareceria banco fora do ar.
    """
    with pytest.raises(psycopg2.OperationalError):
        _conectar(host="127.0.0.1", port=_PORTA_FECHADA, dbname="x", user="x")


def test_sem_host_nenhum_passa():
    """Sem `host`, libpq usa socket local/default: e IPC, nao saida de rede."""
    from tests.conftest import _enderecos_do_psycopg2
    assert _enderecos_do_psycopg2(None, {"dbname": "x"}) == []


def test_dsn_ilegivel_segue_para_o_driver():
    """Trocar o erro nativo do libpq pelo nosso esconderia o defeito real."""
    from tests.conftest import _enderecos_do_psycopg2
    assert _enderecos_do_psycopg2("isto nao e um dsn", {}) == []


# ── as duas portas nao podem divergir ────────────────────────────────────────

def test_as_duas_portas_recusam_o_mesmo_endereco_com_a_mesma_mensagem():
    """Uma regra, duas portas.

    Se a checagem do libpq virasse copia da do socket, a primeira correcao feita
    de um lado so reabriria o furo em silencio. O teste amarra as duas ao mesmo
    texto de recusa.
    """
    with pytest.raises(RuntimeError) as via_socket:
        socket.create_connection((_REMOTO, 5432), timeout=5)
    with pytest.raises(RuntimeError) as via_libpq:
        _conectar(host=_REMOTO, port=5432, dbname="postgres", user="x")
    assert str(via_socket.value) == str(via_libpq.value)


def test_o_caminho_real_do_app_e_barrado():
    """Nao basta `psycopg2.connect`: o app chega la por SQLAlchemy.

    Este e o caminho que `core/database.py::get_engine()` monta. Se o dialeto
    guardasse uma referencia a `connect` no import, o guarda instalado depois
    nao valeria -- e o teste nao teria como saber.
    """
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine(
        f"postgresql+psycopg2://u:s@{_REMOTO}:5432/postgres",
        connect_args={"connect_timeout": 5},
    )
    with pytest.raises(Exception) as erro:
        engine.connect()
    assert "Rede bloqueada" in str(erro.value)
    engine.dispose()
