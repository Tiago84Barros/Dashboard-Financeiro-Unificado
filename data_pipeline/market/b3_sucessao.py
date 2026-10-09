"""Sucessão de códigos de negociação na fita da B3 (troca de ticker).

Quando a companhia troca de código (EMBR3 → EMBJ3 em 03/11/2025, ELET3 →
AXIA3 em 10/11/2025, CCRO3 → MOTV3 em 02/05/2025), a fita oficial
(``market.b3_security_history``) passa a registrar o pregão sob o código novo
e o antigo some. O universo do app (``public.setores``, demonstrações) segue
com o código antigo, e quem lê a fita pelo ticker do universo fica sem preço
depois da troca -- e quem lê pelo novo, sem o histórico de antes dela.

O ISIN não liga os dois: muda junto com o código (BREMBRACNOR4 →
BREMBJACNOR1). A ligação sai dos dados:

- mesmo ``codigo_cvm`` em ``market.ticker_cvm`` (ou, na falta do ticker lá,
  o dos irmãos de mesma raiz: AXIA6 herda o de AXIA3);
- mesmo sufixo de classe (3 com 3, 6 com 6): AXIA7, classe nova, não herda;
- o antigo para de negociar antes de o novo começar, com no máximo
  ``LACUNA_MAXIMA_DIAS`` corridos entre os dois;
- candidato único: dois códigos novos para um antigo (ou o contrário) é
  reestruturação, não troca de nome, e não liga;
- o primeiro fechamento do novo fica entre 1/``SALTO_MAXIMO`` e
  ``SALTO_MAXIMO`` vezes o último do antigo: troca com grupamento ou
  desdobramento no mesmo dia mudaria a escala do preço sem ajuste.

TRAD3 → ECOM3 (07/07/2026) fica de fora enquanto ECOM3 não tiver código CVM:
nenhum irmão de raiz ECOM está em ``ticker_cvm``. NTCO3 → NATU3 não liga (códigos CVM diferentes: a Natura &Co foi incorporada
pela Natura, uma reestruturação societária).
"""
from __future__ import annotations

import re
from datetime import date

LACUNA_MAXIMA_DIAS = 10
SALTO_MAXIMO = 2.0

_CLASSE = re.compile(r"(\d+)$")


def _classe(tk: str) -> str | None:
    m = _CLASSE.search(tk or "")
    return m.group(1) if m else None


def derivar_sucessoes(intervalos, cvm: dict[str, object]) -> dict[str, str]:
    """{antigo: novo} a partir dos intervalos de negociação. Puro.

    ``intervalos``: iterável de (ticker, primeiro_pregao, ultimo_pregao,
    primeiro_fechamento, ultimo_fechamento). ``cvm``: {ticker: codigo_cvm}.
    """
    por_chave: dict[tuple, list] = {}
    for tk, ini, fim, fech_ini, fech_fim in intervalos:
        cod, cls = cvm.get(tk), _classe(tk)
        if cod is None or cls is None or ini is None or fim is None:
            continue
        por_chave.setdefault((cod, cls), []).append((tk, ini, fim, fech_ini, fech_fim))

    candidatos: list[tuple[str, str]] = []
    for grupo in por_chave.values():
        for antigo in grupo:
            for novo in grupo:
                if antigo[0] == novo[0]:
                    continue
                lacuna = (_dia(novo[1]) - _dia(antigo[2])).days
                if not 0 < lacuna <= LACUNA_MAXIMA_DIAS:
                    continue
                if not _sem_salto(antigo[4], novo[3]):
                    continue
                candidatos.append((antigo[0], novo[0]))

    saidas: dict[str, list[str]] = {}
    entradas: dict[str, list[str]] = {}
    for a, n in candidatos:
        saidas.setdefault(a, []).append(n)
        entradas.setdefault(n, []).append(a)
    return {a: n for a, n in candidatos
            if len(saidas[a]) == 1 and len(entradas[n]) == 1}


def _dia(v) -> date:
    return v.date() if hasattr(v, "date") and callable(v.date) else v


def _sem_salto(antes, depois) -> bool:
    try:
        antes, depois = float(antes), float(depois)
    except (TypeError, ValueError):
        return False
    if antes <= 0 or depois <= 0:
        return False
    return 1 / SALTO_MAXIMO <= depois / antes <= SALTO_MAXIMO


def cadeias(sucessoes: dict[str, str]) -> dict[str, list[str]]:
    """{ticker: [todos os códigos da mesma cadeia, do mais antigo ao atual]}."""
    novos = set(sucessoes.values())
    saida: dict[str, list[str]] = {}
    for inicio in sucessoes:
        if inicio in novos:
            continue
        cadeia, tk = [inicio], inicio
        while tk in sucessoes and sucessoes[tk] not in cadeia:
            tk = sucessoes[tk]
            cadeia.append(tk)
        for t in cadeia:
            saida[t] = cadeia
    return saida


def expandir_fita(df, sucessoes: dict[str, str], universo, *,
                  coluna: str = "ticker"):
    """Copia as linhas da fita para cada código da cadeia presente no universo.

    ELET3 (universo) recebe os pregões de AXIA3 depois da troca; AXIA3, se
    também estiver no universo, recebe os de ELET3 antes dela. Linha de
    código fora de cadeia passa como está. Quem consome deduplica por
    (ticker, data) -- as cadeias não se sobrepõem no tempo.
    """
    import pandas as pd
    if df.empty or not sucessoes:
        return df
    universo = set(universo)
    por_tk = cadeias(sucessoes)
    copias = []
    for tk, g in df.groupby(coluna, sort=False):
        for destino in por_tk.get(str(tk), []):
            if destino != tk and destino in universo:
                copias.append(g.assign(**{coluna: destino}))
    if not copias:
        return df
    return pd.concat([df, *copias], ignore_index=True)


_SQL_INTERVALOS = """
    WITH f AS (
        SELECT ticker, trade_date,
               COALESCE(close_unitario, close / NULLIF(fator_cotacao, 0)) AS fech
          FROM market.b3_security_history
         WHERE close > 0 AND ticker = ANY(:tks)
    )
    SELECT ticker, MIN(trade_date), MAX(trade_date),
           (ARRAY_AGG(fech ORDER BY trade_date))[1],
           (ARRAY_AGG(fech ORDER BY trade_date DESC))[1]
      FROM f GROUP BY ticker
"""


def completar_cvm(tickers, cvm: dict[str, object]) -> dict[str, object]:
    """Dá ``codigo_cvm`` ao ticker que falta em ``ticker_cvm`` pela raiz.

    A raiz de quatro letras identifica o emissor na B3: AXIA6 herda o 2437 de
    AXIA3/AXIA7. Só quando todos os tickers com a raiz concordam no código.
    """
    por_raiz: dict[str, set] = {}
    for tk, cod in cvm.items():
        por_raiz.setdefault(tk[:4], set()).add(cod)
    saida = dict(cvm)
    for tk in tickers:
        if tk in saida:
            continue
        cods = por_raiz.get(tk[:4]) or set()
        if len(cods) == 1:
            saida[tk] = next(iter(cods))
    return saida


def carregar_sucessoes(conn) -> dict[str, str]:
    """Deriva as sucessões do armazém: ``ticker_cvm`` + intervalos da fita.

    Só olha os códigos de empresa com mais de um ticker na mesma classe -- o
    resto não tem com quem se ligar e a varredura da fita inteira sairia cara.
    """
    from sqlalchemy import text
    cvm = {str(t): c for t, c in conn.execute(text(
        "SELECT ticker, codigo_cvm FROM market.ticker_cvm "
        "WHERE codigo_cvm IS NOT NULL")).fetchall()}
    ativos = [str(t) for (t,) in conn.execute(text(
        "SELECT DISTINCT ticker FROM market.assets")).fetchall()]
    cvm = completar_cvm(ativos, cvm)
    contagem: dict[tuple, int] = {}
    for tk, cod in cvm.items():
        k = (cod, _classe(tk))
        contagem[k] = contagem.get(k, 0) + 1
    tks = [tk for tk, cod in cvm.items() if contagem[(cod, _classe(tk))] > 1]
    if not tks:
        return {}
    intervalos = conn.execute(text(_SQL_INTERVALOS), {"tks": tks}).fetchall()
    return derivar_sucessoes(intervalos, cvm)
