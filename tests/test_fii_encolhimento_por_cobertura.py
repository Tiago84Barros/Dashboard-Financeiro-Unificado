"""Achado A-106: cobertura parcial encolhia a nota do FII para ZERO, não para o
neutro. Ignorância virava desempenho ruim — e proporcionalmente ao mérito, de
modo que a mesma lacuna custava muito mais ao fundo bom que ao ruim.

Medido em 23/08/2026 sobre as 394 linhas de ``market.fii_selection_inputs``:
entre os 258 fundos que o motor declara ``ready``, 3,4% a 5,1% dos pares saíam
invertidos — o pior vencendo o melhor por ter mais indicadores preenchidos.
Caso concreto: GRUL11 (mérito 67,3, cobertura 68%) caía para 57,5 e perdia para
VILG11 (mérito 60,2, cobertura 92%), que subia para 57,9.
"""
from __future__ import annotations

from core.fii_methodology import COMMON_METRICS, TYPE_METRICS, score_fiis_by_type

# Métricas de um FII de tijolo, na ordem em que o motor as lê.
_DEFINICOES = COMMON_METRICS + TYPE_METRICS["tijolo"]
_CHAVES = tuple(d.fallback_keys[0] if d.fallback_keys else d.key for d in _DEFINICOES)
# Preenchidas para todo mundo; as demais são as que o fundo opaco perde.
_ESSENCIAIS = ("dy_12m", "pvp", "liquidez_diaria")


def _fundo(ticker: str, nivel: float, *, manter: float = 1.0) -> dict:
    """Fundo sintético cujo mérito em TODA métrica é ``nivel`` (0=pior, 1=melhor).

    ``manter`` é a fração das métricas não essenciais que o fundo preenche. É o
    caso que interessa: mesmo desempenho, menos evidência.
    """
    linha: dict = {"ticker": ticker, "tipo": "tijolo", "nome": ticker,
                   "history_months": 60, "data_consistency": .9,
                   "parser_calibration": .9}
    descartaveis = [c for c in _CHAVES if c not in _ESSENCIAIS]
    corte = int(round(len(descartaveis) * manter))
    omitidas = set(descartaveis[corte:])
    for definicao, chave in zip(_DEFINICOES, _CHAVES):
        if chave in omitidas:
            continue
        if definicao.direction == "lower":
            linha[chave] = 1.0 - nivel          # menor é melhor
        elif definicao.direction == "target":
            linha[chave] = definicao.target or .95   # cravado no alvo
        else:
            linha[chave] = nivel
    return linha


def _nota(ticker: str, resultado: list[dict]) -> dict:
    return next(r for r in resultado if r["ticker"] == ticker)


def _universo() -> list[dict]:
    """Seis pares espalhados por todo o espectro, para o percentil ter escala."""
    return [_fundo(f"PAR{i}11", i / 5.0) for i in range(6)]


def test_fundo_mediano_nao_cai_abaixo_da_mediana_que_ele_define():
    """A escala é percentílica: 50 é o par mediano. O encolhimento não pode
    mover o ponto fixo — um fundo mediano com cobertura parcial continua
    mediano, apenas com menos convicção. Sem a correção, raw≈50 virava ≈40."""
    resultado = score_fiis_by_type(_universo() + [_fundo("MEIO11", .5, manter=.35)])
    alvo = _nota("MEIO11", resultado)
    assert alvo["coverage"] < .90, "premissa: o fundo tem mesmo cobertura parcial"
    assert 45.0 <= alvo["type_score"] <= 55.0, (
        f"fundo mediano com cobertura {alvo['coverage']:.0%} foi para "
        f"{alvo['type_score']:.1f}; ausência não pode virar desempenho ruim")


def test_penalidade_por_ignorancia_nao_depende_do_merito():
    """A punição por não saber deve ser simétrica. Multiplicar o score tirava
    4,5x mais pontos do fundo bom que do ruim, pela MESMA lacuna."""
    resultado = score_fiis_by_type(_universo() + [
        _fundo("BOM11", 1.0, manter=.35), _fundo("BOMC11", 1.0),
        _fundo("RUIM11", 0.0, manter=.35), _fundo("RUIMC11", 0.0)])
    perda_bom = _nota("BOMC11", resultado)["type_score"] - _nota("BOM11", resultado)["type_score"]
    perda_ruim = _nota("RUIM11", resultado)["type_score"] - _nota("RUIMC11", resultado)["type_score"]
    # Encolher para o neutro tira do bom e devolve ao ruim, em módulo parecido.
    assert abs(perda_bom - perda_ruim) < 8.0, (
        f"a mesma lacuna custa {perda_bom:.1f} ao fundo bom e {perda_ruim:.1f} "
        f"ao ruim; a penalidade por opacidade não pode ser proporcional ao mérito")


def test_cobertura_nao_inverte_ordem_de_merito():
    """Fundo com mérito medido maior não pode ser ultrapassado por um pior só
    porque o pior tem mais indicadores preenchidos.

    Reproduz a geometria do caso real GRUL11 x VILG11: a diferença de cobertura
    é a que existe DENTRO da faixa que passa no gate (66% a 98%), não uma lacuna
    extrema. Encolher para o neutro pode reordenar quando a ignorância é grande
    o bastante — é para isso que serve —, mas não nesta faixa, e nunca porque a
    penalidade cresce com o mérito.
    """
    resultado = score_fiis_by_type(_universo() + [
        _fundo("MELHOR11", .95, manter=.75), _fundo("PIOR11", .65)])
    a, b = _nota("MELHOR11", resultado), _nota("PIOR11", resultado)
    assert a["raw_score"] > b["raw_score"], "premissa: MELHOR11 vence no mérito"
    assert a["coverage"] < b["coverage"], "premissa: MELHOR11 tem menos evidência"
    assert a["type_score"] > b["type_score"], (
        f"MELHOR11 raw={a['raw_score']:.1f} cob={a['coverage']:.0%} caiu para "
        f"{a['type_score']:.1f}, abaixo de PIOR11 raw={b['raw_score']:.1f} "
        f"cob={b['coverage']:.0%} final={b['type_score']:.1f}")


def test_cobertura_zero_devolve_o_neutro_e_nao_zero():
    """Sem nenhum indicador observado não há o que dizer: a nota é o neutro,
    não a pior nota possível."""
    resultado = score_fiis_by_type(_universo() + [
        {"ticker": "VAZIO11", "tipo": "tijolo", "nome": "VAZIO11"}])
    alvo = _nota("VAZIO11", resultado)
    assert alvo["coverage"] == 0.0
    assert alvo["type_score"] == 50.0, (
        f"cobertura zero devolveu {alvo['type_score']:.1f}; sem evidência a "
        f"nota é o neutro, e o gate de prontidão é que barra o fundo")
    assert alvo["data_readiness_status"] == "insufficient", (
        "quem barra o fundo sem dado é o gate, não a nota")
