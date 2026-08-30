# -*- coding: utf-8 -*-
"""Confronta cada saida com o historico da propria entidade na SEC.

Uma passada por CIK sobre `data.sec.gov/submissions`, que faz duas coisas de
uma vez porque as duas dependem da mesma resposta:

  * **refuta** a saida quando existe relatorio anual em ano igual ou posterior
    ao da ausencia. A derivacao original le `form.idx` e compara a forma por
    igualdade exata contra uma lista sem `10-K/A`, `10-KT` e `40-F`; um emissor
    canadense sob o MJDS, que arquiva 40-F e nada mais, aparece ausente em todos
    os anos. Em 250 saidas sorteadas, 2,0% foram refutadas assim;
  * **resolve o simbolo** do que sobreviveu a refutacao, lendo
    `dei:TradingSymbol` na capa em XBRL inline do ultimo relatorio anual.

O simbolo NAO vem do campo `tickers` do `submissions.json`: a SEC o esvazia
quando a empresa para de arquivar. Em 12 saidas checadas a mao ele veio vazio
em 11, e a unica preenchida era a empresa viva -- resolver por ali nomearia
so quem nao morreu.

A capa so carrega XBRL inline a partir de 2019 (grandes) a 2021 (demais), entao
o aproveitamento cai a quase zero para saidas antigas. O script RELATA o
aproveitamento por ano de ausencia em vez de afirma-lo: numero prometido e
numero que ninguem confere.

Sem `--aplicar` nada e gravado.

Uso::

    python scripts/resolver_simbolo_deslistadas_us.py --desde 2019 --limite 50
    python scripts/resolver_simbolo_deslistadas_us.py --desde 2019 --aplicar
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.us_saidas_sec import (  # noqa: E402
    extrair_trading_symbol,
    filiais_anuais,
    refuta_saida,
)

URL_SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
URL_DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acesso}/{doc}"
FONTE_SIMBOLO = "dei:TradingSymbol"

# A capa fica no comeco do documento. Ler alem disso e baixar o 10-K inteiro --
# dezenas de MB por empresa, milhares de empresas -- para nada.
TETO_BYTES = 3_000_000
BLOCO = 262_144


def _sessao(agente: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": agente, "Accept-Encoding": "gzip, deflate"})
    return s


def submissions(sessao, cik: int, pausa: float) -> dict | None:
    try:
        r = sessao.get(URL_SUB.format(cik=cik), timeout=30)
    except Exception:  # noqa: BLE001
        return None
    finally:
        time.sleep(pausa)
    return r.json() if r.status_code == 200 else None


def simbolo_da_capa(sessao, cik: int, filial: dict, pausa: float) -> str | None:
    acesso = str(filial.get("acesso") or "").replace("-", "")
    doc = filial.get("documento") or ""
    if not acesso or not doc:
        return None
    url = URL_DOC.format(cik=cik, acesso=acesso, doc=doc)
    try:
        with sessao.get(url, timeout=90, stream=True) as r:
            if r.status_code != 200:
                return None
            buffer, lido = "", 0
            for pedaco in r.iter_content(BLOCO):
                buffer += pedaco.decode("utf-8", "ignore")
                lido += len(pedaco)
                simbolo = extrair_trading_symbol(buffer)
                if simbolo:
                    return simbolo
                if lido >= TETO_BYTES:
                    break
                # A tag pode estar partida entre dois pedacos; guardar a cauda
                # custa 4 KB e evita perder o unico fato que interessa.
                buffer = buffer[-4096:]
    except Exception:  # noqa: BLE001
        return None
    finally:
        time.sleep(pausa)
    return None


def resolver(sessao, cik: int, absence_year: int, pausa: float,
             *, com_simbolo: bool = True) -> dict:
    """Veredito de uma saida: refutada, nomeada, ou confirmada sem nome.

    `com_simbolo=False` faz so a refutacao, que custa uma requisicao. Para
    saida anterior a 2019 e o modo certo: a capa so passou a carregar XBRL
    inline naquele ano, entao baixar tres relatorios por empresa para nao
    achar simbolo nenhum seria gastar a cota da SEC com uma resposta ja
    conhecida.
    """
    sub = submissions(sessao, cik, pausa)
    if sub is None:
        return {"cik": cik, "estado": "sem_resposta"}
    refutacao = refuta_saida(sub, absence_year)
    if refutacao:
        return {"cik": cik, "estado": "refutada",
                "refuted_form": refutacao["forma"],
                "refuted_date": refutacao["data"]}
    if not com_simbolo:
        return {"cik": cik, "estado": "confirmada"}
    anuais = filiais_anuais(sub)
    if not anuais:
        return {"cik": cik, "estado": "sem_anual"}
    # Do mais recente para tras: a capa da ultima anual e a que tem mais chance
    # de trazer XBRL inline, mas ela pode ser uma emenda enxuta, sem capa.
    for filial in anuais[:3]:
        simbolo = simbolo_da_capa(sessao, cik, filial, pausa)
        if simbolo:
            return {"cik": cik, "estado": "nomeada", "symbol": simbolo,
                    "symbol_source": FONTE_SIMBOLO,
                    "symbol_as_of": filial.get("data")}
    return {"cik": cik, "estado": "sem_simbolo"}


SQL_LER = (
    "SELECT cik, absence_year FROM market_us.delistings "
    "WHERE absence_year >= :desde AND absence_year <= :ate "
    "  AND checked_at IS NULL "
    "ORDER BY absence_year DESC, cik")

SQL_GRAVAR = (
    "UPDATE market_us.delistings SET "
    "  symbol = COALESCE(:symbol, symbol), "
    "  symbol_source = COALESCE(:symbol_source, symbol_source), "
    "  symbol_as_of = COALESCE(CAST(:symbol_as_of AS DATE), symbol_as_of), "
    "  refuted_form = :refuted_form, "
    "  refuted_date = CAST(:refuted_date AS DATE), "
    "  checked_at = now() "
    "WHERE cik = :cik")


def _linhas(url: str, desde: int, ate: int | None, limite: int | None):
    from sqlalchemy import create_engine, text
    eng = create_engine(url.replace("postgresql://", "postgresql+psycopg2://"))
    with eng.connect() as conn:
        linhas = list(conn.execute(text(SQL_LER),
                                   {"desde": desde, "ate": ate or 9999}))
    return eng, [(int(c), int(a)) for c, a in linhas][:limite or None]


def _gravar(eng, vereditos: list[dict]) -> int:
    from sqlalchemy import text
    gravadas = 0
    with eng.begin() as conn:
        for v in vereditos:
            if v["estado"] == "sem_resposta":
                # Nao houve leitura; marcar como conferida seria mentira, e a
                # linha nunca mais seria tentada.
                continue
            conn.execute(text(SQL_GRAVAR), {
                "cik": v["cik"],
                "symbol": v.get("symbol"),
                "symbol_source": v.get("symbol_source"),
                "symbol_as_of": v.get("symbol_as_of"),
                "refuted_form": v.get("refuted_form"),
                "refuted_date": v.get("refuted_date"),
            })
            gravadas += 1
    return gravadas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", type=int, default=2019,
                    help="ano de ausencia minimo (a capa so tem XBRL desde 2019)")
    ap.add_argument("--ate", type=int, default=None,
                    help="ano de ausencia maximo")
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--lote", type=int, default=200,
                    help="de quantas em quantas linhas gravar")
    ap.add_argument("--sem-simbolo", dest="sem_simbolo", action="store_true",
                    help="so refutar (1 requisicao por saida)")
    ap.add_argument("--pausa", type=float, default=0.12,
                    help="segundos entre requisicoes (a SEC pede <= 10/s)")
    ap.add_argument("--agente", default=None, help="User-Agent exigido pela SEC")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--json", dest="saida_json", default=None)
    args = ap.parse_args()

    from core.config import settings
    agente = args.agente or settings.SEC_USER_AGENT
    if not agente:
        print("defina SEC_USER_AGENT ('Nome email@dominio') ou passe --agente")
        return 2

    from scripts.publish_fii_selection_from_local import _warehouse_url
    eng, alvos = _linhas(_warehouse_url(), args.desde, args.ate, args.limite)
    print(f"saidas a conferir (ausencia >= {args.desde}): {len(alvos)}")

    sessao = _sessao(agente)
    vereditos: list[dict] = []
    pendentes: list[dict] = []
    gravadas = 0
    por_estado: Counter = Counter()
    nomeadas_por_ano: Counter = Counter()
    total_por_ano: Counter = Counter()
    for i, (cik, ano) in enumerate(alvos, 1):
        v = resolver(sessao, cik, ano, args.pausa,
                     com_simbolo=not args.sem_simbolo)
        v["absence_year"] = ano
        vereditos.append(v)
        por_estado[v["estado"]] += 1
        total_por_ano[ano] += 1
        if v["estado"] == "nomeada":
            nomeadas_por_ano[ano] += 1
        pendentes.append(v)
        if args.aplicar and len(pendentes) >= args.lote:
            # Gravar em lotes, e nao so no fim: a passada leva horas, e uma
            # queda no meio devolveria `checked_at` NULL em tudo -- a proxima
            # execucao refaria milhares de requisicoes ja feitas.
            gravadas += _gravar(eng, pendentes)
            pendentes = []
        if i % 100 == 0:
            print(f"  {i}/{len(alvos)}  {dict(por_estado)}", flush=True)

    print("\nvereditos:", dict(por_estado))
    print("aproveitamento do simbolo por ano de ausencia:")
    for ano in sorted(total_por_ano):
        n, t = nomeadas_por_ano[ano], total_por_ano[ano]
        print(f"  {ano}: {n}/{t} ({100.0 * n / t:.1f}%)")

    if args.saida_json:
        Path(args.saida_json).write_text(
            json.dumps(vereditos, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.aplicar:
        print("\n(dry-run) nada gravado -- use --aplicar")
        return 0
    gravadas += _gravar(eng, pendentes)
    print(f"\ngravadas {gravadas} linhas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
