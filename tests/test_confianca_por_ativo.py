"""A-150: a confianca do dado chega ao sinal de qualidade, e chega comparavel."""
from __future__ import annotations

import pandas as pd

import core.global_portfolio.confianca_ativos as ca
from core.global_portfolio import signals


def _pos(*linhas):
    return pd.DataFrame([
        {"symbol": s, "asset_class": c, "payload": {"metrics": {m: v}}}
        for s, c, m, v in linhas])


def test_ancora_torna_as_classes_comparaveis():
    """FII vive em 0-1 e B3 em 0-100. O ativo mediano de cada classe tem de sair
    com peso parecido -- senao o motor prefere uma classe por causa da regua."""
    b3 = ca._ancorar(97.2, "b3")
    fii = ca._ancorar(0.697 * 100.0, "fii")
    assert abs(b3 - fii) < 8.0, (b3, fii)


def test_ancora_nao_passa_de_100():
    assert ca._ancorar(95.0, "fii") == 100.0  # ancora 76.6: acima da norma nao vira bonus


def test_classe_desconhecida_nao_inventa_confianca():
    assert ca._ancorar(80.0, "cripto") is None


def test_uma_classe_indisponivel_zera_todas(monkeypatch):
    """O ponto mais delicado do modulo. Ponderar B3 e nao ponderar EUA faria o
    motor preferir americanos por nao terem sido medidos."""
    monkeypatch.setattr(ca, "_FONTES", {
        "b3": lambda e, a: {"PETR4": 90.0},
        "us": lambda e, a: (_ for _ in ()).throw(RuntimeError("vitrine fora")),
    })
    df = _pos(("PETR4", "b3", "score", 70.0), ("AAPL", "us", "entry_score", 80.0))
    assert ca.confianca_por_ativo(df, engine=object()) == {}


def test_classe_ausente_da_carteira_nao_derruba_o_resto(monkeypatch):
    """Nao ter FII na carteira nao e falha de fonte: a fonte nem e consultada."""
    monkeypatch.setattr(ca, "_FONTES", {
        "b3": lambda e, a: {"PETR4": 90.0},
        "fii": lambda e, a: (_ for _ in ()).throw(RuntimeError("nao deveria rodar")),
    })
    df = _pos(("PETR4", "b3", "score", 70.0))
    assert ca.confianca_por_ativo(df, engine=object()) == {"PETR4": 90.0}


def test_ativo_sem_medida_mantem_peso_pleno(monkeypatch):
    """Ausencia por ativo e diferente de ausencia por classe: dentro da classe,
    nao ter medida nao e evidencia de dado ruim."""
    monkeypatch.setattr(ca, "_FONTES", {"b3": lambda e, a: {"PETR4": 50.0}})
    df = _pos(("PETR4", "b3", "score", 70.0), ("VALE3", "b3", "score", 90.0))
    assert ca.confianca_por_ativo(df, engine=object()) == {"PETR4": 50.0}


def test_confianca_baixa_encolhe_a_conviccao_sem_inverter_o_lado():
    """Confianca pondera a forca do sinal, nunca a direcao. Dado ruim aproxima
    de neutro; nao transforma 'aumentar' em 'reduzir'."""
    df = _pos(("A", "b3", "score", 90.0), ("B", "b3", "score", 10.0))
    plena = {s.symbol: s for s in signals.sinais_qualidade(df)}
    fraca = {s.symbol: s for s in signals.sinais_qualidade(df, confianca={"A": 20.0, "B": 20.0})}
    assert abs(fraca["A"].valor) < abs(plena["A"].valor)
    assert abs(fraca["B"].valor) < abs(plena["B"].valor)
    assert (fraca["A"].valor > 0) == (plena["A"].valor > 0)
    assert (fraca["B"].valor < 0) == (plena["B"].valor < 0)


def test_carteira_vazia_nao_consulta_banco():
    assert ca.confianca_por_ativo(pd.DataFrame()) == {}


def test_sufixo_sa_e_resolvido_de_volta_ao_symbol_da_carteira(monkeypatch):
    """A carteira pode trazer PETR4.SA; a fonte responde PETR4. O dict devolvido
    tem de usar a chave que `sinais_qualidade` vai procurar."""
    visto = {}
    def fonte(e, alvo):
        visto.update(alvo)
        return {alvo["PETR4"]: 90.0}
    monkeypatch.setattr(ca, "_FONTES", {"b3": fonte})
    df = _pos(("PETR4.SA", "b3", "score", 70.0))
    assert ca.confianca_por_ativo(df, engine=object()) == {"PETR4.SA": 90.0}
    assert visto == {"PETR4": "PETR4.SA"}
