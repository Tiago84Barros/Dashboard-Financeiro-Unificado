"""Publica o desempenho diário da Carteira 20 -- ``data/public/carteira20_desempenho.json``.

Roda localmente; o app publicado só lê o arquivo. As curvas saem de
``core/carteira20_desempenho.py`` e as fontes são:

- ações: ``dados/b3_retornos_diarios.parquet`` da pesquisa (retorno total com
  proventos, a mesma série que treinou a receita) até a última data dele; depois
  dela, o mesmo retorno calculado por ``core/carteira20_retornos.py`` com as
  regras da pesquisa, a partir do COTAHIST da B3 (os ZIPs que o alvo
  ``b3_pregao`` da rotina noturna baixa em ``local_staging/fii_b3_cotahist``;
  ``CARTEIRA20_COTAHIST_DIR`` troca a pasta) e dos proventos de
  ``market.dividends`` do armazém. Os códigos de cada ativo são a cadeia de
  ``dados/intermediario_b3/ativos_final.parquet`` da pesquisa. A cada rodada a
  extensão é recalculada inteira desde o fim da pesquisa: provento que a brapi
  publica dias depois do dia ex entra na rodada seguinte;
- BOVA11: fechamento oficial do mesmo COTAHIST (BDI 14). Até 26/10/2026 vinha
  de ``market.historical_prices`` do armazém, que tinha 74 linhas desde 2025,
  buracos de semanas, fechamentos até 5% fora do da B3 e o pregão de 23/10
  gravado às 11 h -- cotação intradiária que nenhuma rodada noturna corrigiu,
  e o gráfico parava ali;
- CDI: ``data/public/cdi_diario.json.gz`` e, depois dele, o BCB;
- ações inteiras: o fechamento do lote padrão no pregão de execução dá o preço
  de compra, e o spread vem das melhores ofertas que o COTAHIST grava no fim de
  cada pregão (``PREOFC``/``PREOFV``). Para cada ação, a mediana dos últimos
  ``PREGOES_SPREAD`` pregões até a execução de ``oferta de venda do fracionário
  / fechamento do lote padrão - 1`` (o que se paga a mais comprando no
  fracionário) e de ``1 - oferta de compra do fracionário / fechamento`` (o que
  se deixa vendendo). Sem fracionário suficiente vale o spread do lote padrão,
  com aviso. O BOVA11 não tem fracionário: usa o do próprio lote padrão.

    python scripts/carteira20_desempenho.py [--seco] [--execucao AAAA-MM-DD]

``--seco`` mostra o resultado sem gravar. ``--execucao`` troca a data de
execução da revisão vigente, só para ensaiar a conta com pregões que já
existem (exige ``--seco``: o artefato nunca leva uma data que o registro não tem).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core import carteira20 as c20  # noqa: E402
from core import carteira20_desempenho as cd  # noqa: E402

ARTEFATO = ROOT / "data" / "public" / "carteira20_desempenho.json"
BDI_ETF = "14"
# Pregões antes do corte que a extensão lê: P_{t-1}, marcas persistentes e as
# janelas de 5 pregões em volta de cada evento.
JANELA_PREGOES = timedelta(days=45)
# Cobertura da tabela de proventos que decide a imputação (regra da pesquisa).
JANELA_PROVENTOS = timedelta(days=400)
# Até este dia de janeiro a B3 pode ainda não ter o ZIP do ano.
TOLERANCIA_ZIP_DO_ANO = 10
FONTE_PESQUISA = "pesquisa: dados/b3_retornos_diarios.parquet (retorno total)"
COLUNAS = ["id", "data", "ret", "suspeito"]
BDI_FRACIONARIO = "96"
TPMERC_FRACIONARIO = "020"
# ~3 meses de pregões para a mediana do spread; abaixo do mínimo a amostra do
# fracionário não basta e vale o spread do lote padrão.
PREGOES_SPREAD = 63
MINIMO_SPREAD = 10
JANELA_SPREAD = timedelta(days=100)


def _parquet_pesquisa() -> Path:
    from scripts.carteira20_revisao import pasta_pesquisa

    return pasta_pesquisa() / "dados" / "b3_retornos_diarios.parquet"


def retornos_da_pesquisa(ids: set[str], desde: date) -> pd.DataFrame:
    df = pd.read_parquet(_parquet_pesquisa(),
                         columns=["id_ativo", "data", "ret_total", "ret_suspeito"],
                         filters=[("data", ">", pd.Timestamp(desde))])
    df = df[df["id_ativo"].isin(ids)]
    return df.rename(columns={"id_ativo": "id", "ret_total": "ret", "ret_suspeito": "suspeito"})


def fim_da_pesquisa() -> date:
    """Última data do parquet da pesquisa, pelas estatísticas dos grupos de linhas."""
    import pyarrow.parquet as pq

    arquivo = pq.ParquetFile(_parquet_pesquisa())
    i = arquivo.schema_arrow.get_field_index("data")
    estat = [arquivo.metadata.row_group(k).column(i).statistics
             for k in range(arquivo.metadata.num_row_groups)]
    if estat and all(e is not None and e.has_min_max for e in estat):
        return pd.Timestamp(max(e.max for e in estat)).date()
    return pd.Timestamp(pd.read_parquet(_parquet_pesquisa(), columns=["data"])["data"].max()).date()


def cadeias_da_pesquisa(ids: set[str]) -> dict[str, str]:
    """Cada código da cadeia de um ativo -> id, de ``ativos_final.parquet``."""
    from scripts.carteira20_revisao import pasta_pesquisa

    a = pd.read_parquet(pasta_pesquisa() / "dados" / "intermediario_b3" / "ativos_final.parquet",
                        columns=["id_ativo", "tickers"])
    a = a[a["id_ativo"].isin(ids)]
    faltam = ids - set(a["id_ativo"])
    if faltam:
        raise SystemExit(f"ativos sem cadeia de códigos na pesquisa: {sorted(faltam)}")
    return {t.strip(): i for i, ts in zip(a["id_ativo"], a["tickers"])
            for t in str(ts).split(",") if t.strip()}


def pasta_cotahist() -> Path:
    from data_pipeline.market.fii_b3_history import CACHE_ROOT

    return Path(os.environ.get("CARTEIRA20_COTAHIST_DIR") or ROOT / CACHE_ROOT)


@lru_cache(maxsize=4)
def _linhas_do_zip(caminho: str, mtime: float) -> tuple[dict, ...]:
    """Ações e ETFs de um ZIP; lido uma vez para as ações e o BOVA11 (8 s por ano).

    Vem com as ofertas de fim de pregão, que o spread usa, e com o fracionário
    (``TPMERC 020``), cujos códigos terminam em "F" e por isso não se misturam
    à cadeia de códigos das ações.
    """
    from core import carteira20_retornos as cr
    from data_pipeline.market.b3_cotahist import ler_texto

    del mtime
    with zipfile.ZipFile(io.BytesIO(Path(caminho).read_bytes())) as compactado:
        nomes = [n for n in compactado.namelist() if n.upper().endswith(".TXT")]
        if not nomes:
            return ()
        texto = compactado.read(nomes[0]).decode("latin-1").splitlines()
    return tuple(ler_texto(texto, bdi_codes=cr.BDIS_ACOES + (BDI_ETF,), ofertas=True)
                 + ler_texto(texto, bdi_codes=(BDI_FRACIONARIO,), tpmerc=TPMERC_FRACIONARIO,
                             ofertas=True))


def linhas_do_cotahist(desde: date, hoje: date) -> list[dict]:
    """Linhas de ações e ETFs nos ZIPs anuais de ``desde.year`` a ``hoje.year``.

    ZIP ausente é erro, e não série mais curta: o gráfico pararia sem aviso.
    A exceção é o ano corrente nos primeiros dias de janeiro.
    """
    pasta, linhas = pasta_cotahist(), []
    for ano in range(desde.year, hoje.year + 1):
        caminho = pasta / f"COTAHIST_A{ano}.ZIP"
        if not caminho.exists():
            if ano == hoje.year and hoje <= date(ano, 1, TOLERANCIA_ZIP_DO_ANO):
                continue
            raise SystemExit(f"{caminho} não existe; o alvo b3_pregao da rotina noturna o "
                             "baixa (ou aponte CARTEIRA20_COTAHIST_DIR para a pasta dos ZIPs)")
        linhas += _linhas_do_zip(str(caminho), caminho.stat().st_mtime)
    return linhas


def proventos_do_armazem(tickers: set[str], desde: date) -> pd.DataFrame:
    import sqlalchemy as sa

    from scripts.publish_fii_selection_from_local import _warehouse_url

    with sa.create_engine(_warehouse_url()).connect() as con:
        return pd.read_sql(sa.text(
            "SELECT ticker, ex_date, payment_date, amount, type FROM market.dividends "
            "WHERE ticker = ANY(:t) AND ex_date >= :d"), con,
            params={"t": sorted(tickers), "d": desde})


def _data(d: date | None) -> str:
    return f"{d:%d/%m/%Y}" if d else "(vazio)"


def retornos_das_acoes(ids: set[str], desde: date, *, pesquisa=retornos_da_pesquisa,
                       fim_pesquisa=fim_da_pesquisa, cadeias=cadeias_da_pesquisa,
                       cotahist=linhas_do_cotahist, proventos=proventos_do_armazem,
                       hoje: date | None = None) -> pd.DataFrame:
    """Retorno total depois de ``desde``: a pesquisa até o fim dela, a extensão depois."""
    from core import carteira20_retornos as cr

    hoje = hoje or datetime.now(c20.FUSO_B3).date()
    fim = fim_pesquisa()
    base = pesquisa(ids, desde)
    base = base.assign(data=pd.to_datetime(base["data"]).dt.date)
    base = base[base["data"] <= fim]
    corte = max(fim, desde)
    ipt = cadeias(ids)
    pregoes = cr.pregoes_de_acoes(cotahist(corte - JANELA_PREGOES, hoje), set(ipt))
    pregoes = pregoes[pregoes["trade_date"].dt.date >= corte - JANELA_PREGOES]
    extensao = cr.calcular(pregoes, proventos(set(ipt), corte - JANELA_PROVENTOS), ipt, corte)
    cotahist_ate = pregoes["trade_date"].max().date() if len(pregoes) else None
    print(f"ações: pesquisa até {_data(fim)}, COTAHIST até {_data(cotahist_ate)}; "
          f"{extensao['data'].nunique()} pregão(ões) calculado(s) depois de {_data(corte)}")
    partes = [p[COLUNAS] for p in (base, extensao) if len(p)]
    df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUNAS)
    df.attrs["fonte"] = (f"pesquisa: dados/b3_retornos_diarios.parquet até {_data(fim)}; "
                         "depois, COTAHIST da B3 + market.dividends do armazém com as regras "
                         "da pesquisa (retorno total)")
    return df


def bova11_do_cotahist(desde: date, *, cotahist=linhas_do_cotahist,
                       hoje: date | None = None) -> pd.Series:
    """Fechamento oficial do BOVA11 (por cota) desde ``desde``, inclusive."""
    hoje = hoje or datetime.now(c20.FUSO_B3).date()
    serie = {}
    for x in cotahist(desde, hoje):
        if x["ticker"] == "BOVA11" and x["bdi"] == BDI_ETF and x.get("close"):
            dia = date.fromisoformat(str(x["trade_date"])[:10])
            if dia >= desde:
                serie[dia] = float(x["close"]) / max(float(x.get("fator_cotacao") or 1), 1.0)
    return pd.Series(dict(sorted(serie.items())), dtype=float)


def _mediana(valores: list[float]) -> float | None:
    return float(pd.Series(valores[-PREGOES_SPREAD:]).median()) if valores else None


def lotes_do_cotahist(execucao: date, carteira: list[dict], *, cotahist=linhas_do_cotahist,
                      hoje: date | None = None) -> dict:
    """Preço de compra na execução e spread de cada ação, e o do BOVA11.

    Devolve ``precos`` (id -> fechamento unitário do lote padrão na execução),
    ``custos`` (id -> ``(compra, venda)``), ``custo_bova11`` (``None`` quando
    o livro do ETF não tem os dois lados), ``padrao`` (os tickers que ficaram
    com o spread do lote padrão) e ``pregoes`` (quantos pregões cada mediana
    usou, pelo menor). Como em ``pregoes_de_acoes``, o fechamento de uma ação
    vem do BDI 02 e, sem ele no dia, de um regime especial (BDI 05 a 09).
    """
    from core import carteira20_retornos as cr

    hoje = hoje or datetime.now(c20.FUSO_B3).date()
    desde = execucao - JANELA_SPREAD
    por_ticker = {c["ticker"]: c["id"] for c in carteira}
    fracionarios = {t + "F": t for t in por_ticker}
    padrao: dict[tuple[str, date], dict] = {}
    fracao: dict[tuple[str, date], dict] = {}
    for x in cotahist(desde, hoje):
        dia = date.fromisoformat(str(x["trade_date"])[:10])
        if not desde <= dia <= execucao or not x.get("close"):
            continue
        fator = max(float(x.get("fator_cotacao") or 1), 1.0)
        if x["ticker"] in fracionarios and x["bdi"] == BDI_FRACIONARIO:
            fracao[(fracionarios[x["ticker"]], dia)] = {**x, "fator": fator}
        elif ((x["ticker"] in por_ticker and x["bdi"] in cr.BDIS_ACOES)
              or (x["ticker"] == "BOVA11" and x["bdi"] == BDI_ETF)):
            visto = padrao.get((x["ticker"], dia))
            if not visto or (visto["bdi"] != "02" and x["bdi"] == "02"):
                padrao[(x["ticker"], dia)] = {**x, "fator": fator}

    def _lados(t: str, livro: dict) -> tuple[list[float], list[float]]:
        """Custo de cada lado, pregão a pregão, das ofertas de ``livro`` contra o fechamento.

        Cada registro vem dividido pelo próprio fator de cotação: o fracionário
        e o lote padrão podem cotar em unidades diferentes.
        """
        compra, venda = [], []
        for (tk, dia), lp in sorted(padrao.items(), key=lambda kv: kv[0][1]):
            o = livro.get((tk, dia))
            if tk != t or not o:
                continue
            fech = float(lp["close"]) / lp["fator"]
            if o.get("best_ask") and o["best_ask"] > 0:
                compra.append(float(o["best_ask"]) / o["fator"] / fech - 1)
            if o.get("best_bid") and o["best_bid"] > 0:
                venda.append(1 - float(o["best_bid"]) / o["fator"] / fech)
        return compra, venda

    precos, custos, usou_padrao, amostras = {}, {}, [], []
    for t, i in por_ticker.items():
        lp = padrao.get((t, execucao))
        if lp:
            precos[i] = float(lp["close"]) / lp["fator"]
        compra, venda = _lados(t, fracao)
        if min(len(compra), len(venda)) < MINIMO_SPREAD:
            compra, venda = _lados(t, padrao)
            usou_padrao.append(t)
        if compra and venda:
            custos[i] = (_mediana(compra), _mediana(venda))
            amostras.append(min(len(compra), len(venda), PREGOES_SPREAD))
    compra, venda = _lados("BOVA11", padrao)
    custo_bova11 = (_mediana(compra), _mediana(venda)) if compra and venda else None
    return {"precos": precos, "custos": custos, "custo_bova11": custo_bova11,
            "padrao": usou_padrao, "pregoes": min(amostras) if amostras else 0}


def cdi_diario(desde: date, ate: date) -> tuple[dict[date, float], str]:
    from core.rentabilidade import obter_cdi

    r = obter_cdi(desde, ate)
    return r["serie"], ", ".join(filter(None, [r.get("fonte"), r.get("motivo")]))


def montar(registros: list[dict], execucao: date | None = None, *,
           retornos=retornos_das_acoes, bova=bova11_do_cotahist, cdi=cdi_diario,
           lotes=lotes_do_cotahist, agora: datetime | None = None) -> dict | None:
    """Artefato da revisão vigente, ou None quando ainda não há pregão depois dela."""
    atual = c20.vigente(registros)
    if not atual:
        return None
    corpo = atual["corpo"]
    execucao = execucao or date.fromisoformat(corpo["pregao_execucao"])
    carteira = [{"id": c["id"], "ticker": c["ticker"]} for c in corpo["carteira"]]
    universo = [{"id": u["id"], "ticker": u["ticker"]}
                for u in (corpo.get("universo") or {}).get("pontuacoes") or []]
    ids = {x["id"] for x in carteira + universo}
    agora = agora or datetime.now(c20.FUSO_B3)
    ret = retornos(ids, execucao)
    serie_bova = bova(execucao)
    serie_cdi, fonte_cdi = cdi(execucao, agora.date())
    lt = lotes(execucao, carteira)
    calc = cd.calcular(execucao, ret, serie_bova, serie_cdi, carteira, universo,
                       precos=lt["precos"], custos=lt["custos"], custo_bova11=lt["custo_bova11"])
    if not calc["pregoes"]:
        return None
    if lt["padrao"]:
        calc["avisos"].append("spread do lote padrão, por falta de fracionário no COTAHIST: "
                              + ", ".join(lt["padrao"]))
    fonte_spread = (f"COTAHIST da B3: mediana de até {PREGOES_SPREAD} pregões até a execução "
                    "das melhores ofertas do fracionário (BDI 96) contra o fechamento do lote "
                    "padrão; BOVA11, as do próprio lote padrão")
    return {
        "schema": 2,
        "gerado_em": agora.isoformat(timespec="seconds"),
        "revisao": {"seq": atual["registro"]["seq"], "hash": atual["registro"]["hash"],
                    "corpo_sha256": cd.impressao_corpo(corpo),
                    "data_formacao": corpo["data_formacao"], "pregao_execucao": execucao.isoformat()},
        "dados_ate": calc["datas"][-1],
        "regras": {"capital": cd.CAPITAL, "custo_por_lado": cd.CUSTO_LADO,
                   "aliquota_ir": cd.ALIQUOTA_IR, "isencao_mensal_acoes": cd.ISENCAO_MENSAL_ACOES,
                   "lote": 1, "caixa_rende": False,
                   "spread_bova11": None if lt["custo_bova11"] is None else
                   {"compra": round(lt["custo_bova11"][0], 6),
                    "venda": round(lt["custo_bova11"][1], 6)},
                   "pregoes_spread": lt["pregoes"]},
        "fontes": {"acoes": ret.attrs.get("fonte") or FONTE_PESQUISA,
                   "bova11": "COTAHIST da B3: fechamento oficial (BDI 14)",
                   "cdi": f"BCB/SGS 12 ({fonte_cdi})",
                   "spread": fonte_spread},
        **calc,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seco", action="store_true", help="mostra sem gravar")
    ap.add_argument("--execucao", type=date.fromisoformat,
                    help="ensaio com outra data de execução (só com --seco)")
    a = ap.parse_args(argv)
    if a.execucao and not a.seco:
        ap.error("--execucao só vale com --seco")
    registros = c20.ler()
    status = c20.verificar_cadeia(registros)
    if not status.integra:
        raise SystemExit("cadeia quebrada: " + "; ".join(status.erros))
    art = montar(registros, a.execucao)
    if art is None:
        print("nenhum pregão depois da execução ainda; nada a publicar")
        return 0
    texto = json.dumps(art, ensure_ascii=False, indent=1) + "\n"
    finais = {k: v[-1] if v else None for k, v in art["curvas"].items()}
    print(f"{art['pregoes']} pregão(ões), dados até {art['dados_ate']}: {finais}")
    print(f"caixa que sobrou: {art.get('caixa')}; cotas de BOVA11: {art.get('cotas_bova11')}")
    for aviso in art["avisos"]:
        print(f"aviso: {aviso}")
    if a.seco:
        print(texto[:2000])
        return 0
    ARTEFATO.write_text(texto, encoding="utf-8", newline="\n")
    print(f"gravado {ARTEFATO.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
