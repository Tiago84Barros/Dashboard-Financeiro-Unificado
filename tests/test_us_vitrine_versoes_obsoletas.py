# -*- coding: utf-8 -*-
"""A vitrine acumulava metodologia morta porque a remocao so olhava a corrente.

`_remover_sobras` reconcilia DENTRO de `score_version = :v`. O filtro que a
torna correta e o mesmo que a torna cega: no dia em que
`US_FUNDAMENTAL_SCORE_VERSION` sobe, a safra anterior sai do alcance da
varredura para sempre. Medido em 31/08/2026 no Supabase: 99.425 linhas, 70.339
delas de 0.5.0, 0.7.1 e 0.7.2 -- 70% de um banco de plano free que e o unico
que a Streamlit Cloud alcanca.

A correcao nao pode ser outra lista branca. O incidente da coorte preferida
apagada neste projeto foi exatamente isso: preservar pelo que alguem lembrou de
escrever, em vez de decidir a partir do que a tabela contem. Por isso a remocao
parte do INVENTARIO e a janela do que se preserva e explicita.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import scripts.publish_us_score_vintages as pub


def _vitrine(linhas):
    """(symbol, score_version, as_of_date, track) -> banco em memoria."""
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("ATTACH ':memory:' AS market_us"))
        c.execute(text("CREATE TABLE market_us.score_vintages (symbol TEXT, "
                       "score_version TEXT, as_of_date TEXT, track TEXT, "
                       "score REAL, coverage REAL, score_confidence REAL)"))
        c.execute(text("CREATE TABLE market_us.prices_monthly (symbol TEXT, "
                       "month_end TEXT, close REAL, adjusted_close REAL, "
                       "volume INTEGER, total_return REAL)"))
        for sym, versao, data, track in linhas:
            c.execute(text("INSERT INTO market_us.score_vintages VALUES "
                           "(:s,:v,:d,:t,60.0,90.0,80.0)"),
                      {"s": sym, "v": versao, "d": data, "t": track})
    return eng


def _versoes(eng):
    with eng.connect() as c:
        return sorted(r[0] for r in c.execute(text(
            "SELECT DISTINCT score_version FROM market_us.score_vintages")))


def test_inventario_enumera_o_que_a_tabela_contem():
    """A decisao parte do conteudo, nao da lista que a versao corrente escreve."""
    eng = _vitrine([("AAPL", "0.5.0", "2020-06-30", "fundamental"),
                    ("MSFT", "0.5.0", "2020-06-30", "fundamental"),
                    ("AAPL", "0.8.0", "2020-06-30", "fundamental")])
    inv = pub._inventario_versoes(eng)
    assert inv == [
        {"score_version": "0.5.0", "track": "fundamental", "linhas": 2,
         "simbolos": 2},
        {"score_version": "0.8.0", "track": "fundamental", "linhas": 1,
         "simbolos": 1},
    ]


def test_versao_de_metodologia_morta_e_apagada():
    """O caso que motivou tudo: 70% da tabela sob versoes que ninguem le."""
    eng = _vitrine([("AAPL", "0.5.0", "2020-06-30", "fundamental"),
                    ("AAPL", "0.7.1", "2020-06-30", "fundamental"),
                    ("AAPL", "0.7.2", "2020-06-30", "fundamental"),
                    ("AAPL", "0.8.0", "2020-06-30", "fundamental")])
    res = pub._remover_versoes_obsoletas(eng, {"0.8.0"})
    assert res["linhas"] == 3
    assert res["por_versao"] == {"0.5.0": 1, "0.7.1": 1, "0.7.2": 1}
    assert _versoes(eng) == ["0.8.0"]


def test_janela_de_retencao_preserva_o_que_foi_pedido():
    """Reter e uma janela declarada, nao um efeito colateral da versao corrente."""
    eng = _vitrine([("AAPL", "0.5.0", "2020-06-30", "fundamental"),
                    ("AAPL", "0.7.2", "2020-06-30", "fundamental"),
                    ("AAPL", "0.8.0", "2020-06-30", "fundamental")])
    res = pub._remover_versoes_obsoletas(eng, {"0.8.0", "0.7.2"})
    assert res["por_versao"] == {"0.5.0": 1}
    assert _versoes(eng) == ["0.7.2", "0.8.0"]


def test_trilha_que_o_script_nao_publica_e_relatada_e_nao_apagada():
    """Apagar o que nao se publica e como a remocao vira destrutiva por surpresa.

    Mas ficar calada sobre ela repetiria o defeito de origem -- o que a
    varredura nao alcanca precisa aparecer em algum lugar.
    """
    eng = _vitrine([("AAPL", "0.5.0", "2020-06-30", "fundamental"),
                    ("AAPL", "0.5.0", "2020-06-30", "asymmetric")])
    res = pub._remover_versoes_obsoletas(eng, {"0.8.0"})
    assert res["por_versao"] == {"0.5.0": 1}
    assert res["trilhas_intocadas"] == [
        {"score_version": "0.5.0", "track": "asymmetric", "linhas": 1,
         "simbolos": 1}]
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM market_us.score_vintages "
                              "WHERE track='asymmetric'")).scalar() == 1


def test_publicar_versao_antiga_nao_apaga_a_corrente():
    """`--versao 0.7.2` para comparar nao pode derrubar o que o app publicado le."""
    corrente = pub.versao_corrente()
    assert pub.janela_de_retencao("0.7.2") == {"0.7.2", corrente}


def test_reter_aceita_versoes_extras():
    corrente = pub.versao_corrente()
    janela = pub.janela_de_retencao(corrente, ["0.7.2", " ", "0.7.1"])
    assert janela == {corrente, "0.7.2", "0.7.1"}


def test_simulacao_mostra_a_contagem_por_versao_antes_de_apagar():
    """Decidir apagar 70 mil linhas exige ver o inventario ANTES, nao depois."""
    local = _vitrine([("AAPL", "0.8.0", "2020-06-30", "fundamental")])
    remoto = _vitrine([("AAPL", "0.5.0", "2020-06-30", "fundamental"),
                       ("MSFT", "0.5.0", "2020-06-30", "fundamental"),
                       ("AAPL", "0.8.0", "2020-06-30", "fundamental")])
    resumo = pub.publicar(local=local, remoto=remoto, aplicar=False,
                          versao="0.8.0")
    assert resumo["gravado"] is False
    assert resumo["versoes_obsoletas"] == {"0.5.0": 2}
    assert resumo["linhas_obsoletas"] == 2
    assert [i["score_version"] for i in resumo["vitrine_por_versao"]] == [
        "0.5.0", "0.8.0"]
    # simulacao nao grava, e nao apaga
    assert _versoes(remoto) == ["0.5.0", "0.8.0"]


def test_simulacao_sem_vitrine_alcancavel_nao_quebra():
    """O resumo perde o inventario; a simulacao continua respondendo."""
    local = _vitrine([("AAPL", "0.8.0", "2020-06-30", "fundamental")])
    resumo = pub.publicar(local=local, remoto=None, aplicar=False,
                          versao="0.8.0")
    assert resumo["ok"] is True
    assert "vitrine_por_versao" not in resumo


def test_portao_de_saidas_le_a_safra_da_versao_corrente():
    """O portao media a tabela inteira; a tela le so a versao corrente.

    Enquanto a vitrine guardava tres metodologias mortas, a fracao de saidas
    "no painel" saia de uma populacao que painel nenhum consulta -- o defeito
    `medir-a-fonte-que-a-decisao-le`, agora do lado do portao de rigor.
    """
    import core.validacao_motor as vm
    fonte = open(vm.__file__, encoding="utf-8").read()
    alvo = fonte.split("def _registro_de_saidas_us")[1].split("\ndef ")[0]
    assert "v.score_version = :sv" in alvo
    assert "US_FUNDAMENTAL_SCORE_VERSION" in alvo
