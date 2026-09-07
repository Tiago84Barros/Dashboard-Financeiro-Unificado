"""O expurgo de retenção, contra os dois bancos que ele de fato usa.

Por que este arquivo existe
---------------------------
``estado_coleta.expurgar`` se descreve como **"o freio do crescimento"**, e o
acervo de notícias só coube no armazém local porque esse freio existe: ~22 MB
por janela de 30 dias, acumulando, contra 23 MB de folga no Supabase
(``memoria: acervo-noticias-nao-cabe-no-supabase``). Se o freio não puxa, a
aritmética que escolheu o destino deixa de valer.

Ele não puxava. O módulo resolvia toda engine com ``core.database.get_engine``
— o Supabase — inclusive para ``DELETE FROM noticias_itens``, tabela que mora no
armazém local e que naquele host nunca existiu. A exceção era engolida, ``itens``
saía ``0`` e o ciclo reportava ``success``: um freio desligado com aparência de
freio que varreu e não achou nada (``memoria: defeito-silencioso-vs-erro``).

Daí os dois casos cobrados aqui:

1. **O DELETE alcança o acervo local** — apaga o que passou da retenção e
   preserva o que não passou, contra Postgres de verdade.
2. **Falhar não pode parecer varrer.** ``itens=None`` com motivo escrito é
   coisa diferente de ``itens=0`` (``memoria: medicao-que-pune-a-evidencia``).
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from core.noticias import estado_coleta as ec

SCHEMA = "app4_expurgo_teste"

_DDL_ACERVO = f"""
    CREATE TABLE {SCHEMA}.noticias_itens (
        id_dedup      TEXT PRIMARY KEY,
        titulo        TEXT NOT NULL DEFAULT '',
        publicado_em  TIMESTAMPTZ
    )
"""


@pytest.fixture()
def acervo():
    """Armazém local num schema descartável, com só o que o expurgo toca."""
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(
            _warehouse_url(),
            connect_args={"options": f"-csearch_path={SCHEMA},public"})
        with motor.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            conn.execute(text(_DDL_ACERVO))
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def _semear(motor, agora: dt.datetime) -> None:
    with motor.begin() as conn:
        conn.execute(text("""
            INSERT INTO noticias_itens (id_dedup, titulo, publicado_em)
            VALUES (:i, :t, :p)
        """), [
            {"i": "velha", "t": "de 90 dias atrás",
             "p": agora - dt.timedelta(days=90)},
            {"i": "nova", "t": "de ontem", "p": agora - dt.timedelta(days=1)},
        ])


def _ids(motor) -> set[str]:
    with motor.connect() as conn:
        return {linha[0] for linha in
                conn.execute(text("SELECT id_dedup FROM noticias_itens"))}


def test_o_delete_alcanca_o_acervo_local(acervo):
    """O caso que o defeito impedia: notícia velha realmente sai.

    Antes da correção este ``DELETE`` era enviado ao Supabase, onde a tabela não
    existe. Nada era apagado em lugar nenhum e o retorno dizia ``itens: 0``.
    """
    agora = dt.datetime.now(dt.timezone.utc)
    _semear(acervo, agora)

    r = ec.expurgar(dias=30, engine=None, engine_acervo=acervo)

    assert r["itens"] == 1, r
    assert _ids(acervo) == {"nova"}


def test_nao_apaga_o_que_esta_dentro_da_retencao(acervo):
    """Retenção larga não pode varrer nada — freio não é demolição."""
    agora = dt.datetime.now(dt.timezone.utc)
    _semear(acervo, agora)

    r = ec.expurgar(dias=3650, engine=None, engine_acervo=acervo)

    assert r["itens"] == 0
    assert _ids(acervo) == {"velha", "nova"}


def test_corte_e_por_data_de_publicacao_nao_de_coleta(acervo):
    """Matéria de 2019 recoletada hoje não vira acervo recente."""
    agora = dt.datetime.now(dt.timezone.utc)
    with acervo.begin() as conn:
        conn.execute(text("""
            INSERT INTO noticias_itens (id_dedup, titulo, publicado_em)
            VALUES ('antiga_recoletada', 'de 2019', :p)
        """), {"p": dt.datetime(2019, 5, 1, tzinfo=dt.timezone.utc)})

    ec.expurgar(dias=30, engine=None, engine_acervo=acervo)

    assert _ids(acervo) == set()
    assert agora  # a data de coleta é hoje e não protegeu a linha


class _EngineQueFalha:
    """Engine cuja conexão levanta — o banco de pé mas a tabela ausente."""

    def begin(self):
        raise RuntimeError('relation "noticias_itens" does not exist')


def test_falha_do_acervo_nao_se_parece_com_acervo_limpo():
    """``None`` é "não varri"; ``0`` é "varri e não havia". Não são iguais.

    Esta é a assinatura exata do defeito original: exceção engolida, ``itens``
    devolvido como ``0``, ciclo reportando sucesso e o acervo crescendo.
    """
    r = ec.expurgar(dias=30, engine=None, engine_acervo=_EngineQueFalha())

    assert r["itens"] is None
    assert r["expurgado"] is False
    assert "noticias_itens" in r["motivo"]


def test_falha_no_acervo_nao_derruba_o_expurgo_de_ciclos(acervo):
    """Dois bancos, duas transações: um lado quebrado não desfaz o outro.

    Enquanto os dois ``DELETE`` dividiam uma transação, o erro do acervo
    abortava a transação inteira e o expurgo de ciclos ia junto, sem aviso.
    """
    r = ec.expurgar(dias=30, engine=_EngineQueFalha(),
                    engine_acervo=acervo)

    assert r["ciclos"] is None          # o lado quebrado se declara
    assert r["itens"] == 0              # o lado são rodou assim mesmo
    assert "ciclos:" in r["motivo"]


def test_sem_acervo_configurado_o_motivo_diz_isso(monkeypatch):
    """Ausência de configuração é ausência declarada, não zero apagado."""
    monkeypatch.setattr("core.noticias.destino.engine_acervo", lambda: None)

    r = ec.expurgar(dias=30, engine=_EngineQueFalha())

    assert r["itens"] is None
    assert "acervo local nao configurado" in r["motivo"]


def test_contar_novas_procura_no_acervo_e_nao_no_supabase(acervo):
    """A contagem de inéditas lê ``noticias_itens`` — no banco que a tem."""
    agora = dt.datetime.now(dt.timezone.utc)
    _semear(acervo, agora)

    novas = ec.contar_novas(["velha", "nova", "inedita"], engine=acervo)

    assert novas == 1


def test_contar_novas_sem_acervo_e_none_e_nao_zero(monkeypatch):
    """Sem banco não se sabe quantas eram inéditas — e dizer ``0`` mentiria."""
    monkeypatch.setattr("core.noticias.destino.engine_acervo", lambda: None)

    assert ec.contar_novas(["a", "b"]) is None
