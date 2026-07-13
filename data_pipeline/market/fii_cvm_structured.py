"""Ingestão PIT dos conjuntos estruturados de FII publicados pela CVM.

Os arquivos anuais da CVM são mutáveis porque reapresentações substituem o ZIP.
Por isso cada coleta preserva hash, cabeçalhos HTTP, versão do documento e data
de entrega. A data de entrega é o ``knowledge_at``; a coleta é apenas o momento
em que este sistema observou aquela versão do arquivo.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile
from zoneinfo import ZoneInfo

import pandas as pd

from data_pipeline.market.fii_sources import metric_observation


SOURCE = "cvm_dados_abertos"
ROOT = "https://dados.cvm.gov.br/dados/FII/DOC"
ARCHIVES = {
    "monthly": ("INF_MENSAL", "inf_mensal_fii_{year}.zip"),
    "quarterly": ("INF_TRIMESTRAL", "inf_trimestral_fii_{year}.zip"),
    "annual": ("INF_ANUAL", "inf_anual_fii_{year}.zip"),
    "financials": ("DFIN", "dfin_fii_{year}.csv"),
    "eventual": ("EVENTUAL", "eventual_fi_{year}.csv"),
}
CACHE_ROOT = Path("local_staging/fii_cvm")


@dataclass(frozen=True)
class CvmArchive:
    kind: str
    year: int
    url: str
    content: bytes
    collected_at: datetime
    headers: dict[str, str]
    sha256: str
    from_cache: bool = False


def archive_url(kind: str, year: int) -> str:
    folder, pattern = ARCHIVES[kind]
    root = "https://dados.cvm.gov.br/dados/FI/DOC" if kind == "eventual" else ROOT
    return f"{root}/{folder}/DADOS/{pattern.format(year=year)}"


def fetch_archive(kind: str, year: int, *, timeout: int = 120,
                  cache_root: Path = CACHE_ROOT) -> CvmArchive | None:
    """Baixa o artefato oficial com retry e cache local endereçado por ano."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    url = archive_url(kind, year)
    suffix = ".zip" if url.endswith(".zip") else ".csv"
    cache = cache_root / kind / f"{year}{suffix}"
    headers_path = cache.with_suffix(cache.suffix + ".headers.json")
    session = requests.Session()
    session.headers["User-Agent"] = "DashboardFinanceiro/1.0 (+cvm-fii-pit)"
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=.8, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True)))
    try:
        response = session.get(url, timeout=timeout)
    except requests.RequestException:
        if not cache.exists():
            return None
        content = cache.read_bytes()
        saved = json.loads(headers_path.read_text(encoding="utf-8")) if headers_path.exists() else {}
        return CvmArchive(kind, year, url, content, datetime.now(timezone.utc), saved,
                          hashlib.sha256(content).hexdigest(), True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    content = response.content
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(content)
    safe_headers = {key: str(value) for key, value in response.headers.items()
                    if key.lower() in {"etag", "last-modified", "content-length", "content-type"}}
    headers_path.write_text(json.dumps(safe_headers, ensure_ascii=False), encoding="utf-8")
    return CvmArchive(kind, year, url, content, datetime.now(timezone.utc), safe_headers,
                      hashlib.sha256(content).hexdigest())


def archive_manifest(archive: CvmArchive) -> dict:
    files: list[dict] = []
    if archive.url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            files = [{"name": item.filename, "bytes": item.file_size,
                      "crc": item.CRC} for item in zipped.infolist()]
    return {"source_url": archive.url, "archive_sha256": archive.sha256,
            "kind": archive.kind, "year": archive.year, "files": files}


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _num(value) -> float | None:
    raw = _text(value)
    if raw is None:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _ratio(value) -> float | None:
    number = _num(value)
    if number is None:
        return None
    if 1 < number <= 100:
        number /= 100.0
    return number if 0 <= number <= 1 else None


def _date(value) -> date | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def _published(value) -> datetime | None:
    day = _date(value)
    if day is None:
        return None
    local = datetime.combine(day, time(23, 59, 59), tzinfo=ZoneInfo("America/Sao_Paulo"))
    return local.astimezone(timezone.utc)


def _read_zip(archive: CvmArchive) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        for name in zipped.namelist():
            if not name.lower().endswith(".csv"):
                continue
            stem = Path(name).stem
            stem = re.sub(r"_\d{4}$", "", stem)
            with zipped.open(name) as stream:
                result[stem] = pd.read_csv(stream, sep=";", encoding="latin-1", dtype=str,
                                           keep_default_na=False, low_memory=False)
    return result


def _version(value) -> int:
    number = _num(value)
    return int(number) if number is not None else 1


def _general_context(tables: dict[str, pd.DataFrame], prefix: str,
                     ticker_by_cnpj: dict[str, str], collected_at: datetime) -> dict:
    general = tables.get(f"{prefix}_geral", pd.DataFrame())
    contexts: dict[tuple[str, date], dict] = {}
    for row in general.to_dict("records"):
        cnpj = _digits(row.get("CNPJ_Fundo_Classe"))
        ticker = ticker_by_cnpj.get(cnpj)
        reference = _date(row.get("Data_Referencia"))
        if not ticker or reference is None:
            continue
        key = (cnpj, reference)
        version = _version(row.get("Versao"))
        if key in contexts and contexts[key]["version"] > version:
            continue
        published = _published(row.get("Data_Entrega"))
        contexts[key] = {"ticker": ticker, "cnpj": cnpj, "reference": reference,
                         "version": version, "published": published,
                         "available": published or collected_at, "general": row}
    return contexts


def _group_rows(frame: pd.DataFrame | None, contexts: dict) -> dict[tuple[str, date], list[dict]]:
    grouped: dict[tuple[str, date], list[dict]] = defaultdict(list)
    if frame is None or frame.empty:
        return grouped
    for row in frame.to_dict("records"):
        key = (_digits(row.get("CNPJ_Fundo_Classe")), _date(row.get("Data_Referencia")))
        context = contexts.get(key)
        if context and _version(row.get("Versao")) == context["version"]:
            grouped[key].append(row)
    return grouped


def _observation(context: dict, metric: str, value, *, source: str,
                 raw_payload_id: int | None, metadata: dict | None = None) -> dict | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    published = context.get("published")
    row = metric_observation(
        ticker=context["ticker"], metric_name=metric, value=value,
        reference_date=context["reference"], available_at=context["available"],
        source=source, raw_payload_id=raw_payload_id,
        vintage=f"{source}:{context['reference']}:v{context['version']}",
        metadata=metadata, source_published_at=published,
        availability_quality=("verified_publication" if published else "first_observed_proxy"))
    row["source_url"] = metadata.get("source_url") if metadata else None
    return row


def _exposures(context: dict, exposure_type: str, amounts: dict[str, float], *,
               source: str, raw_payload_id: int | None, metadata: dict | None = None) -> list[dict]:
    positive = {str(key): float(value) for key, value in amounts.items()
                if key and value is not None and value > 0}
    total = sum(positive.values())
    if total <= 0:
        return []
    published = context.get("published")
    knowledge = published or context["available"]
    return [{
        "ticker": context["ticker"], "exposure_type": exposure_type,
        "exposure_name": name[:300], "exposure_weight": value / total,
        "reference_date": context["reference"].isoformat(),
        "available_at": context["available"].isoformat(),
        "knowledge_at": knowledge.isoformat(),
        "availability_quality": "verified_publication" if published else "first_observed_proxy",
        "vintage": f"{source}:{context['reference']}:v{context['version']}",
        "source": source, "raw_payload_id": raw_payload_id,
        "metadata_json": json.dumps({"declared_total": total, **(metadata or {})},
                                    ensure_ascii=False, default=str),
    } for name, value in positive.items()]


def _append(rows: list[dict], value: dict | None) -> None:
    if value is not None:
        rows.append(value)


def parse_monthly(archive: CvmArchive, ticker_by_cnpj: dict[str, str],
                  raw_payload_id: int | None = None) -> dict:
    tables = _read_zip(archive)
    contexts = _general_context(tables, "inf_mensal_fii", ticker_by_cnpj,
                                archive.collected_at)
    complements = _group_rows(tables.get("inf_mensal_fii_complemento"), contexts)
    assets = _group_rows(tables.get("inf_mensal_fii_ativo_passivo"), contexts)
    observations, exposures = [], []
    source = "cvm_informe_mensal"
    for key, context in contexts.items():
        comp = (complements.get(key) or [{}])[0]
        asset = (assets.get(key) or [{}])[0]
        total_assets = _num(comp.get("Valor_Ativo")) or _num(asset.get("Valor_Ativo"))
        liabilities = _num(asset.get("Total_Passivo"))
        liquidity = sum(_num(asset.get(field)) or 0 for field in
                        ("Disponibilidades", "Titulos_Publicos", "Titulos_Privados", "Fundos_Renda_Fixa"))
        metrics = {
            "nav_per_share": _num(comp.get("Valor_Patrimonial_Cotas")),
            "equity": _num(comp.get("Patrimonio_Liquido")),
            "total_investors": _num(comp.get("Total_Numero_Cotistas")),
            "dy_patrimonial_mes": _ratio(comp.get("Percentual_Dividend_Yield_Mes")),
            "total_assets": total_assets,
            "total_liabilities": liabilities,
            # Passivo/ativo pode superar 1 em fundo com PL negativo; isso é
            # risco econômico extremo, não erro de domínio.
            "leverage": (liabilities / total_assets
                         if total_assets and total_assets > 0
                         and liabilities is not None and liabilities >= 0 else None),
            "liquidity_ratio": liquidity / total_assets if total_assets and liquidity >= 0 else None,
        }
        for metric, value in metrics.items():
            _append(observations, _observation(
                context, metric, value, source=source, raw_payload_id=raw_payload_id,
                metadata={"source_url": archive.url, "archive_sha256": archive.sha256}))
        classes = {
            "real_estate": sum(_num(asset.get(field)) or 0 for field in
                               ("Direitos_Bens_Imoveis", "Terrenos", "Imoveis_Renda_Acabados",
                                "Imoveis_Renda_Construcao", "Imoveis_Venda_Acabados",
                                "Imoveis_Venda_Construcao", "Outros_Direitos_Reais")),
            "credit": sum(_num(asset.get(field)) or 0 for field in
                          ("CRI", "CRI_CRA", "LCI", "LCI_LCA", "LIG", "Debentures")),
            "fund_holdings": sum(_num(asset.get(field)) or 0 for field in
                                 ("FII", "FIP", "FDIC", "Fundo_Acoes", "Outras_Cotas_FI")),
            "cash": liquidity,
        }
        exposures.extend(_exposures(context, "asset_class", classes, source=source,
                                    raw_payload_id=raw_payload_id))
    return {"observations": observations, "exposures": exposures,
            "documents": [], "contexts": len(contexts)}


def parse_quarterly(archive: CvmArchive, ticker_by_cnpj: dict[str, str],
                    raw_payload_id: int | None = None) -> dict:
    tables = _read_zip(archive)
    contexts = _general_context(tables, "inf_trimestral_fii", ticker_by_cnpj,
                                archive.collected_at)
    source = "cvm_informe_trimestral"
    comp = _group_rows(tables.get("inf_trimestral_fii_complemento"), contexts)
    assets = _group_rows(tables.get("inf_trimestral_fii_ativo"), contexts)
    properties = _group_rows(tables.get("inf_trimestral_fii_imovel"), contexts)
    tenants = _group_rows(tables.get("inf_trimestral_fii_imovel_renda_acabado_inquilino"), contexts)
    contracts = _group_rows(tables.get("inf_trimestral_fii_imovel_renda_acabado_contrato"), contexts)
    results = _group_rows(tables.get("inf_trimestral_fii_resultado_contabil_financeiro"), contexts)
    observations, exposures = [], []
    expiry_fields = [
        "Percentual_Vencimento_Receita_FII_Faixa_Ate_3Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_3a6Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_6a9Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_9a12Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_12a15Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_15a18Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_18a21Meses",
        "Percentual_Vencimento_Receita_FII_Faixa_21a24Meses",
    ]
    indexer_fields = {"IPCA": "Percentual_Indexador_Receita_FII_IPCA",
                      "IGP-M": "Percentual_Indexador_Receita_FII_IGPM",
                      "INCC": "Percentual_Indexador_Receita_FII_INCC",
                      "INPC": "Percentual_Indexador_Receita_FII_INPC"}
    for key, context in contexts.items():
        complement = (comp.get(key) or [{}])[0]
        expiry = sum(_ratio(complement.get(field)) or 0 for field in expiry_fields)
        expiry = min(expiry, 1.0) if any(_ratio(complement.get(f)) is not None for f in expiry_fields) else None
        indexers = {name: _ratio(complement.get(field)) or 0 for name, field in indexer_fields.items()}
        indexers = {name: value for name, value in indexers.items() if value > 0}
        indexer_total = sum(indexers.values())
        normalized_indexers = ({name: value / indexer_total for name, value in indexers.items()}
                               if indexer_total else {})
        diversification = (1 - sum(value * value for value in normalized_indexers.values())
                           if len(normalized_indexers) > 1 else (0.0 if normalized_indexers else None))
        _append(observations, _observation(context, "lease_expiry_concentration_24m", expiry,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": archive.url, "formula": "sum_revenue_expiry_bands_0_24m"}))
        _append(observations, _observation(context, "indexer_diversification", diversification,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": archive.url, "formula": "1-HHI"}))
        exposures.extend(_exposures(context, "indexer", normalized_indexers, source=source,
                                    raw_payload_id=raw_payload_id))

        financial = assets.get(key) or []
        issuer_amounts: dict[str, float] = defaultdict(float)
        issue_amounts: dict[str, float] = defaultdict(float)
        maturity_value = maturity_years = total_value = 0.0
        for row in financial:
            value = _num(row.get("Valor")) or 0
            if value <= 0:
                continue
            total_value += value
            issuer = _text(row.get("CNPJ_Emissor")) or _text(row.get("Emissor")) or "não informado"
            issuer_amounts[issuer] += value
            issue = "|".join(filter(None, (_text(row.get("CNPJ_Emissor")), _text(row.get("Emissao")),
                                           _text(row.get("Serie")), _text(row.get("Nome_Ativo")))))
            issue_amounts[issue or issuer] += value
            maturity = _date(row.get("Data_Vencimento"))
            if maturity:
                maturity_value += value
                maturity_years += value * max((maturity - context["reference"]).days / 365.25, 0)
        exposures.extend(_exposures(context, "issuer", issuer_amounts, source=source,
                                    raw_payload_id=raw_payload_id))
        issuer_weights = [value / sum(issuer_amounts.values()) for value in issuer_amounts.values()] if issuer_amounts else []
        issue_weights = [value / sum(issue_amounts.values()) for value in issue_amounts.values()] if issue_amounts else []
        for metric, value, formula in (
            ("issuer_diversification", 1 - sum(x*x for x in issuer_weights) if issuer_weights else None, "1-HHI"),
            ("issuance_concentration", max(issue_weights) if issue_weights else None, "max_issue_share"),
            ("duration_anos", maturity_years / maturity_value if maturity_value and maturity_value / max(total_value, 1) >= .60 else None,
             "weighted_years_to_maturity;coverage>=60%"),
        ):
            _append(observations, _observation(context, metric, value, source=source,
                                               raw_payload_id=raw_payload_id,
                                               metadata={"source_url": archive.url, "formula": formula}))

        prop_rows = properties.get(key) or []
        vacancy_num = delinquency_num = weight_total = 0.0
        property_amounts: dict[str, float] = defaultdict(float)
        for row in prop_rows:
            weight = (_ratio(row.get("Percentual_Receitas_FII")) or
                      _ratio(row.get("Percentual_Imovel_Total_Investido")) or 0)
            if weight <= 0:
                continue
            weight_total += weight
            vacancy_num += weight * (_ratio(row.get("Percentual_Vacancia")) or 0)
            delinquency_num += weight * (_ratio(row.get("Percentual_Inadimplencia")) or 0)
            property_amounts[_text(row.get("Nome_Imovel")) or _text(row.get("Endereco")) or "não informado"] += weight
        for metric, value in (
            ("vacancia_fisica", vacancy_num / weight_total if weight_total else None),
            ("delinquency", delinquency_num / weight_total if weight_total else None),
            ("property_diversification",
             1 - sum((amount / sum(property_amounts.values())) ** 2
                     for amount in property_amounts.values())
             if property_amounts and sum(property_amounts.values()) >= .60 else None),
        ):
            _append(observations, _observation(context, metric, value, source=source,
                                               raw_payload_id=raw_payload_id,
                                               metadata={"source_url": archive.url, "weight": "revenue_or_invested_share"}))

        tenant_rows = tenants.get(key) or []
        tenant_shares = [_ratio(row.get("Percentual_Receitas_FII")) for row in tenant_rows]
        tenant_shares = [value for value in tenant_shares if value is not None and value > 0]
        _append(observations, _observation(context, "tenant_concentration",
                                           max(tenant_shares) if tenant_shares else None,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": archive.url, "formula": "max_disclosed_tenant_revenue_share"}))
        sectors: dict[str, float] = defaultdict(float)
        for row in tenant_rows:
            sector, share = _text(row.get("Setor_Atuacao")), _ratio(row.get("Percentual_Receitas_FII"))
            if sector and share:
                sectors[sector] += share
        exposures.extend(_exposures(context, "sector", sectors, source=source,
                                    raw_payload_id=raw_payload_id,
                                    metadata={"scope": "tenant_sector"}))

        contract_texts = sorted({_text(row.get("Caracteristicas_Contratuais"))
                                 for row in contracts.get(key) or []
                                 if _text(row.get("Caracteristicas_Contratuais"))})
        _append(observations, _observation(context, "contract_profile_text", contract_texts or None,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": archive.url, "requires_qualitative_review": True}))

        result = (results.get(key) or [{}])[0]
        income = sum(max(_num(result.get(field)) or 0, 0) for field in
                     ("Receita_Aluguel_Investimento_Financeiro", "Receita_Juros_TVM_Financeiro",
                      "Receita_Juros_Aplicacao_Financeiro"))
        admin_fee_raw = _num(result.get("Taxa_Administracao_Financeiro"))
        admin_fee = abs(admin_fee_raw) if admin_fee_raw is not None else None
        efficiency = (max(0.0, min(1.0, 1 - admin_fee / income))
                      if income > 0 and admin_fee is not None else None)
        _append(observations, _observation(context, "management_efficiency", efficiency,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": archive.url, "formula": "1-admin_fee/positive_operating_income"}))
    return {"observations": observations, "exposures": exposures,
            "documents": [], "contexts": len(contexts)}


def _truthy(value) -> bool | None:
    raw = (_text(value) or "").lower()
    if not raw:
        return None
    if raw in {"s", "sim", "true", "1", "condenado"}:
        return True
    if raw in {"n", "não", "nao", "false", "0", "nenhuma", "nenhum"}:
        return False
    return None


def parse_annual(archive: CvmArchive, ticker_by_cnpj: dict[str, str],
                 raw_payload_id: int | None = None) -> dict:
    tables = _read_zip(archive)
    contexts = _general_context(tables, "inf_anual_fii", ticker_by_cnpj,
                                archive.collected_at)
    source = "cvm_informe_anual"
    complements = _group_rows(tables.get("inf_anual_fii_complemento"), contexts)
    directors = _group_rows(tables.get("inf_anual_fii_diretor_responsavel"), contexts)
    representatives = _group_rows(tables.get("inf_anual_fii_representante_cotista"), contexts)
    observations = []
    disclosure_fields = (
        "Nome_Gestor", "CNPJ_Gestor", "Nome_Auditor_Independente", "CNPJ_Auditor_Independente",
        "Nome_Custodiante", "Politica_Divulgacao_Fato_Relevante", "Politica_Negociacao_Cotas",
        "Politica_Remuneracao", "Politica_Exercicio_Direito_Voto", "Meio_Comunicacao_Cotistas")
    for key, context in contexts.items():
        general = context["general"]
        complement = (complements.get(key) or [{}])[0]
        filled = sum(bool(_text(complement.get(field))) for field in disclosure_fields)
        metrics = {
            "mandate": _text(general.get("Mandato")),
            "management_type": _text(general.get("Tipo_Gestao")),
            "governance_disclosure_quality": filled / len(disclosure_fields),
        }
        people = (directors.get(key) or []) + (representatives.get(key) or [])
        convictions = [_truthy(row.get("Condenacao_Processo_CVM")) for row in people]
        convictions += [_truthy(row.get("Condenacao_Criminal")) for row in people]
        convictions = [flag for flag in convictions if flag is not None]
        if convictions:
            metrics["governance_integrity"] = 0.0 if any(convictions) else 1.0
        holdings = sum(_num(row.get("Quantidade_Cotas_FII_Detidas")) or 0 for row in people)
        metrics["management_alignment_units"] = holdings if holdings > 0 else None
        for metric, value in metrics.items():
            _append(observations, _observation(context, metric, value, source=source,
                                               raw_payload_id=raw_payload_id,
                                               metadata={"source_url": archive.url,
                                                         "formula": "structured_annual_disclosure"}))
    return {"observations": observations, "exposures": [],
            "documents": [], "contexts": len(contexts)}


def parse_financials(archive: CvmArchive, ticker_by_cnpj: dict[str, str],
                     raw_payload_id: int | None = None) -> dict:
    frame = pd.read_csv(io.BytesIO(archive.content), sep=";", encoding="latin-1", dtype=str,
                        keep_default_na=False, low_memory=False)
    observations, documents = [], []
    source = "cvm_dfin"
    for row in frame.to_dict("records"):
        ticker = ticker_by_cnpj.get(_digits(row.get("CNPJ_Fundo_Classe")))
        reference = _date(row.get("Data_Referencia"))
        if not ticker or reference is None:
            continue
        published = _published(row.get("Data_Entrega"))
        context = {"ticker": ticker, "reference": reference,
                   "version": _version(row.get("Versao")), "published": published,
                   "available": published or archive.collected_at}
        url = _text(row.get("Link_Download"))
        auditor = _text(row.get("Parecer_Auditor"))
        normalized = (auditor or "").lower()
        opinion_quality = None
        if auditor:
            if "absten" in normalized or "advers" in normalized:
                opinion_quality = 0.0
            elif "com ressalva" in normalized:
                opinion_quality = .4
            elif "com ênfase" in normalized or "com enfase" in normalized:
                opinion_quality = .8
            elif "sem ressalva" in normalized:
                opinion_quality = 1.0
        _append(observations, _observation(context, "auditor_opinion", auditor,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": url or archive.url}))
        _append(observations, _observation(context, "auditor_opinion_quality", opinion_quality,
                                           source=source, raw_payload_id=raw_payload_id,
                                           metadata={"source_url": url or archive.url,
                                                     "formula": "categorical_auditor_opinion_v1"}))
        if url:
            documents.append({
                "ticker": ticker, "document_type": "DFIN",
                "natural_key": f"{ticker}|DFIN|{reference}|v{context['version']}",
                "reference_date": reference, "source_published_at": published,
                "first_observed_at": archive.collected_at, "source_url": url,
                "raw_payload_id": raw_payload_id,
                "metadata": {"version": context["version"], "archive_sha256": archive.sha256},
            })
    return {"observations": observations, "exposures": [], "documents": documents,
            "contexts": len({row["ticker"] for row in documents})}


def parse_eventual(archive: CvmArchive, ticker_by_cnpj: dict[str, str],
                   raw_payload_id: int | None = None) -> dict:
    frame = pd.read_csv(io.BytesIO(archive.content), sep=";", encoding="latin-1", dtype=str,
                        keep_default_na=False, low_memory=False)
    source = "cvm_eventuais"
    documents: list[dict] = []
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in frame.to_dict("records"):
        # O arquivo agrega todos os fundos; FIAGRO/FIDC/FIP não podem contaminar
        # a metodologia de FII mesmo quando um CNPJ legado foi mapeado.
        fund_type = (_text(row.get("TP_FUNDO_CLASSE")) or "").upper()
        if "FII" not in fund_type or "FIAGRO" in fund_type:
            continue
        ticker = ticker_by_cnpj.get(_digits(row.get("CNPJ_FUNDO_CLASSE")))
        reference = _date(row.get("DT_COMPTC")) or _date(row.get("DT_RECEB"))
        published = _published(row.get("DT_RECEB"))
        url = _text(row.get("LINK_ARQ"))
        if not ticker or reference is None or not url:
            continue
        kind = (_text(row.get("TP_DOC")) or "EVENTUAL")[:80]
        natural = (_text(row.get("ID_DOC")) or _text(row.get("NM_ARQ")) or url)
        documents.append({
            "ticker": ticker, "document_type": kind,
            "natural_key": f"{ticker}|{kind}|{natural}",
            "reference_date": reference, "source_published_at": published,
            "first_observed_at": archive.collected_at, "source_url": url,
            "raw_payload_id": raw_payload_id,
            "metadata": {"source": source, "archive_sha256": archive.sha256},
        })
        grouped[(ticker, reference.year)].append(row)
    observations: list[dict] = []
    for (ticker, year), rows in grouped.items():
        received = [_published(row.get("DT_RECEB")) for row in rows]
        published = max((value for value in received if value), default=None)
        references = [_date(row.get("DT_COMPTC")) or _date(row.get("DT_RECEB"))
                      for row in rows]
        reference = max((value for value in references if value),
                        default=date(year, 1, 1))
        # Alguns registros trazem competencia posterior ao recebimento. Em uma
        # serie PIT, a referencia nunca pode ultrapassar o conhecimento do dado.
        known_at = published or archive.collected_at
        reference = min(reference, known_at.date())
        context = {"ticker": ticker, "reference": reference, "version": 1,
                   "published": published, "available": known_at}
        managerial = sum((_text(row.get("TP_DOC")) or "").upper() == "RELAT GERENCIAL"
                         for row in rows)
        valid_links = sum(bool(_text(row.get("LINK_ARQ"))) for row in rows) / len(rows)
        regularity = min(managerial / 12.0, 1.0)
        disclosure_quality = .85 * regularity + .15 * valid_links
        _append(observations, _observation(
            context, "cvm_event_quality", disclosure_quality, source=source,
            raw_payload_id=raw_payload_id,
            metadata={"source_url": archive.url, "formula": ".85*monthly_report_regularity+.15*link_coverage",
                      "documents": len(rows), "managerial_reports": managerial}))
        _append(observations, _observation(
            context, "cvm_event_document_count", float(len(rows)), source=source,
            raw_payload_id=raw_payload_id, metadata={"source_url": archive.url}))
    return {"observations": observations, "exposures": [], "documents": documents,
            "contexts": len(grouped)}


PARSERS = {"monthly": parse_monthly, "quarterly": parse_quarterly,
           "annual": parse_annual, "financials": parse_financials,
           "eventual": parse_eventual}


def parse_archive(archive: CvmArchive, ticker_by_cnpj: dict[str, str],
                  raw_payload_id: int | None = None) -> dict:
    return PARSERS[archive.kind](archive, ticker_by_cnpj, raw_payload_id)


def ingest_cvm_structured(*, years: int = 5,
                          kinds: tuple[str, ...] = tuple(ARCHIVES)) -> dict:
    """Coleta, normaliza e persiste a cobertura estruturada oficial da CVM."""
    from email.utils import parsedate_to_datetime
    from sqlalchemy import text
    from data_pipeline.market import repository as repo
    from data_pipeline.market.fii_ingest import (_engine, _persist_document_discoveries,
                                                  audit_methodology_v4_data,
                                                  record_validation_readiness,
                                                  snapshot_methodology_v4)

    progress = {"archives": 0, "missing_archives": 0, "observations": 0,
                "exposures": 0, "documents": 0, "lineage": 0, "errors": [],
                "by_kind": {}}
    engine = _engine()
    if engine is None:
        return {**progress, "status": "failed", "errors": ["banco indisponível"]}
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ticker, regexp_replace(cnpj, '\\D', '', 'g') cnpj
            FROM market.fiis WHERE cnpj IS NOT NULL
        """)).fetchall()
    ticker_by_cnpj = {str(cnpj): str(ticker) for ticker, cnpj in rows if cnpj}
    current = datetime.now(timezone.utc).year
    first = max(2016, current - max(int(years), 1) + 1)
    for kind in kinds:
        kind_progress = {"archives": 0, "observations": 0, "exposures": 0,
                         "documents": 0, "contexts": 0}
        for year in range(first, current + 1):
            try:
                archive = fetch_archive(kind, year)
                if archive is None:
                    progress["missing_archives"] += 1
                    continue
                published = None
                if archive.headers.get("Last-Modified"):
                    try:
                        published = parsedate_to_datetime(archive.headers["Last-Modified"])
                    except (TypeError, ValueError):
                        published = None
                with engine.begin() as conn:
                    raw_id = repo.save_raw_payload(
                        conn, None, f"cvm_fii_{kind}", archive_manifest(archive),
                        request_params={"year": year, "url": archive.url},
                        response_headers=archive.headers, http_status=200,
                        collected_at=archive.collected_at, source_published_at=published,
                        request_fingerprint=hashlib.sha256(
                            f"cvm_fii_{kind}|{year}".encode()).hexdigest(),
                        source=SOURCE)
                    parsed = parse_archive(archive, ticker_by_cnpj, raw_id)
                    kind_progress["observations"] += repo.upsert(
                        conn, "fii_metric_observations", parsed["observations"])
                    kind_progress["exposures"] += repo.upsert(
                        conn, "fii_exposures", parsed["exposures"])
                    kind_progress["documents"] += _persist_document_discoveries(
                        conn, parsed["documents"])
                    progress["lineage"] += repo.record_lineage_for_raw_payload(conn, raw_id)
                kind_progress["archives"] += 1
                kind_progress["contexts"] += int(parsed.get("contexts") or 0)
            except Exception as exc:
                progress["errors"].append({"kind": kind, "year": year,
                                           "error": str(exc)[:500]})
        progress["by_kind"][kind] = kind_progress
        for field in ("archives", "observations", "exposures", "documents"):
            progress[field] += kind_progress[field]
    audit = audit_methodology_v4_data()
    progress["audit"] = audit
    progress["validation"] = record_validation_readiness(audit)
    progress["snapshot"] = snapshot_methodology_v4()
    progress["status"] = "completed" if not progress["errors"] else "partial"
    return progress
