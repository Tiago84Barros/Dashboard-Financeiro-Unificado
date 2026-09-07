"""Os cinco estados do motor, e o que cada um autoriza.

Vocabulário fechado, pelo mesmo motivo de ``core.noticias.taxonomia``: um campo
de texto livre para "nível" vira, em poucas semanas, seis grafias do mesmo
estado, e aí o histórico passa a medir grafia em vez de fato.

O que este módulo NÃO faz
-------------------------
Não decide nível. Ele declara o vocabulário e os **tetos**; quem lê evidência e
escolhe é :mod:`core.eventos_extremos.transicao`. A separação existe para que os
tetos sejam legíveis sem ler a lógica -- e testáveis sem construir evidência.

Ação é sugestão, em todos os cinco
----------------------------------
Nenhuma ação deste vocabulário executa nada. ``ACAO_PROPOR_PLANO`` propõe;
``ACAO_SUSPENDER_RECOMENDACAO`` interrompe o que o APP4 *diz*, não o que o
usuário *tem*. Isso não é detalhe de implementação que uma versão futura possa
afrouxar: é o requisito ("nenhuma operação significativa deverá ser executada
automaticamente") escrito no único lugar onde ele fica visível de fora.
``tests/test_eventos_extremos_niveis.py`` falha se aparecer no vocabulário um
verbo que executa.

Dois tetos, e por que são diferentes
------------------------------------
**Abrangência limita só o Nível 4.** "Crise localizada não deve ser classificada
automaticamente como sistêmica" fala de *sistêmica*, e sistêmico é o 4. Um banco
isolado quebrando é evento de abrangência ``ativo``; se o usuário tem 40% da
carteira nele, aquilo é crise **da carteira dele** e precisa poder chegar ao
Nível 3. O que ele não pode é virar "Sistêmico", porque o resto do mercado não
está quebrando. Por isso o teto por abrangência é 3 para tudo que não seja
regional ou global, e não uma escada de 1 a 4.

**Ausência de evidência de mercado limita o Nível 4.** Manchete, por mais
oficial que seja, descreve; preço confirma. Enquanto a série de mercado não
puder ser medida, o motor declara o teto que a cobertura permite em vez de
escalar com dado que não tem -- e o teto viaja escrito, para que "estamos no 3"
não seja confundido com "o 4 foi avaliado e descartado".
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Códigos ───────────────────────────────────────────────────────────────────
NIVEL_NORMAL = 0
NIVEL_ATENCAO = 1
NIVEL_VIGILANCIA = 2
NIVEL_CRISE = 3
NIVEL_SISTEMICO = 4

CODIGOS = (NIVEL_NORMAL, NIVEL_ATENCAO, NIVEL_VIGILANCIA, NIVEL_CRISE,
           NIVEL_SISTEMICO)

# ── Ações autorizadas ─────────────────────────────────────────────────────────
# Todo verbo aqui é de observar, medir, calcular ou propor. Nenhum é de executar.
ACAO_REGISTRAR = "registrar"
ACAO_MONITORAR = "monitorar"
ACAO_ALERTAR = "alertar"
ACAO_MEDIR_EXPOSICAO = "medir_exposicao"
ACAO_COMPARAR_HISTORICO = "comparar_historico"
ACAO_SIMULAR_CENARIOS = "simular_cenarios"
ACAO_RECALCULAR_CONJUNTURAL = "recalcular_score_conjuntural"
ACAO_SUSPENDER_RECOMENDACAO = "suspender_recomendacao_normal"
ACAO_PROPOR_PLANO = "propor_plano_defensivo"
ACAO_REAVALIAR_TUDO = "reavaliar_carteira_inteira"

ACOES = (
    ACAO_REGISTRAR, ACAO_MONITORAR, ACAO_ALERTAR, ACAO_MEDIR_EXPOSICAO,
    ACAO_COMPARAR_HISTORICO, ACAO_SIMULAR_CENARIOS,
    ACAO_RECALCULAR_CONJUNTURAL, ACAO_SUSPENDER_RECOMENDACAO,
    ACAO_PROPOR_PLANO, ACAO_REAVALIAR_TUDO,
)

#: Radicais que descrevem execução. O vocabulário acima não pode conter nenhum
#: deles, e há teste para isso. É rede contra a evolução distraída: quem um dia
#: acrescentar ``"vender_posicao"`` esbarra no teste antes do merge.
RADICAIS_DE_EXECUCAO = frozenset({
    "compr", "vend", "execut", "ordem", "aport", "resgat", "rebalance",
    "liquid", "transfer", "aplicar", "emitir_ordem",
})

# ── Abrangência do evento ─────────────────────────────────────────────────────
ABRANGENCIA_ATIVO = "ativo"
ABRANGENCIA_SETOR = "setor"
ABRANGENCIA_PAIS = "pais"
ABRANGENCIA_REGIONAL = "regional"
ABRANGENCIA_GLOBAL = "global"

ABRANGENCIAS = (ABRANGENCIA_ATIVO, ABRANGENCIA_SETOR, ABRANGENCIA_PAIS,
                ABRANGENCIA_REGIONAL, ABRANGENCIA_GLOBAL)

#: Só estas podem sustentar "sistêmico".
ABRANGENCIAS_SISTEMICAS = frozenset({ABRANGENCIA_REGIONAL, ABRANGENCIA_GLOBAL})

#: Teto de nível por abrangência. Ver o cabeçalho: o corte é no 4, não uma
#: escada, porque exposição concentrada torna crise de um único ativo uma crise
#: real *desta* carteira.
NIVEL_MAXIMO_POR_ABRANGENCIA: dict[str, int] = {
    ABRANGENCIA_ATIVO: NIVEL_CRISE,
    ABRANGENCIA_SETOR: NIVEL_CRISE,
    ABRANGENCIA_PAIS: NIVEL_CRISE,
    ABRANGENCIA_REGIONAL: NIVEL_SISTEMICO,
    ABRANGENCIA_GLOBAL: NIVEL_SISTEMICO,
}

#: Sem evidência de mercado medível, o motor não passa daqui.
NIVEL_MAXIMO_SEM_EVIDENCIA_DE_MERCADO = NIVEL_CRISE


@dataclass(frozen=True)
class Nivel:
    """Um estado, com o que ele autoriza e com que frequência se revisa.

    ``intervalo_reavaliacao_horas`` é **piso de intervalo**, não agendamento.
    A distinção já custou caro neste projeto: 24 horas corridas contra um
    gatilho de horário fixo publica dia sim, dia não, e o pulo fica "certo" pela
    regra. Por isso o Nível 0 é 20h e não 24h -- quem pendurar isto num job
    diário não perde um dia por deriva de minutos.

    ``silencio_horas`` é o tempo mínimo entre dois alertas do **mesmo** evento
    sem mudança material. Mudança material reemite antes; repetição não.
    """

    codigo: int
    chave: str
    rotulo: str
    resumo: str
    acoes: tuple[str, ...]
    intervalo_reavaliacao_horas: float
    silencio_horas: float

    @property
    def suspende_recomendacao(self) -> bool:
        return ACAO_SUSPENDER_RECOMENDACAO in self.acoes

    def autoriza(self, acao: str) -> bool:
        return acao in self.acoes


_ACOES_0 = (ACAO_REGISTRAR,)
_ACOES_1 = _ACOES_0 + (ACAO_MONITORAR,)
_ACOES_2 = _ACOES_1 + (ACAO_ALERTAR, ACAO_MEDIR_EXPOSICAO,
                       ACAO_COMPARAR_HISTORICO)
_ACOES_3 = _ACOES_2 + (ACAO_SIMULAR_CENARIOS, ACAO_RECALCULAR_CONJUNTURAL,
                       ACAO_SUSPENDER_RECOMENDACAO, ACAO_PROPOR_PLANO)
_ACOES_4 = _ACOES_3 + (ACAO_REAVALIAR_TUDO,)

NIVEIS: tuple[Nivel, ...] = (
    Nivel(NIVEL_NORMAL, "normal", "Nível 0 — Normal",
          "Nada fora do comum. O motor registra e segue a cadência de sempre.",
          _ACOES_0, 20.0, 168.0),
    Nivel(NIVEL_ATENCAO, "atencao", "Nível 1 — Atenção",
          "Há sinal, mas ainda não confirmado ou ainda sem relação com a "
          "carteira. Aumenta a frequência de coleta; não alerta.",
          _ACOES_1, 12.0, 72.0),
    Nivel(NIVEL_VIGILANCIA, "vigilancia", "Nível 2 — Vigilância",
          "Evento confirmado e relacionado. Alerta proporcional, exposição "
          "medida e comparação com eventos históricos comparáveis.",
          _ACOES_2, 6.0, 24.0),
    Nivel(NIVEL_CRISE, "crise", "Nível 3 — Crise",
          "Evento grave com efeito observável. A recomendação normal fica "
          "suspensa e o motor apresenta plano para confirmação humana.",
          _ACOES_3, 2.0, 12.0),
    Nivel(NIVEL_SISTEMICO, "sistemico", "Nível 4 — Sistêmico",
          "O choque atravessa países, setores ou classes. Toda a carteira é "
          "reavaliada; nada é executado sem confirmação.",
          _ACOES_4, 1.0, 6.0),
)

POR_CODIGO: dict[int, Nivel] = {n.codigo: n for n in NIVEIS}
POR_CHAVE: dict[str, Nivel] = {n.chave: n for n in NIVEIS}


def de_codigo(codigo: int) -> Nivel:
    """Nível pelo código. Código fora da escala é erro, não Nível 0.

    Cair para o Normal seria o pior default possível: um bug de escrita viraria
    "está tudo bem" em vez de falha visível.
    """
    try:
        return POR_CODIGO[int(codigo)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"nível desconhecido: {codigo!r}") from exc


def de_chave(chave: str) -> Nivel:
    """Nível pela chave textual, para quem lê estado gravado."""
    try:
        return POR_CHAVE[str(chave).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"nível desconhecido: {chave!r}") from exc


def teto_por_abrangencia(abrangencia: str | None) -> int:
    """Maior nível que a abrangência declarada sustenta.

    Abrangência ausente ou desconhecida recebe o teto mais restritivo, e não o
    mais permissivo: não saber onde o evento acontece não é motivo para deixá-lo
    ser sistêmico.
    """
    return NIVEL_MAXIMO_POR_ABRANGENCIA.get(
        str(abrangencia or "").strip().lower(), NIVEL_CRISE)


def descrever(nivel: "Nivel | int") -> str:
    """Linha única para log e trilha de auditoria."""
    n = nivel if isinstance(nivel, Nivel) else de_codigo(nivel)
    return (f"{n.rotulo} | revisa a cada {n.intervalo_reavaliacao_horas:g}h | "
            f"silêncio {n.silencio_horas:g}h | autoriza: {', '.join(n.acoes)}")
