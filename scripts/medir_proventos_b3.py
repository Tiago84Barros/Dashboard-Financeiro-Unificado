"""Mede a soma anual de proventos da B3 contra duas referências.

Só leitura: o arquivo publicado (``data/public/proventos_b3.json.gz``) e o
armazém local (``market.dividends``, ``market.fii_metric_observations``).
Reproduz os números citados em ``core.proventos_b3`` e
``core.dividend_types.renda_por_evento``.

Definições
----------
* **Ticker-ano**: um ticker do arquivo publicado e um ano de pagamento. Ticker
  fora do arquivo não entra: ele não lê a resposta da brapi.
* **Concordância**: ``|a - b| / max(a, b) <= 1%``. Ano que só um dos lados
  tem conta como zero no outro (junção externa), e discorda.
* **Anos fechados**: até o ano anterior ao da medição. O ano corrente e os
  seguintes estão pela metade ou só declarados.

1. Contra a resposta mais nova da brapi (o arquivo) -- CIRCULAR
   O arquivo é a própria referência e bate 100% por construção. A medida diz
   quanto cada regra de leitura de ``market.dividends`` se afasta do que a
   fonte responde hoje, não se a fonte está certa.

2. Contra a CVM, só FIIs -- a única referência independente
   Rendimento do ano = soma de ``dy_patrimonial_mes x nav_per_share`` do
   informe mensal (``Percentual_Dividend_Yield_Mes`` sobre o valor patrimonial
   da cota), com o pagamento suposto no mês seguinte ao da competência. Só ano
   fechado com as doze competências e rendimento positivo, e só ticker-ano que
   o arquivo tem (junção interna). Só observações de ``source =
   'cvm_informe_mensal'``. A CVM também erra: o fundo que paga no mês da
   competência, ou dois meses depois, desloca valor entre dezembro e janeiro.
   O desacordo medido é entre duas fontes imperfeitas; não é o erro da brapi
   nem um limite para ele, porque as duas podem errar no mesmo sentido.

   O número depende dessas convenções, e o relatório traz a faixa: o mesmo
   cálculo com o pagamento no próprio mês da competência, um mês e dois meses
   depois, cada um pela junção interna e pela junção esquerda (todo ano da CVM
   dos FIIs do arquivo, com o ano que o arquivo não tem contando como zero).
   Ano com menos de doze competências não entra em nenhuma variante: falta
   rendimento de algum mês, e o total da CVM sai menor por falta de dado, não
   por convenção.

Uso::

    python scripts/medir_proventos_b3.py [--json saida.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOLERANCIA = 0.01
FAIXAS_CVM = (0.05, 0.10)

#: Meses entre a competência e o pagamento, e as junções com o arquivo, da
#: faixa de sensibilidade. A medida principal é um mês, junção interna.
DEFASAGENS_CVM = (0, 1, 2)
JUNCOES_CVM = ("interna", "esquerda")

SQL_CRUA = """
    SELECT upper(trim(ticker)) AS ticker, EXTRACT(YEAR FROM event_date)::int AS ano,
           SUM(amount)::float AS total
      FROM market.dividends
     WHERE event_date IS NOT NULL
     GROUP BY 1, 2"""

#: Só a fonte da CVM entra na referência: a ingestão da brapi grava
#: ``nav_per_share`` na mesma tabela, e misturá-lo tiraria a independência.
FONTE_CVM = "cvm_informe_mensal"

#: O valor mais novo de cada mês e fonte: o informe reapresentado substitui o
#: original. :func:`rendimento_cvm` descarta o que não é da CVM.
SQL_CVM = """
    SELECT DISTINCT ON (ticker, reference_date, metric_name, source)
           ticker, reference_date, metric_name, source, value_numeric
      FROM market.fii_metric_observations
     WHERE metric_name IN ('dy_patrimonial_mes', 'nav_per_share')
       AND value_numeric IS NOT NULL
     ORDER BY ticker, reference_date, metric_name, source,
              knowledge_at DESC NULLS LAST, id DESC"""


def anuais_do_arquivo(publicado: dict):
    """``ticker, ano, total`` do arquivo publicado (``por_ticker``)."""
    import pandas as pd

    return pd.DataFrame(
        [(tk, int(ano), float(total))
         for tk, v in (publicado.get("por_ticker") or {}).items()
         for ano, total in (v.get("anos") or {}).items()],
        columns=["ticker", "ano", "total"])


def com_minimo(eventos):
    """A regra do A-129, que :func:`core.dividend_types.renda_por_evento`
    deixou de aplicar: dos eventos com a mesma data-ex e o mesmo pagamento,
    fica o menor valor."""
    chave = ["ticker", "type", "ex_date", "payment_date"]
    return (eventos.sort_values("amount", kind="mergesort")
                   .drop_duplicates(chave, keep="first"))


def concordancia(referencia, candidato, tickers, *, ate_ano: int | None = None,
                 tolerancia: float = TOLERANCIA) -> dict:
    """Fração dos ticker-anos de ``tickers`` em que ``candidato`` bate com
    ``referencia`` (ambos ``ticker, ano, total``), pela junção externa."""
    import numpy as np

    ref = referencia[referencia["ticker"].isin(tickers)]
    cand = candidato[candidato["ticker"].isin(tickers)]
    m = ref.merge(cand, on=["ticker", "ano"], how="outer", suffixes=("_ref", "_cand"))
    if ate_ano is not None:
        m = m[m["ano"] <= ate_ano]
    a, b = m["total_ref"].fillna(0.0), m["total_cand"].fillna(0.0)
    teto = np.maximum(a, b)
    rel = (a - b).abs() / teto.where(teto > 0)
    batem = int((rel.fillna(0.0) <= tolerancia).sum())
    return {"ticker_anos": len(m), "batem": batem,
            "fracao": batem / len(m) if len(m) else None}


def rendimento_cvm(obs, *, ate_ano: int, defasagem: int = 1):
    """``ticker, ano, total`` do rendimento implícito do informe mensal.

    ``obs``: ``ticker, reference_date, metric_name, source, value_numeric``,
    como :data:`SQL_CVM` devolve. Só observações de :data:`FONTE_CVM` e só o
    ano com as doze competências. O pagamento cai ``defasagem`` meses depois
    da competência. Métrica que falta inteira não derruba a medida: o ano só
    não fecha."""
    import pandas as pd

    obs = obs[obs["source"] == FONTE_CVM]
    w = (obs.pivot_table(index=["ticker", "reference_date"], columns="metric_name",
                         values="value_numeric", aggfunc="first")
            .reindex(columns=["dy_patrimonial_mes", "nav_per_share"])
            .dropna().reset_index())
    if w.empty:
        return pd.DataFrame(columns=["ticker", "ano", "total"])
    w["rend"] = w["dy_patrimonial_mes"].astype(float) * w["nav_per_share"].astype(float)
    w["ano"] = (pd.to_datetime(w["reference_date"]).dt.to_period("M") + defasagem).dt.year
    g = w.groupby(["ticker", "ano"])["rend"].agg(["sum", "size"]).reset_index()
    g = g[(g["size"] == 12) & (g["sum"] > 0) & (g["ano"] <= ate_ano)]
    return g.rename(columns={"sum": "total"})[["ticker", "ano", "total"]].reset_index(drop=True)


def contra_cvm(cvm, metodos: dict, base, *, juncao: str = "interna") -> dict:
    """Erro relativo de cada método diante da CVM. Método sem o ano conta como
    zero. Sem ticker-ano, as frações e o erro mediano saem ``None``.

    ``juncao="interna"``: os ticker-anos que a CVM e ``base`` (o arquivo) têm
    em comum. ``"esquerda"``: todo ticker-ano da CVM de um ticker de ``base``,
    tenha ``base`` o ano ou não."""
    m = cvm.rename(columns={"total": "cvm"})
    if juncao == "interna":
        m = m.merge(base[["ticker", "ano"]], on=["ticker", "ano"], how="inner")
    elif juncao == "esquerda":
        m = m[m["ticker"].isin(base["ticker"])]
    else:
        raise ValueError(f"junção desconhecida: {juncao!r}")
    out = {"ticker_anos": len(m), "tickers": int(m["ticker"].nunique())}
    for nome, df in metodos.items():
        v = m.merge(df.rename(columns={"total": "v"}), on=["ticker", "ano"], how="left")
        erro = (v["v"].fillna(0.0) - v["cvm"]).abs() / v["cvm"]
        vazio = not len(erro)
        out[nome] = {f"ate_{int(f * 100)}pct": None if vazio else float((erro <= f).mean())
                     for f in FAIXAS_CVM}
        out[nome]["erro_mediano"] = None if vazio else float(erro.median())
    return out


def medir(publicado: dict, crua, bruto, obs, *, hoje: date) -> dict:
    """Puro: tudo que :func:`main` imprime, a partir do que ela leu."""
    from core.dividend_types import renda_por_ano, renda_por_evento, soma_por_ano

    arquivo = anuais_do_arquivo(publicado)
    tickers = set(publicado.get("por_ticker") or {})
    eventos = renda_por_evento(bruto)
    metodos = {
        "soma crua de market.dividends": crua,
        "renda_por_evento": renda_por_ano(bruto),
        "renda_por_evento com o mínimo (A-129)": soma_por_ano(com_minimo(eventos)),
    }
    fechado = hoje.year - 1
    circular = {
        nome: {"todos os anos": concordancia(arquivo, df, tickers),
               "anos fechados": concordancia(arquivo, df, tickers, ate_ano=fechado)}
        for nome, df in metodos.items()}
    fiis = {tk for tk in tickers if tk.endswith("11")}
    obs_fii = obs[obs["ticker"].isin(fiis)]
    todos = {"arquivo": arquivo, **metodos}
    sensibilidade = []
    for defasagem in DEFASAGENS_CVM:
        cvm = rendimento_cvm(obs_fii, ate_ano=fechado, defasagem=defasagem)
        for juncao in JUNCOES_CVM:
            sensibilidade.append({"defasagem_meses": defasagem, "juncao": juncao,
                                  **contra_cvm(cvm, todos, arquivo, juncao=juncao)})
    principal = next(s for s in sensibilidade
                     if (s["defasagem_meses"], s["juncao"]) == (1, "interna"))
    faixa = {}
    for f in FAIXAS_CVM:
        chave = f"ate_{int(f * 100)}pct"
        fracoes = [s["arquivo"][chave] for s in sensibilidade if s["arquivo"][chave] is not None]
        faixa[chave] = {"min": min(fracoes), "max": max(fracoes)} if fracoes else None
    return {
        "medido_em": hoje.isoformat(),
        "arquivo": {"gerado_em": publicado.get("gerado_em"),
                    "foto_mais_nova": publicado.get("foto_mais_nova"),
                    "tickers": len(tickers)},
        "tolerancia": TOLERANCIA,
        "contra_a_brapi_circular": circular,
        "contra_a_cvm_fii": {k: v for k, v in principal.items()
                             if k not in ("defasagem_meses", "juncao")},
        "contra_a_cvm_sensibilidade": {"variantes": sensibilidade, "faixa_do_arquivo": faixa},
    }


def _pct(x) -> str:
    return "indisponível" if x is None else f"{x:.1%}"


def _imprime(r: dict) -> None:
    a = r["arquivo"]
    print(f"Arquivo gerado em {a['gerado_em']}, resposta mais nova {a['foto_mais_nova']}, "
          f"{a['tickers']} tickers. Tolerância {r['tolerancia']:.0%}.")
    print("\n1. Contra a resposta mais nova da brapi (CIRCULAR: o arquivo bate 100%)")
    for nome, cortes in r["contra_a_brapi_circular"].items():
        partes = [f"{corte}: {_pct(c['fracao'])} de {c['ticker_anos']}"
                  for corte, c in cortes.items()]
        print(f"   {nome:40s} " + " | ".join(partes))
    c = r["contra_a_cvm_fii"]
    print(f"\n2. Contra a CVM (FII, {c['ticker_anos']} ticker-anos, {c['tickers']} fundos)")
    if not c["ticker_anos"]:
        print("   nenhum ticker-ano da CVM com as doze competências: comparação indisponível")
    else:
        for nome, v in c.items():
            if isinstance(v, dict):
                print(f"   {nome:40s} até 5%: {_pct(v['ate_5pct'])}"
                      f" | até 10%: {_pct(v['ate_10pct'])}"
                      f" | erro mediano: {_pct(v['erro_mediano'])}")
    # Outra convenção pode fechar o ano que a principal não fecha: a faixa sai
    # mesmo com a principal vazia.
    s = r["contra_a_cvm_sensibilidade"]
    print("\n   Sensibilidade à convenção (fração até 5% da CVM)")
    for v in s["variantes"]:
        d = v["defasagem_meses"]
        rotulo = f"pagamento +{d} {'mês' if d == 1 else 'meses'}, junção {v['juncao']}"
        print(f"   {rotulo:40s} n={v['ticker_anos']:5d} | arquivo {_pct(v['arquivo']['ate_5pct'])}"
              f" | renda_por_evento {_pct(v['renda_por_evento']['ate_5pct'])}"
              f" | soma crua {_pct(v['soma crua de market.dividends']['ate_5pct'])}")
    for chave, faixa in s["faixa_do_arquivo"].items():
        if faixa:
            print(f"   faixa do arquivo até {chave.removeprefix('ate_').removesuffix('pct')}%:"
                  f" {_pct(faixa['min'])} a {_pct(faixa['max'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=Path, help="grava o resultado neste arquivo")
    args = ap.parse_args()

    import pandas as pd
    from sqlalchemy import create_engine, text

    from core.dividend_types import sql_proventos_de_renda
    from core.proventos_b3 import carregar
    from scripts.publish_fii_selection_from_local import _warehouse_url

    publicado = carregar()
    if not publicado.get("por_ticker"):
        print("arquivo publicado ausente ou vazio", file=sys.stderr)
        return 1
    with create_engine(_warehouse_url()).connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        crua = pd.read_sql_query(text(SQL_CRUA), conn)
        bruto = pd.read_sql_query(text(sql_proventos_de_renda("true")), conn)
        obs = pd.read_sql_query(text(SQL_CVM), conn)
    obs["ticker"] = obs["ticker"].str.strip().str.upper()
    r = medir(publicado, crua, bruto, obs, hoje=date.today())
    if args.json:
        args.json.write_text(json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False),
                             encoding="utf-8")
    _imprime(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
