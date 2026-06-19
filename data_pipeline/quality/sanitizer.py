"""
data_pipeline/quality/sanitizer.py
Política de saneamento inteligente.

Traduz a resolução cruzada (core.data_healing.FieldResolution) numa DECISÃO:
corrigir | atualizar | ignorar | marcar_revisao | excluir.

Invariáveis (do core.data_quality / data_healing):
  • Nunca preencher valor só para remover nulo.
  • Zero ≠ ausente.
  • Só grava com ≥2 fontes concordantes; senão marca para revisão.
"""
from __future__ import annotations

from core.data_healing import FieldResolution

# Decisões possíveis
CORRIGIR = "corrigir"            # banco tinha valor errado → sobrescreve
ATUALIZAR = "atualizar"          # banco vazio/ inválido → preenche com web
IGNORAR = "ignorar"              # banco já corroborado → nada a fazer
MARCAR_REVISAO = "marcar_revisao"  # sem corroboração / divergência não resolvida
EXCLUIR = "excluir"              # valor presente mas inválido e sem substituto confiável

# Mapa ação→decisão
_ACAO_DECISAO = {
    "mantido": IGNORAR,
    "corrigido": CORRIGIR,
    "preenchido": ATUALIZAR,
    "sem_corroboracao": MARCAR_REVISAO,
    "divergencia_nao_resolvida": MARCAR_REVISAO,
    "sem_dado": MARCAR_REVISAO,
}


def decide(resolution: FieldResolution) -> str:
    """Decisão de saneamento para uma resolução de campo."""
    decisao = _ACAO_DECISAO.get(resolution.acao, MARCAR_REVISAO)
    # Valor presente no banco, inválido (fora de faixa) e sem substituto web → excluir (vira N/D)
    if decisao == MARCAR_REVISAO and resolution.bd is None and resolution.novo is None:
        # bd já None aqui significa que o valor do banco foi invalidado pela faixa
        return MARCAR_REVISAO
    return decisao


def is_write_decision(decision: str) -> bool:
    """True para decisões que efetivamente gravam no banco."""
    return decision in (CORRIGIR, ATUALIZAR)
