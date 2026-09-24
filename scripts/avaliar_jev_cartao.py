"""
scripts/avaliar_jev_cartao.py
Piloto do Jev (TypeSafe AI) na categorização do cartão de crédito — SÓ MEDE.

Pergunta ao Jev a categoria de lançamentos que JÁ têm categoria (definida pelas
regras ou por você na tela "A revisar"), esconde o rótulo e compara. Depois
pede sugestão para os lançamentos que ainda estão "A revisar", sem gravar nada.

O que sai:
  - acerto geral e por ORIGEM do rótulo. `usuario:` é o caso difícil (o motor de
    regras não sabia); `merchant:` é fácil (nome conhecido) e infla o acerto.
  - calibração: para cada faixa de probabilidade, quantos acertou. É o que diz
    se dá para confiar no número que o Jev devolve.
  - cobertura x precisão por limiar: quantos o Jev sugeriria acima do limiar e
    quantos desses estariam certos.
  - latência p50/p95, tokens e custo estimado.

NÃO grava no banco. Envia à TypeSafe só o nome do estabelecimento e o valor.

USO:
  python scripts/avaliar_jev_cartao.py              # até 300 estabelecimentos
  python scripts/avaliar_jev_cartao.py --max 50

Requer TYPESAFE_API_KEY no .env (ou no ambiente) e o banco real (MOCK_MODE=false).
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PRECO_USD_POR_MILHAO = 0.042          # entrada; saída não é cobrada (TypeSafe, set/2026)
FAIXAS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0001))
LIMIARES = (0.5, 0.7, 0.8, 0.9)
MAX_ERROS_SEGUIDOS = 5


def origem_do_rotulo(regra: str) -> str:
    """Agrupa a regra que decidiu o rótulo: usuario / merchant / categoria."""
    if regra.startswith("usuario:"):
        return "usuario"
    if regra.startswith("merchant:"):
        return "merchant"
    return "categoria"


def rotulados_unicos(items: list[dict], categorias_validas) -> tuple[list[dict], int]:
    """Um registro por estabelecimento com rótulo inequívoco.

    O mesmo nome com categorias diferentes no histórico é descartado (e
    contado): não há resposta certa contra a qual medir.
    """
    por_desc: dict[str, dict] = {}
    rotulos: dict[str, set] = defaultdict(set)
    for it in items:
        cat = it["categoria_atual"]
        if cat not in categorias_validas:
            continue
        chave = it["descricao"].upper()
        rotulos[chave].add(cat)
        reg = por_desc.setdefault(chave, {
            "descricao": it["descricao"], "rotulo": cat, "valor": it["valor"],
            "origem": origem_do_rotulo(it["regra"]), "n": 0})
        reg["n"] += 1
    ambiguos = [k for k, v in rotulos.items() if len(v) > 1]
    for k in ambiguos:
        por_desc.pop(k, None)
    out = sorted(por_desc.values(), key=lambda r: (-r["n"], r["descricao"]))
    return out, len(ambiguos)


def _pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:5.1f}%" if b else "   — "


def resumir(res: list[dict]) -> list[str]:
    """Linhas do relatório a partir de {rotulo, sugestao, prob, origem, latencia_s, tokens}."""
    linhas: list[str] = []
    validos = [r for r in res if r.get("sugestao") is not None]
    if not validos:
        return ["Nenhuma resposta válida do Jev."]
    ok = sum(r["sugestao"] == r["rotulo"] for r in validos)
    linhas.append(f"Acerto geral: {ok}/{len(validos)} = {_pct(ok, len(validos)).strip()}")

    linhas.append("\nPor origem do rótulo:")
    for origem in ("usuario", "merchant", "categoria"):
        grupo = [r for r in validos if r["origem"] == origem]
        a = sum(r["sugestao"] == r["rotulo"] for r in grupo)
        linhas.append(f"  {origem:<10} {a:>4}/{len(grupo):<4} {_pct(a, len(grupo))}")

    linhas.append("\nCalibração (probabilidade devolvida x acerto observado):")
    sem_prob = [r for r in validos if r["prob"] is None]
    for lo, hi in FAIXAS:
        grupo = [r for r in validos if r["prob"] is not None and lo <= r["prob"] < hi]
        a = sum(r["sugestao"] == r["rotulo"] for r in grupo)
        linhas.append(f"  [{lo:.1f}, {min(hi, 1.0):.1f}) {a:>4}/{len(grupo):<4} {_pct(a, len(grupo))}")
    if sem_prob:
        linhas.append(f"  sem probabilidade: {len(sem_prob)}")

    linhas.append("\nSe só aceitar sugestão acima do limiar:")
    for lim in LIMIARES:
        grupo = [r for r in validos if r["prob"] is not None and r["prob"] >= lim]
        a = sum(r["sugestao"] == r["rotulo"] for r in grupo)
        linhas.append(f"  >= {lim:.1f}: cobre {_pct(len(grupo), len(validos))}"
                      f"  precisão {_pct(a, len(grupo))}")

    erros = Counter((r["rotulo"], r["sugestao"]) for r in validos if r["sugestao"] != r["rotulo"])
    if erros:
        linhas.append("\nConfusões mais comuns (rótulo -> Jev):")
        for (rot, sug), n in erros.most_common(8):
            linhas.append(f"  {n:>3}x  {rot} -> {sug}")

    lat = sorted(r["latencia_s"] for r in validos)
    tokens = sum(r.get("tokens", 0) for r in validos)
    p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
    linhas.append(f"\nLatência: p50 {statistics.median(lat) * 1000:.0f} ms · p95 {p95 * 1000:.0f} ms")
    linhas.append(f"Tokens de entrada: {tokens:,} · custo ~US$ "
                  f"{tokens * PRECO_USD_POR_MILHAO / 1e6:.4f}")
    return linhas


def _perguntar(item: dict, api_key: str, modelo: str) -> dict:
    from core.card_categorization import ler_resposta_jev_categoria, pergunta_jev_categoria
    from core.jev import system_one

    state, questions = pergunta_jev_categoria(item["descricao"], item["valor"])
    resp = system_one(state, questions, api_key=api_key, model=modelo)
    cat, prob = ler_resposta_jev_categoria(resp.answers)
    return {"sugestao": cat, "prob": prob, "latencia_s": resp.latencia_s,
            "tokens": resp.tokens_entrada, "modelo": resp.modelo}


def _rodar(lote: list[dict], api_key: str, modelo: str) -> list[dict]:
    from core.jev import JevErro

    out, seguidos = [], 0
    for i, it in enumerate(lote, 1):
        try:
            out.append({**it, **_perguntar(it, api_key, modelo)})
            seguidos = 0
        except JevErro as exc:
            seguidos += 1
            print(f"  [{i}/{len(lote)}] erro: {exc}")
            if seguidos >= MAX_ERROS_SEGUIDOS:
                print(f"ABORTADO: {seguidos} erros seguidos.")
                break
        if i % 25 == 0:
            print(f"  {i}/{len(lote)}")
        time.sleep(0.06)   # 1.200 req/min é o teto da API
    return out


def main() -> int:
    from core.card_categorization import DESCRICAO_CATEGORIA_JEV, REVIEW_SENTINEL
    from core.config import settings
    from core.database import get_engine
    from scripts.recategorizar_cartao import _load, _scratch_path

    ap = argparse.ArgumentParser(description="Mede o Jev na categorização do cartão (não grava).")
    ap.add_argument("--max", type=int, default=300, help="Máximo de estabelecimentos rotulados.")
    args = ap.parse_args()

    if not settings.TYPESAFE_API_KEY:
        print("TYPESAFE_API_KEY não configurada — abortado.")
        return 1
    if settings.MOCK_MODE or not settings.has_database or not settings.OWNER_USER_ID:
        print("Banco real ou OWNER_USER_ID indisponível — abortado.")
        return 1

    with get_engine().connect() as conn:
        items = _load(conn, settings.OWNER_USER_ID)

    rotulados, ambiguos = rotulados_unicos(items, DESCRICAO_CATEGORIA_JEV)
    lote = rotulados[: args.max]
    print(f"Lançamentos de cartão: {len(items)} · estabelecimentos rotulados: {len(rotulados)}"
          f" (descartados por rótulo ambíguo: {ambiguos}) · medindo: {len(lote)}")
    print("Origem dos rótulos medidos:", dict(Counter(r["origem"] for r in lote)))

    res = _rodar(lote, settings.TYPESAFE_API_KEY, settings.TYPESAFE_MODEL)
    print(f"\nModelo que respondeu: {sorted({r['modelo'] for r in res}) or '—'}\n")
    print("\n".join(resumir(res)))

    pend: dict[str, dict] = {}
    for it in items:
        if it["categoria_atual"] == REVIEW_SENTINEL:
            pend.setdefault(it["descricao"].upper(), {
                "descricao": it["descricao"], "valor": it["valor"],
                "rotulo": REVIEW_SENTINEL, "origem": "pendente", "n": 0})["n"] += 1
    sug = _rodar(list(pend.values()), settings.TYPESAFE_API_KEY, settings.TYPESAFE_MODEL)
    if sug:
        print(f"\nSugestões para os {len(sug)} estabelecimentos 'A revisar' (nada foi gravado):")
        for r in sorted(sug, key=lambda r: -(r["prob"] or 0)):
            p = f"{r['prob']:.2f}" if r["prob"] is not None else " —  "
            print(f"  {p}  {r['descricao'][:40]:<40} -> {r['sugestao']}")

    saida = _scratch_path("avaliacao_jev_cartao.csv")
    with open(saida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["descricao", "n", "origem", "rotulo", "sugestao_jev", "prob", "latencia_ms"])
        for r in res + sug:
            w.writerow([r["descricao"], r["n"], r["origem"], r["rotulo"], r["sugestao"],
                        "" if r["prob"] is None else f"{r['prob']:.3f}",
                        f"{r['latencia_s'] * 1000:.0f}"])
    print(f"\nDetalhe salvo em: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
