"""Recoletar o mesmo item tem de trazer a normalização de entrada de hoje.

Por que este arquivo existe
---------------------------
O upsert de ``noticias_itens`` só reescrevia o que é derivado — ``entidades``,
``evento_id``, sentimento. ``resumo`` era gravado na primeira coleta e nunca
mais.

Isso parecia preservar evidência crua, e não preservava: o que vai para o banco
já passou por ``limpar_html`` e, desde 05/09/2026, por ``sem_rodape_de_feed``.
Congelar não guardava o texto da fonte — guardava a versão do normalizador que
por acaso rodou primeiro. O resultado medido: das 48 linhas do acervo, 21
ficaram com o rodapé de plugin (``The post … appeared first on …``) que as
novas já não tinham, e a vitrine lê o acervo.

O que se cobra aqui:

1. **Recoletar renormaliza** — o texto gravado converge para o que a entrada
   produz hoje, em vez de depender da data da primeira coleta.
2. **Renormalizar não é apagar** — provedor que devolve o mesmo item sem
   descrição numa passada seguinte não pode zerar texto bom. Perder conteúdo
   seria pior que o rodapé (``memoria: medicao-que-pune-a-evidencia``).
"""
from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import create_engine, text

from core.noticias import armazenamento as arm
from core.noticias import impacto as imp
from core.noticias import modelos, relevancia
from core.noticias.coleta import ResultadoColeta
from tests.apoio_noticias import AGORA, noticia, quando

SCHEMA = "app4_reingestao_teste"

RODAPE = (" A empresa comunicou o fato ao mercado."
          " The post Alfa comunica acordo appeared first on Veiculo Teste .")


@pytest.fixture()
def acervo():
    """Armazém local num schema descartável — ``gravar`` cria o resto."""
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(
            _warehouse_url(),
            connect_args={"options": f"-csearch_path={SCHEMA},public"})
        with motor.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def _avaliada(n):
    r = relevancia.calcular(n, agora=AGORA)
    i = imp.estimar(tipo_evento=n.tipo_evento, sentimento=n.sentimento,
                    confiabilidade_fonte=n.fonte.confiabilidade if n.fonte
                    else None, cobertura_relevancia=r.cobertura)
    return modelos.NoticiaAvaliada(noticia=n, relevancia=r, impacto=i)


def _gravar(motor, resumo):
    """Grava a mesma URL — logo, o mesmo ``id_dedup`` — com este resumo."""
    n = noticia("Alfa comunica acordo", "https://veiculo.teste/alfa-acordo",
                resumo=resumo, publicado_em=quando(1))
    arm.gravar(ResultadoColeta(avaliadas=(_avaliada(n),)), engine=motor)


def _resumos(motor) -> list[str]:
    with motor.connect() as conn:
        return [linha[0] for linha in
                conn.execute(text("SELECT resumo FROM noticias_itens"))]


def test_recoletar_traz_a_normalizacao_de_entrada_corrente(acervo):
    """O caso medido: linha antiga com rodapé, coleta nova sem ele."""
    _gravar(acervo, RODAPE)                       # como se tivesse sido gravada
    with acervo.begin() as conn:                  # antes do corte existir
        conn.execute(text("UPDATE noticias_itens SET resumo = :r"),
                     {"r": RODAPE})
    assert "appeared first on" in _resumos(acervo)[0]

    _gravar(acervo, RODAPE)                       # a mesma notícia, hoje

    assert len(_resumos(acervo)) == 1, "recoletar não pode duplicar a linha"
    assert "appeared first on" not in _resumos(acervo)[0]
    assert "comunicou o fato ao mercado" in _resumos(acervo)[0]


def test_passada_sem_descricao_nao_apaga_texto_bom(acervo):
    """``NULLIF``: ausência na fonte não é correção do que já se tinha."""
    _gravar(acervo, "A empresa comunicou o fato ao mercado.")
    _gravar(acervo, None)

    assert _resumos(acervo) == ["A empresa comunicou o fato ao mercado."]


def test_o_titulo_continua_sendo_o_da_primeira_coleta(acervo):
    """Escopo declarado: só ``resumo`` renormaliza.

    Título reescrito seria outra coisa — veículo que edita a manchete depois da
    publicação é fato editorial, não ruído de normalização, e apagá-lo em
    silêncio tiraria a única prova de que a manchete mudou.
    """
    _gravar(acervo, "texto")
    n = noticia("Alfa desmente acordo", "https://veiculo.teste/alfa-acordo",
                resumo="texto", publicado_em=quando(1))
    arm.gravar(ResultadoColeta(avaliadas=(_avaliada(n),)), engine=acervo)

    with acervo.connect() as conn:
        titulos = [linha[0] for linha in
                   conn.execute(text("SELECT titulo FROM noticias_itens"))]
    assert titulos == ["Alfa comunica acordo"]
    assert dataclasses  # a fixação é do schema, não do dataclass
