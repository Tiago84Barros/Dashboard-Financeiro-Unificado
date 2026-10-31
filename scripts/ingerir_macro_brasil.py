"""Ingere no armazém local a Selic meta, o IPCA (mensal e 12m) e o Focus do BCB.

Grava em ``macro_observations`` do banco macro do Docker (``MACRO_LOCAL_DB_URL``),
append-only, com os provedores ``bcb_sgs`` e ``bcb_focus``. Os indicadores
entram com categoria ``unmapped``: o contexto macro das carteiras
(``portfolio_context._ler_insumos``) ignora essa categoria, então estas séries
informam as LLMs sem mexer em score, ranking ou peso de carteira nenhuma.

Fontes: SGS 432, 433 e 13522 (REST e, como ela está fora do DNS desde
03/10/2026, o SOAP do ``core.bcb_sgs``); Focus pelo Olinda. O CDI (SGS 12) já
tem alvo próprio (``cdi_diario``) e não é repetido aqui.

Nunca escreve no Supabase. Saída 1 se alguma fonte falhar -- o que veio das
outras é gravado mesmo assim, e o relatório nomeia a que falhou.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_FREQ_SGS = {"daily": "daily", "monthly": "monthly"}


def coletar(hoje: date, desde: date | None = None) -> tuple[list[dict], dict[str, str]]:
    """``desde`` estende a janela do SGS para trás (carga do histórico).

    A rotina diária não passa ``desde``: a janela curta de cada série basta
    para a revisão, e o histórico já gravado fica (a tabela é append-only).
    """
    from core import macro_brasil as mb

    obs: list[dict] = []
    falhas: dict[str, str] = {}
    for codigo, (_nome, _unidade, _freq, janela) in mb.SERIES_SGS.items():
        inicio = hoje - timedelta(days=janela)
        if desde is not None:
            inicio = min(inicio, desde)
        serie, motivo = mb.baixar_sgs(codigo, inicio, hoje)
        if motivo:
            falhas[f"sgs_{codigo}"] = motivo
        obs += [{"provider": mb.PROVEDOR_SGS, "provider_code": codigo,
                 "reference_period": d, "vintage_date": None, "value": v,
                 "is_forecast": False} for d, v in sorted(serie.items())]
    focus, motivo = mb.baixar_focus(hoje)
    if motivo:
        falhas["focus"] = motivo
    obs += focus
    return obs, falhas


def gravar(engine, obs: list[dict], *, agora: datetime) -> int:
    from core import macro_brasil as mb
    from core.macro_data.models import MacroIndicator, MacroObservation
    from core.macro_data.repository import append_observation, upsert_indicator

    inseridas = 0
    with engine.begin() as conn:
        for codigo, (nome, unidade, freq, _janela) in mb.SERIES_SGS.items():
            upsert_indicator(conn, MacroIndicator(
                canonical_code=f"{mb.PROVEDOR_SGS}.{codigo}", provider_code=codigo,
                provider=mb.PROVEDOR_SGS, name=nome, unit=unidade,
                frequency=_FREQ_SGS[freq], category="unmapped", country_code="BRA",
                source_organization="Banco Central do Brasil (SGS)",
                source_url=f"https://www3.bcb.gov.br/sgspub/consultarvalores/"
                           f"consultarValoresSeries.do?method=getPagina&codigo={codigo}",
            ))
        for codigo, (nome, unidade) in mb.SERIES_FOCUS.items():
            upsert_indicator(conn, MacroIndicator(
                canonical_code=f"{mb.PROVEDOR_FOCUS}.{codigo}", provider_code=codigo,
                provider=mb.PROVEDOR_FOCUS, name=nome, unit=unidade,
                frequency="weekly", category="unmapped", country_code="BRA",
                source_organization="Banco Central do Brasil (Focus, Olinda)",
                source_url=mb.URL_FOCUS,
            ))
        for o in obs:
            inseridas += append_observation(conn, MacroObservation(
                provider=o["provider"], provider_code=o["provider_code"],
                country_code="BRA", reference_period=o["reference_period"],
                value=o["value"], retrieved_at=agora,
                vintage_date=o.get("vintage_date"), is_forecast=o["is_forecast"],
            ))
    return inseridas


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="baixa e relata, sem gravar no armazém")
    p.add_argument("--desde", type=date.fromisoformat, default=None,
                   help="AAAA-MM-DD: carrega o SGS desde esta data (histórico "
                        "dos cenários análogos, core.memoria_mercado.cenarios_macro)")
    args = p.parse_args(argv)

    agora = datetime.now(timezone.utc)
    obs, falhas = coletar(agora.date(), args.desde)
    relatorio = {"observacoes": len(obs), "falhas": falhas, "dry_run": args.dry_run}
    if not args.dry_run and obs:
        from core.macro_data.database import get_local_macro_engine

        engine = get_local_macro_engine()
        if engine is None:
            relatorio["erro"] = "MACRO_LOCAL_DB_URL não configurada -- grava no Docker local"
            print(json.dumps(relatorio, ensure_ascii=False), flush=True)
            return 2
        try:
            relatorio["inseridas"] = gravar(engine, obs, agora=agora)
        finally:
            engine.dispose()
    print(json.dumps(relatorio, ensure_ascii=False, default=str), flush=True)
    return 1 if falhas or not obs else 0


if __name__ == "__main__":
    raise SystemExit(main())
