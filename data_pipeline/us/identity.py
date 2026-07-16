"""
data_pipeline/us/identity.py
Identidade permanente e reconciliação de símbolos americanos.

Regras (do enunciado):
  - A empresa é identificada por CIK, NUNCA só pelo ticker (reutilizado/renomeado).
  - Detectar divergência entre símbolo solicitado e retornado pela API.
  - Manter aliases/histórico de tickers; não apagar histórico ao trocar de ticker.
  - Universo: excluir ETF/fundo/SPAC/sem-empresa-operacional da análise principal;
    ADR é configurável (não excluir de forma irreversível).

Puro (sem rede/DB), coberto por tests/test_us_identity.py.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Tipos SEM empresa operacional utilizável na análise fundamentalista principal.
NON_OPERATING_TYPES = frozenset({"etf", "fund", "spac"})

# CIK da SEC tem até 10 dígitos, normalmente com zero-padding.
def normalize_cik(cik: object) -> Optional[str]:
    """Normaliza CIK para string de dígitos com zero-padding de 10 (chave estável)."""
    if cik is None:
        return None
    s = str(cik).strip().lstrip("0")
    if not s.isdigit():
        digits = "".join(ch for ch in str(cik) if ch.isdigit()).lstrip("0")
        s = digits
    if not s:
        return None
    return s.zfill(10)


def normalize_symbol(symbol: object) -> Optional[str]:
    """Uppercase/trim. Classes de ação: unifica separador para '-' (BRK.B→BRK-B)."""
    if symbol is None:
        return None
    s = str(symbol).strip().upper()
    if not s:
        return None
    return s.replace(".", "-")


def symbols_equivalent(a: object, b: object) -> bool:
    """True se dois símbolos representam o mesmo listing após normalização."""
    na, nb = normalize_symbol(a), normalize_symbol(b)
    return na is not None and na == nb


def detect_symbol_divergence(requested: object, returned: object) -> Optional[dict]:
    """Retorna descrição da divergência se o símbolo retornado != solicitado.

    Usado para rejeitar/gravar dados sob o ticker ERRADO (bug conhecido em outras
    ingestões do projeto — ver memória brapi tickers divergentes).
    """
    if returned in (None, ""):
        return None
    if symbols_equivalent(requested, returned):
        return None
    return {
        "requested": normalize_symbol(requested),
        "returned": normalize_symbol(returned),
        "reason": "symbol_mismatch",
    }


def is_operating_company(security_type: object) -> bool:
    """True se o tipo representa uma empresa operacional (não ETF/fundo/SPAC)."""
    return str(security_type or "common").lower() not in NON_OPERATING_TYPES


def eligible_for_analysis(profile: dict, *, include_adr: bool = True,
                          min_history_years: int = 2, current_year: int = 0,
                          has_statements: bool = True) -> tuple[bool, Optional[str]]:
    """Aplica as regras do universo. Retorna (elegível, motivo_da_exclusão).

    Exclui: ETF/fundo/SPAC, sem demonstrações, e ADR quando include_adr=False.
    NÃO exclui deslistadas do universo histórico (survivorship) — isso é decisão
    do backtest, não do cadastro.
    """
    sec_type = str(profile.get("security_type") or "common").lower()
    if not is_operating_company(sec_type):
        return False, f"tipo não-operacional: {sec_type}"
    if sec_type == "adr" and not include_adr:
        return False, "ADR excluído por configuração"
    if not has_statements:
        return False, "sem demonstrações utilizáveis"
    ipo = profile.get("ipo_date")
    if ipo is not None and current_year:
        ipo_year = getattr(ipo, "year", None)
        if ipo_year and (current_year - ipo_year) < min_history_years:
            return False, f"histórico < {min_history_years} anos"
    return True, None


def resolve_current_symbol(symbol: object, aliases: Iterable[dict]) -> Optional[str]:
    """Segue a cadeia de renomeações (old_symbol→new_symbol) até o símbolo atual."""
    cur = normalize_symbol(symbol)
    if cur is None:
        return None
    by_old: dict[str, str] = {}
    for a in aliases:
        old = normalize_symbol(a.get("old_symbol"))
        new = normalize_symbol(a.get("new_symbol"))
        if old and new and a.get("reason") in (None, "rename", "exchange_move"):
            by_old[old] = new
    seen = set()
    while cur in by_old and cur not in seen:
        seen.add(cur)
        cur = by_old[cur]
    return cur
