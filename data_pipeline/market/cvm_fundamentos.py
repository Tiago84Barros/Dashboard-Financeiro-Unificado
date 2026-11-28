"""Insumos em reais da DFP e ITR oficiais para os indicadores vigentes da B3."""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import tempfile
import time
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

from data_pipeline.market.b3_saidas import (
    _circulante,
    _conta_de_topo,
    _conta_do_ebit,
    _do_controlador,
    _eh_divida,
    _filha,
    _norm,
    _v,
)
from data_pipeline.market.cvm_publicacao import (
    DFP_ZIP,
    ITR_ZIP,
    _codigo,
    _data,
    nome_cabecalho,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 8 * 60 * 60
_MAX_ZIP_BYTES = 128 * 1024 * 1024
_MAX_CSV_BYTES = 256 * 1024 * 1024
_DOWNLOAD_SECONDS = 4 * 60
_DIA = timedelta(days=1)
_ESCALAS = {"MIL": 1000, "UNIDADE": 1}
_HEADER_FIELDS = {"CD_CVM", "DT_REFER", "VERSAO", "DT_RECEB", "ID_DOC"}
_ACCOUNT_FIELDS = {
    "CD_CVM", "DT_REFER", "VERSAO", "MOEDA", "ESCALA_MOEDA", "ORDEM_EXERC",
    "DT_FIM_EXERC", "CD_CONTA", "DS_CONTA", "VL_CONTA",
}
_ZIP_ERRORS = (OSError, ValueError, RuntimeError, csv.Error, zipfile.BadZipFile)
_PACKAGE_ERRORS = _ZIP_ERRORS + (requests.RequestException,)
_Contas = dict[str, tuple[str, float]]
_Fluxos = tuple[float | None, float | None, float | None]
_ChaveDocumento = tuple[int, date, int]


@dataclass(frozen=True)
class DocumentoCVM:
    category: str
    period_end: date
    filing_date: date
    version: int
    document_id: str | None


@dataclass(frozen=True)
class FundamentosCVM:
    period_end: date
    filing_date: date
    scope: str
    revenue_ttm: float | None
    ebit_ttm: float | None
    net_income_ttm: float | None
    equity: float | None
    cash: float | None
    gross_debt: float | None
    net_debt: float | None
    source_documents: tuple[DocumentoCVM, ...]
    total_assets: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None


@dataclass
class _Demonstracao:
    documento: DocumentoCVM
    escopo: str
    dre: dict[tuple[str, date, date], _Contas] = field(default_factory=dict)
    bpa: _Contas = field(default_factory=dict)
    bpp: _Contas = field(default_factory=dict)


@contextmanager
def _abrir_csv(z: zipfile.ZipFile, nome: str, campos: set[str]):
    entrada = z.getinfo(nome)
    if entrada.file_size > _MAX_CSV_BYTES:
        raise ValueError("CSV da CVM excede o limite")
    with z.open(entrada) as raw, io.TextIOWrapper(raw, encoding="latin-1", newline="") as texto:
        linhas = csv.DictReader(texto, delimiter=";")
        if not campos.issubset(linhas.fieldnames or []):
            raise ValueError("colunas do pacote CVM incompatíveis")
        yield linhas


def _documentos(z, cabecalho, categoria, codigos, as_of) -> dict[_ChaveDocumento, DocumentoCVM]:
    por_competencia: dict[tuple[int, date], DocumentoCVM] = {}
    with _abrir_csv(z, cabecalho, _HEADER_FIELDS) as linhas:
        for linha in linhas:
            codigo = _codigo(linha["CD_CVM"])
            if codigo not in codigos:
                continue
            referencia = _data(linha["DT_REFER"])
            recebido = _data(linha["DT_RECEB"])
            versao = _codigo(linha["VERSAO"])
            if not referencia or not recebido or not versao or not referencia <= recebido <= as_of:
                continue
            pid = (codigo, referencia)
            anterior = por_competencia.get(pid)
            if anterior is None or (versao, recebido) > (anterior.version, anterior.filing_date):
                por_competencia[pid] = DocumentoCVM(
                    categoria, referencia, recebido, versao, linha["ID_DOC"] or None,
                )
    return {(codigo, ref, doc.version): doc for (codigo, ref), doc in por_competencia.items()}


def _ler_demonstracao(z, nome, tipo, escopo, documentos, demonstracoes):
    campos = _ACCOUNT_FIELDS | ({"DT_INI_EXERC"} if tipo == "DRE" else set())
    conflitos: set[tuple[_ChaveDocumento, tuple, str]] = set()
    with _abrir_csv(z, nome, campos) as linhas:
        for linha in linhas:
            codigo = _codigo(linha["CD_CVM"])
            if codigo not in demonstracoes:
                continue
            conta = linha["CD_CONTA"]
            if (not conta or conta.count(".") > 2 or conta.startswith("3.99")
                    or not all(parte.isdecimal() for parte in conta.split("."))):
                continue
            chave_doc = (codigo, _data(linha["DT_REFER"]), _codigo(linha["VERSAO"]))
            doc = documentos.get(chave_doc)
            if doc is None or linha["MOEDA"] != "REAL":
                continue
            ordem = linha["ORDEM_EXERC"]
            if ordem not in {"ÚLTIMO", "PENÚLTIMO"}:
                continue
            fim = _data(linha["DT_FIM_EXERC"])
            if fim is None or (ordem == "ÚLTIMO" and fim != doc.period_end):
                continue
            periodo = ()
            if tipo == "DRE":
                inicio = _data(linha["DT_INI_EXERC"])
                if inicio is None or inicio > fim or (ordem == "PENÚLTIMO" and fim >= doc.period_end):
                    continue
                periodo = (ordem, inicio, fim)
            elif ordem != "ÚLTIMO":
                continue
            escala = _ESCALAS.get(linha["ESCALA_MOEDA"])
            if escala is None:
                continue
            try:
                valor = float(linha["VL_CONTA"]) * escala
            except (TypeError, ValueError):
                continue
            if not math.isfinite(valor):
                continue
            demos = demonstracoes[codigo]
            chave_demo = (doc.period_end, escopo)
            if chave_demo not in demos:
                demos[chave_demo] = _Demonstracao(doc, escopo)
            demo = demos[chave_demo]
            contas = demo.dre.setdefault(periodo, {}) if tipo == "DRE" else getattr(demo, tipo.lower())
            chave_conta = (chave_doc, periodo, conta)
            if chave_conta in conflitos:
                continue
            descricao = linha["DS_CONTA"] or ""
            if conta in contas and contas[conta] != (descricao, valor):
                conflitos.add(chave_conta)
                del contas[conta]
            else:
                contas[conta] = (descricao, valor)


def _ler_zip(caminho: Path, categoria: str, ano: int, codigos: set[int], as_of: date):
    if caminho.stat().st_size > _MAX_ZIP_BYTES:
        raise ValueError("ZIP da CVM excede o limite")
    cabecalho = nome_cabecalho(categoria, ano)
    with zipfile.ZipFile(caminho) as z:
        nomes = z.namelist()
        if len(nomes) != len(set(nomes)) or cabecalho not in nomes:
            raise ValueError("cabeçalho CVM ausente ou ZIP com nomes duplicados")
        prefixo = f"{categoria.lower()}_cia_aberta"
        if not any(f"{prefixo}_DRE_{escopo}_{ano}.csv" in nomes for escopo in ("con", "ind")):
            raise ValueError("DRE ausente no ZIP da CVM")
        documentos = _documentos(z, cabecalho, categoria, codigos, as_of)
        demonstracoes = {codigo: {} for codigo, _, _ in documentos}
        if demonstracoes:
            for escopo in ("con", "ind"):
                for tipo in ("DRE", "BPA", "BPP"):
                    nome = f"{prefixo}_{tipo}_{escopo}_{ano}.csv"
                    if nome in nomes:
                        _ler_demonstracao(z, nome, tipo, escopo, documentos, demonstracoes)
    return {codigo: list(demos.values()) for codigo, demos in demonstracoes.items()}


def _etag_valido(etag) -> bool:
    return (
        isinstance(etag, str) and 2 <= len(etag) <= 512
        and etag.startswith(('"', 'W/"')) and etag.endswith('"')
        and etag.isascii() and etag.isprintable()
    )


def _baixar_zip(temporario, url, etag):
    inicio = time.monotonic()
    headers = {"User-Agent": "DashboardFinanceiro/1.0"}
    if etag:
        headers["If-None-Match"] = etag
    with requests.get(
        url, headers=headers,
        timeout=(10, 30), stream=True, allow_redirects=False,
    ) as resposta:
        if resposta.status_code == 304 and etag:
            return None
        if resposta.status_code != 200:
            raise requests.RequestException(f"HTTP {resposta.status_code}")
        tamanho = 0
        for bloco in resposta.iter_content(chunk_size=64 * 1024):
            tamanho += len(bloco)
            if tamanho > _MAX_ZIP_BYTES or time.monotonic() - inicio > _DOWNLOAD_SECONDS:
                raise ValueError("download CVM excede o limite de tamanho ou duração")
            temporario.write(bloco)
        novo_etag = resposta.headers.get("ETag")
        return novo_etag if _etag_valido(novo_etag) else ""


def _ler_etag(caminho: Path, st_zip) -> tuple[str | None, float]:
    try:
        with caminho.open(encoding="utf-8") as arquivo:
            dados = json.loads(arquivo.read(2048))
        etag = dados["etag"]
        if _etag_valido(etag) and (dados["mtime_ns"], dados["tamanho"]) == (st_zip.st_mtime_ns, st_zip.st_size):
            return etag, caminho.stat().st_mtime
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None, st_zip.st_mtime


def _carregar_pacote(cache_dir, categoria, ano, codigos, as_of, somente_cache):
    url = (DFP_ZIP if categoria == "DFP" else ITR_ZIP).format(year=ano)
    caminho = cache_dir / categoria.lower() / url.rsplit("/", 1)[1]
    metadados = caminho.with_suffix(".zip.http")
    cache = None
    etag = None
    try:
        st_cache = caminho.stat()
        cache = _ler_zip(caminho, categoria, ano, codigos, as_of)
        etag, verificado_em = _ler_etag(metadados, st_cache)
        if time.time() - verificado_em < _CACHE_TTL_SECONDS:
            return cache
    except FileNotFoundError:
        pass
    except _ZIP_ERRORS as exc:
        logger.warning("Cache CVM %s %s ilegível (%s)", categoria, ano, type(exc).__name__)
    if somente_cache:
        return cache or {}
    temporario = None
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=caminho.parent, suffix=".part", delete=False) as arquivo:
            temporario = Path(arquivo.name)
            etag_novo = _baixar_zip(arquivo, url, etag)
        if etag_novo is None:
            metadados.touch()
            return cache or {}
        resultado = _ler_zip(temporario, categoria, ano, codigos, as_of)
        st_novo = temporario.stat()
        temporario.replace(caminho)
        if etag_novo:
            # O registro só é usado enquanto identificar exatamente o ZIP
            # instalado. Uma gravação parcial invalida apenas a revalidação.
            try:
                metadados.write_text(json.dumps({
                    "etag": etag_novo, "mtime_ns": st_novo.st_mtime_ns, "tamanho": st_novo.st_size,
                }), encoding="utf-8")
            except OSError:
                logger.debug("CVM %s %s sem metadados de cache", categoria, ano)
        return resultado
    except _PACKAGE_ERRORS as exc:
        logger.warning(
            "Pacote CVM %s %s indisponível (%s), cache íntegro %s",
            categoria, ano, type(exc).__name__, "sim" if cache is not None else "não",
        )
        return cache or {}
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


def _atribuido(contas: _Contas, codigo: str, escopo: str) -> float | None:
    total = _v(contas, codigo)
    if escopo == "ind":
        return total
    controlador = _filha(contas, codigo, _do_controlador)
    minoritarios = _filha(contas, codigo, lambda desc: "nao controlador" in desc)
    direto = _v(contas, controlador)
    parcela_minoritarios = _v(contas, minoritarios)
    if direto not in {None, 0}:
        return direto
    # A CVM pode trazer zero no controlador e o valor nos agregados. Se ambos
    # os agregados aparecem, a diferença também preserva um zero genuíno.
    if total is not None and parcela_minoritarios is not None:
        return total - parcela_minoritarios
    if direto is not None:
        return direto
    descricao = contas.get(codigo, ("", 0))[0]
    return total if _do_controlador(_norm(descricao)) else None


def _fluxos(contas: _Contas, escopo: str) -> _Fluxos:
    lucro = _conta_de_topo(
        contas, "3", lambda desc: ("do periodo" in desc or "do exercicio" in desc)
        and ("lucro" in desc or "prejuizo" in desc) and "antes" not in desc,
    ) or "3.11"
    return _v(contas, "3.01"), _v(contas, _conta_do_ebit(contas)), _atribuido(contas, lucro, escopo)


def _subtrair_ano(dia: date) -> date:
    return dia.replace(year=dia.year - 1, day=28 if (dia.month, dia.day) == (2, 29) else dia.day)


def _exercicio_anual(demo: _Demonstracao) -> _Contas | None:
    fim = demo.documento.period_end
    inicio = _subtrair_ano(fim + _DIA)
    return demo.dre.get(("ÚLTIMO", inicio, fim))


def _trimestre_fiscal(inicio: date, fim: date) -> bool:
    dia_seguinte = fim + _DIA
    meses = (dia_seguinte.year - inicio.year) * 12 + dia_seguinte.month - inicio.month
    return dia_seguinte.day == inicio.day and meses in {3, 6, 9}


def _ttm(atual: _Demonstracao, demonstracoes: list[_Demonstracao]) -> tuple[_Fluxos, DocumentoCVM | None]:
    if atual.documento.category == "DFP":
        return _fluxos(_exercicio_anual(atual) or {}, atual.escopo), None
    fim = atual.documento.period_end
    # A ITR do 2T/3T também contém o trimestre isolado. O acumulado fiscal
    # começa no primeiro dia do exercício, imediatamente após a DFP anterior.
    for (ordem, inicio, fim_ytd), ytd in sorted(atual.dre.items()):
        if ordem != "ÚLTIMO" or fim_ytd != fim or not _trimestre_fiscal(inicio, fim):
            continue
        anterior = next((
            demo for demo in demonstracoes
            if demo.escopo == atual.escopo and demo.documento.category == "DFP"
            and demo.documento.period_end == inicio - _DIA
        ), None)
        comparavel = atual.dre.get(("PENÚLTIMO", _subtrair_ano(inicio), _subtrair_ano(fim + _DIA) - _DIA))
        anual = _exercicio_anual(anterior) if anterior else None
        if not anual or not comparavel:
            continue
        ano = _fluxos(anual, atual.escopo)
        corrente = _fluxos(ytd, atual.escopo)
        passado = _fluxos(comparavel, atual.escopo)
        # Cada conta precisa de três valores da mesma moeda, escopo e janela.
        combinados = [
            base + recente - comparado
            if base is not None and recente is not None and comparado is not None else None
            for base, recente, comparado in zip(ano, corrente, passado)
        ]
        return (combinados[0], combinados[1], combinados[2]), anterior.documento
    return (None, None, None), None


def _snapshot(demos: list[_Demonstracao]) -> FundamentosCVM:
    atual = max(demos, key=lambda demo: (
        demo.documento.period_end, demo.documento.category == "DFP",
        any(ordem == "ÚLTIMO" for ordem, _, _ in demo.dre), demo.escopo == "con",
        demo.documento.version, demo.documento.filing_date,
    ))
    (receita, ebit, lucro), anterior = _ttm(atual, demos)
    pl = _conta_de_topo(atual.bpp, "2", lambda desc: "patrimonio liquido" in desc) or "2.03"
    equity = _atribuido(atual.bpp, pl, atual.escopo)
    caixa_codigo = next((
        codigo for codigo in ("1.01.01", "1.01")
        if "caixa e equivalentes" in _norm(atual.bpa.get(codigo, ("", 0))[0])
    ), None)
    caixa = _v(atual.bpa, caixa_codigo)
    curto = atual.bpp.get("2.01.04")
    longo = atual.bpp.get("2.02.01")
    divida = curto[1] + longo[1] if curto and longo and _eh_divida(curto[0]) and _eh_divida(longo[0]) else None
    documentos = (atual.documento, anterior) if anterior else (atual.documento,)
    return FundamentosCVM(
        atual.documento.period_end, max(doc.filing_date for doc in documentos), atual.escopo,
        receita, ebit, lucro, equity, caixa, divida,
        divida - caixa if divida is not None and caixa is not None else None,
        documentos,
        total_assets=_v(atual.bpa, "1"),
        current_assets=_v(atual.bpa, "1.01") if _circulante(atual.bpa, "1.01") else None,
        current_liabilities=_v(atual.bpp, "2.01") if _circulante(atual.bpp, "2.01") else None,
    )


def carregar_fundamentos_cvm(
    codigos_cvm: Iterable[int], *, as_of: date, cache_dir: Path | None = None,
    somente_cache: bool = False,
) -> dict[int, FundamentosCVM]:
    """Retorna a competência oficial mais recente entregue até `as_of`.

    A DFP fornece o fluxo anual diretamente e o TTM da ITR combina a DFP do
    exercício anterior com a diferença entre os acumulados atual e comparável
    da mesma ITR. O balanço vale na competência da ITR/DFP mais recente.

    Um código ausente não tem demonstração utilizável nos pacotes disponíveis.
    Um campo None indica conta ou período comparável indisponível no documento.
    Os arquivos abertos da CVM podem suprimir versões históricas substituídas.
    """
    codigos = {codigo for valor in codigos_cvm if (codigo := _codigo(valor)) is not None}
    if not codigos:
        return {}
    raiz = Path(cache_dir) if cache_dir is not None else Path.cwd() / "data" / "cache" / "cvm"
    demonstracoes: dict[int, list[_Demonstracao]] = defaultdict(list)
    for categoria, anos in (("ITR", 2), ("DFP", 3)):
        for ano in range(as_of.year, as_of.year - anos, -1):
            pacote = _carregar_pacote(raiz, categoria, ano, codigos, as_of, somente_cache)
            for codigo, demos in pacote.items():
                demonstracoes[codigo].extend(demos)
    return {codigo: _snapshot(demos) for codigo, demos in demonstracoes.items() if demos}
