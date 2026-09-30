"""
core/inteligencia_ativos/armazem_fatos.py
Os números do detalhe do armazém como campos do contexto estruturado do
Portfolio Fit.

O detalhe já ia ao prompt como texto, colado ao bloco de mercado. Medido com
LLM real em 30/09/2026 (TAEE11), o modelo não citou nenhum número dele. Só
levar os números para o JSON, com uma regra dizendo onde usá-los, também não
bastou (0 de 4); o que funcionou foi dar a eles um campo próprio na resposta,
``market_behavior`` (4 de 4). Aqui os números viram campos, formatados pelas
mesmas funções do texto: o que a LLM lê no JSON é, caractere por caractere, o
que está no bloco, e a validação ancora nos dois.

Puro: recebe o detalhe bruto que o leitor da classe já trouxe.
"""
from __future__ import annotations

NAO_SE_APLICA = "não se aplica: o armazém não tem leitor para esta classe"


def _b3(d: dict) -> dict:
    from core import b3_detalhe_armazem as b3

    p = b3.resumo_pregoes(d.get("pregoes") or [])
    if p is None:
        return {"estado": "sem negócios deste papel no recorte do armazém"}
    return {
        "fonte": "pregão diário da B3 (COTAHIST, preço bruto), armazém local",
        "pregao_de_referencia": f"{p['ultimo_dia']:%d/%m/%Y}",
        "liquidez": {
            "volume_financeiro_mediano_21_pregoes": f"{b3._brl(p['vol21'])}/dia",
            "volume_financeiro_mediano_63_pregoes": f"{b3._brl(p['vol63'])}/dia",
            "negocios_medianos_por_dia_21_pregoes": (
                None if p["negocios21"] is None else f"{p['negocios21']:,.0f}"),
            "pregoes_com_negocio_3m": p["pregoes_3m"],
            "pregoes_com_negocio_12m": p["pregoes_ano"],
        },
        "retorno_de_mercado_sem_proventos": {
            "1m": b3._pct(p["r1m"]), "3m": b3._pct(p["r3m"]), "12m": b3._pct(p["r12m"])},
        "volatilidade_anualizada_12m": b3._pct(p["vol_anual"], sinal=False),
        "reacao_a_resultados": ("no bloco DETALHE DO ARMAZÉM LOCAL, por data de "
                                "divulgação"),
    }


def _us(d: dict) -> dict:
    from core import us_detalhe_armazem as us

    p = us.resumo_precos(d.get("precos") or [])
    if p is None:
        return {"estado": "sem série recente de preço deste ticker no armazém"}
    return {
        "fonte": "preço diário ajustado, armazém local",
        "pregao_de_referencia": f"{p['ultimo_dia']:%d/%m/%Y}",
        "liquidez": {"giro_medio_3m": f"US$ {us._usd(p['giro3m'])}/dia"},
        "retorno": {"1m": us._pct(p["r1m"]), "3m": us._pct(p["r3m"]),
                    "6m": us._pct(p["r6m"]), "12m": us._pct(p["r12m"])},
        "distancia_do_topo_52s": us._pct(p["do_topo"]),
        "queda_maxima_12m": us._pct(p["dd12m"]),
        "volatilidade_anualizada_12m": "n/d" if p["vol"] is None else f"{p['vol']:.0%}",
        "trimestres_e_proventos": "no bloco DETALHE DO ARMAZÉM LOCAL",
    }


def _fii(d: dict) -> dict:
    from core import fii_detalhe_armazem as fii

    saida: dict = {"fonte": "informe mensal da CVM, cotação e COTAHIST, armazém local"}
    p = fii.resumo_precos(d.get("precos") or [])
    if p is not None:
        saida["cotacao_de_referencia"] = f"{p['ultimo_dia']:%d/%m/%Y}"
        saida["retorno"] = {"3m": fii._pct(p["r3m"]), "12m": fii._pct(p["r12m"])}
        saida["queda_maxima_12m"] = fii._pct(p["dd12m"])
    liq = (fii.resumo_liquidez(d.get("pregoes") or [], d.get("calendario") or [])
           if "calendario" in d else None)
    if liq is not None:
        saida["liquidez"] = {
            f"volume_mediano_{n}_pregoes": (
                "janela incompleta" if liq[f"j{n}"] is None
                else f"{fii._brl(liq[f'j{n}']['volume_mediano'])}/dia")
            for n in fii.JANELAS_LIQUIDEZ}
        saida["liquidez"]["pregao_de_referencia"] = f"{liq['ultimo_pregao']:%d/%m/%Y}"
    s = fii.resumo_serie(d.get("serie") or [])
    if s is not None:
        saida["informe_cvm"] = {"referencia": f"{s['ref']:%m/%Y}",
                                "vpa_12m": fii._pct(s["vpa12"]),
                                "rendimento_patrimonial_12m": (
                                    "n/d" if s["dy12m"] is None else f"{s['dy12m']:.2%}")}
    if len(saida) == 1:
        return {"estado": "sem série deste fundo no armazém"}
    saida["composicao_imoveis_e_proventos"] = "no bloco DETALHE DO ARMAZÉM LOCAL"
    return saida


_POR_CLASSE = {"b3": _b3, "us": _us, "fii": _fii}


def fatos(classe: str | None, ticker: str, bruto: dict | None, *,
          aviso: str = "") -> dict:
    """Campo ``warehouse_market`` do contexto. ``aviso`` é a linha que o
    leitor devolveu quando não trouxe dado (túnel fora, falha): ela vai no
    campo, para a ausência ser nomeada e não virar zero."""
    if classe not in _POR_CLASSE:
        return {"estado": NAO_SE_APLICA}
    d = (bruto or {}).get(str(ticker).strip().upper())
    if not d:
        return {"estado": "indisponível",
                "motivo": (aviso.strip().splitlines() or ["o armazém não devolveu "
                                                          "dado deste ativo"])[0][:240],
                "leitura": "lacuna, não dado zero"}
    return _POR_CLASSE[classe](d)
