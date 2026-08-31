# -*- coding: utf-8 -*-
"""Mede quanto o excesso dos EUA ainda depende de convencao, depois da correcao.

O painel PIT ja NAO descarta toda linha sem preco futuro. Quem tem data de
saida e causa apurada (item do 8-K, ver core/us_saida_causa.py) entra pela
convencao declarada em core/us_convencao_saida.py -- 841 linhas em 21.592 na
apuracao de 31/08/2026. O que sobra sem medicao sao 7.446 linhas: saidas
anteriores a cobertura da ingestao, tickers reciclados e simbolos ambiguos.

Este script mede a banda que RESTA. Ele mantem a convencao por causa ligada e
varia o retorno atribuido as linhas que continuam invisiveis:

  descartar : sai da conta -- supoe que a invisivel rendeu a media das visiveis
  0%        : saida neutra
  -30%      : convencao de deslistagem por desempenho da literatura (CRSP)
  -100%     : perda total

A pergunta que ele responde nao e "qual e o numero", e sim "de quanto o numero
publicado ainda depende do que se supoe sobre quem nao deu para medir".

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
        desfechos = pd.read_sql(text(
            "SELECT DISTINCT ON (a.symbol) a.symbol, a.delisted_date, "
            "       a.delisting_cause "
            "FROM market_us.assets a "
            "WHERE a.delisted_date IS NOT NULL AND a.delisting_cause IS NOT NULL "
            "ORDER BY a.symbol, a.delisted_date DESC"), conn)
    saidas = {linha.symbol: {"delisted_date": linha.delisted_date,
                             "cause": linha.delisting_cause}
              for linha in desfechos.itertuples()}
    # A base ja e a corrigida: quem tem causa apurada entra pela convencao por
    # causa. Medir a sensibilidade sobre o painel ANTIGO responderia a pergunta
    # de antes da correcao e daria uma banda que o app nao publica mais.
    vivo = build_annual_panel(vintages, monthly, horizon_months=12,
                              saidas=saidas)
    medidos = set(zip(pd.to_datetime(vivo["date"]).dt.date, vivo["symbol"]))
    todos = set(zip(vintages["as_of_date"], vintages["symbol"]))
    faltam = todos - medidos
    print(f"linhas do painel        : {len(todos)}")
    print(f"medidas ou convencionadas: {len(medidos)} "
          f"(sendo {vivo.attrs.get('n_convencionado', 0)} por causa apurada)")
    print(f"SEM medicao possivel    : {len(faltam)} "
          f"({len(faltam) / len(todos):.1%} do painel)")
    print(f"desfechos conhecidos    : {len(saidas)}")
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
