"""Confere cada vitrine pelo mesmo caminho que a tela usa.

Vale para FIIs, EUA e B3 (substituiu o `verificar_frescor_vitrine_fii.py`, que
só olhava um módulo). O que ele tem de diferente de um `SELECT count(*)` é a
escolha do ponto de medição: a checagem passa pelo leitor que a decisão consome
-- `load_fii_methodology_inputs`, `load_snapshot_scored`, `load_multiplos_todos`
-- e não pela tabela crua.

Isso não é preciosismo. Em 31/08/2026 a vitrine de FIIs venceu, a leitura
devolveu linhas sem as colunas de métrica, e a tela creditou a falha aos filtros
de elegibilidade: os 394 fundos apareceram como reprovados por métrica ausente
(PR #190). A tabela tinha linhas; o quadro que a decisão lia não tinha colunas.
Uma checagem na tabela teria dito "está tudo certo".

Daí as quatro perguntas por vitrine, nesta ordem:

1. lê sem erro?
2. veio com linhas?
3. **as colunas que a decisão lê estão presentes?**  (a que pegou o PR #190)
4. a idade está dentro do limite do módulo?

Sai com 1 se qualquer vitrine reprovar; 0 se todas servirem.

Uso:
    python scripts/verificar_frescor_vitrines.py
    python scripts/verificar_frescor_vitrines.py --modulo fii --modulo us
    python scripts/verificar_frescor_vitrines.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.frescor import idade_em_dias as _idade_em_dias  # noqa: E402
from core.frescor import idade_limite  # noqa: E402

# O limite de cada módulo sai de `core.frescor`, que é o mesmo que a tela usa
# para decidir se avisa o usuário. Ter um número aqui e outro lá seria a
# verificação aprovando o que a tela reprova -- que não verifica nada. O de FII
# é o mais apertado porque é o único com validade dura no código:
# `fii_methodology` recusa snapshot com mais de 4 dias, então uma vitrine de 5
# dias não deixa a tela degradada, deixa a tela errada.
IDADE_MAXIMA = {m: idade_limite(m) for m in ("fii", "us", "b3")}


def _resultado(modulo, ok, linhas=0, idade=None, detalhe="") -> dict:
    return {"modulo": modulo, "ok": bool(ok), "linhas": int(linhas),
            "idade_dias": idade, "detalhe": detalhe}


def _conferir_quadro(modulo, frame, exigidas, idade) -> dict:
    erro = frame.attrs.get("load_error")
    if erro:
        return _resultado(modulo, False, detalhe=f"a vitrine não pôde ser lida ({erro})")
    if frame.empty:
        return _resultado(modulo, False, detalhe="a vitrine foi lida vazia")
    faltando = [c for c in exigidas if c not in frame.columns]
    if faltando:
        return _resultado(modulo, False, len(frame), idade,
                          f"faltam as colunas que a decisão lê: {faltando}")
    limite = IDADE_MAXIMA[modulo]
    if idade is not None and idade > limite:
        return _resultado(modulo, False, len(frame), idade,
                          f"idade {idade}d acima do limite {limite}d")
    return _resultado(modulo, True, len(frame), idade, "legível, com colunas de decisão")


def verificar_fii() -> dict:
    import core.market_read as mr

    mr._reset_fii_snapshot_memory_cache()
    mr.load_fii_methodology_inputs.clear()
    frame = mr.load_fii_methodology_inputs()
    return _conferir_quadro(
        "fii", frame,
        ("dy_12m", "pvp", "liquidez_diaria", "history_months", "max_drawdown"),
        frame.attrs.get("snapshot_age_days"),
    )


def verificar_us() -> dict:
    import core.us_read as ur

    for fn in ("load_snapshot_scored", "load_snapshot_overview"):
        getattr(getattr(ur, fn), "clear", lambda: None)()
    frame = ur.load_snapshot_scored()
    idade = _idade_em_dias((ur.load_snapshot_overview() or {}).get("last_update"))
    resultado = _conferir_quadro(
        "us", frame, ("symbol", "score", "sector"), idade)
    if not resultado["ok"]:
        return resultado
    # O painel PIT é publicado por outro alvo e falha de outro jeito: ele volta
    # vazio com o motivo em attrs em vez de levantar. Sem olhar aqui, subir
    # US_FUNDAMENTAL_SCORE_VERSION sem republicar a safra desligaria o backtest
    # em silêncio -- e a vitrine de score, essa, continuaria passando.
    try:
        painel = ur.load_score_panel()
        if painel.empty:
            resultado["detalhe"] += ("; ATENÇÃO painel PIT vazio: "
                                     + str(painel.attrs.get("motivo", "sem motivo")))
        else:
            resultado["detalhe"] += f"; painel PIT com {len(painel)} linhas"
    except Exception as exc:  # noqa: BLE001
        resultado["detalhe"] += f"; painel PIT falhou: {exc}"
    return resultado


def verificar_b3() -> dict:
    import core.market_health as mh
    import core.market_read as mr

    getattr(mr.load_multiplos_todos, "clear", lambda: None)()
    frame = mr.load_multiplos_todos()
    # A idade NÃO sai da coluna `data` do quadro: ali `data` é 31/12 do exercício
    # de referência (`_attach_data`), uma data contábil que fica no futuro
    # durante todo o ano corrente. Medida assim, a B3 acusaria idade negativa e
    # passaria no teste de frescor para sempre, inclusive com a publicação
    # parada há meses. Quem sabe quando a vitrine foi escrita é `updated_at` de
    # `market.calculated_metrics`, que é o que `market_health_summary` lê.
    frescor = (mh.market_health_summary() or {}).get("frescor") or {}
    idade = _idade_em_dias(frescor.get("ultimo_calc"))
    return _conferir_quadro(
        "b3", frame, ("Ticker", "P/L", "P/VP", "DY", "ROE"), idade)


VERIFICADORES = {"fii": verificar_fii, "us": verificar_us, "b3": verificar_b3}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--modulo", action="append", dest="modulos",
                   choices=sorted(VERIFICADORES), default=None,
                   help="Verifica só estes módulos (padrão: todos).")
    p.add_argument("--json", action="store_true", help="Saída em JSON.")
    args = p.parse_args(argv)

    resultados = []
    for modulo in (args.modulos or sorted(VERIFICADORES)):
        try:
            resultados.append(VERIFICADORES[modulo]())
        except Exception as exc:  # noqa: BLE001
            # Exceção na leitura é reprovação, nunca "sem informação": foi
            # justamente um quadro devolvido em vez de um erro que fez os 394
            # fundos passarem por inelegíveis.
            resultados.append(_resultado(
                modulo, False, detalhe=f"{type(exc).__name__}: {exc}"))

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, default=str))
    else:
        for r in resultados:
            marca = "OK      " if r["ok"] else "REPROVOU"
            idade = "?" if r["idade_dias"] is None else f"{r['idade_dias']}d"
            print(f"{marca} {r['modulo']:4s} linhas={r['linhas']:<6} "
                  f"idade={idade:<5} {r['detalhe']}")
    return 0 if all(r["ok"] for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
