# -*- coding: utf-8 -*-
"""Avalia se um modelo LLM faz JULGAMENTOS bons o bastante para esta tela.

Trocar de provedor por reputacao de modelo e barato e errado: o que decide e o
comportamento na tarefa real, com o prompt real. Este script roda o mesmo
`_PROMPT_EMPRESA` que a aba de Criacao de Portfolio B3 usa em producao, contra
casos em que a resposta certa e verificavel, e pontua o resultado por criterio.

Os dois casos sao armadilhas de analista, nao exercicios de escrita:

  ARMADILHA  score quantitativo alto (82) sobre uma serie de 10 anos que se
             deteriora -- ROE de 22 para 4, margem caindo, FCO negativo em tres
             anos, divida dobrando e DY inflado pelo preco em queda. Um analista
             competente NAO chama isso de "forte"; ele aponta a contradicao
             entre o score e os fundamentos, e reconhece a armadilha de
             dividendos. Um modelo fraco ancora no 82 e concorda com ele.

  VIRADA     score moderado (58) sobre uma serie que melhora de forma
             consistente -- desalavancagem, FCO virando forte, barata contra os
             pares. Aqui o erro simetrico e chamar de "fraca" porque o score nao
             e alto.

Os dois juntos separam quem le a serie de quem parafraseia o score: um modelo
que so segue o numero acerta um caso e erra o outro; a media nao salva.

Criterios (todos mecanicos, nenhum julgado por LLM):

  schema      JSON valido, chaves EXATAS, enums e faixas legais. A tela consome
              o dicionario direto; campo faltando vira fallback silencioso.
  ancoragem   fracao dos numeros citados no texto que existem na entrada. E o
              teste de alucinacao: numero inventado em tese de investimento e o
              pior defeito possivel aqui.
  pares       cita algum par do mesmo segmento, que o prompt exige.
  generico    frases proibidas pelo prompt ("desempenho solido") sem numero.
  julgamento  o veredito do caso, que e onde mora a pergunta do usuario.

Uso::

    python scripts/avaliar_provedor_llm.py --modelos nvidia/nemotron-3-super-120b-a12b
    python scripts/avaliar_provedor_llm.py            # todos os candidatos

Chaves: NVIDIA_TOKEN (endpoint da NVIDIA), OPENROUTER_API_KEY (OpenRouter),
GEMINI_API_KEY. Nenhuma e impressa; o relatorio sai em artifacts/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CANDIDATOS = (
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-3-nano-30b-a3b",
)

SCHEMA = {
    "perspectiva": ("forte", "moderada", "fraca"),
    "acao_sugerida": ("manter", "aumentar", "reduzir", "revisar"),
    "confianca": (0, 100),
    "score_qualitativo": (0, 100),
    "resumo": str, "alerta_principal": str, "proxima_acao": str,
    "riscos": list, "catalisadores": list, "sensibilidade_macro": list,
    "alocacao_sugerida_pct": (0.0, 25.0),
    "justificativa_alocacao": str, "tese_final": str,
}

GENERICAS = ("desempenho solido", "desempenho sólido", "cenario favoravel",
             "cenário favorável", "fundamentos solidos",
             "fundamentos sólidos", "boas perspectivas",
             "empresa bem posicionada")

TEXTO_LIVRE = ("resumo", "alerta_principal", "proxima_acao", "tese_final",
               "justificativa_alocacao")


# -- casos --------------------------------------------------------------------

def _caso_armadilha() -> dict:
    anos = list(range(2016, 2026))
    mult = pd.DataFrame({
        "Data": [f"{a}-12-31" for a in anos],
        "ROE": [22.4, 21.1, 19.8, 17.2, 14.0, 11.5, 9.1, 6.8, 5.2, 4.1],
        "ROIC": [18.0, 17.1, 15.4, 13.9, 11.0, 9.2, 7.4, 5.9, 4.6, 3.8],
        "Margem_Liquida": [16.2, 15.4, 14.1, 12.0, 9.4, 7.8, 6.0, 4.2, 3.1, 2.4],
        "DY": [4.1, 4.4, 5.0, 5.8, 7.2, 8.9, 10.4, 12.1, 13.8, 15.2],
        "P/L": [14.2, 13.1, 11.8, 10.4, 8.9, 7.6, 6.2, 5.1, 4.4, 3.9],
        "P/VP": [2.8, 2.6, 2.2, 1.9, 1.5, 1.2, 0.9, 0.7, 0.6, 0.5],
        "Endividamento_Total": [38.0, 41.0, 45.0, 52.0, 61.0, 68.0, 74.0, 79.0,
                                84.0, 88.0],
        "Liquidez_Corrente": [1.8, 1.7, 1.6, 1.4, 1.3, 1.1, 1.0, 0.9, 0.8, 0.7],
    })
    fin = pd.DataFrame({
        "Data": [f"{a}-12-31" for a in anos],
        "Receita_Liquida": [8.2e9, 8.6e9, 8.9e9, 8.7e9, 8.1e9, 8.4e9, 8.0e9,
                            7.6e9, 7.1e9, 6.8e9],
        "Lucro_Liquido": [1.33e9, 1.32e9, 1.26e9, 1.04e9, 7.6e8, 6.6e8, 4.8e8,
                          3.2e8, 2.2e8, 1.6e8],
        "Divida_Liquida": [3.1e9, 3.5e9, 4.0e9, 4.8e9, 5.9e9, 6.8e9, 7.6e9,
                           8.3e9, 9.0e9, 9.6e9],
        "FCO": [1.9e9, 1.8e9, 1.5e9, 1.1e9, 6.0e8, 2.0e8, -1.5e8, -3.4e8,
                -5.1e8, -6.2e8],
        "Dividendos": [5.4e8, 5.8e8, 6.3e8, 6.6e8, 6.9e8, 7.1e8, 7.4e8, 7.6e8,
                       7.8e8, 8.0e8],
    })
    return {
        "nome": "ARMADILHA",
        "kwargs": dict(
            ticker="XPTO3", nome="Companhia Exemplo Industrial",
            setor="Bens Industriais", segmento="Maquinas e Equipamentos",
            peso_pct=6.5, score=82.0, alpha_selic=3.4,
            df_mult=mult, df_fin=fin,
            macro_hist={2023: {"selic": 0.1175, "ipca": 0.0462, "pib": 0.029},
                        2024: {"selic": 0.1225, "ipca": 0.0483, "pib": 0.034},
                        2025: {"selic": 0.1500, "ipca": 0.0510, "pib": 0.021}},
            portfolio_ctx=("Carteira de 12 ativos, concentracao setorial de 22% "
                           "em industriais."),
            peers_ctx=("  PARE3: P/L=9.8, ROE=15.2, DY=5.1\n"
                       "  PARF4: P/L=11.4, ROE=17.8, DY=4.2"),
        ),
        # O veredito verificavel: nao pode ser "forte", e a tese precisa nomear
        # a deterioracao. Aceitar "forte" aqui e o erro que custa dinheiro.
        "veredito": lambda r: str(r.get("perspectiva")) in ("fraca", "moderada"),
        "evidencia": ("fco", "fluxo de caixa", "caixa operacional", "divida",
                      "dívida", "alavancagem", "deterior", "queda",
                      "armadilha"),
    }


def _caso_virada() -> dict:
    anos = list(range(2016, 2026))
    mult = pd.DataFrame({
        "Data": [f"{a}-12-31" for a in anos],
        "ROE": [3.2, 2.1, 4.4, 6.8, 8.1, 10.9, 13.2, 15.8, 18.1, 19.7],
        "ROIC": [2.8, 1.9, 3.9, 5.7, 7.2, 9.4, 11.6, 13.9, 15.4, 16.8],
        "Margem_Liquida": [1.9, 1.2, 2.8, 4.1, 5.6, 7.4, 9.1, 10.8, 12.2, 13.4],
        "DY": [0.8, 0.5, 1.2, 1.8, 2.4, 3.1, 3.8, 4.4, 4.9, 5.3],
        "P/L": [28.0, 34.0, 22.0, 17.0, 14.0, 11.0, 9.4, 8.2, 7.4, 6.9],
        "P/VP": [1.1, 0.9, 1.0, 1.1, 1.2, 1.2, 1.3, 1.3, 1.4, 1.4],
        "Endividamento_Total": [82.0, 86.0, 78.0, 71.0, 64.0, 56.0, 48.0, 41.0,
                                35.0, 30.0],
        "Liquidez_Corrente": [0.8, 0.7, 0.9, 1.1, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2],
    })
    fin = pd.DataFrame({
        "Data": [f"{a}-12-31" for a in anos],
        "Receita_Liquida": [4.1e9, 3.9e9, 4.4e9, 5.0e9, 5.6e9, 6.4e9, 7.2e9,
                            8.1e9, 9.0e9, 9.8e9],
        "Lucro_Liquido": [7.8e7, 4.7e7, 1.23e8, 2.05e8, 3.14e8, 4.74e8, 6.55e8,
                          8.75e8, 1.10e9, 1.31e9],
        "Divida_Liquida": [5.2e9, 5.4e9, 5.0e9, 4.5e9, 3.9e9, 3.2e9, 2.6e9,
                           2.0e9, 1.5e9, 1.1e9],
        "FCO": [2.0e8, 1.4e8, 3.6e8, 5.9e8, 8.2e8, 1.15e9, 1.48e9, 1.82e9,
                2.15e9, 2.44e9],
        "Dividendos": [0.0, 0.0, 3.0e7, 6.0e7, 1.1e8, 1.8e8, 2.6e8, 3.5e8,
                       4.4e8, 5.2e8],
    })
    return {
        "nome": "VIRADA",
        "kwargs": dict(
            ticker="ABCD4", nome="Companhia Exemplo Servicos",
            setor="Consumo Ciclico", segmento="Comercio Varejista",
            peso_pct=4.0, score=58.0, alpha_selic=-0.6,
            df_mult=mult, df_fin=fin,
            macro_hist={2023: {"selic": 0.1175, "ipca": 0.0462, "pib": 0.029},
                        2024: {"selic": 0.1225, "ipca": 0.0483, "pib": 0.034},
                        2025: {"selic": 0.1500, "ipca": 0.0510, "pib": 0.021}},
            portfolio_ctx="Carteira de 12 ativos, sem exposicao a varejo.",
            peers_ctx=("  VARE3: P/L=12.6, ROE=14.1, DY=3.0\n"
                       "  VARF4: P/L=15.2, ROE=11.7, DY=2.4"),
        ),
        "veredito": lambda r: str(r.get("perspectiva")) in ("forte", "moderada"),
        "evidencia": ("melhor", "recupera", "desalavanca", "reduc", "reduç",
                      "divida liquida", "dívida líquida", "fco",
                      "crescimento"),
    }


CASOS = (_caso_armadilha, _caso_virada)


# -- clientes -----------------------------------------------------------------

def _env(nome: str) -> str:
    v = os.environ.get(nome, "")
    if v:
        return v.strip()
    arq = ROOT / ".env"
    if arq.exists():
        m = re.search(rf'^{nome}\s*=\s*"?([^"\n]+)"?',
                      arq.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip()
    return ""


def _cliente(modelo: str):
    """Devolve o client OpenAI-compativel do provedor que serve o modelo."""
    from openai import OpenAI
    if modelo.startswith("gemini"):
        return OpenAI(
            api_key=_env("GEMINI_API_KEY"), timeout=180,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    chave_or = _env("OPENROUTER_API_KEY")
    if chave_or:
        return OpenAI(api_key=chave_or, timeout=180,
                      base_url="https://openrouter.ai/api/v1")
    chave_nv = _env("NVIDIA_TOKEN")
    if not chave_nv:
        raise SystemExit("sem OPENROUTER_API_KEY nem NVIDIA_TOKEN")
    return OpenAI(api_key=chave_nv, timeout=180,
                  base_url="https://integrate.api.nvidia.com/v1")


# -- pontuacao ----------------------------------------------------------------

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _numeros(texto: str) -> list[float]:
    saida = []
    for bruto in _NUM.findall(texto):
        try:
            saida.append(float(bruto.replace(",", ".")))
        except ValueError:
            continue
    return saida


def _ancoragem(resposta: dict, prompt: str) -> tuple[float, list[float]]:
    """Fracao dos numeros do texto que existem na entrada.

    Tolera arredondamento (1%) e ignora inteiros pequenos (0-12), que sao
    contagem e ordinal ("3 anos", "2 riscos") e nao afirmacao sobre a empresa.
    Anos entram na checagem: citar 2019 quando a serie comeca em 2016 e erro.
    """
    fonte = _numeros(prompt)
    citados, ancorados, orfaos = 0, 0, []
    for campo in TEXTO_LIVRE:
        for n in _numeros(str(resposta.get(campo) or "")):
            if abs(n) <= 12 and float(n).is_integer():
                continue
            citados += 1
            if any(abs(n - f) <= max(0.01 * abs(f), 0.05) for f in fonte):
                ancorados += 1
            else:
                orfaos.append(n)
    return (1.0 if citados == 0 else ancorados / citados), orfaos


def _schema_ok(r: dict) -> tuple[bool, list[str]]:
    problemas = []
    if set(r) != set(SCHEMA):
        faltam, sobram = set(SCHEMA) - set(r), set(r) - set(SCHEMA)
        if faltam:
            problemas.append("faltam: " + ",".join(sorted(faltam)))
        if sobram:
            problemas.append("extras: " + ",".join(sorted(sobram)))
    for campo, regra in SCHEMA.items():
        v = r.get(campo)
        if v is None:
            continue
        if isinstance(regra, tuple) and isinstance(regra[0], str):
            if str(v) not in regra:
                problemas.append(f"{campo}={v!r} fora do enum")
        elif isinstance(regra, tuple):
            try:
                if not (regra[0] <= float(v) <= regra[1]):
                    problemas.append(f"{campo}={v!r} fora da faixa")
            except (TypeError, ValueError):
                problemas.append(f"{campo}={v!r} nao numerico")
        elif regra is list and not isinstance(v, list):
            problemas.append(f"{campo} nao e lista")
    return (not problemas), problemas


def _avaliar(caso: dict, resposta: dict, prompt: str) -> dict:
    texto = " ".join(str(resposta.get(c) or "") for c in TEXTO_LIVRE).lower()
    texto += " " + " ".join(str(x).lower() for x in (resposta.get("riscos") or []))
    ok_schema, problemas = _schema_ok(resposta)
    anc, orfaos = _ancoragem(resposta, prompt)
    pares = [p for p in re.findall(r"\b[A-Z]{4}\d\b", prompt)
             if p != caso["kwargs"]["ticker"]]
    veredito = bool(caso["veredito"](resposta))
    evidencia = any(e in texto for e in caso["evidencia"])
    return {
        "schema_ok": ok_schema,
        "schema_problemas": problemas,
        "ancoragem": round(anc, 3),
        "numeros_orfaos": orfaos[:6],
        "cita_par": any(p.lower() in texto for p in pares),
        "genericas": [g for g in GENERICAS if g in texto],
        "veredito_ok": veredito,
        "evidencia_ok": evidencia,
        "julgamento_ok": veredito and evidencia,
        "perspectiva": resposta.get("perspectiva"),
        "acao": resposta.get("acao_sugerida"),
        "tese_final": str(resposta.get("tese_final") or "")[:400],
    }


# -- execucao -----------------------------------------------------------------

def _completar(client, modelo: str, prompt: str) -> tuple[str, str | None]:
    """Repete o caminho de `core.llm_b3._chat_complete`, inclusive a degradacao.

    Producao pede `response_format={"type": "json_object"}` e, se o provedor
    recusar, refaz a chamada sem ele. Avaliar sem isso mediria um caminho que a
    tela nunca percorre -- e e justamente no JSON mode que provedores novos
    costumam divergir.
    """
    msgs = [{"role": "user", "content": prompt}]
    try:
        resp = client.chat.completions.create(
            model=modelo, messages=msgs, temperature=0.2,
            response_format={"type": "json_object"})
        return (resp.choices[0].message.content or ""), None
    except Exception as exc_json:  # noqa: BLE001
        primeiro = f"{type(exc_json).__name__}: {exc_json}"
    try:
        resp = client.chat.completions.create(
            model=modelo, messages=msgs, temperature=0.2)
        return (resp.choices[0].message.content or ""), None
    except Exception as exc:  # noqa: BLE001
        return "", f"json_mode[{primeiro[:120]}] + simples[{type(exc).__name__}: {exc}]"


def rodar(modelo: str, repeticoes: int = 1) -> dict:
    from core.llm_b3 import (
        _PROMPT_EMPRESA,
        _fmt_dre,
        _fmt_macro,
        _fmt_multiplos,
        _parse_json,
    )

    client = _cliente(modelo)
    saida = {"modelo": modelo, "casos": []}
    # Repeticao existe porque schema quebrado uma vez em tres e um defeito de
    # producao, nao ruido: a tela cai no fallback silencioso naquela empresa.
    for rodada in range(max(1, repeticoes)):
      for fabrica in CASOS:
        caso = fabrica()
        k = caso["kwargs"]
        prompt = _PROMPT_EMPRESA.format(
            ticker=k["ticker"], nome=k["nome"], setor=k["setor"],
            segmento=k["segmento"], peso_pct=k["peso_pct"], score=k["score"],
            alpha_selic=k["alpha_selic"],
            multiplos=_fmt_multiplos(k["df_mult"]), dre=_fmt_dre(k["df_fin"]),
            peers_ctx=k["peers_ctx"], macro=_fmt_macro(k["macro_hist"]),
            rag_context="  Nenhum documento CVM disponivel para este ativo.",
            portfolio_ctx=k["portfolio_ctx"])
        t0 = time.time()
        bruto, erro = _completar(client, modelo, prompt)
        segundos = round(time.time() - t0, 1)
        if erro:
            saida["casos"].append({"caso": caso["nome"], "erro": erro,
                                   "segundos": segundos})
            continue
        # Sentinela impossivel: se o parse falhar, `_parse_json` devolveria o
        # fallback e a nota mediria o fallback, nao o modelo.
        resposta = _parse_json(bruto, {"__parse_falhou__": True})
        if resposta.get("__parse_falhou__"):
            saida["casos"].append({"caso": caso["nome"], "segundos": segundos,
                                   "erro": "resposta nao e JSON",
                                   "bruto": bruto[:400]})
            continue
        nota = _avaliar(caso, resposta, prompt)
        nota.update({"caso": caso["nome"], "segundos": segundos,
                     "rodada": rodada + 1})
        saida["casos"].append(nota)
    return saida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modelos", nargs="*", default=list(CANDIDATOS))
    ap.add_argument("--repeticoes", type=int, default=1)
    ap.add_argument("--saida", default="artifacts/avaliacao_llm_nemotron.json")
    args = ap.parse_args()

    todos = []
    for modelo in args.modelos:
        print(f"\n=== {modelo} ===", flush=True)
        r = rodar(modelo, args.repeticoes)
        todos.append(r)
        for c in r["casos"]:
            if c.get("erro"):
                print(f"  {c['caso']}: ERRO {c['erro'][:160]}")
                continue
            print(f"  [{c.get('rodada', 1)}] {c['caso']}: schema={c['schema_ok']} "
                  f"ancoragem={c['ancoragem']} par={c['cita_par']} "
                  f"julgamento={c['julgamento_ok']} "
                  f"({c['perspectiva']}/{c['acao']}) {c['segundos']}s")
            if c["numeros_orfaos"]:
                print(f"      numeros sem lastro: {c['numeros_orfaos']}")
            if c["schema_problemas"]:
                print(f"      schema: {c['schema_problemas']}")

    destino = ROOT / args.saida
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(todos, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    print(f"\nrelatorio: {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
