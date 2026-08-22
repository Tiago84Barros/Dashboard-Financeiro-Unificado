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
