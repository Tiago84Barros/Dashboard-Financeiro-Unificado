# -*- coding: utf-8 -*-
"""Quem entra no universo de análise americano (A-140).

Regra do usuário, explícita: **o módulo de Empresas Americanas analisa AÇÕES**.
REIT, fundo, ETF, SPAC, preferencial, warrant, right e unit não são ação e não
podem disputar ranking com uma companhia operacional — a comparação relativa
por indústria pressupõe que os concorrentes tenham a mesma estrutura econômica.
O REIT distribui o resultado por obrigação legal, deprecia imóvel contra o lucro
e carrega alavancagem que não se compara à de uma indústria: aparece barato em
P/L e alavancado em dívida/EBITDA pelos dois motivos errados.

Este módulo é o único lugar que decide isso. `data_pipeline/us/enrichment.py`
o consome para gravar `analysis_status` na ingestão, e `core/us_read.py` o
aplica na leitura da vitrine — a vitrine publicada é anterior a esta regra, e
sem o filtro de leitura os 128 REITs continuariam na tela até a próxima
republicação.

O que este módulo NÃO faz: excluir operadora imobiliária. Corretora (JLL,
RE/MAX, Compass), incorporadora e administradora são companhias operacionais
com lucro e EBIT legíveis. Só o veículo sai.
"""
from __future__ import annotations

import re

# Tipos que já chegam classificados na ingestão.
TIPOS_FORA = frozenset({"etf", "fund", "spac", "preferred", "warrant",
                        "right", "unit", "reit"})

_SETORES_NAO_OPERACIONAIS = {"blank checks"}

# Descrição SIC da SEC (o `sector` do cadastro guarda a descrição, não GICS).
_SIC_REIT = re.compile(r"real estate investment trust", re.IGNORECASE)

# Nome societário: pega o REIT cujo cadastro veio de fonte sem SIC.
_NOME_REIT = re.compile(
    r"(?:^|[^a-z])reits?(?:$|[^a-z])|real estate investment trust|"
    r"realty trust|property trust|properties trust", re.IGNORECASE)

# Rótulo genérico "Real Estate" (sem SIC): nesse conjunto convivem REIT
# declarado e incorporadora operacional, e o cadastro não distingue os dois.
# Medido em 26/08/2026: 20 linhas, das quais ao menos 13 são REIT. Marcar como
# não confirmado é o que o dado sustenta -- afirmar "é REIT" seria inventar
# identidade, e deixar passar contaminaria o ranking com metade REIT.
_SETOR_IMOBILIARIO_GENERICO = re.compile(r"^\s*real estate\s*$", re.IGNORECASE)

_EXPLICIT_NON_COMMON = re.compile(r"(?:-P[A-Z0-9]?|-WT|-WS|-UN)$")
_NASDAQ_ISSUE_SUFFIX = re.compile(r"^[A-Z]{4,}[WRU]$")

MOTIVO_REIT = "REIT: veículo imobiliário, fora do universo de ações"
MOTIVO_TIPO_NAO_CONFIRMADO = (
    "tipo de ativo não confirmado: setor genérico 'Real Estate' sem SIC"
)


def e_reit(*, security_type: object = None, sector: object = None,
           industry: object = None, name: object = None,
           is_reit: object = None) -> bool:
    """True quando alguma evidência do cadastro identifica um REIT.

    Aceita as quatro fontes porque nenhuma delas é completa sozinha: a flag
    `is_reit` cobre 128 dos ~148 do universo medido, o SIC cobre os mesmos 128,
    e o nome resgata um que as duas perdem (Angel Oak Mortgage REIT).
    """
    if bool(is_reit):
        return True
    if str(security_type or "").lower().strip() == "reit":
        return True
    for texto in (sector, industry):
        if _SIC_REIT.search(str(texto or "")):
            return True
    return bool(_NOME_REIT.search(str(name or "")))


def motivo_exclusao_ativo(symbol: str | None, security_type: str | None,
                          sector: str | None,
                          related_symbols: tuple[str, ...] = (),
                          *, industry: str | None = None,
                          name: str | None = None,
                          is_reit: object = None) -> str | None:
    """Motivo auditável quando o ativo não é ação operacional ordinária."""
    sym = str(symbol or "").upper().strip()
    sec = str(security_type or "common").lower().strip()
    sector_norm = str(sector or "").lower().strip()
    if e_reit(security_type=sec, sector=sector, industry=industry,
              name=name, is_reit=is_reit):
        return MOTIVO_REIT
    if sec in TIPOS_FORA:
        return "instrumento sem ação operacional ordinária"
    if sector_norm in _SETORES_NAO_OPERACIONAIS:
        return "companhia de cheque em branco (SPAC)"
    if _SETOR_IMOBILIARIO_GENERICO.match(str(sector or "")):
        return MOTIVO_TIPO_NAO_CONFIRMADO
    if _EXPLICIT_NON_COMMON.search(sym):
        return "preferencial, warrant ou unit"
    if _NASDAQ_ISSUE_SUFFIX.fullmatch(sym):
        return "sufixo Nasdaq de warrant, right ou unit"
    base = sym[:-1] if sym.endswith(("W", "R", "U")) else ""
    if base and any(
            str(other).upper() != sym
            and len(str(other).upper()) == len(sym)
            and str(other).upper()[:-1] == base
            and str(other).upper()[-1] in "WRU"
            for other in related_symbols):
        return "classe acessória de companhia sem ação ordinária identificada"
    return None
