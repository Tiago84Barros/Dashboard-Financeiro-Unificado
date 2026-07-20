"""Motor determinístico e somente leitura do Analista Financeiro Pessoal.

Os cálculos ficam neste módulo para que a view Streamlit apenas apresente resultados.
Nenhuma função movimenta dinheiro ou altera dados do usuário.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from math import isfinite
import re
import unicodedata


REAL_SOURCE = "real"
FIXED_CATEGORIES = {"moradia", "assinaturas", "educacao", "seguros"}
ESSENTIAL_CATEGORIES = {"moradia", "alimentacao", "saude", "transporte", "educacao", "seguros"}


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _slug(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9 ]", " ", text)).strip().lower()


def _iso_date(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value or "")[:10]


def calcular_metricas(controles: list[dict], carteira: dict, proventos: dict) -> dict:
    """Calcula métricas sem preencher lacunas com estimativas silenciosas."""
    validos = [item for item in controles if item.get("data_source") == REAL_SOURCE]
    atuais = validos[-1] if validos else (controles[-1] if controles else {})
    receitas = _number(atuais.get("receitas"))
    despesas = _number(atuais.get("despesas"))
    resultado = receitas - despesas
    categorias = atuais.get("categorias") or []
    gastos = {_slug(c.get("nome")): _number(c.get("gasto")) for c in categorias}
    fixas = sum(v for k, v in gastos.items() if k in FIXED_CATEGORIES)
    essenciais = sum(v for k, v in gastos.items() if k in ESSENTIAL_CATEGORIES)
    aporte = 0.0
    if atuais:
        aporte = sum(
            abs(_number(t.get("valor")))
            for t in atuais.get("transacoes", [])
            if t.get("tipo_fluxo") == "investment" or t.get("eh_investimento")
        )
    total_mercado = _number(carteira.get("total_mercado"))
    maior_posicao = max((_number(p.get("pct_carteira")) for p in carteira.get("posicoes", [])), default=0.0)
    media_despesas = (
        sum(_number(x.get("despesas")) for x in validos) / len(validos) if validos else None
    )
    return {
        "receitas": receitas,
        "despesas": despesas,
        "resultado": resultado,
        "taxa_poupanca_pct": round(resultado / receitas * 100, 1) if receitas > 0 else None,
        "taxa_investimento_pct": round(aporte / receitas * 100, 1) if receitas > 0 else None,
        "gastos_fixos_pct": round(fixas / receitas * 100, 1) if receitas > 0 and categorias else None,
        "gastos_essenciais_pct": round(essenciais / despesas * 100, 1) if despesas > 0 and categorias else None,
        "media_despesas_mensal": round(media_despesas, 2) if media_despesas is not None else None,
        "patrimonio_investido": total_mercado,
        "proventos_12m": _number(proventos.get("total_12m")),
        "maior_posicao_pct": maior_posicao or None,
        "meses_reais_analisados": len(validos),
    }


def detectar_anomalias(transacoes: list[dict]) -> list[dict]:
    """Gera candidatos para revisão humana; nunca rotula uma compra como desperdício."""
    despesas = [t for t in transacoes if t.get("eh_despesa") or t.get("tipo_fluxo") == "expense"]
    grupos: dict[tuple[str, float], list[dict]] = defaultdict(list)
    descricoes: dict[str, list[dict]] = defaultdict(list)
    for item in despesas:
        valor = round(abs(_number(item.get("valor"))), 2)
        nome = _slug(item.get("descricao")) or "sem descricao"
        grupos[(nome, valor)].append(item)
        descricoes[nome].append(item)

    achados: list[dict] = []
    for (nome, valor), itens in grupos.items():
        datas = {_iso_date(i.get("data") or i.get("data_compra")) for i in itens}
        if len(itens) >= 2 and len(datas) < len(itens):
            achados.append({
                "tipo": "possivel_duplicidade", "titulo": "Possível lançamento duplicado",
                "descricao": f"{len(itens)} lançamentos de {nome.title()} com o mesmo valor e data.",
                "valor": valor * len(itens), "confianca": "alta", "requer_revisao": True,
            })
    for nome, itens in descricoes.items():
        if len(itens) >= 3:
            total = sum(abs(_number(i.get("valor"))) for i in itens)
            achados.append({
                "tipo": "recorrencia", "titulo": "Despesa recorrente para revisar",
                "descricao": f"{nome.title()} apareceu {len(itens)} vezes no período.",
                "valor": round(total, 2), "confianca": "media", "requer_revisao": True,
            })
    pequenos = [t for t in despesas if 0 < abs(_number(t.get("valor"))) <= 30]
    if len(pequenos) >= 5:
        achados.append({
            "tipo": "pequenos_frequentes", "titulo": "Pequenos gastos frequentes",
            "descricao": f"{len(pequenos)} despesas de até R$ 30 somaram impacto relevante.",
            "valor": round(sum(abs(_number(t.get("valor"))) for t in pequenos), 2),
            "confianca": "alta", "requer_revisao": True,
        })
    return sorted(achados, key=lambda x: x["valor"], reverse=True)


def analisar_carteira(carteira: dict) -> list[dict]:
    analises = []
    posicoes = carteira.get("posicoes") or []
    if not posicoes:
        return analises
    maior = max(posicoes, key=lambda p: _number(p.get("pct_carteira")))
    pct = _number(maior.get("pct_carteira"))
    if pct >= 25:
        analises.append({
            "tipo": "inferencia", "prioridade": "alta" if pct >= 40 else "media",
            "titulo": "Concentração por ativo",
            "descricao": f"{maior.get('ticker', maior.get('nome', 'Um ativo'))} representa {pct:.1f}% da carteira.",
            "evidencia": "Percentual calculado sobre o valor de mercado informado.",
        })
    setores = carteira.get("por_setor") or []
    if setores and _number(setores[0].get("pct_carteira")) >= 40:
        analises.append({
            "tipo": "inferencia", "prioridade": "media", "titulo": "Concentração setorial",
            "descricao": f"O setor {setores[0].get('nome')} concentra {_number(setores[0].get('pct_carteira')):.1f}% da carteira.",
            "evidencia": "Agrupamento por setor disponível no App4.",
        })
    return analises


def gerar_recomendacoes(metricas: dict, metas: dict, carteira: dict) -> list[dict]:
    recomendacoes = []
    taxa = metricas.get("taxa_poupanca_pct")
    if taxa is not None and taxa < 10:
        recomendacoes.append({
            "prioridade": "alta", "titulo": "Recuperar margem mensal",
            "acao": "Revise primeiro despesas recorrentes e categorias acima do orçamento.",
            "motivo": f"A taxa de poupança do mês está em {taxa:.1f}%.",
            "tipo": "recomendacao", "requer_revisao": True,
        })
    atrasadas = int(metas.get("metas_atras", 0) or 0)
    if atrasadas:
        recomendacoes.append({
            "prioridade": "alta", "titulo": "Recalibrar metas atrasadas",
            "acao": "Revise prazo, valor-alvo e aporte mensal antes de assumir novos compromissos.",
            "motivo": f"Há {atrasadas} meta(s) com prazo vencido.",
            "tipo": "recomendacao", "requer_revisao": True,
        })
    recomendacoes.extend({
        "prioridade": a["prioridade"], "titulo": a["titulo"],
        "acao": "Avalie rebalanceamento gradual considerando custos, impostos e seu perfil de risco.",
        "motivo": a["descricao"], "tipo": "recomendacao", "requer_revisao": True,
    } for a in analisar_carteira(carteira))
    if not recomendacoes:
        recomendacoes.append({
            "prioridade": "baixa", "titulo": "Manter acompanhamento mensal",
            "acao": "Compare o fechamento do próximo mês com este diagnóstico.",
            "motivo": "Nenhum gatilho prioritário foi identificado com os dados disponíveis.",
            "tipo": "recomendacao", "requer_revisao": False,
        })
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    return sorted(recomendacoes, key=lambda x: ordem[x["prioridade"]])


def simular_patrimonio(valor_inicial: float, aporte_mensal: float, anos: int, taxa_anual_pct: float) -> list[dict]:
    """Projeção nominal; não é promessa de retorno nem recomendação de investimento."""
    inicial = max(0.0, _number(valor_inicial))
    aporte = max(0.0, _number(aporte_mensal))
    meses = max(0, int(anos)) * 12
    taxa_mensal = (1 + _number(taxa_anual_pct) / 100) ** (1 / 12) - 1
    saldo = inicial
    serie = [{"mes": 0, "patrimonio": round(saldo, 2), "aportado": round(inicial, 2)}]
    for mes in range(1, meses + 1):
        saldo = saldo * (1 + taxa_mensal) + aporte
        if mes % 12 == 0 or mes == meses:
            serie.append({"mes": mes, "patrimonio": round(saldo, 2), "aportado": round(inicial + aporte * mes, 2)})
    return serie


def montar_diagnostico(controles: list[dict], carteira: dict, metas: dict, proventos: dict) -> dict:
    fontes = {
        "controle": [c.get("data_source", "indisponivel") for c in controles],
        "carteira": carteira.get("data_source", "indisponivel"),
        "metas": metas.get("data_source", "indisponivel"),
        "proventos": proventos.get("data_source", "indisponivel"),
    }
    transacoes = [t for c in controles for t in c.get("transacoes", [])]
    metricas = calcular_metricas(controles, carteira, proventos)
    confiavel = (
        bool(controles)
        and all(c.get("data_source") == REAL_SOURCE for c in controles)
        and all(fontes[nome] == REAL_SOURCE for nome in ("carteira", "metas", "proventos"))
    )
    return {
        "fontes": fontes, "dados_reais": confiavel,
        "metricas": metricas, "anomalias": detectar_anomalias(transacoes),
        "carteira": analisar_carteira(carteira),
        "recomendacoes": gerar_recomendacoes(metricas, metas, carteira),
        "metas": metas.get("metas", []),
        "categorias": (controles[-1].get("categorias", []) if controles else []),
    }


def get_diagnostico(ano: int, mes: int, meses: int = 6) -> dict:
    """Carrega serviços existentes do App4 e monta um diagnóstico somente leitura."""
    from core.controle import get_controle
    from core.investimentos import get_carteira
    from core.metas import get_metas
    from core.proventos import get_proventos

    referencias = []
    y, m = ano, mes
    for _ in range(max(1, meses)):
        referencias.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    controles = [get_controle(y, m) for y, m in reversed(referencias)]
    return montar_diagnostico(controles, get_carteira(), get_metas(), get_proventos())
