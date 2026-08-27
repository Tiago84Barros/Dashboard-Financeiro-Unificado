# -*- coding: utf-8 -*-
"""Quem entra no universo de análise americano (A-140).

Regra do usuário, explícita: **o módulo de Empresas Americanas analisa AÇÕES**.
REIT, fundo, ETF, SPAC, BDC, preferencial, warrant, right e unit não são ação e não
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
com lucro e EBIT legíveis. Só o veículo sai. Pelo mesmo motivo, gestora de
recursos (Blackstone) e corretora (StoneX, LPL) ficam: administram o veículo,
não são o veículo. Ver `e_veiculo_agrupado` (A-144).

A BDC (fundo de crédito fechado) sai pelo mesmo argumento, mas por outra fonte:
a SEC não lhe atribui SIC nenhum, então a evidência vem do formulário arquivado
e chega por `is_investment_company`. Ver `MOTIVO_COMPANHIA_INVESTIMENTO` (A-147).
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

# ── A-144: ETF e trust de commodity chegando como `security_type='common'` ──
#
# O tipo do ativo vem do cadastro e o cadastro erra: iShares Gold Trust,
# Grayscale Bitcoin Trust, ProShares Trust II e os CurrencyShares estavam todos
# `security_type='common'`, `analysis_status='eligible'`, disputando ranking com
# companhia operacional. São 45 no universo medido em 27/08/2026. `TIPOS_FORA`
# não os pega porque ninguém os classificou como 'etf' ou 'fund'.
#
# O que os identifica de fato é o SIC da SEC: quase todo veículo de commodity,
# metal, moeda ou cripto declara "Commodity Contracts Brokers & Dealers".
_SIC_VEICULO_AGRUPADO = frozenset({
    "commodity contracts brokers & dealers",
    "unit investment trusts",
    "unit investment trusts, face amount certificate offices, and closed-end "
    "management investment offices",
    "management investment offices, open-end",
    "face-amount certificate offices",
})

# ETF declarado no nome vale por si, sem depender do SIC -- o Bitwise Ethereum
# ETF está catalogado como "Finance Services". A borda de palavra não é
# decoração: `%etf%` casaria com NETFLIX.
_NOME_ETF = re.compile(r"(?:^|[^A-Za-z])ETFs?(?:$|[^A-Za-z])")

# Escape para a companhia operacional que caiu no mesmo SIC: dentro de
# "Commodity Contracts Brokers & Dealers" convivem 45 veículos e 2 empresas
# (AIB Data Centers Inc., AI Financial Corp). Quem tem forma societária de
# companhia e nenhum substantivo de veículo no nome fica.
_NOME_VEICULO = re.compile(
    r"(?:^|[^A-Za-z])(?:trust|fund|funds)(?:$|[^A-Za-z])", re.IGNORECASE)
_FORMA_OPERACIONAL = re.compile(
    r"(?:^|[^A-Za-z])(?:inc|corp|corporation|company|co|holdings|group|plc|"
    r"n\.?v|s\.?a|ltd|limited|technologies|systems)\.?(?:$|[^A-Za-z])",
    re.IGNORECASE)

MOTIVO_VEICULO_AGRUPADO = (
    "veículo agrupado (ETF, trust de commodity, moeda ou cripto), não é ação"
)

# ── A-147: companhia de investimento (BDC, fundo fechado) ────────────────────
#
# Mesmo argumento do REIT, outra fonte de evidência. Uma BDC é um fundo de
# crédito: não tem receita operacional (tem investment income), distribui ~90%
# do resultado por obrigação legal de RIC e opera alavancada como fundo. Medidas
# lado a lado com companhia operacional, as 40 que estavam na vitrine apareciam
# sem receita e com "lucro" recorrente -- barato e sólido pelos dois motivos
# errados, exatamente como o REIT ficava barato em P/L.
#
# Aqui a evidência NÃO pode vir do SIC: a SEC devolve `sic` vazio para todas
# elas. Vem do formulário arquivado, apurado em `data_pipeline/us/edgar.py` e
# gravado em `companies.is_investment_company`.
MOTIVO_COMPANHIA_INVESTIMENTO = (
    "companhia de investimento (BDC ou fundo fechado), não é ação operacional"
)

MOTIVO_REIT = "REIT: veículo imobiliário, fora do universo de ações"
MOTIVO_TIPO_NAO_CONFIRMADO = (
    "tipo de ativo não confirmado: setor genérico 'Real Estate' sem SIC"
)


# ── A-151: uma companhia, uma linha no ranking ───────────────────────────────
#
# Medido em 27/08/2026: 54 tickers elegíveis são classe adicional de uma empresa
# que já está no universo por outro ticker -- SOJC/SOJD/SOJF/SOMN contra SO,
# HBANL/HBANP/HBANZ contra HBAN, NEWTH/NEWTI/NEWTP contra NEWT, HOVNP contra HOV.
#
# Nenhum deles chegava à vitrine, mas por acidente e não por regra: ninguém
# tinha ingerido demonstração sob esses símbolos. O EDGAR é por CIK, então a
# próxima ingestão sobre a lista de elegíveis buscaria o CIK da Newtek e
# gravaria as MESMAS demonstrações sob NEWTP -- e a companhia passaria a ocupar
# quatro linhas do ranking com fundamento idêntico e preço de outro papel.
#
# A lista tem duas naturezas, e afirmar uma só seria mentira. A maioria é baby
# bond ou preferencial (HBANL, SOJC, HOVNP): não é ação, e o P/L calculado com
# preço de título de dívida contra o lucro da ordinária não significa nada. Mas
# há classe ordinária legítima também -- ZG contra Z (Zillow), HEI-A contra HEI
# (Heico), AGM-A contra AGM (Farmer Mac): são ação de verdade. Marcar essas como
# "não é ação" repetiria exatamente o defeito do A-147, que lia um documento e
# afirmava outra coisa.
#
# O que vale para as duas naturezas, e é o que o motivo declara: é a MESMA
# companhia, e ela já está representada. A tela chama-se Empresas Americanas --
# ela ranqueia companhia, não papel. Uma companhia, uma linha.
#
# A evidência exigida é dupla e verificável no cadastro, não heurística de
# sufixo: mesmo `company_id` E o símbolo base é prefixo estrito deste. Sufixo
# solto não serve -- as letras de baby bond (L, P, Z, C, D, N, O) são as mesmas
# de ticker legítimo, e cortar por letra derrubaria empresa operacional.
MOTIVO_CLASSE_ADICIONAL = (
    "classe adicional da mesma companhia: a análise já é representada por {base}"
)


def classe_adicional_da_mesma_companhia(
        symbol: str | None, related_symbols: tuple[str, ...] = ()) -> str | None:
    """Símbolo base quando este ticker é classe adicional da mesma companhia.

    Devolve o base (mais curto e prefixo estrito) ou None. O base precisa
    sobreviver por si: se ele mesmo for warrant, unit ou preferencial explícita,
    excluir os dois deixaria a companhia sem nenhuma linha.
    """
    sym = str(symbol or "").upper().strip()
    if not sym:
        return None
    bases = sorted({str(o or "").upper().strip() for o in related_symbols}, key=len)
    for base in bases:
        if (base and base != sym and len(base) < len(sym) and sym.startswith(base)
                and not _EXPLICIT_NON_COMMON.search(base)
                and not _NASDAQ_ISSUE_SUFFIX.fullmatch(base)):
            return base
    return None


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


def e_veiculo_agrupado(*, name: object = None, sector: object = None,
                       industry: object = None) -> bool:
    """True quando o ativo é ETF, trust de commodity/moeda/cripto ou fundo.

    Duas evidências independentes, porque nenhuma cobre sozinha: o SIC pega os
    40 que se declaram corretora de contratos de commodity mas são o fundo em
    si, e o nome pega os 5 cujo SIC genérico ('Finance Services') não diz nada.
    """
    nome = str(name or "")
    if _NOME_ETF.search(nome):
        return True
    for texto in (sector, industry):
        if str(texto or "").lower().strip() in _SIC_VEICULO_AGRUPADO:
            # A empresa operacional que caiu no mesmo SIC não é veículo.
            return not (_FORMA_OPERACIONAL.search(nome)
                        and not _NOME_VEICULO.search(nome))
    return False


def motivo_exclusao_ativo(symbol: str | None, security_type: str | None,
                          sector: str | None,
                          related_symbols: tuple[str, ...] = (),
                          *, industry: str | None = None,
                          name: str | None = None,
                          is_reit: object = None,
                          is_investment_company: object = None) -> str | None:
    """Motivo auditável quando o ativo não é ação operacional ordinária."""
    sym = str(symbol or "").upper().strip()
    sec = str(security_type or "common").lower().strip()
    sector_norm = str(sector or "").lower().strip()
    if e_reit(security_type=sec, sector=sector, industry=industry,
              name=name, is_reit=is_reit):
        return MOTIVO_REIT
    if bool(is_investment_company):
        return MOTIVO_COMPANHIA_INVESTIMENTO
    if e_veiculo_agrupado(name=name, sector=sector, industry=industry):
        return MOTIVO_VEICULO_AGRUPADO
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
    base_classe = classe_adicional_da_mesma_companhia(sym, related_symbols)
    if base_classe:
        return MOTIVO_CLASSE_ADICIONAL.format(base=base_classe)
    base = sym[:-1] if sym.endswith(("W", "R", "U")) else ""
    if base and any(
            str(other).upper() != sym
            and len(str(other).upper()) == len(sym)
            and str(other).upper()[:-1] == base
            and str(other).upper()[-1] in "WRU"
            for other in related_symbols):
        return "classe acessória de companhia sem ação ordinária identificada"
    return None
