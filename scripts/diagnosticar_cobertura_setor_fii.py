"""Diz por que cada fundo da carteira montada não tinha setor na data da decisão.

Lê a validação PIT do warehouse local. As carteiras recusadas saem de
``optimizer_failures`` e os controles aprovados de ``fii_backtest_positions``.
Para cada fundo sem exposição ``sector`` conhecida até a decisão, o script
classifica o buraco:

- ``a_ausente``: nenhum informe trimestral do fundo conhecido até a data
  (arquivo da CVM ausente ou não ingerido);
- ``a_conhecido_depois``: há setor para aquela referência, mas o
  ``knowledge_at`` é posterior à decisão (entrega tardia ou proxy de coleta);
- ``b_layout_ou_sem_inquilino``: o informe trimestral do fundo entrou
  (emissor, indexador ou região) e mesmo assim não veio setor. É layout que o
  parser não lê, ou fundo que não declarou inquilino;
- ``c_sem_imovel_para_renda``: ``pct_imoveis_renda`` abaixo de 20%. É fundo de
  obra, terreno ou cotas, e a regra de aplicabilidade já o tira da máscara.

Uso:  python scripts/diagnosticar_cobertura_setor_fii.py [--run ID]
      [--controles 2019-06-30,2023-06-30]
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

_PISO_RENDA = .20


def _carteiras(conn, run_id: int, controles: list[str]) -> dict[str, list[str]]:
    from sqlalchemy import text

    metrics = conn.execute(text(
        "SELECT metrics_json FROM market.fii_validation_runs WHERE id=:id"
    ), {"id": run_id}).scalar_one()
    backtest = (metrics or {}).get("backtest") or {}
    carteiras: dict[str, list[str]] = {}
    for falha in backtest.get("optimizer_failures") or []:
        pesos = (falha.get("candidate_pool") or {}).get("selected_weights") or {}
        if "sector" in str(falha.get("reason") or "") and pesos:
            carteiras[str(falha["decision_date"])] = sorted(pesos)
    for dia in controles:
        tickers = conn.execute(text("""
            SELECT DISTINCT p.ticker FROM market.fii_backtest_positions p
            JOIN market.fii_backtest_runs r ON r.id = p.backtest_run_id
            WHERE r.validation_run_id = :id AND p.decision_date = :dia
              AND p.weight > 0
        """), {"id": run_id, "dia": dia}).scalars().all()
        carteiras[f"{dia} (controle)"] = sorted(tickers)
    return carteiras


def _fatos(conn, ticker: str, dia: date) -> dict:
    from sqlalchemy import text

    corte = {"t": ticker, "d": dia}
    filtro = ("ticker=:t AND availability_quality <> 'migration_baseline' "
              "AND reference_date <= :d")
    conhecido = filtro + " AND knowledge_at <= (CAST(:d AS date) + interval '1 day')"
    setor = conn.execute(text(
        f"SELECT max(reference_date) FROM market.fii_exposures "
        f"WHERE exposure_type='sector' AND {conhecido}"), corte).scalar()
    setor_tarde = conn.execute(text(
        f"SELECT min(knowledge_at) FROM market.fii_exposures "
        f"WHERE exposure_type='sector' AND {filtro}"), corte).scalar()
    trimestral = conn.execute(text(
        f"SELECT max(reference_date), string_agg(DISTINCT exposure_type, ',') "
        f"FROM market.fii_exposures WHERE source='cvm_informe_trimestral' "
        f"AND {conhecido}"), corte).first()
    renda = conn.execute(text(
        f"SELECT value_numeric, reference_date FROM market.fii_metric_observations "
        f"WHERE metric_name='pct_imoveis_renda' AND {conhecido} "
        f"ORDER BY reference_date DESC, knowledge_at DESC LIMIT 1"), corte).first()
    classes = conn.execute(text(f"""
        SELECT exposure_name, round(exposure_weight::numeric, 3)
        FROM market.fii_exposures
        WHERE exposure_type='asset_class' AND {conhecido}
          AND reference_date = (SELECT max(reference_date) FROM market.fii_exposures
                                WHERE exposure_type='asset_class' AND {conhecido})
    """), corte).all()
    tipo = conn.execute(text("SELECT tipo FROM market.fiis WHERE ticker=:t"),
                        corte).scalar()
    return {
        "tipo_cadastro_atual": tipo,
        "setor_ref": str(setor) if setor else None,
        "setor_primeiro_knowledge_at": str(setor_tarde) if setor_tarde else None,
        "trimestral_ref": str(trimestral[0]) if trimestral and trimestral[0] else None,
        "trimestral_tipos": trimestral[1] if trimestral else None,
        "pct_imoveis_renda": float(renda[0]) if renda and renda[0] is not None else None,
        "asset_class": {str(nome): float(peso) for nome, peso in classes},
    }


def _classifica(fatos: dict) -> str:
    if fatos["setor_ref"]:
        return "ok"
    renda = fatos["pct_imoveis_renda"]
    if renda is not None and renda < _PISO_RENDA:
        return "c_sem_imovel_para_renda"
    if fatos["setor_primeiro_knowledge_at"]:
        return "a_conhecido_depois"
    if not fatos["trimestral_ref"]:
        return "a_ausente"
    return "b_layout_ou_sem_inquilino"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, default=None,
                        help="id em market.fii_validation_runs; padrão: o mais recente")
    parser.add_argument("--controles", default="2019-06-30,2023-06-30")
    args = parser.parse_args()

    from sqlalchemy import create_engine, text

    from data_pipeline.market.fii_pit import _normalize_type, _type_from_asset_classes
    from scripts.publish_fii_selection_from_local import _warehouse_url

    engine = create_engine(_warehouse_url())
    controles = [dia.strip() for dia in args.controles.split(",") if dia.strip()]
    with engine.connect() as conn:
        run_id = args.run or conn.execute(text(
            "SELECT max(id) FROM market.fii_validation_runs")).scalar_one()
        cargas = conn.execute(text("""
            SELECT archive_kind, archive_year, status, parser_version, exposure_count
            FROM market.fii_cvm_archive_loads
            WHERE archive_kind IN ('monthly','quarterly')
            ORDER BY archive_kind, archive_year, parser_version
        """)).all()
        resultado: dict = {"validation_run_id": run_id, "periodos": {},
                           "cargas_cvm": [list(map(str, linha)) for linha in cargas]}
        contagem: dict[str, int] = {}
        for rotulo, tickers in _carteiras(conn, run_id, controles).items():
            dia = date.fromisoformat(rotulo[:10])
            fundos = {}
            for ticker in tickers:
                fatos = _fatos(conn, ticker, dia)
                # Mesmo critério do PIT: tipo pelo informe mensal, cadastro
                # só na falta dele. Setor não se aplica a papel nem a FoF.
                tipo = (_type_from_asset_classes(fatos["asset_class"])
                        or _normalize_type(fatos["tipo_cadastro_atual"]))
                fatos["tipo_pit"] = tipo
                if tipo not in {"tijolo", "hibrido"}:
                    continue
                causa = _classifica(fatos)
                if causa != "ok":
                    fundos[ticker] = {"causa": causa, **fatos}
                    contagem[causa] = contagem.get(causa, 0) + 1
            resultado["periodos"][rotulo] = {"carteira": tickers, "sem_setor": fundos}
        resultado["contagem_por_causa"] = contagem
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
