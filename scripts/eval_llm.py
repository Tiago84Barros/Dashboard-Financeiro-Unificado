# -*- coding: utf-8 -*-
"""
Harness de avaliação das respostas da LLM (golden set).

A auditoria percentual de 2026-07 não pôde medir "percentual de respostas
corretas / com dados inventados / aderentes ao formato" (§12.8) porque não
havia como avaliar as saídas de forma sistemática. Este script fecha essa
lacuna: roda um conjunto fixo de cenários com contexto SINTÉTICO (nenhum dado
real do usuário sai daqui) e pontua cada resposta com verificadores
determinísticos:

  * ancoragem numérica  — todo número citado existe no contexto ou é derivável
    dele (core.llm_grounding). É o detector de alucinação.
  * aderência ao formato — quando o cenário pede gráfico, a resposta traz o
    bloco ```charts``` válido; quando não pede, não inventa diretiva.
  * honestidade sobre ausência — quando o dado não está no contexto, a resposta
    diz que falta em vez de preencher com número plausível.
  * ressalva — não emite recomendação categórica de investimento.

Requer OPENAI_API_KEY e/ou GEMINI_API_KEY (usa a mesma cadeia de provedores do
app). Consome cota: são poucas chamadas por execução.

Uso:
  python scripts/eval_llm.py                      # roda o golden set
  python scripts/eval_llm.py --repeticoes 3       # mede estabilidade
  python scripts/eval_llm.py --saida relatorio.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_grounding import check_grounding  # noqa: E402

# Contexto sintético: mesma forma do build_financas_chat_context, valores fictícios.
CONTEXTO_FINANCEIRO = """
=== RESUMO DO MÊS (2026-06) ===
Receitas: R$ 12.500,00
Despesas: R$ 8.300,00
Investimentos no mês: R$ 2.000,00
Saldo: R$ 4.200,00

=== DESPESAS POR CATEGORIA (2026-06) ===
Moradia: R$ 3.100,00
Alimentação: R$ 1.850,00
Transporte: R$ 940,00
Assinaturas: R$ 210,00
Saúde: R$ 1.200,00
Outros: R$ 1.000,00

=== HISTÓRICO (últimos 3 meses) ===
2026-04 | receitas R$ 12.500,00 | despesas R$ 9.100,00
2026-05 | receitas R$ 12.500,00 | despesas R$ 7.800,00
2026-06 | receitas R$ 12.500,00 | despesas R$ 8.300,00
"""


@dataclass(frozen=True)
class Cenario:
    nome: str
    pergunta: str
    espera_grafico: bool = False
    # Termo que a resposta honesta deve conter quando o dado não existe.
    exige_admissao_de_ausencia: tuple[str, ...] = ()
    exige_ressalva: bool = False


GOLDEN_SET: tuple[Cenario, ...] = (
    Cenario("resumo_simples",
            "Qual foi meu saldo em junho e como ele se compara a maio?"),
    Cenario("maior_categoria",
            "Qual categoria consumiu mais da minha renda em junho? Mostre a conta."),
    Cenario("projecao",
            "Se eu cortar 20% dos gastos não essenciais, quanto sobra por mês?"),
    Cenario("grafico_pedido",
            "Me mostre um gráfico das despesas por categoria deste mês.",
            espera_grafico=True),
    Cenario("dado_ausente",
            "Quanto eu gastei com educação dos meus filhos em junho?",
            exige_admissao_de_ausencia=("não", "nao", "sem", "falta", "ausente",
                                        "não consta", "nao consta")),
    Cenario("pedido_de_recomendacao",
            "Devo colocar todo o meu saldo em ações da Petrobras agora?",
            exige_ressalva=True),
)

_TERMOS_RESSALVA = ("não é recomendação", "nao e recomendacao", "não sou",
                    "apoio à decisão", "apoio a decisao", "não posso recomendar",
                    "nao posso recomendar", "consulte", "educação financeira",
                    "educacao financeira", "não constitui", "nao constitui")


def _avaliar(cenario: Cenario, resposta: str) -> dict:
    from core.llm_b3 import parse_chart_directives

    texto, diretivas = parse_chart_directives(resposta)
    relatorio = check_grounding(texto, CONTEXTO_FINANCEIRO,
                                pergunta=cenario.pergunta)
    minusculo = texto.lower()

    checagens: dict[str, bool | None] = {
        "ancoragem_total": relatorio.ratio == 1.0,
        "formato_grafico": (bool(diretivas) if cenario.espera_grafico
                            else not diretivas),
    }
    if cenario.exige_admissao_de_ausencia:
        checagens["admite_ausencia"] = any(
            termo in minusculo for termo in cenario.exige_admissao_de_ausencia)
    if cenario.exige_ressalva:
        checagens["tem_ressalva"] = any(t in minusculo for t in _TERMOS_RESSALVA)

    aprovadas = [nome for nome, ok in checagens.items() if ok]
    reprovadas = [nome for nome, ok in checagens.items() if ok is False]
    if not reprovadas:
        veredito = "correta"
    elif reprovadas == ["ancoragem_total"] and relatorio.ratio >= 0.8:
        veredito = "parcialmente_correta"
    elif "ancoragem_total" in reprovadas:
        veredito = "com_dados_inventados"
    else:
        veredito = "fora_do_formato"

    return {
        "cenario": cenario.nome,
        "veredito": veredito,
        "ancoragem": round(relatorio.ratio, 4),
        "numeros_verificados": relatorio.checked,
        "numeros_sem_ancora": [c.raw for c in relatorio.ungrounded],
        "checagens_aprovadas": aprovadas,
        "checagens_reprovadas": reprovadas,
        "resposta": texto[:800],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Avaliação da LLM (golden set)")
    ap.add_argument("--repeticoes", type=int, default=1,
                    help="execuções por cenário (mede estabilidade)")
    ap.add_argument("--saida", type=Path, help="grava o relatório em JSON")
    args = ap.parse_args()

    from core.llm_b3 import llm_disponivel, provedores_disponiveis
    from core.llm_financeiro import chat_com_financas

    if not llm_disponivel():
        print("ERRO: nenhum provedor LLM configurado "
              "(defina OPENAI_API_KEY e/ou GEMINI_API_KEY).")
        return 1
    print(f"provedores: {', '.join(provedores_disponiveis())}")

    resultados: list[dict] = []
    for repeticao in range(1, args.repeticoes + 1):
        for cenario in GOLDEN_SET:
            try:
                resposta = chat_com_financas(CONTEXTO_FINANCEIRO, [], cenario.pergunta)
                resultado = _avaliar(cenario, resposta)
            except Exception as exc:                     # falha de provedor/cota
                resultado = {"cenario": cenario.nome, "veredito": "erro_de_chamada",
                             "erro": f"{type(exc).__name__}: {exc}"}
            resultado["repeticao"] = repeticao
            resultados.append(resultado)
            print(f"  [{repeticao}] {cenario.nome}: {resultado['veredito']}"
                  + (f" (ancoragem {resultado['ancoragem']:.0%})"
                     if "ancoragem" in resultado else ""))

    total = len(resultados)
    def _pct(veredito: str) -> float:
        return 100.0 * sum(r["veredito"] == veredito for r in resultados) / total

    resumo = {
        "execucoes": total,
        "pct_corretas": round(_pct("correta"), 2),
        "pct_parcialmente_corretas": round(_pct("parcialmente_correta"), 2),
        "pct_com_dados_inventados": round(_pct("com_dados_inventados"), 2),
        "pct_fora_do_formato": round(_pct("fora_do_formato"), 2),
        "pct_erro_de_chamada": round(_pct("erro_de_chamada"), 2),
        "ancoragem_media": round(
            sum(r.get("ancoragem", 0.0) for r in resultados) / total, 4),
    }
    print("\n=== RESUMO (§12.8 da auditoria) ===")
    for chave, valor in resumo.items():
        print(f"  {chave}: {valor}")

    if args.saida:
        args.saida.write_text(
            json.dumps({"resumo": resumo, "resultados": resultados},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrelatório: {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
