"""Macro brasileiro de alta frequência para o contexto das LLMs.

Selic meta, IPCA (mensal e em 12 meses), CDI e as expectativas do Focus, com a
unidade de cada número vinda da fonte e o juro real calculado por Fisher.

Existe porque o bloco CONTEXTO DE MERCADO só tinha ``public.macro`` (anual), e
lá o ano corrente engana duas vezes (auditoria app4, 04/10/2026, LLM-A2/A7):

- o IPCA de 2026 é o acumulado no ano até o último mês (3,11%), que a LLM lia
  como inflação anual -- o IPCA em 12 meses era 4,22%;
- o "juro real ex ante" é Selic − IPCA realizado: conta aritmética e ex post,
  que dava 10,64% contra ~7,3% do Tesouro IPCA+ no mesmo bloco.

Caminho local-first (CLAUDE.md): ``scripts/ingerir_macro_brasil.py`` grava as
séries no armazém (``macro_observations``, provedores ``bcb_sgs``/``bcb_focus``,
categoria ``unmapped`` para não mexer no score das carteiras), e
``scripts/publish_macro_brasil.py`` publica ``data/public/macro_brasil.json.gz``
para a produção, que não alcança o armazém. O CDI já tem arquivo próprio
(``cdi_diario``, SGS 12) e é lido de lá.

Nada aqui grava no Supabase.
"""
from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)

PROVEDOR_SGS = "bcb_sgs"
PROVEDOR_FOCUS = "bcb_focus"
PROVEDORES = frozenset({PROVEDOR_SGS, PROVEDOR_FOCUS})

#: Séries do SGS: código -> (nome, unidade da fonte, frequência, janela em dias).
#: A janela cobre a defasagem de divulgação: o IPCA sai ~40 dias depois do mês, e
#: 13 meses consecutivos do 433 são o que a reserva do IPCA 12m precisa.
SERIES_SGS: dict[str, tuple[str, str, str, int]] = {
    "432": ("Selic meta (Copom)", "% a.a.", "daily", 40),
    "433": ("IPCA mensal", "% a.m.", "monthly", 450),
    "13522": ("IPCA acumulado em 12 meses", "%", "monthly", 450),
}
#: Expectativas Focus guardadas: código -> (nome, unidade).
SERIES_FOCUS: dict[str, tuple[str, str]] = {
    "ipca_12m": ("Focus: IPCA esperado para os próximos 12 meses (mediana)", "%"),
    "selic_anual": ("Focus: Selic esperada no fim do ano (mediana)", "% a.a."),
    "ipca_anual": ("Focus: IPCA esperado no ano-calendário (mediana)", "%"),
}

URL_FOCUS = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"

ESQUEMA = "macro_brasil.v1"
CAMINHO_PADRAO = Path(__file__).resolve().parents[1] / "data" / "public" / "macro_brasil.json.gz"
#: A Selic meta é diária e o Focus semanal: um arquivo de 10 dias ainda descreve
#: o cenário; depois disso a coleta parou e o arquivo deixa de ser lido.
IDADE_MAXIMA_DIAS = 10

#: Idade a partir da qual o número vai marcado como defasado na linha do prompt.
#: Mensal: o IPCA de agosto sai em ~10/09, então 75 dias do 1º do mês é atraso.
_DEFASADO_DIAS = {"432": 7, "433": 75, "13522": 75, "focus": 14, "cdi": 7}


# ─────────────────────────────────────────────────────────────────────────────
# Contas
# ─────────────────────────────────────────────────────────────────────────────

def fisher(nominal_pct: float | None, inflacao_pct: float | None) -> float | None:
    """Juro real em % por Fisher: ``(1 + i) / (1 + π) − 1``.

    Selic − IPCA superestima o juro real em ``r·π`` (≈0,4 p.p. com Selic de
    13,75% e IPCA de 4,22%), e a diferença cresce com o nível dos dois.
    """
    if nominal_pct is None or inflacao_pct is None:
        return None
    return ((1 + nominal_pct / 100.0) / (1 + inflacao_pct / 100.0) - 1) * 100.0


def ipca_12m_de_mensais(mensais: Mapping[date, float]) -> tuple[float, date] | None:
    """IPCA em 12 meses composto dos 12 últimos IPCAs mensais (SGS 433).

    Só com 12 meses consecutivos terminando no último divulgado: buraco no meio
    daria um "12 meses" de 11, com cara de número certo.
    """
    if not mensais:
        return None
    meses = sorted(mensais)
    ultimo = meses[-1]
    esperado, fator = ultimo, 1.0
    for _ in range(12):
        if esperado not in mensais:
            return None
        fator *= 1 + float(mensais[esperado]) / 100.0
        esperado = (esperado.replace(day=1) - timedelta(days=1)).replace(day=1)
    return (fator - 1) * 100.0, ultimo


def cdi_anualizado(taxa_dia_pct: float) -> float:
    """CDI do SGS 12 (% ao dia útil) em % a.a., base 252 dias úteis."""
    return ((1 + taxa_dia_pct / 100.0) ** 252 - 1) * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Coleta (BCB) — usada pelo script de ingestão, nunca pelo app
# ─────────────────────────────────────────────────────────────────────────────

def baixar_sgs(codigo: str, inicio: date, fim: date,
               timeout: float = 45.0) -> tuple[dict[date, float], str | None]:
    """Série SGS pela API REST e, se ela falhar, pelo SOAP (``core.bcb_sgs``).

    Desde 03/10/2026 ``api.bcb.gov.br`` não resolve no DNS; o SOAP responde.
    """
    import requests

    motivo_rest = None
    try:
        r = requests.get(
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{int(codigo)}/dados",
            params={"formato": "json", "dataInicial": f"{inicio:%d/%m/%Y}",
                    "dataFinal": f"{fim:%d/%m/%Y}"},
            timeout=timeout)
        if r.ok:
            out = {}
            for item in r.json() or []:
                d = datetime.strptime(item["data"], "%d/%m/%Y").date()
                out[d] = float(str(item["valor"]).replace(",", "."))
            if out:
                return out, None
            motivo_rest = "a REST do SGS devolveu a série vazia"
        else:
            motivo_rest = f"a REST do SGS respondeu HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001 -- REST fora do ar cai no SOAP
        motivo_rest = f"a REST do SGS não respondeu ({type(exc).__name__})"

    from core.bcb_sgs import baixar_sgs_soap

    serie, motivo_soap = baixar_sgs_soap(int(codigo), inicio, fim, timeout=timeout)
    if motivo_soap is None and serie:
        return serie, None
    return {}, f"{motivo_rest}; {motivo_soap or 'o SOAP devolveu a série vazia'}"


def _consulta_focus(recurso: str, timeout: float, **params: str) -> list[dict]:
    from urllib.parse import quote

    import requests

    # O Olinda devolve corpo vazio se o ``$`` dos parâmetros OData chegar
    # codificado (``%24``), que é o que ``requests`` faz com ``params=``: a URL
    # precisa ir montada à mão.
    consulta = "&".join(f"${k}={quote(v)}" for k, v in params.items())
    r = requests.get(f"{URL_FOCUS}{recurso}?{consulta}", timeout=timeout)
    r.raise_for_status()
    return list(r.json().get("value") or [])


def parse_focus_12m(linhas: Iterable[Mapping]) -> list[dict]:
    """``ExpectativasMercadoInflacao12Meses`` -> observações (suavizada, mediana)."""
    out = []
    for linha in linhas:
        try:
            pesquisa = date.fromisoformat(str(linha["Data"])[:10])
            valor = float(linha["Mediana"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"provider": PROVEDOR_FOCUS, "provider_code": "ipca_12m",
                    "reference_period": pesquisa, "vintage_date": pesquisa,
                    "value": valor, "is_forecast": True})
    return out


def parse_focus_anuais(linhas: Iterable[Mapping]) -> list[dict]:
    """``ExpectativasMercadoAnuais`` (Selic e IPCA) -> uma observação por ano-alvo.

    O período de referência é o fim do ano esperado; a data da pesquisa vai no
    ``vintage_date``, que é o que separa uma semana do Focus da outra.
    """
    codigos = {"Selic": "selic_anual", "IPCA": "ipca_anual"}
    out = []
    for linha in linhas:
        codigo = codigos.get(str(linha.get("Indicador")))
        try:
            pesquisa = date.fromisoformat(str(linha["Data"])[:10])
            ano = int(str(linha["DataReferencia"])[:4])
            valor = float(linha["Mediana"])
        except (KeyError, TypeError, ValueError):
            continue
        if codigo is None:
            continue
        out.append({"provider": PROVEDOR_FOCUS, "provider_code": codigo,
                    "reference_period": date(ano, 12, 31), "vintage_date": pesquisa,
                    "value": valor, "is_forecast": True})
    return out


def baixar_focus(hoje: date, timeout: float = 45.0) -> tuple[list[dict], str | None]:
    """Medianas do Focus das últimas 3 semanas: IPCA 12m e Selic/IPCA anuais."""
    desde = f"{hoje - timedelta(days=21):%Y-%m-%d}"
    try:
        doze = _consulta_focus(
            "ExpectativasMercadoInflacao12Meses", timeout, top="30",
            filter=(f"Indicador eq 'IPCA' and Suavizada eq 'S' and baseCalculo eq 0 "
                    f"and Data ge '{desde}'"),
            orderby="Data desc", format="json", select="Data,Mediana")
        anuais = _consulta_focus(
            "ExpectativasMercadoAnuais", timeout, top="300",
            filter=("(Indicador eq 'Selic' or Indicador eq 'IPCA') and baseCalculo eq 0 "
                    f"and Data ge '{desde}'"),
            orderby="Data desc", format="json",
            select="Indicador,Data,DataReferencia,Mediana")
    except Exception as exc:  # noqa: BLE001 -- motivo vai para o relatório
        return [], f"o Olinda (Focus) não respondeu ({type(exc).__name__}: {str(exc)[:120]})"
    obs = parse_focus_12m(doze) + parse_focus_anuais(anuais)
    if not obs:
        return [], "o Olinda (Focus) devolveu a consulta vazia"
    return obs, None


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do armazém e arquivo publicado
# ─────────────────────────────────────────────────────────────────────────────

def ler_do_armazem(engine, *, hoje: date | None = None) -> list[dict]:
    """Observações BCB do armazém, a versão mais nova de cada (série, período, safra)."""
    from sqlalchemy import text

    corte = (hoje or date.today()) - timedelta(days=450)
    with engine.connect() as conn:
        linhas = conn.execute(text("""
            SELECT DISTINCT ON (provider, provider_code, reference_period,
                                COALESCE(vintage_date, '9999-12-31'::date))
                   provider, provider_code, reference_period, vintage_date,
                   value, is_forecast, retrieved_at
              FROM macro_observations
             WHERE provider = ANY(:provedores) AND value IS NOT NULL
               AND COALESCE(vintage_date, reference_period) >= :corte
             ORDER BY provider, provider_code, reference_period,
                      COALESCE(vintage_date, '9999-12-31'::date), retrieved_at DESC
        """), {"provedores": sorted(PROVEDORES), "corte": corte}).mappings().all()
    return [{**dict(r), "value": float(r["value"])} for r in linhas]


@dataclass(frozen=True)
class MacroBrasilPublicado:
    gerado_em: datetime
    observacoes: tuple[dict, ...]


def _iso(valor):
    return valor.isoformat() if isinstance(valor, (date, datetime)) else valor


def serializar(gerado_em: datetime, observacoes: Iterable[Mapping]) -> bytes:
    obs = sorted(
        ({k: _iso(v) for k, v in dict(o).items()} for o in observacoes),
        key=lambda o: (o["provider"], o["provider_code"], o["reference_period"],
                       o.get("vintage_date") or ""))
    # ``gerado_em`` no topo: é a chave que ``atualizar_vitrines._carimbo_do_arquivo``
    # lê para saber quando o alvo foi publicado.
    carga = {"schema": ESQUEMA, "gerado_em": gerado_em.isoformat(), "observations": obs}
    # mtime=0: mesmo conteúdo, mesmos bytes -- a rotina não commita o que não mudou.
    return gzip.compress(json.dumps(carga, ensure_ascii=False, sort_keys=True).encode(),
                         mtime=0)


def desserializar(dados: bytes) -> MacroBrasilPublicado:
    carga = json.loads(gzip.decompress(dados))
    if carga.get("schema") != ESQUEMA:
        raise ValueError(f"esquema de macro_brasil desconhecido: {carga.get('schema')!r}")
    obs = []
    for bruto in carga["observations"]:
        linha = dict(bruto)
        for chave in ("reference_period", "vintage_date"):
            linha[chave] = date.fromisoformat(linha[chave]) if linha.get(chave) else None
        if linha.get("retrieved_at"):
            linha["retrieved_at"] = datetime.fromisoformat(linha["retrieved_at"])
        linha["value"] = float(linha["value"])
        obs.append(linha)
    gerado = datetime.fromisoformat(carga["gerado_em"])
    if gerado.tzinfo is None:
        gerado = gerado.replace(tzinfo=timezone.utc)
    return MacroBrasilPublicado(gerado_em=gerado, observacoes=tuple(obs))


def carregar_publicado(caminho: Path = CAMINHO_PADRAO, *,
                       agora: datetime | None = None) -> MacroBrasilPublicado | None:
    """O arquivo publicado, ou ``None`` se não existir, não abrir ou estiver vencido."""
    try:
        publicado = desserializar(caminho.read_bytes())
    except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        logger.info("[macro_brasil] arquivo publicado ilegível (%s).", type(exc).__name__)
        return None
    agora = agora or datetime.now(timezone.utc)
    if agora - publicado.gerado_em > timedelta(days=IDADE_MAXIMA_DIAS):
        return None
    return publicado


# ─────────────────────────────────────────────────────────────────────────────
# Resumo e linhas de prompt
# ─────────────────────────────────────────────────────────────────────────────

def resumo(observacoes: Iterable[Mapping]) -> dict:
    """O último valor de cada série e a pesquisa Focus mais recente."""
    sgs: dict[str, dict[date, float]] = {}
    focus: dict[str, dict[date, dict[int, float]]] = {}
    for o in observacoes:
        periodo = o.get("reference_period")
        if not isinstance(periodo, date):
            continue
        if o.get("provider") == PROVEDOR_SGS:
            sgs.setdefault(str(o["provider_code"]), {})[periodo] = float(o["value"])
        elif o.get("provider") == PROVEDOR_FOCUS:
            pesquisa = o.get("vintage_date") or periodo
            focus.setdefault(str(o["provider_code"]), {}).setdefault(
                pesquisa, {})[periodo.year] = float(o["value"])

    out: dict = {}
    for codigo, serie in sgs.items():
        if serie:
            ultimo = max(serie)
            out[codigo] = {"valor": serie[ultimo], "data": ultimo}
    if "13522" not in out:
        composto = ipca_12m_de_mensais(sgs.get("433", {}))
        if composto is not None:
            out["13522"] = {"valor": composto[0], "data": composto[1],
                            "composto_do_433": True}
    for codigo, pesquisas in focus.items():
        if pesquisas:
            ultima = max(pesquisas)
            out[f"focus_{codigo}"] = {"data": ultima, "por_ano": pesquisas[ultima]}
    return out


def _num(valor: float, casas: int = 2) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _idade(chave: str, quando: date, hoje: date) -> str:
    dias = (hoje - quando).days
    limite = _DEFASADO_DIAS.get(chave)
    if limite is not None and dias > limite:
        return f" · DEFASADO: {dias} dias"
    return ""


def linha_cdi(cdi: Mapping[date, float] | None, hoje: date | None = None) -> str:
    """Último CDI do arquivo ``cdi_diario``, na unidade da fonte e anualizado."""
    if not cdi:
        return "    CDI (SGS 12): arquivo cdi_diario ausente ou ilegível."
    hoje = hoje or date.today()
    dia = max(cdi)
    return (f"    CDI (SGS 12): {_num(cdi[dia], 6)}% ao dia útil em {dia:%d/%m/%Y} = "
            f"{_num(cdi_anualizado(cdi[dia]))}% a.a. (base 252)" + _idade("cdi", dia, hoje))


def linhas_macro_brasil(res: Mapping, cdi: Mapping[date, float] | None, *,
                        origem: str, hoje: date | None = None) -> list[str]:
    """Bloco do BCB para o prompt; cada número com fonte, data e unidade.

    Componente ausente é nomeado na linha -- "sem Focus" não pode virar calmaria.
    """
    hoje = hoje or date.today()
    linhas = [f"  Banco Central do Brasil — alta frequência ({origem}):"]

    selic = res.get("432")
    if selic:
        linhas.append(f"    Selic meta (Copom, SGS 432): {_num(selic['valor'])}% a.a., "
                      f"vigente em {selic['data']:%d/%m/%Y}"
                      + _idade("432", selic["data"], hoje))
    else:
        linhas.append("    Selic meta (SGS 432): ausente nesta fonte.")

    linhas.append(linha_cdi(cdi, hoje))

    doze = res.get("13522")
    mensal = res.get("433")
    if doze:
        fonte = ("composto dos 12 últimos IPCAs mensais do SGS 433"
                 if doze.get("composto_do_433") else "SGS 13522")
        linhas.append(f"    IPCA acumulado em 12 meses ({fonte}): {_num(doze['valor'])}% "
                      f"até {doze['data']:%m/%Y}" + _idade("13522", doze["data"], hoje))
    else:
        linhas.append("    IPCA acumulado em 12 meses (SGS 13522): ausente nesta fonte.")
    if mensal:
        linhas.append(f"    IPCA do mês (SGS 433): {_num(mensal['valor'])}% em "
                      f"{mensal['data']:%m/%Y}")

    if selic and doze:
        real = fisher(selic["valor"], doze["valor"])
        linhas.append(f"    Juro real EX POST (Fisher, (1 + Selic meta) / (1 + IPCA 12m) − 1): "
                      f"{_num(real)}% a.a. — deflaciona pela inflação já ocorrida.")

    f12 = res.get("focus_ipca_12m")
    fsel = res.get("focus_selic_anual")
    fipca = res.get("focus_ipca_anual")
    if not (f12 or fsel or fipca):
        linhas.append("    Focus (expectativas do mercado): ausente nesta fonte.")
        return linhas
    partes = []
    datas = [f["data"] for f in (f12, fsel, fipca) if f]
    if f12:
        partes.append(f"IPCA dos próximos 12 meses {_num(next(iter(f12['por_ano'].values())))}%")
    for rotulo, serie in (("Selic no fim de", fsel), ("IPCA de", fipca)):
        if serie:
            anos = sorted(a for a in serie["por_ano"] if a >= hoje.year)[:2]
            partes += [f"{rotulo} {a} {_num(serie['por_ano'][a])}%" for a in anos]
    linhas.append(f"    Focus (mediana, pesquisa de {max(datas):%d/%m/%Y}): "
                  + "; ".join(partes) + _idade("focus", max(datas), hoje))
    if selic and f12:
        esperado = next(iter(f12["por_ano"].values()))
        real = fisher(selic["valor"], esperado)
        linhas.append(
            "    Juro real EX ANTE aproximado (Fisher, (1 + Selic meta atual) / "
            f"(1 + IPCA esperado 12m do Focus) − 1): {_num(real)}% a.a. — usa a Selic "
            "de hoje, não a esperada; o juro real que o mercado negocia é a curva "
            "do Tesouro IPCA+ acima.")
    return linhas
