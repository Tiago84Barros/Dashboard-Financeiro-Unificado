# -*- coding: utf-8 -*-
"""Etapa 1 da §21 do guia: a IA reconhece a empresa e o ano num dossiê anônimo?

Se reconhece (> 20% de acerto no ticker), qualquer nota que ela der a uma
empresa-safra do passado pode ser memória do que veio depois, e a etapa 2
(notas às cegas, retroativas) fica invalidada.

O dossiê leva só números do retrato PIT da safra (múltiplos, setor), o
histórico de ROIC e o de lucro, com os anos relativos (N-1, N-2, ...). Sem
nome, ticker, data nem texto. Amostra fixa (``--semente``), prompt fixo.

Uso::

    python scripts/testar_vazamento_ia_b3.py --cache-dir data/cache_wh --n 60
    python scripts/testar_vazamento_ia_b3.py --cache-dir data/cache_wh --n 2 --dry-run

Grava `data/oos_vazamento_ia_b3.json`. Chama a LLM do app pela cadeia de
`_chat_complete` (custo baixo: uma chamada curta por empresa-safra) com dados públicos de balanço.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_vazamento_ia_b3.json"
LIMITE_VAZAMENTO = 0.20
COLUNAS = ["P/L", "P/VP", "DY", "ROE", "ROIC", "Margem_Operacional",
           "Margem_Liquida", "Endividamento_Total", "Liquidez_Corrente",
           "P_FCO", "EV_EBIT"]

PROMPT = (
    "Abaixo há o retrato financeiro anônimo de uma empresa listada na B3. Os "
    "anos são relativos: 'N-1' é o último exercício publicado. Diga qual é a "
    "empresa (ticker B3) e qual é o ano N-1. Se não souber, responda "
    "'desconhecida'. Não invente. Responda só com um JSON: "
    '{"ticker": "XXXX3 ou desconhecida", "ano_n1": 2018 ou null, '
    '"confianca": 0 a 1}.\n\n')


def _fmt(v) -> str:
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return "n/d"


def dossie(linha, setor, roic_hist: dict[int, float], lucro: dict[int, float],
           ano_max: int) -> str:
    """Texto anônimo: múltiplos do retrato, setor e séries com anos relativos."""
    partes = [f"Setor: {setor or 'n/d'}", "Múltiplos do exercício N-1:"]
    partes += [f"- {c}: {_fmt(linha.get(c))}" for c in COLUNAS]
    if roic_hist:
        partes.append("ROIC por exercício: " + ", ".join(
            f"N-{ano_max - a + 1}={_fmt(v)}" for a, v in sorted(roic_hist.items())))
    if lucro:
        partes.append("Lucro líquido (R$ mi) por exercício: " + ", ".join(
            f"N-{ano_max - a + 1}={_fmt(v)}" for a, v in sorted(lucro.items())))
    return "\n".join(partes)


def parse_resposta(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def acertou(resp: dict, ticker: str, ano_verdadeiro: int) -> tuple[bool, bool]:
    """(acertou o ticker, acertou o ticker e o ano). Casa pela raiz do ticker."""
    t = str(resp.get("ticker") or "").upper()
    raiz = lambda x: re.sub(r"\d+$", "", x)  # noqa: E731  (ON/PN/UNIT contam)
    ok_t = bool(t) and "DESCONHECIDA" not in t and raiz(t) == raiz(ticker.upper())
    ok_a = ok_t and resp.get("ano_n1") is not None and int(resp["ano_n1"]) == int(ano_verdadeiro)
    return ok_t, ok_a


def resumo(amostra: list[dict], respostas: list[dict], modelos, n_universo: int,
           semente: int) -> dict:
    """Pontua as respostas (na ordem da amostra) e monta o JSON de saída."""
    linhas, hit_t, hit_a = [], 0, 0
    for c, resp in zip(amostra, respostas):
        ok_t, ok_a = acertou(resp, c["ticker"], c["ano_n1"])
        hit_t += ok_t
        hit_a += ok_a
        linhas.append({"ticker": c["ticker"], "safra": c["safra"], "ano_n1": c["ano_n1"],
                       "resposta": resp, "acertou_ticker": ok_t, "acertou_ticker_e_ano": ok_a})
    n = max(len(amostra), 1)
    taxa = hit_t / n
    return {
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "modelos_que_responderam": sorted(modelos), "semente": semente, "amostra": len(amostra),
        "universo_de_casos": n_universo,
        "dossie": "só números (retrato PIT, setor, ROIC e lucro por exercício), sem texto",
        "taxa_acerto_ticker": taxa, "taxa_acerto_ticker_e_ano": hit_a / n,
        "limite": LIMITE_VAZAMENTO,
        "invalida_etapa_2": bool(taxa > LIMITE_VAZAMENTO),
        "casos": linhas,
    }


def corrigir(gabarito: Path, respostas: Path, modelo: str, saida: Path) -> int:
    """Modo sem LLM: pontua respostas dadas fora do app (ex.: ajudante do Claude Code).

    ``respostas`` é uma lista JSON de objetos com ``id`` e os campos do PROMPT.
    """
    gab = json.loads(gabarito.read_text(encoding="utf-8"))
    por_id = {str(r.get("id")): r for r in json.loads(respostas.read_text(encoding="utf-8"))}
    amostra = gab["casos"]
    resps = [por_id.get(str(c["id"]), {}) for c in amostra]
    out = resumo(amostra, resps, [modelo], gab["universo_de_casos"], gab["semente"])
    out["canal"] = "fora do app: ajudante sem contexto, um lote de dossiês por chamada"
    out["sem_resposta"] = sum(1 for r in resps if not r)
    saida.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{modelo}: acerto ticker={out['taxa_acerto_ticker']:.1%} "
          f"ticker+ano={out['taxa_acerto_ticker_e_ano']:.1%} sem_resposta={out['sem_resposta']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--n", type=int, default=60, help="tamanho da amostra")
    ap.add_argument("--semente", type=int, default=20261122)
    ap.add_argument("--dry-run", action="store_true", help="não grava o JSON")
    ap.add_argument("--exportar", type=Path, default=None,
                    help="grava dossies.json (sem resposta) e gabarito.json nesta pasta; não chama LLM")
    ap.add_argument("--corrigir", type=Path, default=None, help="respostas.json a pontuar")
    ap.add_argument("--gabarito", type=Path, default=None)
    ap.add_argument("--modelo", default="desconhecido")
    ap.add_argument("--saida", type=Path, default=CAMINHO)
    args = ap.parse_args()

    if args.corrigir:
        return corrigir(args.gabarito, args.corrigir, args.modelo, args.saida)

    import core.llm_b3 as llm
    cadeia_original = llm._provider_chain  # `preparar` desliga a LLM da medição
    from scripts.medir_nota_fatores_b3 import preparar
    prep = preparar(args.cache_dir)
    llm._provider_chain = cadeia_original
    M = prep["M"]
    resultados, precos, params, decisao = prep["rodada"]

    import pandas as pd

    from core.b3_retrato_pit import historico_ate, series_pl_ate
    from core.b3_vigencia import ano_base_do_score

    contexto, por_safra = M._segmentos_por_safra(
        resultados, precos, params, decisao, prep["cost_cfg"], prep["hoje"])
    series_pl = contexto["series_pl"]
    hist_batch = decisao.get("hist_batch") or {}
    cls = decisao["classificacao"]
    setor_de = {str(t).upper(): s for t, s in zip(cls["ticker"], cls["SETOR"])}

    casos = []
    for p in por_safra:
        safra = p["safra"]
        ano_max = ano_base_do_score(safra)
        alvos = {str(t).upper() for t in p["universo"]}
        quadro = p["df_pit"]
        if quadro is None or quadro.empty:
            continue
        hist = historico_ate({tk: df for tk, df in hist_batch.items() if tk in alvos},
                             ano_max, M._aceita_da_safra(safra))
        pl = series_pl_ate({tk: s for tk, s in series_pl.items() if tk in alvos}, ano_max)
        for _, linha in quadro.iterrows():
            tk = str(linha["Ticker"]).upper()
            if tk not in alvos:
                continue
            h = hist.get(tk)
            roic = {}
            if h is not None and not h.empty and "ROIC" in h.columns:
                anos = pd.to_datetime(h["Data"], errors="coerce").dt.year
                roic = {int(a): float(r) for a, r in zip(anos, pd.to_numeric(h["ROIC"], errors="coerce"))
                        if pd.notna(a) and pd.notna(r)}
            lucro = {int(r["ano"]): r.get("lucro_mi") for r in (pl.get(tk) or [])
                     if r.get("ano") is not None and r.get("lucro_mi") is not None}
            casos.append({"ticker": tk, "safra": safra, "ano_n1": ano_max,
                          "texto": dossie(linha, setor_de.get(tk), roic, lucro, ano_max)})

    random.Random(args.semente).shuffle(casos)
    amostra = casos[:args.n]
    print(f"{len(casos)} empresa-safras; amostra de {len(amostra)}", flush=True)

    if args.exportar:
        # Ids opacos: a ordem não revela nada; o gabarito fica em outro arquivo.
        args.exportar.mkdir(parents=True, exist_ok=True)
        for i, c in enumerate(amostra, 1):
            c["id"] = f"d{i:03d}"
        (args.exportar / "dossies.json").write_text(json.dumps(
            {"prompt": PROMPT, "dossies": [{"id": c["id"], "texto": c["texto"]} for c in amostra]},
            indent=1, ensure_ascii=False), encoding="utf-8")
        (args.exportar / "gabarito.json").write_text(json.dumps(
            {"semente": args.semente, "universo_de_casos": len(casos),
             "casos": [{k: c[k] for k in ("id", "ticker", "safra", "ano_n1")} for c in amostra]},
            indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"exportado em {args.exportar}")
        return 0

    if not llm.llm_disponivel():
        raise SystemExit("nenhum provedor de LLM disponível")
    modelos: set[str] = set()

    respostas = []
    for i, c in enumerate(amostra, 1):
        # Pelo portão do app (`_chat_complete`): mesma cadeia de provedores.
        txt = llm._chat_complete([{"role": "user", "content": PROMPT + c["texto"]}],
                                 json_mode=True)
        modelos.add(str(llm.ultimo_modelo()))
        resp = parse_resposta(txt)
        respostas.append(resp)
        print(f"{i}/{len(amostra)} {c['ticker']}/{c['safra']} -> {resp.get('ticker')}", flush=True)

    saida = resumo(amostra, respostas, modelos, len(casos), args.semente)
    print(f"acerto ticker={saida['taxa_acerto_ticker']:.1%} "
          f"ticker+ano={saida['taxa_acerto_ticker_e_ano']:.1%} "
          f"invalida_etapa_2={saida['invalida_etapa_2']}", flush=True)
    if not args.dry_run:
        args.saida.write_text(json.dumps(saida, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        print(f"gravado em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
