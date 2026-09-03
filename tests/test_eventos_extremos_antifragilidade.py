"""Guardas do Índice de Antifragilidade.

O teste que justifica este arquivo é o do teto: uma carteira bem diversificada e
sem nenhum caixa tira nota alta na média ponderada, e média ponderada é
exatamente o lugar onde a especificação proibiu esconder risco. Os outros dois
que importam são o da banda cambial -- porque monotonicidade daria nota máxima
para o viés doméstico total -- e o do papel indeterminado, que não pode ser lido
como "este ativo não é defensivo".
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.eventos_extremos import antifragilidade as af
from core.global_portfolio.roles import PapelDoAtivo

SETORES = ["Bancos", "Energia", "Tecnologia", "Consumo",
           "Saude", "Industria", "Imobiliario", "Utilidades"]


def carteira(n: int = 16, *, caixa: int = 2) -> pd.DataFrame:
    """16 posições equiponderadas, 8 setores, 4 países, 4 moedas.

    Diversificada de propósito: as fragilidades que os testes querem exercitar
    entram uma de cada vez, e não vêm de brinde no quadro.
    """
    paises = ["BR"] * 11 + ["US"] * 3 + ["JP", "DE"]
    moedas = ["BRL"] * 11 + ["USD"] * 3 + ["JPY", "EUR"]
    linhas = []
    for i in range(n):
        linhas.append({
            "symbol": f"AT{i:02d}",
            "weight_global": 1.0 / n,
            "sector": SETORES[i % len(SETORES)],
            "country": paises[i % len(paises)],
            "currency": moedas[i % len(moedas)],
            "asset_class": "caixa" if i < caixa else "acoes",
        })
    return pd.DataFrame(linhas)


def saudavel(**extra):
    base = dict(liquidez=0.25, correlacao_estresse=0.10,
                qualidade_credito=0.95, perda_simulada=0.05)
    base.update(extra)
    return af.calcular(carteira(), **base)


# -- Publicar os doze, sempre ------------------------------------------------
def test_os_doze_componentes_saem_publicados_mesmo_sem_fonte():
    i = af.calcular(carteira())
    assert tuple(p.chave for p in i.partes) == af.COMPONENTES
    assert all(p.evidencia for p in i.partes)


def test_componente_sem_fonte_fica_none_e_entra_na_cobertura():
    i = af.calcular(carteira(), liquidez=0.25)
    assert i.nota_de(af.C_PERDA) is None
    assert af.C_PERDA in i.nao_medidos
    assert i.cobertura < 1.0
    assert any("capacidade de suportar perdas" in lim.lower()
               for lim in i.limitacoes)


def test_medido_em_zero_nao_e_a_mesma_coisa_que_nao_medido():
    zero = af.calcular(carteira(caixa=0), liquidez=0.0)
    ausente = af.calcular(carteira(caixa=0).drop(columns=["asset_class"]))
    assert zero.nota_de(af.C_LIQUIDEZ) == pytest.approx(0.0)
    assert af.C_LIQUIDEZ in zero.criticos
    assert ausente.nota_de(af.C_LIQUIDEZ) is None
    assert af.C_LIQUIDEZ in ausente.nao_medidos


# -- A nota única não pode esconder o risco ----------------------------------
def test_componente_critico_limita_o_indice_por_mais_diversificada_que_seja():
    i = saudavel(liquidez=0.0)
    assert i.bruto > af.TETO_COM_COMPONENTE_CRITICO, (
        "a média ponderada precisa mesmo estar alta, senão o teste não testa nada")
    assert i.valor <= af.TETO_COM_COMPONENTE_CRITICO
    assert i.teto_aplicado


def test_o_componente_que_causou_o_teto_sai_nomeado_no_alerta():
    i = saudavel(liquidez=0.0)
    assert i.criticos == (af.C_LIQUIDEZ,)
    assert len(i.alertas) == 1
    assert "Liquidez" in i.alertas[0]
    assert "não compensável" in i.alertas[0]


def test_carteira_sem_fragilidade_eliminatoria_nao_leva_teto():
    i = saudavel()
    assert not i.teto_aplicado
    assert i.valor == pytest.approx(i.bruto)
    assert i.alertas == ()
    assert i.valor > 0.6


def test_piores_ordena_do_pior_para_o_melhor_e_ignora_nao_medidos():
    i = af.calcular(carteira(), liquidez=0.02, correlacao_estresse=0.10)
    notas = [p.nota for p in i.piores]
    assert notas == sorted(notas)
    assert all(p.medido for p in i.piores)
    assert i.piores[0].chave == af.C_LIQUIDEZ


# -- A exposição cambial é banda, não escada ---------------------------------
def test_cambio_no_meio_da_banda_e_nota_maxima():
    assert af._nota_em_banda(0.30) == pytest.approx(1.0)


def test_vies_domestico_total_e_tao_fragil_quanto_cambio_descoberto():
    assert af._nota_em_banda(0.0) == pytest.approx(0.0)
    assert af._nota_em_banda(1.0) == pytest.approx(0.0)


def test_cambio_fora_da_banda_cai_gradualmente_e_nao_de_uma_vez():
    assert 0.0 < af._nota_em_banda(0.05) < 1.0
    assert 0.0 < af._nota_em_banda(0.65) < 1.0


def test_carteira_toda_em_real_marca_fragilidade_cambial():
    df = carteira()
    df["currency"] = "BRL"
    i = af.calcular(df, liquidez=0.25)
    assert i.nota_de(af.C_CAMBIAL) == pytest.approx(0.0)
    assert af.C_CAMBIAL in i.criticos


# -- Papel indeterminado não é papel negado ----------------------------------
def _carteira4() -> pd.DataFrame:
    return pd.DataFrame([
        {"symbol": s, "weight_global": 0.25, "sector": "Bancos",
         "country": "BR", "currency": "BRL", "asset_class": "acoes"}
        for s in ("A", "B", "C", "D")])


def _papel(symbol, papeis=(), indeterminados=()):
    return PapelDoAtivo(symbol=symbol, papeis=tuple(papeis), evidencias=(),
                        indeterminados=tuple(indeterminados), justificativa="")


def test_ativo_com_papel_indeterminado_sai_do_denominador():
    """Contá-lo como "não defensivo" puniria a carteira com pior cobertura."""
    papeis = [
        _papel("A", papeis=("renda",)),
        _papel("B"),
        _papel("C", indeterminados=af.PAPEIS_DEFENSIVOS),
        _papel("D", indeterminados=af.PAPEIS_DEFENSIVOS),
    ]
    i = af.calcular(_carteira4(), papeis=papeis, liquidez=0.25)
    parte = i.parte(af.C_DEFENSIVOS)
    assert parte.bruto == pytest.approx(0.50), (
        "1 defensivo entre os 2 avaliáveis, e não 1 entre 4")
    assert "cobertura 50%" in parte.evidencia


def test_cobertura_baixa_de_papeis_vira_limitacao_escrita():
    papeis = [_papel("A", papeis=("renda",))] + [
        _papel(s, indeterminados=af.PAPEIS_DEFENSIVOS) for s in ("B", "C", "D")]
    i = af.calcular(_carteira4(), papeis=papeis, liquidez=0.25)
    assert any("apenas 25%" in lim for lim in i.limitacoes)


def test_sem_papeis_os_dois_componentes_ficam_none():
    i = af.calcular(carteira(), liquidez=0.25)
    assert i.nota_de(af.C_DEFENSIVOS) is None
    assert i.nota_de(af.C_BENEFICIARIOS) is None


def test_papeis_de_choque_contam_no_componente_certo():
    papeis = [_papel(s, papeis=("hedge_cambial",)) for s in ("A", "B")] + [
        _papel(s) for s in ("C", "D")]
    i = af.calcular(_carteira4(), papeis=papeis, liquidez=0.25)
    assert i.parte(af.C_BENEFICIARIOS).bruto == pytest.approx(0.50)
    assert i.parte(af.C_DEFENSIVOS).bruto == pytest.approx(0.0)


# -- Ausência de dado nunca vira ausência de risco ---------------------------
def test_coluna_de_pais_ausente_nao_vira_independencia_do_brasil():
    i = af.calcular(carteira().drop(columns=["country"]), liquidez=0.25)
    assert i.nota_de(af.C_BRASIL) is None
    assert i.nota_de(af.C_CONC_PAIS) is None
    assert any("country" in lim for lim in i.limitacoes)


def test_coluna_presente_e_toda_nula_e_tao_inutil_quanto_ausente():
    df = carteira()
    df["currency"] = None
    i = af.calcular(df, liquidez=0.25)
    assert i.nota_de(af.C_CAMBIAL) is None
    assert i.nota_de(af.C_CONC_MOEDA) is None


def test_cobertura_abaixo_do_minimo_nao_publica_indice():
    df = carteira()[["symbol", "weight_global"]]
    i = af.calcular(df)
    assert i.valor is None
    assert i.cobertura < af.COBERTURA_MINIMA
    assert any("cobertura" in lim for lim in i.limitacoes)
    assert len(i.partes) == len(af.COMPONENTES), "os doze saem mesmo assim"


def test_carteira_vazia_nao_e_carteira_antifragil():
    i = af.calcular(pd.DataFrame())
    assert i.valor is None and i.cobertura == pytest.approx(0.0)
    assert i.limitacoes


def test_quadro_sem_a_coluna_de_peso_parece_falha():
    i = af.calcular(carteira().drop(columns=["weight_global"]))
    assert i.valor is None
    assert any("weight_global" in lim for lim in i.limitacoes)


# -- Escala e determinismo ----------------------------------------------------
def test_pesos_nao_normalizados_dao_o_mesmo_indice():
    """HHI de pesos em percentual sairia noutra escala, sem erro nenhum."""
    df = carteira()
    df["weight_global"] = df["weight_global"] * 100.0
    a = saudavel()
    b = af.calcular(df, liquidez=0.25, correlacao_estresse=0.10,
                    qualidade_credito=0.95, perda_simulada=0.05)
    assert b.valor == pytest.approx(a.valor)
    assert b.nota_de(af.C_CONC_ATIVO) == pytest.approx(a.nota_de(af.C_CONC_ATIVO))


def test_ordem_das_linhas_nao_muda_o_indice():
    df = carteira()
    kw = dict(liquidez=0.25, correlacao_estresse=0.10, perda_simulada=0.05)
    a = af.calcular(df, **kw)
    b = af.calcular(df.iloc[::-1].reset_index(drop=True), **kw)
    assert b.valor == pytest.approx(a.valor)


# -- Diversificação sozinha não responde a pergunta ---------------------------
def test_so_o_quadro_de_posicoes_nao_publica_indice():
    """Regressão: as concentrações saem de graça e passariam a cobertura.

    Sem liquidez, perda simulada nem correlação sob estresse, o índice saía em
    0,81 -- e quem não tem stress test tirava nota melhor que quem tem.
    """
    i = af.calcular(carteira().drop(columns=["asset_class"]))
    assert i.cobertura > af.COBERTURA_MINIMA, "a cobertura global passava"
    assert i.valor is None
    assert any("resistência a choque" in lim for lim in i.limitacoes)


def test_a_limitacao_nomeia_o_que_falta_do_nucleo():
    i = af.calcular(carteira(), liquidez=0.25)
    texto = " ".join(i.limitacoes)
    assert "capacidade de suportar perdas" in texto
    assert "correlação durante estresse" in texto


def test_dois_dos_tres_do_nucleo_ja_publicam_o_indice():
    i = af.calcular(carteira(), liquidez=0.25, perda_simulada=0.05)
    assert i.valor is not None


def test_as_partes_continuam_publicadas_mesmo_sem_indice():
    """Não medir o núcleo não apaga a concentração que foi medida."""
    i = af.calcular(carteira().drop(columns=["asset_class"]))
    assert i.valor is None
    assert i.nota_de(af.C_CONC_ATIVO) == pytest.approx(1.0)
    assert len(i.piores) >= 6


def test_medicao_invalida_nao_vira_nota():
    i = af.calcular(carteira(), liquidez=float("nan"), perda_simulada=float("inf"))
    assert af.C_PERDA in i.nao_medidos
    # NaN cai fora e a liquidez volta a ser derivada da classe de ativo.
    assert i.nota_de(af.C_LIQUIDEZ) is not None


# -- Leitura ------------------------------------------------------------------
def test_descrever_publica_todos_os_componentes_e_a_cobertura():
    texto = "\n".join(saudavel().descrever())
    for rotulo in af.ROTULOS.values():
        assert rotulo in texto
    assert "da pergunta" in texto


def test_descrever_diz_quando_o_teto_escondeu_uma_media_melhor():
    texto = "\n".join(saudavel(liquidez=0.0).descrever())
    assert "teto" in texto and "média ponderada seria" in texto


def test_indice_carrega_a_versao_da_metodologia():
    assert saudavel().versao
