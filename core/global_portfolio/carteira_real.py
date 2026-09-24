"""A carteira REAL lida pelas classes do Portfólio Global.

Por que existe: o plano de aporte do Portfólio Global somava `weight_global`
das carteiras-modelo, que por construção É o alvo. O desvio saía sempre 0% e
a tela "comparava o alvo com o real" sem nunca ler o real. Aqui entra a
posição de verdade (`core.investimentos.get_carteira()["posicoes"]`, valor de
mercado já em R$), agrupada nas mesmas chaves da alocação-alvo -- mais renda
fixa, que não tem carteira-modelo mas é boa parte do patrimônio de quem
investe no Brasil.

Classes:
  b3         Ações BR e ETF Brasil negociados em reais;
  fii        FIIs;
  us         tudo cotado fora do Brasil ou em dólar, e BDR (exposição externa);
  renda_fixa Renda fixa, Tesouro Direto e fundos de renda fixa;
  outros     o que não se encaixa (cripto, "Outros"). Não tem alvo: entra no
             patrimônio e aparece acima do alvo (0%), nunca recebe aporte.

Módulo puro: sem Streamlit e sem banco.
"""
from __future__ import annotations

CLASSES_REAIS: tuple[str, ...] = ("b3", "fii", "us", "renda_fixa", "outros")

ROTULOS: dict[str, str] = {
    "b3": "Empresas B3",
    "fii": "Fundos imobiliários",
    "us": "Internacional",
    "renda_fixa": "Renda fixa",
    "outros": "Outros (sem alvo)",
}

# Rótulo de `core.investimentos._CLASS_LABEL` -> classe global.
_POR_ROTULO: dict[str, str] = {
    "Ações BR": "b3",
    "ETF Brasil": "b3",
    "ETF": "b3",
    "FII": "fii",
    "ETF Internacional": "us",
    "BDR": "us",
    "Renda Fixa": "renda_fixa",
    "Tesouro Direto": "renda_fixa",
    "Fundo RF": "renda_fixa",
}


def classe_global(posicao: dict) -> str:
    """Classe do Portfólio Global para uma posição de `get_carteira()`.

    Moeda/país vêm antes do rótulo: uma ação americana gravada como `stock`
    ganha o rótulo "Ações BR" em `core.investimentos`, e contá-la como B3
    esconderia a exposição cambial.
    """
    moeda = str(posicao.get("moeda") or "BRL").strip().upper()
    pais = str(posicao.get("pais") or "BR").strip().upper()
    rotulo = str(posicao.get("classe") or "").strip()
    if _POR_ROTULO.get(rotulo) == "renda_fixa":
        return "renda_fixa"
    if moeda != "BRL" or pais not in ("", "BR"):
        return "us"
    return _POR_ROTULO.get(rotulo, "outros")


def valores_reais_por_classe(posicoes: list[dict] | None) -> dict[str, float]:
    """{classe: valor de mercado em R$}. Só classes com valor positivo."""
    valores: dict[str, float] = {}
    for p in posicoes or []:
        try:
            vm = float(p.get("valor_mercado") or 0.0)
        except (TypeError, ValueError):
            continue
        if vm <= 0:
            continue
        c = classe_global(p)
        valores[c] = valores.get(c, 0.0) + vm
    return valores


def alvos_globais(alvos_modelo: dict[str, float] | None,
                  renda_fixa: float | None) -> dict[str, float]:
    """Alvo sobre o patrimônio inteiro.

    `alvos_modelo` divide a parcela de risco (soma 1, como a tela salva);
    `renda_fixa` é a fração do patrimônio em renda fixa. Sem `renda_fixa`
    definida, devolve só os alvos de modelo -- e quem chama deve tirar a
    renda fixa do plano em vez de tratá-la como alvo 0% (que mandaria todo
    aporte para longe dela sem o usuário ter pedido isso).
    """
    base = {k: float(v) for k, v in (alvos_modelo or {}).items() if float(v or 0) > 0}
    soma = sum(base.values())
    if soma <= 0:
        return {"renda_fixa": 1.0} if renda_fixa else {}
    base = {k: v / soma for k, v in base.items()}
    if renda_fixa is None:
        return base
    rf = float(renda_fixa)
    alvos = {k: v * (1.0 - rf) for k, v in base.items()}
    if rf > 0:
        alvos["renda_fixa"] = rf
    return alvos


def base_do_plano(posicoes: list[dict] | None, alvos_modelo: dict[str, float] | None,
                  renda_fixa: float | None) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """(valores, alvos, fora_do_plano) prontos para `core.aporte.plano_de_aporte`.

    Sem alvo de renda fixa, a renda fixa sai do plano -- e o nome dela volta
    em `fora_do_plano` para a tela dizer isso, em vez de o patrimônio "sumir".
    """
    valores = valores_reais_por_classe(posicoes)
    alvos = alvos_globais(alvos_modelo, renda_fixa)
    fora: list[str] = []
    if renda_fixa is None and valores.get("renda_fixa", 0.0) > 0:
        valores.pop("renda_fixa")
        fora.append("renda_fixa")
    return valores, alvos, fora
