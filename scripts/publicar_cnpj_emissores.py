"""Publica ticker -> (CNPJ, razão social) em ``data/public/cnpj_emissores.json``.

A ficha Bens e Direitos do IRPF pede o CNPJ da empresa em cada ação, e as
fichas de Rendimentos Isentos (09) e de Tributação Exclusiva (10) pedem o
CNPJ e o nome da fonte pagadora. O app não guarda isso por ticker; a CVM
guarda (Dados Abertos, sem auth):

  • FCA valor_mobiliario (zip anual) ... CNPJ -> Codigo_Negociacao
  • cad_cia_aberta.csv .................. CNPJ -> DENOM_SOCIAL

Lê do FCA de 2019 (início do extrato da B3) até o ano corrente; ticker que
mudou de emissor fica com o do FCA mais recente. Inclui companhia com
registro cancelado: ação vendida em 2025 de empresa que saiu da bolsa ainda
precisa do CNPJ na declaração.

FII e Fiagro não estão aqui (não são companhia aberta): vêm de
``market.fiis.cnpj`` no Supabase, em ``core.irpf_declaracao``.

Recusa gravar (saída 1) se o cadastro ou todos os FCAs falharem.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO_PADRAO = ROOT / "data" / "public" / "cnpj_emissores.json"
PRIMEIRO_ANO = 2019


def formatar_cnpj(digitos: str) -> str:
    d = digitos.zfill(14)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def montar(cad_csv: bytes, fcas: dict[int, bytes]) -> dict[str, list[str]]:
    """{ticker: [cnpj formatado, razão social]} -- puro, para teste."""
    from core.cvm_cadastro import _rows, cnpj_digits, parse_fca_valmob

    razao = {}
    for r in _rows(cad_csv):
        cnpj = cnpj_digits(r.get("CNPJ_CIA"))
        nome = (r.get("DENOM_SOCIAL") or r.get("DENOM_COMERC") or "").strip()
        # O cadastro repete o CNPJ em registros antigos; o ativo vence.
        if cnpj and nome and (cnpj not in razao or (r.get("SIT") or "").upper() == "ATIVO"):
            razao[cnpj] = nome
    out: dict[str, list[str]] = {}
    for ano in sorted(fcas):
        for row in parse_fca_valmob(fcas[ano]):
            if row["cnpj"]:
                out[row["ticker"]] = [formatar_cnpj(row["cnpj"]), razao.get(row["cnpj"], "")]
    return dict(sorted(out.items()))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from core.cvm_cadastro import fetch_cad, fetch_fca_valmob

    relatorio: dict = {"saida": str(args.saida)}
    cad = fetch_cad(timeout=120)
    if not cad:
        relatorio["erro"] = "cadastro da CVM indisponível"
        print(json.dumps(relatorio, ensure_ascii=False))
        return 1
    fcas = {}
    for ano in range(PRIMEIRO_ANO, date.today().year + 1):
        conteudo = fetch_fca_valmob(ano, timeout=120)
        if conteudo:
            fcas[ano] = conteudo
    relatorio["fca_anos"] = sorted(fcas)
    if not fcas:
        relatorio["erro"] = "nenhum FCA da CVM baixado"
        print(json.dumps(relatorio, ensure_ascii=False))
        return 1

    tickers = montar(cad, fcas)
    relatorio["tickers"] = len(tickers)
    relatorio["sem_razao_social"] = sum(1 for v in tickers.values() if not v[1])
    if not args.dry_run:
        args.saida.write_text(json.dumps({
            "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fonte": "CVM Dados Abertos: FCA valor_mobiliario + cad_cia_aberta",
            "tickers": tickers,
        }, ensure_ascii=False, indent=0), encoding="utf-8")
    print(json.dumps(relatorio, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
