"""
core/cenario/referencias.py
Valores de referência para o cenário, lidos dos insumos macro publicados
(``data/public/macro_insumos.json.gz``, gerado do armazém local).

Servem a duas coisas, e nenhuma grava nada:

- ``referencias`` alimenta os sinais de revisão (``divergencia.sinais``);
- ``sugestoes`` preenche o formulário quando o usuário PEDE para atualizar a
  partir dos dados. A sugestão só vira cenário se ele conferir e salvar.

Cada valor sai com a série e a data de referência por extenso: uma Selic
anual de dezembro não pode aparecer como "a Selic de hoje".
"""
from __future__ import annotations

import datetime as dt
import logging

from core.cenario.divergencia import Referencia

logger = logging.getLogger(__name__)

# chave do cenário → (país, código da série, nome por extenso, unidade)
_SERIES: dict[str, tuple[str, str, str, str]] = {
    "interest_rate": ("BRA", "selic", "Selic (série anual publicada)", "% a.a."),
    "inflation": ("BRA", "ipca", "IPCA (série anual publicada)", "% a.a."),
    "fx": ("BRA", "cambio", "câmbio (série anual publicada)", "R$ por US$"),
}
_GLOBAL: tuple[tuple[str, str, str], ...] = (
    ("US", "FEDFUNDS", "Fed Funds"),
    ("US", "DGS10", "Treasury 10 anos"),
)


def _ultimas(observacoes) -> dict[tuple[str, str], dict]:
    ultimas: dict[tuple[str, str], dict] = {}
    for o in observacoes:
        if o.get("is_forecast") or o.get("reference_period") is None:
            continue
        chave = (o.get("country_code"), o.get("provider_code"))
        atual = ultimas.get(chave)
        if atual is None or o["reference_period"] > atual["reference_period"]:
            ultimas[chave] = o
    return ultimas


def _numero(o: dict) -> float | None:
    try:
        return float(o.get("value"))
    except (TypeError, ValueError):
        return None


def de_observacoes(observacoes) -> dict[str, Referencia]:
    """Puro: as observações publicadas → referência por item do cenário."""
    ultimas = _ultimas(observacoes)
    saida = {}
    for chave, (pais, codigo, nome, unidade) in _SERIES.items():
        o = ultimas.get((pais, codigo))
        valor = None if o is None else _numero(o)
        if valor is not None:
            saida[chave] = Referencia(valor, o["reference_period"], nome,
                                      unidade)
    return saida


def _br(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def sugestoes_de_observacoes(observacoes) -> dict[str, dict]:
    """Puro: valores sugeridos (``current_value`` e ``source``) por item.

    Direção e confiança ficam em branco de propósito: são julgamento do
    usuário, não dado publicado.
    """
    saida = {}
    for chave, ref in de_observacoes(observacoes).items():
        saida[chave] = {
            "current_value": f"{_br(ref.valor)} {ref.unidade}",
            "source": f"{ref.fonte}, referência "
                      f"{ref.data.strftime('%d/%m/%Y')}",
        }
    ultimas = _ultimas(observacoes)
    partes = []
    for pais, codigo, nome in _GLOBAL:
        o = ultimas.get((pais, codigo))
        valor = None if o is None else _numero(o)
        if valor is not None:
            partes.append(f"{nome} {_br(valor)}% "
                          f"({o['reference_period'].strftime('%d/%m/%Y')})")
    if partes:
        saida["global_economy"] = {"current_value": "; ".join(partes),
                                   "source": "FRED (insumos macro publicados)"}
    return saida


def _observacoes(agora: dt.datetime | None = None):
    from core.macro_data.insumos_publicados import carregar_insumos_publicados
    try:
        insumos = carregar_insumos_publicados(agora=agora)
    except Exception:  # noqa: BLE001 — referência é opcional
        logger.warning("cenario: insumos macro ilegíveis", exc_info=True)
        return ()
    return () if insumos is None else insumos.observacoes


def referencias(agora: dt.datetime | None = None) -> dict[str, Referencia]:
    return de_observacoes(_observacoes(agora))


def sugestoes(agora: dt.datetime | None = None) -> dict[str, dict]:
    return sugestoes_de_observacoes(_observacoes(agora))
