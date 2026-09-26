"""
core/estrategia/portao.py
Portão entre a Estratégia de Investimentos e a análise individual dos ativos.

A análise só roda com uma política CONCLUÍDA e vigente. Quem decide isso é
este módulo, não a tela: a aba de Investimentos só pergunta e mostra, e o
serviço de análise (``core/inteligencia_ativos.py``) pergunta de novo antes
de trabalhar. Assim a regra vale para qualquer chamador, não só para quem
passou pela interface.

O bloqueio é resposta de domínio, não exceção: ``Liberacao.como_dict()`` diz
por que está fechado, quanto já foi feito, o que falta e onde resolver.

Coberto por tests/test_estrategia_portao.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.estrategia import politica as pol
from core.estrategia import repositorio as repo

logger = logging.getLogger(__name__)

# Motivos do bloqueio. Um motivo por coisa que o usuário resolve de um jeito
# diferente: configurar, revisar, ou esperar o admin rodar a migration.
CONFIGURACAO_NECESSARIA = "STRATEGY_CONFIGURATION_REQUIRED"
REVISAO_NECESSARIA = "STRATEGY_REVIEW_REQUIRED"
ESTRATEGIA_INDISPONIVEL = "STRATEGY_UNAVAILABLE"

PROXIMO_PASSO = {"section": "settings", "tab": "general",
                 "feature": "investment_strategy"}
CAMINHO = ("Configurações", "Geral", "Estratégia de Investimentos")


@dataclass(frozen=True)
class Liberacao:
    disponivel: bool
    status: str
    pct: float
    faltantes: tuple[str, ...] = ()
    motivo: str | None = None
    politica: repo.Registro | None = field(default=None, repr=False)

    def como_dict(self) -> dict:
        """Resposta de domínio, no formato que o serviço devolve."""
        saida = {
            "analysis_available": self.disponivel,
            "configuration_status": self.status,
            "completion_percentage": self.pct,
        }
        if self.disponivel:
            saida["policy_version"] = self.politica.version
            return saida
        saida.update({
            "reason": self.motivo,
            "missing_fields": list(self.faltantes),
            "next_step": dict(PROXIMO_PASSO),
        })
        return saida


def avaliar(estado: repo.Estado) -> Liberacao:
    """Decide a liberação a partir do estado já carregado. Pura.

    A vigente concluída libera mesmo com uma edição aberta: o repositório
    garante que ela continua valendo até a edição ser concluída. Progresso e
    pendências vêm do rascunho quando há um, porque é nele que o usuário está
    trabalhando.
    """
    if estado.tabela_ausente:
        return Liberacao(False, pol.NOT_STARTED, 0.0,
                         faltantes=pol.OBRIGATORIOS,
                         motivo=ESTRATEGIA_INDISPONIVEL)

    vigente = estado.vigente
    if vigente is not None and vigente.status == pol.COMPLETED:
        return Liberacao(True, pol.COMPLETED, vigente.progresso.pct,
                         politica=vigente)

    base = estado.rascunho or vigente
    if base is None:
        return Liberacao(False, pol.NOT_STARTED, 0.0,
                         faltantes=pol.OBRIGATORIOS,
                         motivo=CONFIGURACAO_NECESSARIA)

    prog = base.progresso
    if vigente is not None and vigente.status == pol.NEEDS_REVIEW:
        return Liberacao(False, pol.NEEDS_REVIEW, prog.pct,
                         faltantes=prog.faltantes, motivo=REVISAO_NECESSARIA)
    return Liberacao(False, pol.IN_PROGRESS, prog.pct,
                     faltantes=prog.faltantes, motivo=CONFIGURACAO_NECESSARIA)


def verificar(*, engine=None, owner_id=None) -> Liberacao:
    """Carrega a estratégia do usuário e avalia. Falha fecha, nunca abre.

    Um banco fora do ar não pode virar "liberado": sem conseguir ler a
    política, não há premissa para a análise usar.
    """
    try:
        estado = repo.carregar(engine=engine, owner_id=owner_id)
    except Exception:  # noqa: BLE001
        logger.exception("estrategia: falha ao ler a política")
        return Liberacao(False, pol.NOT_STARTED, 0.0,
                         faltantes=pol.OBRIGATORIOS,
                         motivo=ESTRATEGIA_INDISPONIVEL)
    return avaliar(estado)
