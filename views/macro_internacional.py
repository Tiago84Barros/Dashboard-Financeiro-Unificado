"""Painel somente-leitura da camada macro internacional.

Não chama provedores: a rede é responsabilidade do job de ingestão. Assim a
tela informa claramente ausência, erro parcial e procedência sem expor chaves.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy import text

from core.config import settings
from core.macro_data.database import get_local_macro_engine
from design.componentes import container_pagina, secao_titulo
from design.lacunas import aviso_lacuna, detalhe_tecnico

_INDISPONIVEL = "Esta análise não está disponível no momento."


def _display_temporal(value: object, empty: str = "—") -> str:
    """Converte datas/horários do banco para texto homogêneo no Arrow."""
    if value is None or value == "":
        return empty
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def render() -> None:
    container_pagina(
        "Macro Internacional", "Indicadores oficiais, vintages e procedência", "🌍"
    )
    enabled = [
        p
        for p in (
            "fred",
            "world_bank",
            "imf",
            "oecd",
            "bis",
            "ecb",
            "eurostat",
            "trading_economics",
        )
        if settings.macro_enabled(p)
    ]
    if not enabled:
        st.info(_INDISPONIVEL)
        aviso_lacuna(
            "Nenhuma fonte macro internacional está habilitada (feature flags e séries no `.env`).",
            codigo="tela.macro.sem_fonte_habilitada",
        )
        return
    engine = get_local_macro_engine()
    if engine is None:
        st.info(_INDISPONIVEL)
        aviso_lacuna(
            "Banco macro local não configurado (`MACRO_LOCAL_DB_URL`; migração 065).",
            codigo="tela.macro.banco_local_ausente",
        )
        return
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
                SELECT i.provider, i.provider_code, i.name, i.country_code, i.unit, i.frequency,
                       MAX(o.reference_period) AS ultimo_periodo, MAX(o.retrieved_at) AS coletado_em,
                       COUNT(o.id) AS observacoes
                  FROM macro_indicators i
             LEFT JOIN macro_observations o ON o.provider=i.provider AND o.provider_code=i.provider_code
                 WHERE i.provider = ANY(:providers)
              GROUP BY i.provider, i.provider_code, i.name, i.country_code, i.unit, i.frequency
              ORDER BY i.provider, i.provider_code
            """),
                    {"providers": enabled},
                )
                .mappings()
                .all()
            )
    except Exception:
        st.info(_INDISPONIVEL)
        aviso_lacuna(
            "A estrutura macro não está disponível no banco (migração 065 pendente).",
            codigo="tela.macro.estrutura_ausente",
        )
        return
    secao_titulo(
        "Séries configuradas",
        "📊",
        "Último valor vem da ingestão; nenhuma estimativa é exibida.",
    )
    if not rows:
        aviso_lacuna(
            "Nenhuma série macro internacional foi ingerida (job `update_macro_international`).",
            codigo="tela.macro.sem_series_ingeridas",
        )
    else:
        st.dataframe(
            [
                {
                    "Fonte": r["provider"],
                    "Código": r["provider_code"],
                    "Indicador": r["name"],
                    "País": r["country_code"] or "—",
                    "Unidade": r["unit"],
                    "Frequência": r["frequency"],
                    "Último período": _display_temporal(r["ultimo_periodo"]),
                    "Coletado em UTC": _display_temporal(r["coletado_em"]),
                    "Observações": r["observacoes"],
                }
                for r in rows
            ],
            hide_index=True,
            width="stretch",
        )
        detalhe_tecnico(
            "Revisões permanecem no histórico. Backtests devem consultar apenas registros disponíveis na data simulada.",
            codigo="macro.revisoes_historico",
        )
    secao_titulo(
        "Saúde das fontes", "🩺", "Última verificação da ingestão, sem dados sensíveis."
    )
    try:
        with engine.connect() as conn:
            health_rows = (
                conn.execute(
                    text("""
                SELECT DISTINCT ON (provider) provider, available, detail, checked_at
                  FROM macro_provider_health_checks
                 WHERE provider = ANY(:providers)
                 ORDER BY provider, checked_at DESC
            """),
                    {"providers": enabled},
                )
                .mappings()
                .all()
            )
    except Exception:
        aviso_lacuna(
            "Ainda não há histórico operacional de saúde das fontes macro.",
            codigo="tela.macro.saude_fontes_indisponivel",
        )
    else:
        if not health_rows:
            aviso_lacuna(
                "Nenhuma verificação de saúde das fontes macro foi executada.",
                codigo="tela.macro.saude_fontes_sem_verificacao",
            )
        else:
            st.dataframe(
                [
                    {
                        "Fonte": row["provider"],
                        "Disponível": "sim" if row["available"] else "não",
                        "Detalhe": row["detail"],
                        "Verificado em UTC": _display_temporal(row["checked_at"]),
                    }
                    for row in health_rows
                ],
                hide_index=True,
                width="stretch",
            )
    if "trading_economics" not in enabled:
        return
    secao_titulo(
        "Calendário econômico", "🗓️", "Agenda opcional; horários gravados em UTC."
    )
    try:
        with engine.connect() as conn:
            releases = (
                conn.execute(
                    text("""
                SELECT country_code, event_name, scheduled_at, status, previous_value,
                       revised_previous_value, consensus_value, forecast_value, actual_value,
                       unit, importance, provider
                  FROM macro_releases
                 WHERE provider = 'trading_economics'
                 ORDER BY scheduled_at ASC
                 LIMIT 100
            """),
                )
                .mappings()
                .all()
            )
    except Exception:
        aviso_lacuna(
            "Não foi possível carregar o calendário macro local.",
            codigo="tela.macro.calendario_indisponivel",
        )
        return
    if not releases:
        aviso_lacuna(
            "Nenhuma divulgação do calendário macro foi coletada.",
            codigo="tela.macro.calendario_vazio",
        )
        return
    st.dataframe(
        [
            {
                "País": release["country_code"],
                "Evento": release["event_name"],
                "Horário (UTC)": _display_temporal(release["scheduled_at"]),
                "Status": release["status"],
                "Anterior": release["previous_value"],
                "Consenso": release["consensus_value"],
                "Previsão": release["forecast_value"],
                "Resultado": release["actual_value"],
                "Unidade": release["unit"] or "—",
                "Importância": release["importance"] if release["importance"] is not None else "—",
                "Fonte": release["provider"],
            }
            for release in releases
        ],
        hide_index=True,
        width="stretch",
    )
