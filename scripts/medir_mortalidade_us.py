# -*- coding: utf-8 -*-
"""Mede a mortalidade real do universo americano no indice da SEC (A-157).

Por que fora do painel: `score_vintages` acusa zero saidas, e por construcao
nunca acusaria outra coisa -- quem morreu jamais entrou nele. O tamanho do vies
so aparece numa fonte que nao dependa de estar vivo hoje. O `full-index` da SEC
e essa fonte: lista quem arquivou relatorio anual naquele trimestre, e uma
empresa que arquivava em 2010 e nao arquiva mais saiu do mercado.

Os indices sao grandes (~40 MB por trimestre) e ficam em cache no diretorio de
trabalho, que NAO entra no repositorio -- so o resultado agregado entra.

    python scripts/medir_mortalidade_us.py [--anos 2010 2015 2020 2025]
                                           [--cache DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.us_survivorship import (  # noqa: E402
    CAMINHO_MEDICAO,
    carregar_medicao,
    ciks_com_relatorio_anual,
    coorte_ampla_verificada,
    gravar_medicao,
    medir_mortalidade,
)

URL_IDX = "https://www.sec.gov/Archives/edgar/full-index/{ano}/QTR{q}/form.idx"


def _baixar(ano: int, q: int, cache: Path, agente: str) -> str:
    alvo = cache / f"{ano}Q{q}.idx"
    if not alvo.exists():
        req = urllib.request.Request(URL_IDX.format(ano=ano, q=q),
                                     headers={"User-Agent": agente})
        alvo.write_bytes(urllib.request.urlopen(req, timeout=180).read())
        time.sleep(0.3)
    return alvo.read_text(encoding="latin-1", errors="ignore")


def ciks_do_ano(ano: int, cache: Path, agente: str) -> set[int]:
    ciks: set[int] = set()
    for q in (1, 2, 3, 4):
        try:
            ciks |= ciks_com_relatorio_anual(_baixar(ano, q, cache, agente))
        except Exception as exc:  # noqa: BLE001
            print(f"   {ano}Q{q}: indisponivel ({type(exc).__name__})")
    return ciks


def painel_por_ano(anos: list[int]) -> dict[int, set[int]]:
    """CIKs que o NOSSO painel registra em cada safra. Vazio sem armazem."""
    try:
        from sqlalchemy import create_engine, text

        from scripts.publish_fii_selection_from_local import _warehouse_url
        eng = create_engine(
            _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
        with eng.connect() as conn:
            linhas = list(conn.execute(text(
                "SELECT date_part('year', v.as_of_date)::int, c.cik "
                "FROM market_us.score_vintages v "
                "JOIN market_us.companies c ON c.id=v.company_id "
                "WHERE c.cik IS NOT NULL")))
    except Exception as exc:  # noqa: BLE001
        print(f"painel indisponivel ({type(exc).__name__}): mede so o universo")
        return {}
    por_ano: dict[int, set[int]] = {ano: set() for ano in anos}
    for ano, cik in linhas:
        if int(ano) in por_ano:
            por_ano[int(ano)].add(int(cik))
    return por_ano


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anos", type=int, nargs="+", default=[2010, 2015, 2020, 2025])
    ap.add_argument("--cache", default=None)
    ap.add_argument("--agente", default="Dashboard Financeiro Unificado contato@exemplo.com")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cache = Path(args.cache) if args.cache else ROOT / ".cache" / "sec_full_index"
    cache.mkdir(parents=True, exist_ok=True)
    anos = sorted(set(args.anos))

    por_ano = {}
    for ano in anos:
        por_ano[ano] = ciks_do_ano(ano, cache, args.agente)
        print(f"{ano}: {len(por_ano[ano])} empresas com relatorio anual")

    coorte = medir_mortalidade(por_ano, painel_por_ano(anos) or None)
    for ano, dados in coorte["curva"].items():
        print(f"   da coorte {coorte['ano_base']}, vivas em {ano}: "
              f"{dados['vivas']} ({dados['sobrevivencia_pct']}%)")
    print(f"mortalidade ate {coorte['ano_final']}: {coorte['mortalidade_pct']}%")
    if "cobertura_pct" in coorte:
        print(f"painel cobre {coorte['cobertura_pct']}% do universo do ano base; "
              f"mortes observadas no painel: {coorte['mortes_no_painel']}")

    if args.dry_run:
        print("[dry-run] nada gravado.")
        return 0
    if not coorte_ampla_verificada(coorte):
        print("[bloqueado] coorte ampla fora do contrato; nada gravado.")
        return 2
    medicao = dict(carregar_medicao() or {})
    if not medicao:
        print("AVISO: medicao de turnover ausente; gravando so a coorte.")
    medicao["coorte"] = coorte
    print("gravado em", gravar_medicao(medicao, CAMINHO_MEDICAO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
