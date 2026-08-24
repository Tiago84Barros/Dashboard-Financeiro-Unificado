"""Leitor da serie mensal dos EUA no formato do analogo da B3 (core.market_read.load_precos_mensais)."""


def test_load_precos_mensais_us_devolve_indice_mensal_por_simbolo():
    import pandas as pd
    from sqlalchemy import create_engine, text

    import core.us_read as ur

    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE prices_monthly (symbol TEXT, month_end TEXT, "
                       "adjusted_close REAL, close REAL)"))
        for s, d, p in [("AAPL", "2024-01-31", 100.0), ("AAPL", "2024-02-29", 110.0),
                        ("MSFT", "2024-01-31", 200.0), ("MSFT", "2024-02-29", 210.0)]:
            c.execute(text("INSERT INTO prices_monthly VALUES (:s,:d,:p,:p)"),
                      {"s": s, "d": d, "p": p})

    df = ur.load_precos_mensais_us(("AAPL", "MSFT"), engine=eng)
    assert list(df.columns) == ["AAPL", "MSFT"]
    assert len(df) == 2
    assert df["AAPL"].iloc[-1] == 110.0
    assert isinstance(df.index, pd.DatetimeIndex)


def test_load_precos_mensais_us_sem_simbolos_devolve_vazio():
    import core.us_read as ur
    assert ur.load_precos_mensais_us(()).empty


def test_load_precos_mensais_us_com_tabela_ausente_devolve_vazio():
    from sqlalchemy import create_engine

    import core.us_read as ur
    assert ur.load_precos_mensais_us(("AAPL",),
                                     engine=create_engine("sqlite:///:memory:")).empty


def test_load_precos_mensais_us_descarta_preco_nao_positivo():
    """A-122: preço <= 0 não é preço.

    O ajuste por proventos leva o `adjusted_close` a zero e a negativo — no
    painel da B3 isso produzia queda máxima de -2.638%. O leitor dos EUA passa
    pelo mesmo caminho (`core.global_portfolio.returns` concatena os dois), e
    aqui a observação inválida sai na origem, devolvendo o mês como ausente.
    """
    import numpy as np
    from sqlalchemy import create_engine, text

    import core.us_read as ur

    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE prices_monthly (symbol TEXT, month_end TEXT, "
                       "adjusted_close REAL, close REAL)"))
        for d, p in [("2024-01-31", 100.0), ("2024-02-29", -4.0),
                     ("2024-03-31", 0.0), ("2024-04-30", 120.0)]:
            c.execute(text("INSERT INTO prices_monthly VALUES ('AAPL',:d,:p,:p)"),
                      {"d": d, "p": p})

    df = ur.load_precos_mensais_us(("AAPL",), engine=eng)
    validos = df["AAPL"].dropna()
    assert sorted(validos.tolist()) == [100.0, 120.0]
    assert not (df["AAPL"] <= 0).any()
    assert np.isfinite(validos).all()
