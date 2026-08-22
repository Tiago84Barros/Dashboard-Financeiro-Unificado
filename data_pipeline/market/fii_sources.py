"""Registro de fontes e planos de coleta específicos por categoria de FII."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from core.fii_methodology import source_plan_for_type

SOURCE_REGISTRY = {
    "brapi_quote_monthly": {
        "license": "conforme plano Brapi", "access": "api", "quality": .80,
        "scope": "preço, proventos e liquidez pelo endpoint legado",
    },
    "brapi_fii_indicators": {
        "license": "Brapi Pro", "access": "api/v2/fii/indicators", "quality": .80,
        "scope": "tipo, P/VP, DY, NAV, patrimônio, cotistas, mandato e administração",
        "url": "https://brapi.dev/docs/fiis/indicadores",
    },
    "brapi_fii_reports": {
        "license": "Brapi Pro", "access": "api/v2/fii/reports", "quality": .80,
        "scope": "informes mensais normalizados, taxas, passivos e composição patrimonial",
        "url": "https://brapi.dev/docs/fiis",
    },
    "brapi_fii_properties": {
        "license": "Brapi Pro", "access": "api/v2/fii/properties", "quality": .80,
        "scope": "imóveis, área, vacância, inadimplência e participação na receita",
        "url": "https://brapi.dev/docs/fiis",
    },
    "brapi_fii_portfolio": {
        "license": "Brapi Pro", "access": "api/v2/fii/portfolio", "quality": .80,
        "scope": "CRIs, emissores, holdings de FoFs e composição trimestral",
        "url": "https://brapi.dev/docs/fiis/carteira",
    },
    "cvm_informe_mensal": {
        "license": "dados abertos CVM", "access": "csv", "quality": .95,
        "scope": "patrimônio, cotas, cotistas e campos regulatórios mensais",
        "url": "https://dados.cvm.gov.br/dataset/fii-doc-inf_mensal",
    },
    "cvm_informe_trimestral": {
        "license": "dados abertos CVM", "access": "csv", "quality": .95,
        "scope": "informes trimestrais e composição divulgada",
        "url": "https://dados.cvm.gov.br/dataset/fii-doc-inf_trimestral",
    },
    "cvm_eventuais": {
        "license": "dados abertos CVM", "access": "csv/documentos", "quality": .95,
        "scope": "fatos relevantes, emissões, regulamentos, ratings e comunicados",
        "url": "https://dados.cvm.gov.br/dataset/fi-doc-eventual",
    },
    "cvm_informe_anual": {
        "license": "dados abertos CVM", "access": "documentos", "quality": .95,
        "scope": "governança, mandato, taxas e conflitos divulgados",
    },
    "cvm_cri_monthly": {
        "license": "dados abertos CVM", "access": "csv/zip", "quality": .95,
        "scope": "duration, LTV, rating, subordinacao, inadimplencia, indexadores e devedores por CRI, conciliados por chave regulatoria",
        "url": "https://dados.cvm.gov.br/dataset/securit-doc-inf_mensal_cri",
    },
    "public_fii_documents": {
        "license": "documentos públicos", "access": "CVM/Brapi + PDF/OCR", "quality": .70,
        "scope": "WAULT, contratos, locatários, CRIs, LTV, rating, indexadores e governança; exige evidência e revisão",
        "url": "https://dados.cvm.gov.br/dataset/?groups=fundos-de-investimento",
    },
    "cvm_fund_registry": {
        "license": "dados abertos CVM", "access": "zip/csv", "quality": .95,
        "scope": "registro, início, situação, cancelamento, gestor e administrador",
        "url": "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip",
    },
    "b3_cotahist": {
        "license": "dados públicos B3", "access": "zip/fixed-width", "quality": .95,
        "scope": "security master e cotações históricas, inclusive fundos fora do universo atual",
        "url": "https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista/cotacoes-historicas/",
    },
}


def collection_plan(fii_type: str) -> dict[str, Any]:
    plan = source_plan_for_type(fii_type)
    return {kind: [{"source": name, **SOURCE_REGISTRY.get(name, {})} for name in names]
            for kind, names in plan.items()}


def metric_observation(*, ticker: str, metric_name: str, value: Any,
                       reference_date: date, available_at: datetime,
                       source: str, raw_payload_id: int | None = None,
                       vintage: str | None = None, metadata: dict | None = None,
                       source_published_at: datetime | None = None,
                       availability_quality: str = "first_observed_proxy",
                       source_release_id: int | None = None) -> dict[str, Any]:
    """Normaliza uma observação sem perder o instante em que ficou conhecida."""
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=timezone.utc)
    numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    textual = value if isinstance(value, str) else None
    structured = value if isinstance(value, (dict, list)) else None
    if source_published_at is not None and source_published_at.tzinfo is None:
        source_published_at = source_published_at.replace(tzinfo=timezone.utc)
    knowledge_at = (source_published_at if availability_quality == "verified_publication"
                    and source_published_at is not None else available_at)
    semantic = json.dumps({
        "ticker": ticker.upper(), "metric": metric_name, "value": value,
        "reference_date": reference_date.isoformat(), "source": source,
        "vintage": vintage or available_at.date().isoformat(),
    }, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
    return {
        "ticker": ticker.upper(), "metric_name": metric_name,
        "value_numeric": numeric, "value_text": textual,
        "value_json": json.dumps(structured, ensure_ascii=False) if structured is not None else None,
        "reference_date": reference_date.isoformat(), "available_at": available_at.isoformat(),
        "vintage": vintage or available_at.date().isoformat(), "source": source,
        "raw_payload_id": raw_payload_id,
        "source_published_at": source_published_at.isoformat() if source_published_at else None,
        "knowledge_at": knowledge_at.isoformat(),
        "availability_quality": availability_quality,
        "content_hash": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
        "source_release_id": source_release_id,
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, default=str),
        "quality_status": "observed",
    }
