"""Ponto-no-tempo do score histórico: disponibilidade MEDIDA vs MODELADA.

Motivo histórico (2026-07): quando a 1ª vintage real (first_seen_proxy) apareceu
no banco, a coluna AvailableAt passou a vir no batch inteiro (NaT p/ linhas de
backfill `migration_baseline`) e um filtro `notna() & <= decisão` zerava o
histórico → Criação de Portfólio caía em "Nenhum segmento retornou dados
suficientes". Por isso exigir `notna()` puro continua ERRADO: apagaria o
backtest inteiro em produção (hoje 100 % das vintages são baseline) e trocaria
uma regra de publication lag justificada por nada.

Auditoria 2026-08 (achado A-002): o defeito real não é aceitar a linha sem
vintage — é apresentar uma disponibilidade MODELADA (prazo legal da CVM) como se
fosse MEDIDA. Este arquivo cristaliza as quatro regras da correção:

1. lote só-baseline com lag=1 e rebalance em abril PASSA (o prazo de publicação
   do exercício N−1 venceu em 31/03), mas a cobertura MEDIDA reportada é 0.0;
2. vintage medida nunca é contornada — carimbo posterior à decisão barra a linha
   mesmo havendo linha baseline do mesmo ticker;
3. sem vintage medida e com a decisão ANTES do prazo legal, a linha é BARRADA
   (fail-closed) — era look-ahead latente com lag=0 ou rebalance em janeiro;
4. a cobertura é consultável pelos chamadores point-in-time.

Unidades: `cobertura_medida` é adimensional (0..1) sobre snapshots usados.
"""
import pandas as pd

from views.empresas_b3 import (
    PITCoverage,
    _get_pesos_setor,
    _prazo_publicacao_cvm,
    _score_historico_ano,
    _score_historico_ano_com_cobertura,
)


def _hist(tk, anos, availableat=None):
    df = pd.DataFrame({
        "Ticker": tk,
        "Data": [pd.Timestamp(a, 12, 31) for a in anos],
        "ROE": [0.15 + 0.01 * i for i in range(len(anos))],
        "ROIC": [0.12] * len(anos),
        "Margem_Liquida": [0.10] * len(anos),
        "P/L": [8.0] * len(anos),
        "P/VP": [1.2] * len(anos),
        "DY": [0.05] * len(anos),
    })
    if availableat is not None:
        df["AvailableAt"] = availableat
    return df


_TKG = {t: {"SETOR": "Materiais Básicos", "SUBSETOR": "x", "SEGMENTO": "y"}
        for t in ("AAAA3", "BBBB3", "CCCC3")}
_PESOS = _get_pesos_setor("Materiais Básicos")


# ── 1. Modelo justificado: passa, mas declarado como MODELADO ────────────────

def test_nat_availableat_passa_pelo_corte_fiscal_como_modelado():
    anos = list(range(2015, 2024))
    batch = {
        # backfill puro: AvailableAt existe mas é toda NaT
        t: _hist(t, anos, availableat=[pd.NaT] * len(anos))
        for t in ("AAAA3", "BBBB3", "CCCC3")
    }
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2020, _PESOS, _TKG, lag=1)
    assert sm, "NaT (backfill) não pode zerar o score — o prazo CVM já venceu"
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}
    # o ponto do achado A-002: passou, mas NÃO é point-in-time medido
    assert cov.cobertura_medida == 0.0
    assert cov.nivel == "modelada"
    assert cov.snapshots_modelados == 3
    assert cov.snapshots_medidos == 0
    assert cov.linhas_medidas == 0
    assert cov.linhas_barradas_vintage == 0


def test_sem_coluna_availableat_segue_o_mesmo_modelo():
    anos = list(range(2015, 2024))
    batch = {t: _hist(t, anos) for t in ("AAAA3", "BBBB3", "CCCC3")}
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2020, _PESOS, _TKG, lag=1)
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}
    assert cov.cobertura_medida == 0.0
    assert cov.nivel == "modelada"


def test_assinatura_publica_preservada_para_os_quatro_call_sites():
    anos = list(range(2015, 2024))
    batch = {t: _hist(t, anos) for t in ("AAAA3", "BBBB3", "CCCC3")}
    sm = _score_historico_ano(batch, list(batch), 2020, _PESOS, _TKG, lag=1)
    assert isinstance(sm, dict)
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}


# ── 2. Vintage medida nunca é contornada ─────────────────────────────────────

def test_vintage_medida_posterior_a_decisao_e_barrada():
    anos = [2019, 2020]
    # vintage real: o dado de 2020 só ficou disponível em jun/2021 → para a
    # decisão de abr/2021 a linha de 2020 NÃO pode entrar; a de 2019 (sem
    # vintage) entra pelo modelo, porque o prazo legal venceu em 31/03/2020.
    av = [pd.NaT, pd.Timestamp(2021, 6, 30, tz="UTC")]
    batch = {"AAAA3": _hist("AAAA3", anos, availableat=av),
             "BBBB3": _hist("BBBB3", anos, availableat=[pd.NaT, pd.NaT]),
             "CCCC3": _hist("CCCC3", anos, availableat=[pd.NaT, pd.NaT])}
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=1)
    assert "AAAA3" in sm            # entra com a linha de 2019
    assert cov.linhas_barradas_vintage == 1
    # a linha baseline do MESMO ticker não "reabilita" o ano barrado
    assert sm["AAAA3"] == _score_historico_ano(
        {"AAAA3": _hist("AAAA3", [2019], availableat=[pd.NaT]),
         "BBBB3": batch["BBBB3"], "CCCC3": batch["CCCC3"]},
        list(batch), 2021, _PESOS, _TKG, lag=1)["AAAA3"]


def test_vintage_posterior_bloqueia_baseline_duplicada_do_mesmo_exercicio():
    """Uma NaT concorrente não pode contornar a vintage real posterior."""
    baseline = _hist("AAAA3", [2019, 2020], availableat=[pd.NaT, pd.NaT])
    vintage_posterior = _hist(
        "AAAA3", [2020], availableat=[pd.Timestamp(2021, 6, 30, tz="UTC")]
    )
    batch = {
        "AAAA3": pd.concat([baseline, vintage_posterior], ignore_index=True),
        "BBBB3": _hist("BBBB3", [2019], availableat=[pd.NaT]),
        "CCCC3": _hist("CCCC3", [2019], availableat=[pd.NaT]),
    }

    score, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=1
    )
    score_sem_exercicio_barrado = _score_historico_ano(
        {
            "AAAA3": _hist("AAAA3", [2019], availableat=[pd.NaT]),
            "BBBB3": batch["BBBB3"],
            "CCCC3": batch["CCCC3"],
        },
        list(batch), 2021, _PESOS, _TKG, lag=1,
    )

    assert score["AAAA3"] == score_sem_exercicio_barrado["AAAA3"]
    assert cov.linhas_barradas_vintage == 2
    assert cov.linhas_modeladas == 3  # 2020 NaT foi barrada pela vintage rival
    assert cov.linhas_barradas_prazo == 0


def test_vintage_medida_anterior_a_decisao_passa_e_conta_como_medida():
    anos = [2019, 2020]
    av = [pd.Timestamp(2020, 3, 20, tz="UTC"), pd.Timestamp(2021, 3, 20, tz="UTC")]
    batch = {"AAAA3": _hist("AAAA3", anos, availableat=av),
             "BBBB3": _hist("BBBB3", anos, availableat=av),
             "CCCC3": _hist("CCCC3", anos, availableat=av)}
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=1)
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}
    assert cov.cobertura_medida == 1.0
    assert cov.nivel == "medida"
    assert cov.linhas_barradas_vintage == 0
    assert cov.linhas_barradas_prazo == 0


def test_lote_misto_reporta_cobertura_entre_zero_e_um():
    anos = [2019, 2020]
    batch = {
        # medida e já disponível na decisão de abr/2021
        "AAAA3": _hist("AAAA3", anos, availableat=[
            pd.NaT, pd.Timestamp(2021, 3, 20, tz="UTC")]),
        # medida, mas posterior à decisão → cai para a linha modelada de 2019
        "BBBB3": _hist("BBBB3", anos, availableat=[
            pd.NaT, pd.Timestamp(2021, 8, 1, tz="UTC")]),
        # só baseline
        "CCCC3": _hist("CCCC3", anos, availableat=[pd.NaT, pd.NaT]),
    }
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=1)
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}
    assert cov.snapshots_medidos == 1 and cov.snapshots_modelados == 2
    assert 0.0 < cov.cobertura_medida < 1.0
    assert cov.nivel == "mista"
    assert cov.linhas_barradas_vintage == 1


# ── 3. Fail-closed onde o modelo não se justifica ────────────────────────────

def test_prazo_publicacao_e_31_03_do_ano_seguinte():
    assert _prazo_publicacao_cvm(2020) == pd.Timestamp(2021, 3, 31)


def test_decisao_antes_do_prazo_barra_linha_sem_vintage():
    # rebalance em JANEIRO/2021 com lag=1: o exercício 2020 só é público em
    # 31/03/2021. Sem vintage medida a linha não tem como ser point-in-time.
    batch = {t: _hist(t, [2020], availableat=[pd.NaT])
             for t in ("AAAA3", "BBBB3", "CCCC3")}
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=1, rebal_month=1)
    assert sm == {}, "look-ahead latente: decisão anterior ao prazo legal"
    assert cov.linhas_barradas_prazo == 3
    assert cov.nivel == "indisponivel"
    # mesma configuração em abril: o prazo venceu e o modelo se justifica
    sm_abr, cov_abr = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=1, rebal_month=4)
    assert set(sm_abr) == {"AAAA3", "BBBB3", "CCCC3"}
    assert cov_abr.linhas_barradas_prazo == 0


def test_lag_zero_barra_o_exercicio_ainda_nao_publicado():
    # lag=0 pede o exercício do próprio ano da decisão: em abr/2021 o balanço
    # de 2021 nem existe. Sem vintage medida, fail-closed.
    batch = {t: _hist(t, [2021], availableat=[pd.NaT])
             for t in ("AAAA3", "BBBB3", "CCCC3")}
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=0)
    assert sm == {}
    assert cov.linhas_barradas_prazo == 3


def test_lag_zero_nao_apaga_exercicios_ja_publicados():
    # a barreira é POR LINHA: com lag=0 os exercícios antigos continuam válidos,
    # só o do ano corrente é barrado — fail-closed não vira fail-tudo.
    batch = {t: _hist(t, [2019, 2020, 2021], availableat=[pd.NaT] * 3)
             for t in ("AAAA3", "BBBB3", "CCCC3")}
    sm, cov = _score_historico_ano_com_cobertura(
        batch, list(batch), 2021, _PESOS, _TKG, lag=0)
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}
    assert cov.linhas_barradas_prazo == 3          # só as linhas de 2021
    assert cov.linhas_modeladas == 6               # 2019 e 2020 de cada ticker


# ── 4. Agregação da cobertura ────────────────────────────────────────────────

def test_cobertura_agrega_por_soma_e_nao_inventa_medicao():
    vazia = PITCoverage()
    assert vazia.cobertura_medida == 0.0, "sem snapshot a cobertura é 0, não 1"
    assert vazia.nivel == "indisponivel"
    soma = PITCoverage(snapshots_medidos=1, decisoes=1) + PITCoverage(
        snapshots_modelados=3, decisoes=1)
    assert soma.snapshots == 4
    assert soma.decisoes == 2
    assert soma.cobertura_medida == 0.25
    assert soma.as_dict()["nivel"] == "mista"
