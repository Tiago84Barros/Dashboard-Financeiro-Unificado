# -*- coding: utf-8 -*-
"""Ingere as saidas do universo americano a partir do full-index da SEC.

`scripts/medir_mortalidade_us.py` mediu o TAMANHO do vies -- 70% das 9.686
empresas de 2010 sumiram ate 2025 -- amostrando quatro anos. Tamanho nao e
registro: para o painel deixar de ser 100% sobrevivente e preciso saber QUEM
saiu e QUANDO, o que exige varrer o indice ano a ano, sem buracos.

O que muda em relacao ao script de mortalidade:

  * varre TODOS os anos da janela, nao uma amostra -- a amostra de 5 em 5 anos
    nao consegue datar a saida melhor que "em algum ponto do quinquenio";
  * nao guarda o indice em disco por padrao. Sao ~40 MB por trimestre e 64
    trimestres; com 1,9 GB livres no C: o cache encheria o disco no meio da
    execucao e a falha apareceria como "ano truncado", ou seja, como extincao
    em massa. Use `--cache DIR` so se houver espaco;
  * grava em `market_us.delistings` (migration 055), nao em `assets`: a imensa
    maioria das empresas que sairam nunca teve linha em `assets`.

Sem `--aplicar` o script apenas relata. Gravacao remota mexe em banco
compartilhado e exige decisao humana explicita.

Uso::

    python scripts/ingerir_deslistadas_us.py --inicio 2010 --fim 2026
    python scripts/ingerir_deslistadas_us.py --inicio 2010 --fim 2026 --aplicar
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.us_delistings import (  # noqa: E402
    FONTE,
    derivar_saidas,
    resumo,
)
from core.us_survivorship import ciks_com_relatorio_anual  # noqa: E402

URL_IDX = "https://www.sec.gov/Archives/edgar/full-index/{ano}/QTR{q}/form.idx"


def _texto_do_trimestre(ano: int, q: int, agente: str,
                        cache: Path | None) -> str:
    if cache is not None:
        alvo = cache / f"{ano}Q{q}.idx"
        if alvo.exists():
            return alvo.read_text(encoding="latin-1", errors="ignore")
    req = urllib.request.Request(URL_IDX.format(ano=ano, q=q),
                                 headers={"User-Agent": agente})
    dados = urllib.request.urlopen(req, timeout=240).read()
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{ano}Q{q}.idx").write_bytes(dados)
    time.sleep(0.4)  # limite de cortesia da SEC
    return dados.decode("latin-1", errors="ignore")


def ciks_do_ano(ano: int, agente: str, cache: Path | None) -> tuple[set[int], int]:
    """CIKs com relatorio anual no ano, e quantos trimestres falharam.

    O numero de falhas volta junto porque um ano com trimestre faltando nao e
    um ano pequeno: e um ano que NAO SE SABE. Tratar os dois igual e o caminho
    curto para declarar morta metade do mercado.
    """
    ciks: set[int] = set()
    falhas = 0
    for q in (1, 2, 3, 4):
        try:
            do_q = ciks_com_relatorio_anual(
                _texto_do_trimestre(ano, q, agente, cache))
        except Exception as exc:  # noqa: BLE001
            falhas += 1
            print(f"   {ano}Q{q}: indisponivel ({type(exc).__name__})")
            continue
        # Trimestre que responde 200 e nao traz UM relatorio anual nao e um
        # trimestre calmo: e um indice que nao foi lido. A SEC serve o futuro
        # assim -- 2026Q4 devolve 200 com 459 bytes de cabecalho -- e contar
        # isso como "zero arquivadores" faz o ano inteiro parecer uma extincao.
        if not do_q:
            falhas += 1
            print(f"   {ano}Q{q}: indice sem nenhum relatorio anual -- "
                  f"tratado como trimestre ausente")
            continue
        ciks |= do_q
    return ciks, falhas


def _mapa_cik_para_empresa(url: str) -> dict[int, tuple[int, str | None]]:
    """CIK -> (company_id, simbolo) do nosso cadastro, para cruzar as saidas."""
    from sqlalchemy import create_engine, text
    eng = create_engine(url.replace("postgresql://", "postgresql+psycopg2://"))
    with eng.connect() as conn:
        linhas = list(conn.execute(text(
            "SELECT c.cik, c.id, min(a.symbol) "
            "FROM market_us.companies c "
            "LEFT JOIN market_us.assets a ON a.company_id = c.id "
            "WHERE c.cik IS NOT NULL GROUP BY c.cik, c.id")))
    return {int(cik): (int(cid), sym) for cik, cid, sym in linhas}


def _gravar(url: str, saidas, mapa) -> tuple[int, int]:
    """Grava a derivacao e RECONCILIA: quem nao esta mais no conjunto derivado
    perde a linha. Sem isso, uma empresa que volta a arquivar -- ou uma saida
    inventada por indice truncado -- fica marcada como deslistada para sempre,
    porque upsert nunca apaga. O registro precisa ser a derivacao corrente, nao
    a uniao de todas as derivacoes ja feitas."""
    from sqlalchemy import create_engine, text
    eng = create_engine(url.replace("postgresql://", "postgresql+psycopg2://"))
    sql = text(
        "INSERT INTO market_us.delistings "
        "(cik, company_id, symbol, last_annual_report_year, absence_year, "
        " delisted_date, reason, source) "
        "VALUES (:cik, :company_id, :symbol, :ultimo, :ausencia, :data, "
        "        :motivo, :fonte) "
        "ON CONFLICT (cik) DO UPDATE SET "
        "  company_id = EXCLUDED.company_id, symbol = EXCLUDED.symbol, "
        "  last_annual_report_year = EXCLUDED.last_annual_report_year, "
        "  absence_year = EXCLUDED.absence_year, "
        "  delisted_date = EXCLUDED.delisted_date, derived_at = now()")
    gravadas = 0
    with eng.begin() as conn:
        removidas = conn.execute(
            text("DELETE FROM market_us.delistings "
                 "WHERE source = :fonte AND NOT (cik = ANY(:ciks))"),
            {"fonte": FONTE, "ciks": [s.cik for s in saidas]}).rowcount or 0
        for s in saidas:
            cid, sym = mapa.get(s.cik, (None, None))
            conn.execute(sql, {
                "cik": s.cik, "company_id": cid, "symbol": sym,
                "ultimo": s.ultimo_ano_com_relatorio,
                "ausencia": s.ano_da_ausencia, "data": s.data_saida,
                "motivo": s.motivo, "fonte": s.fonte})
            gravadas += 1
    return gravadas, int(removidas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inicio", type=int, default=2010)
    ap.add_argument("--fim", type=int, default=2026)
    ap.add_argument("--cache", default=None,
                    help="diretorio para guardar os .idx (~40 MB por trimestre)")
    ap.add_argument("--agente", default="Dashboard Financeiro Unificado contato@exemplo.com")
    ap.add_argument("--aplicar", action="store_true",
                    help="grava em market_us.delistings (exige autorizacao)")
    ap.add_argument("--saida", default="artifacts/us_delistings_diagnostico.json")
    args = ap.parse_args(argv)

    cache = Path(args.cache) if args.cache else None
    anos = list(range(args.inicio, args.fim + 1))

    por_ano: dict[int, set[int]] = {}
    incompletos: list[int] = []
    for ano in anos:
        ciks, falhas = ciks_do_ano(ano, args.agente, cache)
        por_ano[ano] = ciks
        if falhas:
            # Ano com trimestre faltando nao DATA saida -- ele so poderia
            # inventar mortes. Mas continua entrando na janela, porque quem ele
            # mostrou arquivando esta vivo, e essa metade da evidencia e boa.
            incompletos.append(ano)
            print(f"{ano}: {len(ciks)} empresas, {falhas} trimestre(s) "
                  f"faltando -- nao data saida, so desmente", flush=True)
            continue
        print(f"{ano}: {len(ciks)} empresas com relatorio anual", flush=True)

    diag = derivar_saidas(por_ano, ano_corrente=date.today().year,
                          anos_incompletos=set(incompletos))
    rel = resumo(diag)
    rel["anos_incompletos"] = incompletos
    print("\n" + json.dumps(rel, indent=2, ensure_ascii=False))

    destino = ROOT / args.saida
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(rel, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    print(f"diagnostico: {destino}")

    if not diag.saidas:
        print(f"\nnada a gravar: {diag.motivo}")
        return 0
    if not args.aplicar:
        print(f"\n[relatorio] {len(diag.saidas)} saidas derivadas. "
              f"Rode com --aplicar para gravar.")
        return 0

    from scripts.publish_fii_selection_from_local import _warehouse_url
    url = _warehouse_url()
    mapa = _mapa_cik_para_empresa(url)
    no_cadastro = sum(1 for s in diag.saidas if s.cik in mapa)
    print(f"\ncruzamento: {no_cadastro} das {len(diag.saidas)} saidas tem "
          f"cadastro em market_us.companies")
    gravadas, removidas = _gravar(url, diag.saidas, mapa)
    print(f"gravadas: {gravadas}; removidas por nao estarem mais na "
          f"derivacao: {removidas}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
