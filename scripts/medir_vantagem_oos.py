# -*- coding: utf-8 -*-
"""Mede a vantagem fora da amostra de B3 e EUA e grava o resultado (SCORE-05).

As duas telas já calculam o número; nenhuma o guarda. A tela de Grau de
Confiança roda em outra sessão e, sem arquivo, só podia dizer "não apurado" --
que é o mesmo que o motor de FII dizia por omissão até A-162. Este script fecha
o circuito: mede com uma configuração declarada, contra o armazém local, e grava
em `data/vantagem_oos.json` com data, versão e procedência.

Duas escolhas que decidem se o número significa alguma coisa:

**A medição roda o mesmo código da tela, não uma reimplementação.** Para a B3 o
script executa `views/portfolio_b3.py` sem cabeça (`AppTest`) e lê os pares
(ano, score, retorno) que a própria aba montou. Reimplementar o pipeline daria
um segundo número afirmando a mesma coisa -- e dois números discordantes sobre
"a vantagem do motor" é pior que nenhum.

**A configuração é fixa e fica gravada.** Excesso medido com 20 ativos e com 5
não são o mesmo número. Sem a configuração ao lado, o resultado não é
reproduzível nem contestável, e um gate que lê número irreprodutível é
decoração.

Métricas, por motor -- moedas diferentes, mesma pergunta ("o intervalo exclui o
zero?"):

  B3  -- Rank-IC anual do universo: score de abril/N contra o retorno de
         abril/N a março/N+1, todas as empresas de uma vez. É previsão à
         frente, não ajuste dentro da amostra.
  EUA -- excesso por período da carteira sobre o equal-weight do universo, no
         walk-forward anual de `core/us_backtest.walk_forward`.

Uso::

    python scripts/medir_vantagem_oos.py --motor us
    python scripts/medir_vantagem_oos.py --motor b3
    python scripts/medir_vantagem_oos.py --motor todos

O armazém local precisa estar de pé (`docker ps --filter name=dfu_warehouse`).
Sem ele o script falha dizendo isso -- nunca grava medição a partir da vitrine
publicada, que não carrega o histórico.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Configuração declarada da medição da B3: os defaults da aba, sem filtro
# opcional ligado. Mudar aqui muda o número -- por isso ele vai gravado junto.
B3_CONFIG = {
    "pb3_thr_selic_hist": 15.0,
    "pb3_teto_setor": 100,
    "pb3_teto_ciclico": 100,
    "pb3_criterio_aprov2": "Econômico (Brasil)",
    "pb3_min_empresas": 5,
    "pb3_cheapness": 0,
    "pb3_roic_spread": 0.0,
}
US_TOP_N = 20
US_WEIGHTING = "score"

BOOTSTRAP_AMOSTRAS = 2000


def _apontar_para_armazem_local() -> str:
    """Redireciona `DATABASE_URL` para o armazém local antes de qualquer import.

    A vitrine publicada não tem `score_vintages` nem o histórico de preços que a
    B3 usa; medir contra ela devolveria "sem períodos utilizáveis" e, se alguém
    gravasse isso, viraria uma reprovação por falta de dado disfarçada de
    reprovação por falta de vantagem.
    """
    url = os.getenv("DFU_WAREHOUSE_URL") or ""
    if not url:
        # `docker inspect` nem sempre esta ao alcance de quem roda o script
        # (sandbox, agendador, container). Ai a URL vem por variavel de
        # ambiente -- nunca do `settings`, que aponta para a nuvem.
        try:
            from scripts.publish_fii_selection_from_local import _warehouse_url
            url = _warehouse_url()
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"armazem local inalcancavel ({type(exc).__name__}): suba o "
                f"container dfu_warehouse ou informe DFU_WAREHOUSE_URL") from exc
    if not url:
        raise SystemExit(
            "armazem local inalcancavel: suba o container dfu_warehouse "
            "(docker ps --filter name=dfu_warehouse)")
    os.environ["DATABASE_URL"] = url
    return url


# ── EUA ──────────────────────────────────────────────────────────────────────

def medir_us() -> dict:
    import core.us_data as us
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
    from core.vantagem_oos import FORMATO_PERCENTUAL, nova_medicao

    resultado = us.backtest(top_n=US_TOP_N, weighting=US_WEIGHTING)
    if not resultado.get("ok"):
        raise SystemExit(f"backtest EUA nao rodou: {resultado.get('reason')}")

    ci = resultado.get("bootstrap_excess") or {}
    return nova_medicao(
        motor="us",
        versao_metodologia=US_FUNDAMENTAL_SCORE_VERSION,
        metrica="excesso por periodo sobre o equal-weight do universo",
        formato=FORMATO_PERCENTUAL,
        media=ci.get("mean"),
        ic_low=ci.get("ci_low"),
        ic_high=ci.get("ci_high"),
        n_periodos=int(resultado.get("n_periods") or 0),
        configuracao=f"walk-forward anual, top_n={US_TOP_N}, pesos={US_WEIGHTING}",
        fonte="armazem local, market_us.score_vintages + prices_monthly",
        janela=(str(resultado.get("start_date")), str(resultado.get("end_date"))),
        extras={
            "rank_ic_medio": (resultado.get("rank_ic") or {}).get("mean"),
            "rank_ic_t": (resultado.get("rank_ic") or {}).get("t_stat"),
            "rank_ic_n": (resultado.get("rank_ic") or {}).get("n"),
            "bootstrap_amostras": ci.get("samples"),
        },
    )


# ── B3 ───────────────────────────────────────────────────────────────────────

def _desligar_llm() -> None:
    """Corta as chamadas de LLM da aba durante a medicao.

    A tese narrativa nao entra em nenhuma conta desta medicao, mas a aba a pede
    por empresa; com a cota da OpenAI esgotada, cada pedido vira 429 com
    backoff e a medicao trava sem nunca falhar -- o pior dos dois mundos. A
    chave nao pode ser apagada por variavel de ambiente porque `llm_b3` le
    `settings.OPENAI_API_KEY` primeiro; entao zeramos os clientes.
    """
    try:
        import core.llm_b3 as llm
    except Exception:  # noqa: BLE001 -- sem LLM disponivel, nada a desligar
        return
    for nome in ("_get_openai_client", "_get_gemini_client"):
        if hasattr(llm, nome):
            setattr(llm, nome, lambda *a, **k: None)


_SCRIPT_B3 = """
import views.portfolio_b3 as view
view.render(show_header=False)
"""


def _rodar_aba_b3(timeout: int = 1800):
    """Executa a Criação de Portfólio B3 sem cabeça e devolve os resultados.

    Chave ausente é diferente de lista vazia: ausente significa que a execução
    não chegou ao fim, e tratar isso como "nenhum par de IC" gravaria uma
    medição vazia como se fosse resultado -- mesmo erro que quase passou por
    defeito de determinismo em 29/07/2026.
    """
    from streamlit.testing.v1 import AppTest

    _desligar_llm()
    app = AppTest.from_string(_SCRIPT_B3).run(timeout=timeout)
    for chave, valor in B3_CONFIG.items():
        for colecao in (app.number_input, app.selectbox, app.slider):
            try:
                colecao(key=chave).set_value(valor)
                break
            except (KeyError, ValueError):
                continue
    app.button(key="pb3_rodar").click().run(timeout=timeout)
    if app.exception:
        raise SystemExit(f"aba da B3 lancou excecao: {app.exception[0].value}")
    try:
        return list(app.session_state["pb3_resultados"] or [])
    except (KeyError, AttributeError) as exc:
        raise SystemExit(
            "a aba da B3 nao concluiu (timeout ou interrupcao): resultado "
            "INCONCLUSIVO, nada foi gravado") from exc


def _fonte_efetiva_b3() -> str:
    """De qual banco a aba da B3 REALMENTE leu -- e nao de qual mandamos ler.

    `core.b3_db` tem prioridade propria (`SUPABASE_DB_URL_B3`/`SUPABASE_DB_URL`,
    achado A-009) e ignora o `DATABASE_URL` que este script aponta para o
    armazem local. O numero continua valido -- e a mesma fonte que a tela usa em
    producao -- mas carimbar "armazem local" seria procedencia falsa, o defeito
    exato que este modulo existe para impedir.

    Grava a CLASSE da fonte, nunca a URL: `data/vantagem_oos.json` e versionado,
    e host e usuario do projeto sao identidade de infraestrutura -- nao viram
    conteudo de repositorio so para responder "de onde veio o numero".
    """
    from core.b3_db import _resolve_url
    try:
        url = (_resolve_url() or "").lower()
    except Exception as exc:  # noqa: BLE001
        return f"fonte nao resolvida ({type(exc).__name__})"
    if not url:
        return "fonte nao resolvida"
    if "localhost" in url or "127.0.0.1" in url:
        return "armazem local"
    return "banco publicado do App 1 (nuvem)"


def medir_b3(resultados=None) -> dict:
    from core.b3_methodology import SCORE_VERSION
    from core.b3_pooled_evidence import pooled_yearly_ics
    from core.us_backtest import bootstrap_mean_ci
    from core.vantagem_oos import FORMATO_COEFICIENTE, nova_medicao

    resultados = _rodar_aba_b3() if resultados is None else resultados
    pares: list[tuple] = []
    for res in resultados:
        pares.extend(res.get("ic_pairs") or [])
    if not pares:
        raise SystemExit("nenhum par (ano, score, retorno): sem base para medir")

    ics = pooled_yearly_ics(pares)
    if len(ics) < 2:
        raise SystemExit(f"apenas {len(ics)} ano(s) com Rank-IC calculavel")
    valores = [ics[ano] for ano in sorted(ics)]
    ci = bootstrap_mean_ci(valores, samples=BOOTSTRAP_AMOSTRAS)
    anos = sorted(ics)

    return nova_medicao(
        motor="b3",
        versao_metodologia=SCORE_VERSION,
        metrica="Rank-IC anual do universo (score de t vs retorno t->t+1)",
        formato=FORMATO_COEFICIENTE,
        media=ci.get("mean"),
        ic_low=ci.get("ci_low"),
        ic_high=ci.get("ci_high"),
        n_periodos=len(anos),
        configuracao=(f"defaults da aba: {json.dumps(B3_CONFIG, ensure_ascii=False)}"),
        fonte=f"aba de Criacao de Portfolio B3 sobre {_fonte_efetiva_b3()}",
        janela=(str(anos[0]), str(anos[-1])),
        extras={
            "n_pares": len(pares),
            "n_medio_ativos_ano": round(len(pares) / len(anos), 1),
            "ic_por_ano": {str(a): round(float(ics[a]), 4) for a in anos},
            "bootstrap_amostras": ci.get("samples"),
        },
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--motor", choices=("b3", "us", "todos"), default="todos")
    ap.add_argument("--dry-run", action="store_true",
                    help="mede e imprime sem gravar")
    args = ap.parse_args()

    url = _apontar_para_armazem_local()
    print(f"armazem local: {url.split('@')[-1]}")

    from core.vantagem_oos import CAMINHO_MEDICAO, gravar_medicao

    alvos = ("b3", "us") if args.motor == "todos" else (args.motor,)
    for motor in alvos:
        print(f"\n== medindo {motor} ==")
        medicao = medir_b3() if motor == "b3" else medir_us()
        print(json.dumps(medicao, indent=2, ensure_ascii=False))
        if not args.dry_run:
            gravar_medicao(medicao)
            print(f"gravado em {CAMINHO_MEDICAO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
