# -*- coding: utf-8 -*-
"""Mede quanto o excesso dos EUA depende da convencao de retorno de deslistagem.

O universo do ranking deixou de ser sobrevivente: 971 dos 3.145 simbolos do
painel PIT sao empresas que morreram. A MEDICAO de retorno nao acompanhou --
nenhuma fonte acessivel serve preco de ticker morto (yfinance devolve zero
barra, Stooq responde desafio de bot), entao a linha sem preco futuro e
descartada pelo backtest e o excesso continua sendo apurado entre sobreviventes.

Nao da para inventar o preco que nao existe. Da para dizer de quanto o numero
publicado depende do que se supoe sobre quem morreu, e e isso que este script
faz: reexecuta o walk-forward atribuindo as linhas sem preco futuro um retorno
por CONVENCAO, e devolve a banda. Incerteza com tamanho vira banda declarada,
nao portao que trava.

As convencoes cobrem o intervalo defensavel:

  descartar : o que o app publica hoje -- a linha sai da conta (equivale a supor
              que quem morreu teria rendido o mesmo que a media dos vivos)
  0%        : saida neutra, o caso da aquisicao pelo valor de tela
  -30%      : convencao de deslistagem por desempenho da literatura (CRSP)
  -100%     : perda total, o caso da falencia

O numero honesto nao e um ponto: e a banda entre -100% e 0%, com o valor
publicado hoje ("descartar") dito como o que e -- uma suposicao, nao uma
medicao.

    python scripts/sensibilidade_retorno_deslistagem_us.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import core.us_backtest as bt  # noqa: E402
from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION  # noqa: E402
from data_pipeline.us.scoring_history import build_annual_panel  # noqa: E402
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

CONVENCOES = [("descartar", None), ("0%", 0.0), ("-30%", -0.30), ("-100%", -1.0)]


def main() -> int:
    eng = create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
    with eng.connect() as conn:
        vintages = pd.read_sql(text(
            "SELECT as_of_date, symbol, score FROM market_us.score_vintages "
            "WHERE track='fundamental' AND score_version=:v"),
            conn, params={"v": US_FUNDAMENTAL_SCORE_VERSION})
        monthly = pd.read_sql(text(
            "SELECT symbol, month_end, adjusted_close FROM market_us.prices_monthly"),
            conn)
    vivo = build_annual_panel(vintages, monthly, horizon_months=12)
    # `build_annual_panel` ja descartou quem nao tem preco futuro; a diferenca
    # entre as duas chaves e exatamente a populacao invisivel a medicao.
    medidos = set(zip(pd.to_datetime(vivo["date"]).dt.date, vivo["symbol"]))
    todos = set(zip(vintages["as_of_date"], vintages["symbol"]))
    faltam = todos - medidos
    print(f"linhas do painel        : {len(todos)}")
    print(f"com preco futuro        : {len(medidos)}")
    print(f"SEM preco futuro        : {len(faltam)} "
          f"({len(faltam) / len(todos):.1%} do painel)")
    print()
    ausentes = pd.DataFrame(sorted(faltam), columns=["date", "symbol"])
    ausentes = ausentes.merge(
        vintages.rename(columns={"as_of_date": "date"}), on=["date", "symbol"])
    print(f"{'convencao':<12}{'n_obs':>8}{'censura':>9}{'rank_ic_t':>11}"
          f"{'ann_return':>12}{'excesso_ew':>12}{'sharpe':>9}")
    for nome, r in CONVENCOES:
        painel = vivo if r is None else pd.concat(
            [vivo, ausentes.assign(fwd_return=r)], ignore_index=True)
        painel["date"] = pd.to_datetime(painel["date"])
        res = bt.walk_forward(painel, top_n=20, periods_per_year=1,
                              bootstrap_samples=0)
        if not res.get("ok"):
            print(f"{nome:<12}  falhou: {res.get('reason')}")
            continue
        cen = res["censura"]
        print(f"{nome:<12}{cen['n_observacoes']:8}"
              f"{(cen['fracao_censurada'] or 0):9.1%}"
              f"{res['rank_ic']['t_stat']:11.2f}"
              f"{res['portfolio']['ann_return']:12.2%}"
              f"{res['excess_ann_vs_ew']:12.2%}"
              f"{res['portfolio']['sharpe']:9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
