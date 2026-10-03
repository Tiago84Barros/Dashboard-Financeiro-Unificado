"""Busca de empresa por ticker OU por nome.

O campo de análise aceitava só ticker: quem digitava "Banco do Brasil" caía em
"não está entre os papéis da B3". Aqui mora a regra de resolução, sem Streamlit,
para a tela só decidir o que mostrar com o resultado.

Ordem de precedência:
1. ticker exato (com ou sem ``.SA``) — vence sempre, inclusive de nome;
2. prefixo de ticker ("PETR" → PETR3, PETR4);
3. nome contendo todas as palavras digitadas, sem acento e sem caixa;
4. aproximação por ``difflib`` sobre os nomes, para erro de digitação.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Iterable

import pandas as pd

_RUIDO_NOME = re.compile(r"[^A-Z0-9 ]+")


def normalizar(texto: object) -> str:
    """Caixa alta, sem acento, sem pontuação, espaços colapsados."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = _RUIDO_NOME.sub(" ", s)
    return " ".join(s.split())


def normalizar_ticker(texto: object) -> str:
    return str(texto or "").strip().upper().replace(".SA", "").strip()


def buscar_empresas(
    consulta: str,
    df_set: pd.DataFrame | None,
    universo: Iterable[str] = (),
    limite: int = 8,
    col_ticker: str = "ticker",
    col_nome: str = "nome_empresa",
) -> list[tuple[str, str]]:
    """Devolve ``[(ticker, nome)]`` que casam com ``consulta``, melhor primeiro.

    ``df_set`` precisa das colunas ``col_ticker`` e ``col_nome`` (B3:
    ``ticker``/``nome_empresa``; EUA: ``symbol``/``name``); ``universo``
    acrescenta tickers sem nome cadastrado (só casam por ticker). Lista vazia
    significa que nada casou — a tela decide como avisar.
    """
    q_tk = normalizar_ticker(consulta)
    q_nome = normalizar(consulta)
    if not q_tk:
        return []

    nomes: dict[str, str] = {}
    if df_set is not None and not df_set.empty and col_ticker in df_set.columns:
        col_nome = col_nome if col_nome in df_set.columns else None
        for _, row in df_set.iterrows():
            tk = normalizar_ticker(row[col_ticker])
            if not tk:
                continue
            nome = str(row[col_nome]).strip() if col_nome and pd.notna(row[col_nome]) else ""
            if nome and nome.upper() != tk:
                nomes.setdefault(tk, nome)
            else:
                nomes.setdefault(tk, "")
    for tk in universo:
        tk = normalizar_ticker(tk)
        if tk:
            nomes.setdefault(tk, "")

    def _par(tk: str) -> tuple[str, str]:
        return tk, nomes.get(tk) or tk

    # 1. ticker exato
    if q_tk in nomes:
        return [_par(q_tk)]

    resultado: list[str] = []

    def _add(tks: Iterable[str]) -> None:
        for tk in tks:
            if tk not in resultado:
                resultado.append(tk)

    # 2. prefixo de ticker (só faz sentido para consulta sem espaço)
    if " " not in q_tk and len(q_tk) >= 3:
        _add(sorted(tk for tk in nomes if tk.startswith(q_tk)))

    # 3. nome contendo todas as palavras; quem começa pela consulta vem antes
    if q_nome:
        palavras = q_nome.split()
        com_nome = {tk: normalizar(n) for tk, n in nomes.items() if n}
        casam = [tk for tk, n in com_nome.items()
                 if all(re.search(rf"\b{re.escape(p)}", n) for p in palavras)]
        casam.sort(key=lambda tk: (not com_nome[tk].startswith(q_nome),
                                   len(com_nome[tk]), tk))
        _add(casam)

        # 4. aproximação, só se nada casou até aqui
        if not resultado and len(q_nome) >= 4:
            # Compara também com o começo do nome, do tamanho da consulta:
            # "microsft" contra "MICROSOFT CORPORATION" inteiro fica abaixo do
            # corte, contra "MICROSOFT" não.
            por_nome: dict[str, list[str]] = {}
            for tk, n in com_nome.items():
                for chave in {n, " ".join(n.split()[:len(palavras)])}:
                    por_nome.setdefault(chave, []).append(tk)
            proximos = difflib.get_close_matches(q_nome, list(por_nome), n=limite, cutoff=0.75)
            for n in proximos:
                _add(sorted(set(por_nome[n])))

    return [_par(tk) for tk in resultado[:limite]]
