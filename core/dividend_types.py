"""Distingue renda de devolucao de capital em ``market.dividends``.

A-128: ``AMORTIZACAO`` e ``REST CAP DIN`` nao sao rendimento -- sao devolucao
do capital do proprio cotista. Somados ao provento, inflam o dividend yield e,
em FII, inflam justamente a metrica de manchete da decisao. Medido em
2026-08-24 sobre o Supabase: 415 pares ticker-ano inflados em 234 tickers,
inflacao media de 139%; RBRI11/2026 exibia 252,20 de "provento" com renda
real zero.

A-129: a mesma agregacao somava o eco de classe da brapi (o mesmo evento
gravado sob CEBR5 e CEBR6, por exemplo). Dentro de um mesmo (data, tipo), o
valor honesto e o MINIMO -- somar repete o evento. Entre tipos DISTINTOS na
mesma data, somar e correto: dividendo e JCP no mesmo dia sao dois eventos de
verdade, e sao 1.120 ocorrencias na base.
"""
from __future__ import annotations

#: Eventos que remuneram o investidor sem devolver o principal.
TIPOS_RENDA: frozenset[str] = frozenset({"RENDIMENTO", "JCP", "DIVIDENDO"})

#: Eventos que devolvem capital. Entram no fluxo de caixa, nunca no yield.
TIPOS_DEVOLUCAO_CAPITAL: frozenset[str] = frozenset({"AMORTIZAÇÃO", "REST CAP DIN"})


def eh_renda(tipo: str | None) -> bool:
    """``True`` para evento de renda. Tipo desconhecido conta como renda.

    A escolha e deliberada: um tipo novo que a ingestao traga aparece no yield
    (e portanto e visivel) em vez de sumir em silencio. Sumir seria o erro
    caro -- a-124 mostrou o que custa um sinal que ninguem ve.
    """
    if tipo is None:
        return True
    return str(tipo).strip().upper() not in _CAPITAL_UPPER


_CAPITAL_UPPER = frozenset(t.upper() for t in TIPOS_DEVOLUCAO_CAPITAL)

#: Predicado SQL que mantem apenas eventos de renda. ``col`` e a coluna tipo.
def sql_apenas_renda(col: str = "type") -> str:
    lista = ", ".join(f"'{t}'" for t in sorted(TIPOS_DEVOLUCAO_CAPITAL))
    return f"({col} IS NULL OR upper({col}) NOT IN ({lista}))"


# ---------------------------------------------------------------------------
# A-131: as duas safras do mesmo pagamento
# ---------------------------------------------------------------------------
# `market.dividends` guarda o mesmo evento duas vezes para 307 tickers. Uma
# linha traz o calendario real da B3 (RELG11: ex 05/09, pagamento 12/09); a
# outra, criada em bloco entre 23 e 25/07/2026, projeta o evento para o dia 1
# do mes de pagamento e grava `payment_date = ex_date`.
#
# Esse colapso e a assinatura: nenhum evento real paga no dia em que fica ex --
# a mediana da defasagem na tabela e de 14 dias. O dia colapsado nao e uma
# data, e a ausencia de uma.
#
# Custo medido nos FIIs investiveis: 187 fundos com renda de 12 meses inflada,
# mediana +35,8%, maximo +90,9%. HGLG11 sai 64,7% acima; RELG11 quase dobra.
#
# A chave e o MES DO PAGAMENTO, nao o valor. HGLG11 tem copias de 0,9574 e
# 1,0734 ao lado de reais de 1,1000: casar por valor deixa as duas passarem, e
# alargar a tolerancia comeca a apagar evento legitimo. Tambem nao serve o mes
# do EX -- o par de HGLG11 fica ex em 31/10 e paga em 14/11, e a copia cai em
# 01/11; pelo mes do ex os dois nunca se encontrariam.
#
# Uma colapsada SEM gemea de calendario real sobrevive. Sao ~20 mil linhas de
# historico antigo em que a brapi nunca forneceu as duas datas, e descarta-las
# por formato apagaria evento que nao tem substituto. A regra so age quando ha
# o que preferir.
#
# Nada disso apaga linha: e filtro de leitura. A safra colapsada continua na
# tabela como evidencia de que a ingestao a produziu.


def eh_safra_colapsada(ex_date, payment_date) -> bool:
    """``True`` quando o pagamento cai no proprio dia da data-ex.

    Datas ausentes devolvem ``False``: nao ha prova de colapso, e presumir
    seria descartar por ignorancia.
    """
    if ex_date is None or payment_date is None:
        return False
    try:
        import pandas as _pd
        if _pd.isna(ex_date) or _pd.isna(payment_date):
            return False
    except Exception:  # pragma: no cover - pandas sempre presente no app
        pass
    return bool(ex_date == payment_date)


def descarta_safra_colapsada(df):
    """Remove a copia colapsada quando o mesmo (ticker, tipo, mes de pagamento)
    tem linha de calendario real. Devolve um quadro novo.

    Quadro sem as colunas de data volta intacto -- nao ha o que julgar.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return df if df is not None else pd.DataFrame()
    if not {"ex_date", "payment_date"} <= set(df.columns):
        return df

    ex = pd.to_datetime(df["ex_date"], errors="coerce")
    pay = pd.to_datetime(df["payment_date"], errors="coerce")
    colapsada = ex.notna() & pay.notna() & (ex == pay)
    if not colapsada.any():
        return df

    chave = pd.MultiIndex.from_arrays([
        df["ticker"].astype(str) if "ticker" in df.columns else pd.Series([""] * len(df)),
        df["type"].astype(str) if "type" in df.columns else pd.Series([""] * len(df)),
        pay.dt.to_period("M").astype(str),
    ])
    # Meses que ja tem a versao de calendario real: so neles a copia sobra.
    com_real = set(chave[~colapsada & pay.notna()])
    descartar = colapsada & pd.Series([k in com_real for k in chave], index=df.index)
    return df.loc[~descartar]


def sql_safra_canonica(alias: str = "d", tabela: str = "market.dividends") -> str:
    """Predicado ``WHERE`` que aplica a mesma regra dentro do banco.

    O ``COALESCE`` nao e decorativo: ``payment_date = ex_date`` vale NULL
    quando uma das datas falta, e ``NOT NULL`` tambem e NULL -- a linha sairia
    do resultado em silencio. Descartar o que nao se sabe julgar e o erro que
    este modulo existe para nao cometer.
    """
    return f"""NOT COALESCE(
        {alias}.payment_date = {alias}.ex_date
        AND EXISTS (SELECT 1 FROM {tabela} _sc
                     WHERE _sc.ticker = {alias}.ticker
                       AND _sc.type IS NOT DISTINCT FROM {alias}.type
                       AND _sc.payment_date IS NOT NULL
                       AND _sc.payment_date <> _sc.ex_date
                       AND date_trunc('month', _sc.payment_date)
                           = date_trunc('month', {alias}.payment_date)),
        false)"""
