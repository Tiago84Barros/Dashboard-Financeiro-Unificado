"""Detalhe do armazém local para o chat do Portfólio Global.

`core.llm_context_global` só formata o que a tela já calculou e não busca
nada. Este módulo é a parte com I/O: escolhe, por classe, os ativos que
merecem o detalhe do armazém e chama o leitor de cada classe -- o mesmo que os
chats de Empresas B3, Seleção de FIIs e Empresas Americanas usam, direto no
desenvolvimento e pelo túnel na produção.

Sem este bloco, a LLM do patrimônio consolidado via só a foto dos snapshots,
enquanto a pergunta sobre um único ativo, feita na aba da classe, via a
liquidez diária, os trimestres, os proventos e o score mês a mês.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

import pandas as pd

from core.fii_ticker import tickers_citados as fiis_citados

logger = logging.getLogger(__name__)

#: Ativos com detalhe por classe. Os três leitores somados, com 4 cada, dão
#: ~40 linhas; o limite de cada leitor sozinho (5–6) triplicado pesaria mais
#: que o resto do contexto do patrimônio.
MAX_POR_CLASSE = 4

_ROTULOS = {"b3": "AÇÕES DA B3", "fii": "FUNDOS IMOBILIÁRIOS", "us": "AÇÕES AMERICANAS"}

# Ação da B3 fora da carteira: 4 letras e classe 3–8 (ON, PN, PNA...). Final
# 11 fica com o padrão de FII, que é validado.
_RX_ACAO_B3 = re.compile(r"\b([A-Z]{4}[3-8])\b")


def _leitores() -> dict[str, Callable[[list[str]], str]]:
    from core import llm_context_b3, llm_context_fii, llm_context_us

    return {"b3": llm_context_b3.get_warehouse_detail_context,
            "fii": llm_context_fii.get_warehouse_detail_context,
            "us": llm_context_us.get_warehouse_detail_context}


def _peso(valor: Any) -> float:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return 0.0
    return numero if numero == numero else 0.0


def _citado(simbolo: str, classe: str, pergunta: str) -> bool:
    # Ticker americano de 1–3 letras colide com palavra comum ("MET", "EAT",
    # "A"): só vale escrito em maiúsculas e com ao menos duas letras.
    if classe == "us":
        return len(simbolo) >= 2 and re.search(
            rf"(?<![A-Za-z0-9.]){re.escape(simbolo)}(?![A-Za-z0-9])", pergunta) is not None
    return re.search(rf"\b{re.escape(simbolo)}\b", pergunta, re.IGNORECASE) is not None


def ativos_para_detalhe(df: pd.DataFrame, pergunta: str,
                        max_por_classe: int = MAX_POR_CLASSE) -> dict[str, list[str]]:
    """Por classe: citados na pergunta primeiro, depois os de maior peso global.

    Ação da B3 ou FII citado que não está na carteira também entra, na sua
    classe -- "e se eu trocar X por Y" precisa do detalhe de Y. Ticker
    americano fora da carteira não entra: sem o universo, ele é
    indistinguível de sigla ("FED", "CPI").
    """
    pergunta = str(pergunta or "")
    carteira: dict[str, list[tuple[str, float]]] = {}
    if df is not None and not df.empty and {"symbol", "asset_class"} <= set(df.columns):
        pesos = df["weight_global"] if "weight_global" in df.columns else [None] * len(df)
        for simbolo, classe, peso in zip(df["symbol"], df["asset_class"], pesos):
            classe = str(classe or "").strip().lower()
            simbolo = str(simbolo or "").strip().upper().removesuffix(".SA")
            if classe in _ROTULOS and simbolo:
                carteira.setdefault(classe, []).append((simbolo, _peso(peso)))

    maiusculo = pergunta.upper()
    externos = {"fii": fiis_citados(pergunta),
                "b3": _RX_ACAO_B3.findall(maiusculo)}
    na_carteira = {s for itens in carteira.values() for s, _ in itens}

    saida: dict[str, list[str]] = {}
    for classe in _ROTULOS:
        itens = carteira.get(classe, [])
        citados = [s for s, _ in itens if _citado(s, classe, pergunta)]
        citados += [s for s in externos.get(classe, []) if s not in na_carteira]
        por_peso = [s for s, _ in sorted(itens, key=lambda i: i[1], reverse=True)]
        escolhidos = list(dict.fromkeys(citados + por_peso))[:max_por_classe]
        if escolhidos:
            saida[classe] = escolhidos
    return saida


def bloco_detalhe_armazem(df: pd.DataFrame, pergunta: str,
                          max_por_classe: int = MAX_POR_CLASSE) -> str:
    """Detalhe do armazém por classe, com a falha de cada classe nomeada.

    Os leitores já devolvem uma linha quando o túnel falta ou cai; o
    try/except aqui cobre o que escapar deles, para que o erro de uma classe
    não apague as outras duas.
    """
    escolhidos = ativos_para_detalhe(df, pergunta, max_por_classe)
    if not escolhidos:
        return ""
    leitores = _leitores()
    partes = ["=== DETALHE DO ARMAZÉM POR CLASSE === (os ativos citados na pergunta "
              f"e, depois, os de maior peso; até {max_por_classe} por classe. Ativo "
              "da carteira fora desta lista não tem detalhe aqui -- não suponha o "
              "dele a partir dos outros.)"]
    for classe, simbolos in escolhidos.items():
        try:
            corpo = leitores[classe](simbolos) or "sem detalhe para estes ativos."
        except Exception as exc:  # noqa: BLE001 - uma classe não derruba as outras
            logger.warning("detalhe do armazém (%s) falhou: %s", classe, exc)
            motivo = str(exc).splitlines()[0][:140] if str(exc) else type(exc).__name__
            corpo = f"DETALHE DO ARMAZÉM LOCAL: indisponível agora ({motivo})."
        partes.append(f"--- {_ROTULOS[classe]} ({', '.join(simbolos)}) ---\n{corpo}")
    return "\n\n".join(partes)
