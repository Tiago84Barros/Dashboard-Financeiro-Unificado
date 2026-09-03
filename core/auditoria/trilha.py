"""Registro de cada recomendação, com o que basta para reconstruí-la depois.

O que este módulo NÃO faz, de propósito
----------------------------------------
Não grava a resposta da LLM como justificativa. A explicação do modelo é
apresentação; o motivo é o que o backend calculou. Guardar a frase bonita no
lugar das evidências faria a trilha responder "porque o texto dizia isso" --
que é precisamente a resposta que o requisito proíbe ao separar as quatro
camadas.

O campo :attr:`Registro.explicacao_llm` existe, mas ao lado das evidências e
marcado como tal, junto do veredito da validação. Ele serve para uma auditoria
posterior perguntar *o que foi mostrado ao usuário*, que é uma pergunta
diferente de *por que o sistema recomendou*.

Segredo não entra
-----------------
Todo texto passa por :func:`core.seguranca.segredos.mascarar` antes de ir para
o banco, com ``pessoais=True``. A trilha é lida por quem investiga um problema,
e é o lugar mais fácil de esquecer que existe -- em ``memoria:
faixa-de-validacao-apaga-evidencia`` o custo de apagar evidência já foi pago;
aqui o mascaramento preserva o rótulo do que foi ocultado.

Retenção
--------
:data:`RETENCAO_DIAS` limita o tamanho da trilha. O Supabase está em 425 MB de
500 MB (``memoria: supabase-voltou-para-baixo-do-limite``) e uma tabela que só
cresce é uma dívida com data marcada. :func:`expurgar` implementa os itens
"retenção adequada" e "exclusão segura" -- e devolve quantas linhas removeu,
porque expurgo que não conta o que apagou não é verificável.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text

from core.seguranca import segredos

log = logging.getLogger(__name__)

TABELA = "public.recomendacao_auditoria"

#: Um ano. Prazo escolhido para caber um ciclo inteiro de mercado -- auditar
#: uma recomendação de crise exige comparar com a mesma época do ano anterior.
RETENCAO_DIAS = 365

# ── Decisões possíveis ───────────────────────────────────────────────────────
PROPOSTA = "proposta"
CONFIRMADA = "confirmada"
RECUSADA = "recusada"
BLOQUEADA = "bloqueada"


@dataclass(frozen=True)
class Registro:
    """Uma recomendação, com as três partes da pergunta do requisito."""

    # -- "essa mudança"
    acao: str
    ativo: str = ""
    percentual: float | None = None
    valor: float | None = None

    # -- "por que"
    motivo: str = ""
    evidencias: tuple[str, ...] = ()
    motor: str = ""
    nivel_crise: int | None = None

    # -- "naquele momento"
    momento: dt.datetime | None = None
    versao_modelo: str = ""
    versao_dados: str = ""
    frescor_horas: float | None = None

    # -- o que aconteceu com ela
    decisao: str = PROPOSTA
    bloqueios: tuple[str, ...] = ()
    travas_nao_verificadas: tuple[str, ...] = ()

    # -- o que foi mostrado (≠ por que foi recomendado)
    explicacao_llm: str = ""
    llm_aprovada: bool | None = None
    llm_motivo: str = ""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.acao.strip():
            raise ValueError(
                "registro sem ação não responde 'essa mudança'; recusado na "
                "origem em vez de virar linha vazia na trilha.")
        if self.decisao not in (PROPOSTA, CONFIRMADA, RECUSADA, BLOQUEADA):
            raise ValueError(f"decisão desconhecida: {self.decisao!r}")

    @property
    def carimbo(self) -> dt.datetime:
        return self.momento or dt.datetime.now(dt.timezone.utc)

    def responder(self) -> str:
        """A resposta à pergunta do requisito, em texto corrido."""
        quando = self.carimbo.strftime("%d/%m/%Y %H:%M UTC")
        alvo = f" em {self.ativo}" if self.ativo else ""
        tamanho = ""
        if self.percentual is not None:
            tamanho = f" ({self.percentual:.2f}% da carteira)"
        elif self.valor is not None:
            tamanho = f" (R$ {self.valor:,.2f})"
        linhas = [f"Em {quando}, o APP4 propôs: {self.acao}{alvo}{tamanho}."]
        if self.motor:
            linhas.append(f"Motor: {self.motor}"
                          + (f", nível de crise {self.nivel_crise}"
                             if self.nivel_crise is not None else ""))
        if self.motivo:
            linhas.append(f"Motivo: {self.motivo}")
        for e in self.evidencias:
            linhas.append(f"  · evidência: {e}")
        versoes = [v for v in (
            f"modelo {self.versao_modelo}" if self.versao_modelo else "",
            f"dados {self.versao_dados}" if self.versao_dados else "",
            (f"frescor {self.frescor_horas:.1f}h"
             if self.frescor_horas is not None else ""),
        ) if v]
        if versoes:
            linhas.append("Vigente naquele momento: " + ", ".join(versoes))
        if self.bloqueios:
            linhas.append("Bloqueios ativos: " + ", ".join(self.bloqueios))
        if self.travas_nao_verificadas:
            linhas.append("Travas não verificadas: "
                          + ", ".join(self.travas_nao_verificadas))
        linhas.append(f"Desfecho: {self.decisao}.")
        if self.llm_aprovada is False:
            linhas.append(
                "A explicação gerada pelo modelo foi reprovada e não foi "
                f"exibida ({self.llm_motivo or 'sem motivo registrado'}).")
        return "\n".join(linhas)

    def para_linha(self) -> dict:
        """Dicionário pronto para o INSERT, com todo texto já mascarado."""
        def limpo(s: str) -> str:
            return segredos.mascarar(s or "", pessoais=True)

        return {
            "id": self.id,
            "momento": self.carimbo,
            "acao": limpo(self.acao),
            "ativo": limpo(self.ativo),
            "percentual": self.percentual,
            "valor": self.valor,
            "motivo": limpo(self.motivo),
            "evidencias": json.dumps([limpo(e) for e in self.evidencias],
                                     ensure_ascii=False),
            "motor": self.motor,
            "nivel_crise": self.nivel_crise,
            "versao_modelo": self.versao_modelo,
            "versao_dados": self.versao_dados,
            "frescor_horas": self.frescor_horas,
            "decisao": self.decisao,
            "bloqueios": json.dumps(list(self.bloqueios), ensure_ascii=False),
            "travas_nao_verificadas": json.dumps(
                list(self.travas_nao_verificadas), ensure_ascii=False),
            "explicacao_llm": limpo(self.explicacao_llm)[:4000],
            "llm_aprovada": self.llm_aprovada,
            "llm_motivo": limpo(self.llm_motivo),
        }


class AuditoriaIndisponivel(RuntimeError):
    """A trilha não pôde ser gravada.

    Erro próprio, e não ``Exception`` genérica, porque quem chama precisa
    distinguir esta falha de qualquer outra: ela é o gatilho da trava
    ``auditoria_falhou``, que bloqueia mudanças estratégicas. Capturar tudo em
    ``except Exception`` transformaria a trava num ``pass``.
    """


_SQL_INSERT = text(f"""
    INSERT INTO {TABELA} (
        id, momento, acao, ativo, percentual, valor, motivo, evidencias,
        motor, nivel_crise, versao_modelo, versao_dados, frescor_horas,
        decisao, bloqueios, travas_nao_verificadas, explicacao_llm,
        llm_aprovada, llm_motivo)
    VALUES (
        :id, :momento, :acao, :ativo, :percentual, :valor, :motivo,
        CAST(:evidencias AS JSONB), :motor, :nivel_crise, :versao_modelo,
        :versao_dados, :frescor_horas, :decisao, CAST(:bloqueios AS JSONB),
        CAST(:travas_nao_verificadas AS JSONB), :explicacao_llm,
        :llm_aprovada, :llm_motivo)
    ON CONFLICT (id) DO UPDATE SET
        decisao = EXCLUDED.decisao,
        bloqueios = EXCLUDED.bloqueios,
        llm_aprovada = EXCLUDED.llm_aprovada,
        llm_motivo = EXCLUDED.llm_motivo
""")


def registrar(reg: Registro, *, engine=None) -> Registro:
    """Grava e devolve o registro. Levanta :class:`AuditoriaIndisponivel`.

    Não engole exceção: recomendação sem trilha é exatamente o caso que a
    trava ``auditoria_falhou`` existe para pegar, e engolir aqui a desligaria.
    """
    if engine is None:  # import tardio: mantém o módulo testável sem banco
        from core.database import get_engine
        engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(_SQL_INSERT, reg.para_linha())
    except Exception as exc:  # noqa: BLE001 - reetiquetado logo abaixo
        log.warning("trilha de auditoria indisponível: %s", exc)
        raise AuditoriaIndisponivel(str(exc)) from exc
    return reg


def historico(*, engine=None, ativo: str = "", limite: int = 200) -> list[dict]:
    """Últimos registros, mais recentes primeiro."""
    if engine is None:
        from core.database import get_engine
        engine = get_engine()
    filtro = "WHERE ativo = :ativo" if ativo else ""
    sql = text(f"SELECT * FROM {TABELA} {filtro} "
               "ORDER BY momento DESC LIMIT :limite")
    params = {"limite": int(limite)}
    if ativo:
        params["ativo"] = ativo
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql, params)]


def expurgar(*, engine=None, dias: int = RETENCAO_DIAS,
             agora: dt.datetime | None = None, aplicar: bool = False) -> int:
    """Remove registros mais velhos que ``dias``. Simula por omissão.

    Simulação por omissão é o padrão do projeto para script que apaga. Quem
    quiser apagar de verdade escreve ``aplicar=True`` e assume o ato.
    """
    if engine is None:
        from core.database import get_engine
        engine = get_engine()
    corte = (agora or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=dias)
    with engine.begin() as conn:
        n = conn.execute(
            text(f"SELECT COUNT(*) FROM {TABELA} WHERE momento < :corte"),
            {"corte": corte}).scalar_one()
        if aplicar and n:
            conn.execute(text(f"DELETE FROM {TABELA} WHERE momento < :corte"),
                         {"corte": corte})
    log.info("expurgo da trilha: %s linha(s) anteriores a %s%s",
             n, corte.date(), "" if aplicar else " (simulação)")
    return int(n)
