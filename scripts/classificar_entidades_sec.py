# -*- coding: utf-8 -*-
"""Apura a identidade de cada CIK em data.sec.gov/submissions (A-158).

Motivo. A tela americana afirma ao usuario que 70% das empresas desapareceram
entre 2010 e 2025. O numero e real, mas foi medido sobre 9.686 CIKs que
arquivaram QUALQUER relatorio anual em 2010 -- trust de leasing, emissor de ABS,
subsidiaria de seguradora, fundo fechado, emissor estrangeiro de 20-F. O painel
analisa acao operacional americana, e nada disso e acao. Trust de leasing
termina por desenho, nao por fracasso: contar seu encerramento como morte de
empresa infla a mortalidade que o usuario le.

Sem o SIC de cada CIK a conta nao pode ser refeita na populacao certa, e o
`full-index` nao traz SIC. Traz aqui: submissions responde 200 para CIK morto.

Uso (a gravacao no armazem local exige `--aplicar`):

    python scripts/classificar_entidades_sec.py --origem coorte --aplicar
    python scripts/classificar_entidades_sec.py --origem deslistadas --aplicar

Retomavel: CIK ja gravado com `http_status` 200 nao e consultado de novo.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# A SEC pede no maximo 10 requisicoes por segundo. Seis threads com 0,70 s de
# pausa cada ficam em ~8,5 req/s, abaixo do teto com folga -- ser barrado por
# excesso custaria a execucao inteira, e nao ha pressa que compense.
THREADS = 6
PAUSA_S = 0.70


def _agente() -> str:
    from core.config import settings
    return settings.SEC_USER_AGENT or "Dashboard Financeiro Unificado"


def _engine():
    from sqlalchemy import create_engine

    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def _ciks_alvo(origem: str, anos: list[int], cache: Path) -> set[int]:
    """CIKs a apurar.

    `coorte` e o universo do ano base da medicao de mortalidade -- e ele que
    precisa ser filtrado para a conta virar honesta.
    """
    from sqlalchemy import text

    from core.us_survivorship import ciks_com_relatorio_anual

    alvo: set[int] = set()
    if origem in ("coorte", "ambos"):
        for ano in anos:
            for q in (1, 2, 3, 4):
                arq = cache / f"{ano}Q{q}.idx"
                if arq.exists():
                    alvo |= ciks_com_relatorio_anual(
                        arq.read_text(encoding="latin-1", errors="ignore"))
    if origem in ("deslistadas", "ambos"):
        with _engine().connect() as conn:
            alvo |= {int(r[0]) for r in conn.execute(
                text("SELECT cik FROM market_us.delistings"))}
    return alvo


def _ja_apurados() -> set[int]:
    from sqlalchemy import text
    try:
        with _engine().connect() as conn:
            return {int(r[0]) for r in conn.execute(text(
                "SELECT cik FROM market_us.sec_entidade WHERE http_status = 200"))}
    except Exception as exc:  # noqa: BLE001
        print(f"sem cache previo ({type(exc).__name__})")
        return set()


def _juntar(valores) -> str | None:
    return ",".join(str(x) for x in (valores or []) if x) or None


def consultar(cik: int, agente: str, sessao) -> dict:
    """Uma linha por CIK, inclusive quando a consulta falha.

    Gravar a falha e o que separa "nao consultado" de "consultado e sem
    resposta". Sem essa distincao a proxima execucao nao sabe se a ausencia e
    lacuna de apuracao ou fato do mundo, que e o defeito de
    [[foto-truncada-vira-evidencia]].
    """
    try:
        r = sessao.get(URL.format(cik=cik), headers={"User-Agent": agente},
                       timeout=30)
    except Exception:  # noqa: BLE001
        return {"cik": cik, "http_status": 0}
    if r.status_code != 200:
        return {"cik": cik, "http_status": r.status_code}
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        return {"cik": cik, "http_status": -1}
    sic = j.get("sic")
    return {
        "cik": cik,
        "nome": j.get("name") or None,
        "sic": (str(sic).strip() or None) if sic else None,
        "sic_descricao": j.get("sicDescription") or None,
        "entity_type": j.get("entityType") or None,
        "exchanges": _juntar(j.get("exchanges")),
        "tickers": _juntar(j.get("tickers")),
        "estado_incorporacao": j.get("stateOfIncorporation") or None,
        "http_status": 200,
    }


def gravar(linhas: list[dict]) -> int:
    from sqlalchemy import text
    if not linhas:
        return 0
    sql = text(
        "INSERT INTO market_us.sec_entidade "
        "(cik, nome, sic, sic_descricao, entity_type, exchanges, tickers, "
        " estado_incorporacao, http_status, apurado_em) "
        "VALUES (:cik, :nome, :sic, :sic_descricao, :entity_type, :exchanges, "
        "        :tickers, :estado_incorporacao, :http_status, now()) "
        "ON CONFLICT (cik) DO UPDATE SET "
        "  nome = EXCLUDED.nome, sic = EXCLUDED.sic, "
        "  sic_descricao = EXCLUDED.sic_descricao, "
        "  entity_type = EXCLUDED.entity_type, exchanges = EXCLUDED.exchanges, "
        "  tickers = EXCLUDED.tickers, "
        "  estado_incorporacao = EXCLUDED.estado_incorporacao, "
        "  http_status = EXCLUDED.http_status, apurado_em = now()")
    vazio = {"nome": None, "sic": None, "sic_descricao": None,
             "entity_type": None, "exchanges": None, "tickers": None,
             "estado_incorporacao": None}
    with _engine().begin() as conn:
        for i in range(0, len(linhas), 500):
            conn.execute(sql, [{**vazio, **linha} for linha in linhas[i:i + 500]])
    return len(linhas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origem", choices=("coorte", "deslistadas", "ambos"),
                    default="ambos")
    ap.add_argument("--anos", type=int, nargs="+", default=[2010])
    ap.add_argument("--cache", default=None)
    ap.add_argument("--limite", type=int, default=0, help="0 = sem limite")
    ap.add_argument("--aplicar", action="store_true",
                    help="grava no armazem local (exige autorizacao)")
    args = ap.parse_args(argv)

    cache = Path(args.cache) if args.cache else ROOT / ".cache" / "sec_full_index"
    agente = _agente()
    alvo = _ciks_alvo(args.origem, sorted(set(args.anos)), cache)
    pendentes = sorted(alvo - _ja_apurados())
    if args.limite:
        pendentes = pendentes[:args.limite]
    print(f"alvo={len(alvo)} pendentes={len(pendentes)}", flush=True)
    if not args.aplicar:
        print("[dry-run] nada consultado nem gravado; use --aplicar.")
        return 0

    sessao = requests.Session()
    lote: list[dict] = []
    feitos = 0

    def _um(cik: int) -> dict:
        r = consultar(cik, agente, sessao)
        time.sleep(PAUSA_S)
        return r

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        for res in pool.map(_um, pendentes):
            lote.append(res)
            feitos += 1
            if len(lote) >= 500:
                gravar(lote)
                lote = []
                print(f"   {feitos}/{len(pendentes)}", flush=True)
    gravar(lote)
    print(f"gravados {feitos} CIKs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
