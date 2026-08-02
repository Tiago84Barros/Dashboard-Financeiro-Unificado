# -*- coding: utf-8 -*-
"""Harness de avaliação do GATE DE SELEÇÃO da Criação de Portfólio.

Por que existe. O gate qualitativo **reprova empresas da carteira** e substitui
pelo próximo do segmento — é o único componente do caminho da decisão movido por
linguagem natural, e a acurácia dele nunca foi medida. Nesta base, toda vez que
um motor decidia sem ser verificado, ele estava errado de alguma forma: o piso
reprovava banco lucrativo por métrica que não se aplica, o verificador de
ancoragem acusava resposta correta, a faixa de validação apagava a evidência.
Não havia razão para supor que este fosse a exceção.

O que ele mede. Dossiês SINTÉTICOS com veredito conhecido, cobrindo os dois
erros possíveis:

* **veto que deveria acontecer** — lucro insustentável mascarado, patrimônio
  negativo, evento societário grave;
* **veto que NÃO deveria acontecer** — cíclica em vale de ciclo (o prompt proíbe
  veto por opinião de preço) e banco com EBIT negativo, que é artefato da
  métrica e não falha do negócio. Este último é exatamente o falso positivo que
  o piso determinístico cometeu até 01/08/2026.

Um gate que só acerta os vetos é inútil: veta tudo. Um que só acerta as
aprovações também: não veta nada. Por isso as duas metades pesam igual.

Nenhum dado real do usuário sai daqui. Requer OPENAI_API_KEY e/ou GEMINI_API_KEY.

Uso:
  python scripts/eval_gate_selecao.py
  python scripts/eval_gate_selecao.py --repeticoes 3 --saida relatorio.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _serie(anos: list[tuple], pl: float, caixa: float, div: float) -> list[dict]:
    """(ano, receita, ebit, lucro, fco) → linhas da série anual do dossiê."""
    return [
        {"ano": ano, "receita_mi": rec, "ebit_mi": ebit, "lucro_mi": lucro,
         "pl_mi": pl, "caixa_mi": caixa, "div_liq_mi": div, "fco_mi": fco,
         "margem_liq_pct": round(lucro / rec * 100, 1) if rec else None,
         "roe_pct": round(lucro / pl * 100, 1) if pl else None}
        for ano, rec, ebit, lucro, fco in anos
    ]


@dataclass
class Caso:
    nome: str
    deve_vetar: bool
    porque: str
    dossie: dict
    aceita_ressalva: bool = False   # quando ressalva também é resposta correta
    tags: tuple[str, ...] = field(default_factory=tuple)


# ── Dossiês sintéticos ───────────────────────────────────────────────────────

SOLIDA = Caso(
    nome="solida_nao_deve_vetar",
    deve_vetar=False,
    porque="lucro consistente, dívida baixa, dividendo coberto — nada a vetar",
    dossie={
        "ticker": "BOAA3", "nome": "Companhia Sólida S.A.",
        "setor": "Consumo não Cíclico", "subsetor": "Alimentos", "segmento": "Alimentos",
        "serie_anual": _serie(
            [(2021, 8000, 1200, 800, 1100), (2022, 8600, 1300, 870, 1200),
             (2023, 9100, 1400, 950, 1250), (2024, 9800, 1520, 1030, 1400),
             (2025, 10500, 1650, 1120, 1500)], pl=6000, caixa=1800, div=900),
        "dividendos": {"por_ano": {"2023": 0.90, "2024": 0.98, "2025": 1.05},
                       "ult_12m_ps": 1.05, "dy_12m_pct": 5.2},
        "metricas_banco": {"ROE": 0.187, "Margem_Operacional": 0.157,
                           "Endividamento_Total": 0.15, "Liquidez_Corrente": 2.1,
                           "Payout": 0.42},
        "valuation": {"p_l": 12.4, "p_vp": 2.1},
        # n_docs > 0 de propósito: empresa grande e antiga TEM documentos
        # indexados, só não teve evento relevante. Zerar aqui confundiria
        # "sem evento" com "sem dados", e dado insuficiente é motivo
        # legítimo de veto — o caso de teste ficaria injusto.
        "eventos_societarios": {"n_docs": 28, "docs_desde": "2022-03",
                                "eventos": []},
        "red_flags": [],
    },
)

LUCRO_MASCARADO = Caso(
    nome="lucro_insustentavel_deve_vetar",
    deve_vetar=True,
    porque="operação no prejuízo; lucro de 2025 vem de venda de ativo e o "
           "dividendo distribui muito acima dele",
    dossie={
        "ticker": "MASC4", "nome": "Mascarada Participações S.A.",
        "setor": "Consumo Cíclico", "subsetor": "Comércio", "segmento": "Varejo",
        "serie_anual": _serie(
            [(2021, 5000, -180, -260, -300), (2022, 4700, -240, -390, -420),
             (2023, 4300, -310, -520, -560), (2024, 4000, -350, -610, -640),
             (2025, 3800, -300, 900, -580)], pl=400, caixa=120, div=3900),
        "dividendos": {"por_ano": {"2024": 0.10, "2025": 2.40},
                       "ult_12m_ps": 2.40, "dy_12m_pct": 21.0},
        "metricas_banco": {"ROE": 2.25, "Margem_Operacional": -0.079,
                           "Endividamento_Total": 9.75, "Liquidez_Corrente": 0.6,
                           "Payout": 3.10},
        "valuation": {"p_l": 3.1, "p_vp": 1.9},
        "eventos_societarios": {
            "n_docs": 34, "docs_desde": "2023-01",
            "eventos": [{"data": "2025-08-14", "categoria": "Alienação de ativo",
                         "titulo": "Venda da principal unidade operacional; ganho "
                                   "não recorrente de R$ 1,2 bi no resultado"}]},
        "red_flags": ["FCO negativo em 5 anos consecutivos",
                      "payout de 310% sobre lucro não recorrente"],
    },
    tags=("lucro_nao_recorrente", "payout_insustentavel"),
)

PATRIMONIO_NEGATIVO = Caso(
    nome="patrimonio_negativo_deve_vetar",
    deve_vetar=True,
    porque="passivo excede o ativo e o prejuízo se aprofunda — insolvência contábil",
    dossie={
        "ticker": "QUEB3", "nome": "Quebrada Indústria S.A.",
        "setor": "Bens Industriais", "subsetor": "Máquinas", "segmento": "Máquinas",
        "serie_anual": _serie(
            [(2021, 2000, -100, -300, -250), (2022, 1700, -260, -700, -500),
             (2023, 1400, -380, -1100, -800), (2024, 1100, -520, -1600, -1000),
             (2025, 900, -600, -2100, -1200)], pl=-3400, caixa=40, div=5200),
        "dividendos": {"por_ano": {}, "ult_12m_ps": 0.0, "dy_12m_pct": 0.0},
        "metricas_banco": {"ROE": None, "Margem_Operacional": -0.667,
                           "Endividamento_Total": None, "Liquidez_Corrente": 0.3,
                           "Payout": 0.0},
        "valuation": {"p_l": None, "p_vp": None},
        "eventos_societarios": {
            "n_docs": 51, "docs_desde": "2022-06",
            "eventos": [{"data": "2025-11-02", "categoria": "Recuperação judicial",
                         "titulo": "Pedido de recuperação judicial protocolado"}]},
        "red_flags": ["patrimônio líquido negativo", "FCO negativo persistente"],
    },
    tags=("insolvencia", "evento_grave"),
)

CICLICA_EM_VALE = Caso(
    nome="ciclica_em_vale_nao_deve_vetar",
    deve_vetar=False,
    porque="margem comprimida pelo ciclo, mas balanço sólido e histórico de "
           "recuperação — vetar aqui seria opinião de preço, que o prompt proíbe",
    aceita_ressalva=True,
    dossie={
        "ticker": "CICL3", "nome": "Cíclica Metais S.A.",
        "setor": "Materiais Básicos", "subsetor": "Siderurgia", "segmento": "Siderurgia",
        "serie_anual": _serie(
            [(2021, 14000, 3200, 2400, 2900), (2022, 15500, 3600, 2700, 3100),
             (2023, 12000, 1400, 900, 1600), (2024, 10500, 600, 320, 900),
             (2025, 10200, 380, 140, 700)], pl=13000, caixa=3200, div=4100),
        "dividendos": {"por_ano": {"2023": 0.80, "2024": 0.35, "2025": 0.12},
                       "ult_12m_ps": 0.12, "dy_12m_pct": 1.4},
        "metricas_banco": {"ROE": 0.011, "Margem_Operacional": 0.037,
                           "Endividamento_Total": 0.32, "Liquidez_Corrente": 2.4,
                           "Payout": 0.28},
        "valuation": {"p_l": 41.0, "p_vp": 0.6},
        # n_docs > 0 de propósito: empresa grande e antiga TEM documentos
        # indexados, só não teve evento relevante. Zerar aqui confundiria
        # "sem evento" com "sem dados", e dado insuficiente é motivo
        # legítimo de veto — o caso de teste ficaria injusto.
        "eventos_societarios": {"n_docs": 28, "docs_desde": "2022-03",
                                "eventos": []},
        "red_flags": [],
    },
    tags=("vale_de_ciclo",),
)

BANCO_EBIT_NEGATIVO = Caso(
    nome="banco_ebit_negativo_nao_deve_vetar",
    deve_vetar=False,
    porque="EBIT e margem operacional não são conceitos válidos para banco; o "
           "lucro é consistente e o FCO negativo vem de originação de crédito",
    aceita_ressalva=True,
    dossie={
        "ticker": "BANK4", "nome": "Banco Exemplo S.A.",
        "setor": "Financeiro", "subsetor": "Bancos", "segmento": "Bancos",
        "serie_anual": _serie(
            [(2021, 7200, -320, 780, -1400), (2022, 8100, -290, 860, -1800),
             (2023, 8900, -410, 940, -2100), (2024, 9600, -380, 1010, -1900),
             (2025, 10300, -456, 1090, -2300)], pl=7800, caixa=15000, div=None),
        "dividendos": {"por_ano": {"2023": 0.62, "2024": 0.70, "2025": 0.78},
                       "ult_12m_ps": 0.78, "dy_12m_pct": 6.1},
        "metricas_banco": {"ROE": 0.140, "Margem_Operacional": -0.044,
                           "Endividamento_Total": None, "Liquidez_Corrente": None,
                           "Payout": 0.51},
        "valuation": {"p_l": 7.2, "p_vp": 1.0},
        # n_docs > 0 de propósito: empresa grande e antiga TEM documentos
        # indexados, só não teve evento relevante. Zerar aqui confundiria
        # "sem evento" com "sem dados", e dado insuficiente é motivo
        # legítimo de veto — o caso de teste ficaria injusto.
        "eventos_societarios": {"n_docs": 28, "docs_desde": "2022-03",
                                "eventos": []},
        "red_flags": ["margem operacional negativa (métrica não aplicável a bancos)"],
    },
    tags=("metrica_inaplicavel",),
)

CASOS: tuple[Caso, ...] = (SOLIDA, LUCRO_MASCARADO, PATRIMONIO_NEGATIVO,
                           CICLICA_EM_VALE, BANCO_EBIT_NEGATIVO)


def _limpar_cache_parecer() -> None:
    """Sem isto, ``--repeticoes`` mede a mesma resposta N vezes.

    ``_parecer_llm_cached`` guarda 24h por prompt, e o prompt é idêntico entre
    repetições — o cache serve à produção, onde reprocessar a mesma empresa no
    mesmo dia é desperdício, mas aqui anularia justamente o que se quer medir:
    a ESTABILIDADE do veredito.
    """
    try:
        from core.dossie_b3 import _parecer_llm_cached
        _parecer_llm_cached.clear()
    except Exception:
        pass


def _avaliar(caso: Caso) -> dict:
    from core.dossie_b3 import gerar_parecer_empresa

    _limpar_cache_parecer()

    parecer, _ = gerar_parecer_empresa(caso.dossie["ticker"], dossie=caso.dossie)
    classificacao = str(parecer.get("classificacao_selecao", ""))
    motivo = str(parecer.get("motivo_selecao", ""))

    vetou = classificacao == "vetar"
    if caso.deve_vetar:
        acertou = vetou
    else:
        acertou = not vetou or False
        if caso.aceita_ressalva and classificacao == "aprovar_com_ressalvas":
            acertou = True

    # Fallback não é veredito: significa que o LLM não respondeu. Contabilizar
    # como acerto inflaria a nota (o fallback nunca veta, então "acertaria"
    # todos os casos de não-veto).
    indisponivel = "Parecer não gerado" in motivo or parecer.get("confianca") == 0
    return {
        "caso": caso.nome, "deve_vetar": caso.deve_vetar,
        "classificacao": classificacao, "vetou": vetou,
        "acertou": None if indisponivel else acertou,
        "indisponivel": indisponivel,
        "motivo": motivo[:400], "tags": list(caso.tags),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeticoes", type=int, default=1)
    p.add_argument("--saida", default=None)
    args = p.parse_args()

    resultados: list[dict] = []
    for volta in range(1, max(1, args.repeticoes) + 1):
        for caso in CASOS:
            r = _avaliar(caso)
            r["repeticao"] = volta
            resultados.append(r)
            marca = ("indisponível" if r["indisponivel"]
                     else ("acertou" if r["acertou"] else "ERROU"))
            print(f"  [{volta}] {caso.nome}: {r['classificacao']} — {marca}")

    validos = [r for r in resultados if not r["indisponivel"]]
    devem = [r for r in validos if r["deve_vetar"]]
    nao_devem = [r for r in validos if not r["deve_vetar"]]

    def _pct(rs: list[dict]) -> float:
        return round(100.0 * sum(1 for r in rs if r["acertou"]) / len(rs), 1) if rs else 0.0

    resumo = {
        "execucoes": len(resultados),
        "indisponiveis": len(resultados) - len(validos),
        "acuracia_geral_pct": _pct(validos),
        "vetos_corretos_pct": _pct(devem),          # sensibilidade
        "aprovacoes_corretas_pct": _pct(nao_devem),  # especificidade
        "falsos_vetos": [r["caso"] for r in nao_devem if r["acertou"] is False],
        "vetos_perdidos": [r["caso"] for r in devem if r["acertou"] is False],
    }
    print("\n=== RESUMO ===")
    for k, v in resumo.items():
        print(f"  {k}: {v}")

    if args.saida:
        Path(args.saida).write_text(
            json.dumps({"resumo": resumo, "resultados": resultados},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrelatório: {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
