"""Publica a medição mensal do protocolo da Carteira 20 -- ``data/public/carteira20_medicao.json``.

Roda localmente, toda noite; o app publicado só lê o arquivo. A conta sai de
``core/carteira20_medicao.py`` e as fontes são as da curva diária
(``scripts/carteira20_desempenho.py``), com duas diferenças:

- ações: em vez do retorno total pronto, as partes dele -- fechamento, fator de
  desdobramento e dinheiro por ação --, porque a curva por aportes conta ações
  inteiras e credita os proventos em caixa. Até o fim da pesquisa vêm de
  ``dados/b3_retornos_diarios.parquet``; depois, de
  ``core.carteira20_retornos.calcular(componentes=True)`` sobre o COTAHIST e
  ``market.dividends``, com as mesmas regras;
- IPCA (SGS 433): os meses que a medição publicada já usou, o arquivo
  ``data/public/macro_brasil.json.gz`` e, para o que faltar, o SOAP do SGS.

A conta é refeita inteira desde a inaugural a cada rodada: provento que a brapi
publica dias depois do dia ex entra na rodada seguinte. Uma rodada que mede
menos fechamentos do que o arquivo publicado (fonte que encolheu) não grava e
sai com erro: a medição do protocolo não anda para trás em silêncio.

    python scripts/carteira20_medicao.py [--seco] [--ensaio-ate AAAA-MM-DD]

``--seco`` mostra o resultado sem gravar. ``--ensaio-ate`` trata um pregão que
já existe como fechamento, para ensaiar a conta antes do primeiro mês (exige
``--seco``; pode repetir).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core import carteira20 as c20  # noqa: E402
from core import carteira20_desempenho as cd  # noqa: E402
from core import carteira20_medicao as cm  # noqa: E402
from scripts import carteira20_desempenho as diario  # noqa: E402

ARTEFATO = ROOT / "data" / "public" / "carteira20_medicao.json"
COLUNAS = ["id", "data", "preco", "k", "caixa"]
# Pregões antes da inaugural: o preço de quem não negociou no dia da execução.
JANELA_PRECO = timedelta(days=30)


def componentes_da_pesquisa(ids: set[str], desde: date) -> pd.DataFrame:
    """Fechamento, fator e dinheiro por ação do parquet da pesquisa, a partir de ``desde``.

    ``1 + ret_total = (fator_acoes × (preço + distribuição) + provento + imputado) / P_{t-1}``
    vale linha a linha no parquet. Linha de retorno vazio fica com o valor da
    posição parado, como o retorno zero da curva diária.
    """
    df = pd.read_parquet(diario._parquet_pesquisa(),
                         columns=["id_ativo", "data", "ret_total", "preco_fechamento",
                                  "fator_acoes", "distribuicao_especie", "provento_dinheiro",
                                  "provento_imputado"],
                         filters=[("data", ">=", pd.Timestamp(desde))])
    df = df[df["id_ativo"].isin(ids)].sort_values(["id_ativo", "data"])
    k = df["fator_acoes"].fillna(1.0)
    dinheiro = (k * df["distribuicao_especie"].fillna(0.0) + df["provento_dinheiro"].fillna(0.0)
                + df["provento_imputado"].fillna(0.0))
    p_ant = df.groupby("id_ativo")["preco_fechamento"].shift(1)
    vazio = df["ret_total"].isna() & p_ant.notna()
    return pd.DataFrame({"id": df["id_ativo"].values,
                         "data": pd.to_datetime(df["data"]).dt.date.values,
                         "preco": df["preco_fechamento"].values,
                         "k": k.where(~vazio, p_ant / df["preco_fechamento"]).values,
                         "caixa": dinheiro.where(~vazio, 0.0).values})


def componentes_das_acoes(ids: set[str], desde: date, *, pesquisa=componentes_da_pesquisa,
                          fim_pesquisa=diario.fim_da_pesquisa,
                          cadeias=diario.cadeias_da_pesquisa,
                          cotahist=diario.linhas_do_cotahist,
                          proventos=diario.proventos_do_armazem,
                          hoje: date | None = None) -> pd.DataFrame:
    """As partes do retorno desde ``desde``: a pesquisa até o fim dela, a extensão depois."""
    from core import carteira20_retornos as cr

    hoje = hoje or datetime.now(c20.FUSO_B3).date()
    fim = fim_pesquisa()
    base = pesquisa(ids, desde)
    base = base[base["data"] <= fim]
    corte = max(fim, desde)
    ipt = cadeias(ids)
    pregoes = cr.pregoes_de_acoes(cotahist(corte - diario.JANELA_PREGOES, hoje), set(ipt))
    pregoes = pregoes[pregoes["trade_date"].dt.date >= corte - diario.JANELA_PREGOES]
    extensao = cr.calcular(pregoes, proventos(set(ipt), corte - diario.JANELA_PROVENTOS), ipt,
                           corte, componentes=True)
    cotahist_ate = pregoes["trade_date"].max().date() if len(pregoes) else None
    print(f"ações: pesquisa até {diario._data(fim)}, COTAHIST até {diario._data(cotahist_ate)}")
    partes = [p[COLUNAS] for p in (base, extensao) if len(p)]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUNAS)


def ipca_mensal(desde: date, ate: date, *, publicado: dict[date, float] | None = None,
                baixar=None) -> tuple[dict[date, float], str]:
    """IPCA (% no mês, pela data do dia 1) de ``desde`` a ``ate``, e de onde veio.

    Os meses já usados pela medição publicada não mudam; o arquivo do
    ``macro_brasil`` cobre os 13 últimos; o SOAP do SGS, o resto.
    """
    serie = dict(publicado or {})
    fontes = ["medição publicada"] if serie else []
    try:
        from core import macro_brasil as mb

        pub = mb.desserializar(mb.CAMINHO_PADRAO.read_bytes())
        novos = {o["reference_period"]: o["value"] for o in pub.observacoes
                 if o.get("provider_code") == "433" and o.get("reference_period")
                 and not o.get("is_forecast")}
        if novos:
            serie = {**novos, **serie}
            fontes.append("data/public/macro_brasil.json.gz")
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        pass
    primeiro = date(desde.year, desde.month, 1)
    meses = []
    d = primeiro
    while d <= ate:
        meses.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    if any(m not in serie for m in meses):
        if baixar is None:
            from core.bcb_sgs import baixar_sgs_soap

            def baixar(ini, fim):
                return baixar_sgs_soap(433, ini, fim)
        novos, motivo = baixar(primeiro, ate)
        novos = {date(m.year, m.month, 1): v for m, v in novos.items()}
        if novos:
            serie = {**novos, **serie}
            fontes.append("BCB/SGS 433 (SOAP)")
        elif motivo and "Value(s) not found" not in motivo:
            # "Value(s) not found" é o mês ainda não divulgado, não falha
            fontes.append(f"SGS indisponível: {motivo}")
    return {m: serie[m] for m in sorted(serie) if primeiro <= m <= ate}, "; ".join(fontes)


def _publicado() -> dict | None:
    try:
        return json.loads(ARTEFATO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def montar(registros: list[dict], *, componentes=componentes_das_acoes,
           bova=diario.bova11_do_cotahist, cdi=diario.cdi_diario,
           lotes=diario.lotes_do_cotahist, ipca=ipca_mensal, publicado: dict | None = None,
           agora: datetime | None = None, extras: tuple[date, ...] = ()) -> dict | None:
    """O artefato da medição, ou None antes do primeiro pregão depois da inaugural."""
    rvs = c20.revisoes(registros)
    if not rvs:
        return None
    agora = agora or datetime.now(c20.FUSO_B3)
    hoje = agora.date()
    inaugural = date.fromisoformat(rvs[0]["corpo"]["pregao_execucao"])
    if hoje <= inaugural:
        return None
    tickers: dict[str, str] = {}
    revisoes: list[cm.Revisao] = []
    anterior: list[dict] = []
    for rv in rvs:
        corpo = rv["corpo"]
        execucao = date.fromisoformat(corpo["pregao_execucao"])
        carteira = [{"id": c["id"], "ticker": c["ticker"]} for c in corpo["carteira"]]
        universo = [{"id": u["id"], "ticker": u["ticker"]}
                    for u in (corpo.get("universo") or {}).get("pontuacoes") or []]
        for x in carteira + universo:
            tickers[x["id"]] = x["ticker"]
        rev = cm.Revisao(rv["registro"]["seq"], execucao, [c["id"] for c in carteira],
                         [u["id"] for u in universo])
        if execucao < hoje:
            # o spread de venda de quem sai é medido na mesma execução
            medidas = lotes(execucao, carteira + [c for c in anterior if c not in carteira])
            rev.custos, rev.custo_bova11 = medidas["custos"], medidas["custo_bova11"]
        revisoes.append(rev)
        anterior = carteira
    pregoes = componentes(set(tickers), inaugural - JANELA_PRECO)
    serie_bova = bova(inaugural)
    serie_cdi, fonte_cdi = cdi(inaugural, hoje)
    ja = {date.fromisoformat(k): v for k, v in ((publicado or {}).get("ipca") or {}).items()}
    serie_ipca, fonte_ipca = ipca(inaugural, hoje, publicado=ja)
    calc = cm.calcular(revisoes, pregoes, serie_bova, serie_cdi, serie_ipca, ate=hoje,
                       tickers=tickers, extras=extras)
    proto = registros[0] if registros and registros[0].get("tipo") == "protocolo" else {}
    return {
        "schema": 1,
        "gerado_em": agora.isoformat(timespec="seconds"),
        "protocolo": {"seq": proto.get("seq"), "hash": proto.get("hash")},
        "revisoes": [{"seq": rv["registro"]["seq"], "hash": rv["registro"]["hash"],
                      "corpo_sha256": cd.impressao_corpo(rv["corpo"]),
                      "pregao_execucao": rv["corpo"]["pregao_execucao"]} for rv in rvs],
        "regras": {"capital_inicial": cm.CAPITAL_INICIAL, "aporte_mensal": cm.APORTE_MENSAL,
                   "custo_por_lado": cm.CUSTO_LADO, "aliquota_ir": cm.ALIQUOTA_IR,
                   "isencao_mensal_acoes": cm.ISENCAO_MENSAL_ACOES, "lote": 1,
                   "caixa_rende": False,
                   "aporte": "no fechamento do mês, depois da medição; o primeiro em 30/11/2026",
                   "valor": "líquido de liquidação: vendendo tudo no fechamento, com spread, "
                            "corretagem e o IR que a venda geraria; antes do aporte do dia",
                   "real": "dividido pelo IPCA desde o fechamento da execução da inaugural "
                           "(o mês da execução pró-rata pelos dias corridos)"},
        "fontes": {"acoes": ("pesquisa: dados/b3_retornos_diarios.parquet (fechamento, fator "
                             "e proventos) até o fim dela; depois, COTAHIST da B3 + "
                             "market.dividends do armazém com as regras da pesquisa"),
                   "bova11": "COTAHIST da B3: fechamento oficial (BDI 14)",
                   "cdi": f"BCB/SGS 12 ({fonte_cdi})",
                   "ipca": f"BCB/SGS 433 ({fonte_ipca})",
                   "spread": "o da curva diária, medido no COTAHIST na execução de cada revisão"},
        "ipca": {m.isoformat(): v for m, v in serie_ipca.items()},
        **calc,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seco", action="store_true", help="mostra sem gravar")
    ap.add_argument("--ensaio-ate", type=date.fromisoformat, action="append", default=[],
                    help="trata este pregão como fechamento (só com --seco; repetível)")
    a = ap.parse_args(argv)
    if a.ensaio_ate and not a.seco:
        ap.error("--ensaio-ate só vale com --seco")
    registros = c20.ler()
    status = c20.verificar_cadeia(registros)
    if not status.integra:
        raise SystemExit("cadeia quebrada: " + "; ".join(status.erros))
    publicado = _publicado()
    art = montar(registros, publicado=publicado,
                 extras=tuple(a.ensaio_ate))
    if art is None:
        print("nenhum pregão depois da inaugural ainda; nada a medir")
        return 0
    for aviso in art["avisos"]:
        print(f"aviso: {aviso}")
    for m in art["meses"]:
        ap_ = m["aportes"]
        print(f"{m['fechamento']}: aportado R$ {ap_['aportado']:,.2f} | "
              + " | ".join(f"{k} {(ap_[k] or {}).get('liquido')}" for k in cm.CARTEIRAS))
    if not art["meses"]:
        # grava assim mesmo: o arquivo diz até onde a fonte chegou e quando é o próximo
        print(f"nenhum mês fechado ainda; o primeiro fecha em {art['proximo_fechamento']}")
    texto = json.dumps(art, ensure_ascii=False, indent=1) + "\n"
    if a.seco:
        print(texto[:3000])
        return 0
    antes = len((publicado or {}).get("meses") or [])
    if len(art["meses"]) < antes:
        raise SystemExit(f"a medição publicada tem {antes} fechamento(s) e esta rodada mediu "
                         f"{len(art['meses'])}; nada gravado (veja os avisos)")
    ARTEFATO.write_text(texto, encoding="utf-8", newline="\n")
    print(f"gravado {ARTEFATO.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
