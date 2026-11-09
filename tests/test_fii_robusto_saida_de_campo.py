"""A-118 no backtest que certifica a carteira ao vivo.

A correcao do A-118 (fundo que sai de campo rende zero, nao o que os
sobreviventes renderam) existia so em `point_in_time_backtest`. O walk-forward
que roda em producao -- `robust_optimizer_point_in_time_backtest`, chamado por
`data_pipeline/market/fii_pit.py` -- seguia renormalizando os pesos sobre os
fundos com retorno no periodo.

A regra testada aqui e uma equivalencia: um fundo da carteira que SUMIU do dado
no periodo tem de produzir exatamente o mesmo retorno que esse fundo PRESENTE
com retorno zero. Qualquer outra coisa inventa ganho (renormalizar) ou perda.
"""
import pandas as pd
import pytest

from core.fii_validation import robust_optimizer_point_in_time_backtest

DECISOES = ("2025-01-31", "2025-02-28")
DATAS = pd.date_range("2023-01-31", "2025-03-31", freq="ME")
# Primeira decisao (31/01) executa no primeiro mes com retorno depois dela:
# o periodo da primeira carteira e 28/02/2025.
PERIODO_1 = pd.Timestamp("2025-02-28")


def _snapshots() -> pd.DataFrame:
    """Mesmo universo de `test_fii_validation_v4`: 12 fundos, quatro tipos."""
    tipos = ["tijolo", "papel", "fof", "hibrido"] * 3
    linhas = []
    for i_decisao, decisao in enumerate(DECISOES):
        for i, tipo in enumerate(tipos):
            row = {
                "ticker": f"F{i:03d}11", "tipo": tipo,
                "type_score": 80 - i + i_decisao, "confidence": .90,
                "coverage": .95, "dy_12m": .10, "pvp": .90,
                "income_recurrence": .90,
                "liquidez_diaria": 3_000_000, "history_months": 36,
                "max_drawdown": -.15, "duration_anos": 3.0, "leverage": .05,
                "vacancia_fisica": .05, "delinquency": .01, "ltv": .55,
                "manager": f"manager-{i}", "sector": f"sector-{i}",
            }
            if tipo in {"tijolo", "hibrido"}:
                row.update(tenants={f"tenant-{i}": 1.0}, regions={f"region-{i}": 1.0})
            if tipo in {"papel", "hibrido"}:
                row.update(debtors={f"debtor-{i}": 1.0}, issuers={f"issuer-{i}": 1.0},
                           indexers={f"indexer-{i}": 1.0})
            linhas.append({
                "reference_date": decisao, "available_at": decisao,
                "ticker": row["ticker"], "fii_type": tipo,
                "score": row["type_score"], "confidence": row["confidence"],
                "coverage": row["coverage"],
                "availability_quality": "verified_publication",
                "portfolio_input_json": row,
            })
    return pd.DataFrame(linhas)


def _retornos(sai: str | None = None, *, modo: str = "presente") -> pd.DataFrame:
    """Retornos mensais positivos; `sai` some (ou zera) no periodo 1."""
    linhas = []
    for data in DATAS:
        for i in range(12):
            ticker = f"F{i:03d}11"
            total = .006 + i / 100_000 + (data.month % 3) / 1_000_000
            if ticker == sai and data == PERIODO_1:
                if modo == "sumiu":
                    continue
                total = 0.0
            linhas.append({"date": data, "ticker": ticker, "total_return": total})
    return pd.DataFrame(linhas)


def _rodar(retornos: pd.DataFrame) -> dict:
    return robust_optimizer_point_in_time_backtest(
        _snapshots(), retornos, pd.Series(.005, index=DATAS),
        {d: {"selic": 12.0, "ipca": 4.5} for d in DECISOES},
        transaction_cost=0, slippage=0,
    )


def _primeiro(resultado: dict) -> dict:
    assert resultado["status"] == "calculated", resultado
    return resultado["observations"][0]


@pytest.fixture(scope="module")
def fundo_que_sai() -> tuple[str, float]:
    """O fundo de maior peso na primeira carteira, e esse peso."""
    holdings = _primeiro(_rodar(_retornos()))["holdings"]
    ticker = max(holdings, key=holdings.get)
    return ticker, holdings[ticker]


def test_fundo_que_sumiu_rende_o_mesmo_que_fundo_presente_com_retorno_zero(fundo_que_sai):
    ticker, _ = fundo_que_sai
    zerado = _primeiro(_rodar(_retornos(ticker, modo="zero")))
    sumiu = _primeiro(_rodar(_retornos(ticker, modo="sumiu")))

    assert sumiu["portfolio_return_bruto"] == pytest.approx(
        zerado["portfolio_return_bruto"], abs=1e-12), (
        "a fatia do fundo que sumiu nao pode render o que os sobreviventes renderam")


def test_fundo_que_sumiu_nao_herda_o_ganho_dos_sobreviventes(fundo_que_sai):
    ticker, peso = fundo_que_sai
    completo = _primeiro(_rodar(_retornos()))
    sumiu = _primeiro(_rodar(_retornos(ticker, modo="sumiu")))

    # A versao antiga devolvia o ganho INTEIRO dos sobreviventes, como se a
    # carteira toda tivesse rendido. Com a fatia ausente a zero, o retorno
    # fica abaixo do da carteira completa (todos os retornos sao positivos).
    assert sumiu["portfolio_return_bruto"] < completo["portfolio_return_bruto"]
    assert sumiu["peso_ausente"] == pytest.approx(peso, abs=1e-9)


def test_a_saida_de_campo_e_declarada_no_resultado(fundo_que_sai):
    ticker, peso = fundo_que_sai
    resultado = _rodar(_retornos(ticker, modo="sumiu"))
    bloco = resultado["saida_de_campo"]

    assert bloco["periodos"] == resultado["periods"]
    assert bloco["periodos_com_ausencia"] == 1
    assert bloco["peso_ausente_maximo"] == pytest.approx(peso, abs=1e-9)


def test_sem_ausencia_o_bloco_zera():
    bloco = _rodar(_retornos())["saida_de_campo"]

    assert bloco["periodos_com_ausencia"] == 0
    assert bloco["peso_ausente_medio"] == pytest.approx(0.0)
    assert bloco["peso_ausente_maximo"] == pytest.approx(0.0)
