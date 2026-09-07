"""As nove chaves independentes, e o teto que a fase impõe sobre elas.

O requisito exige flag própria para cada uma destas funcionalidades: coleta,
classificação, impacto histórico, alteração de prioridade, alertas externos,
Modo Crise, LLM, antifragilidade e recomendações emergenciais. "Independentes"
é literal -- desligar a LLM não pode desligar a coleta, e desligar as
recomendações não pode apagar o painel.

Duas travas, e elas não se substituem
--------------------------------------
1. **A flag**, que é a vontade de quem configura.
2. **A fase**, que é o teto do que aquela vontade pode alcançar.

Ligar ``recomendacao_emergencial`` na Fase 2 não a liga. :func:`ativo` devolve
``False`` e :func:`motivo` diz que a fase é o impedimento -- em vez de a tela
simplesmente não mostrar nada e deixar quem configurou achando que ligou.

Sem esse teto, "estamos na Fase 2" seria uma frase no README enquanto o código
fizesse outra coisa: o defeito de ``memoria: declaracao-de-rigor-nao-verificada``,
em que a tela afirmava um rigor que o código não praticava.

Por omissão, tudo começa desligado
-----------------------------------
``PADRAO`` liga apenas a coleta e a classificação -- o que a Fase 1 permite. O
requisito é explícito: *"se algo estiver incompleto, informe claramente e
mantenha a funcionalidade correspondente desativada por feature flag"*. Default
ligado transformaria "esqueci de configurar" em "liberado para decisão real".

Puro: sem Streamlit e sem banco. A leitura de configuração passa por
:func:`core.config._get_secret`, que é o único ponto do projeto que fala com
``os.environ`` e ``st.secrets``.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Fases ────────────────────────────────────────────────────────────────────
OBSERVACAO = 1
PAINEL = 2
RECOMENDACAO = 3
CRISE = 4

NOME_FASE: dict[int, str] = {
    OBSERVACAO: "Fase 1 — Observação silenciosa",
    PAINEL: "Fase 2 — Painel informativo",
    RECOMENDACAO: "Fase 3 — Recomendações conjunturais",
    CRISE: "Fase 4 — Modo Crise",
}

DESCRICAO_FASE: dict[int, str] = {
    OBSERVACAO: "O sistema coleta e mede. Nada é apresentado como base para "
                "decisão, e nenhuma recomendação é gerada.",
    PAINEL: "O que foi medido aparece na tela, com fonte, data e frescor. "
            "Nenhuma recomendação é emitida.",
    RECOMENDACAO: "Recomendações conjunturais são apresentadas, sempre com "
                  "confirmação explícita e nunca executadas pelo sistema.",
    CRISE: "O comportamento excepcional está completo, incluindo alertas "
           "externos e recomendações emergenciais.",
}

# ── As nove flags ────────────────────────────────────────────────────────────
COLETA = "coleta"
CLASSIFICACAO = "classificacao"
IMPACTO_HISTORICO = "impacto_historico"
ALTERACAO_PRIORIDADE = "alteracao_prioridade"
ALERTAS_EXTERNOS = "alertas_externos"
MODO_CRISE = "modo_crise"
LLM = "llm"
ANTIFRAGILIDADE = "antifragilidade"
RECOMENDACAO_EMERGENCIAL = "recomendacao_emergencial"


@dataclass(frozen=True)
class Chave:
    """Uma funcionalidade liberável.

    ``fase_minima`` é o teto de que fala o docstring do módulo. ``efeito`` é o
    que some da tela quando ela está desligada -- escrito para que a tela de
    administração possa dizer a consequência em vez de mostrar um interruptor
    sem legenda.
    """

    nome: str
    fase_minima: int
    rotulo: str
    efeito: str
    variavel: str

    @property
    def rotulo_fase(self) -> str:
        return NOME_FASE[self.fase_minima]


CHAVES: dict[str, Chave] = {
    c.nome: c for c in (
        Chave(COLETA, OBSERVACAO, "Coleta de notícias",
              "nenhuma notícia nova entra; o painel mostra o que já existe",
              "APP4_FLAG_COLETA"),
        Chave(CLASSIFICACAO, OBSERVACAO, "Classificação de eventos",
              "as notícias entram sem tipo de evento e sem severidade",
              "APP4_FLAG_CLASSIFICACAO"),
        Chave(IMPACTO_HISTORICO, PAINEL, "Impacto histórico",
              "some a comparação com eventos passados e a faixa histórica",
              "APP4_FLAG_IMPACTO_HISTORICO"),
        Chave(ANTIFRAGILIDADE, PAINEL, "Índice de antifragilidade",
              "some o índice e a decomposição dos seus componentes",
              "APP4_FLAG_ANTIFRAGILIDADE"),
        Chave(LLM, PAINEL, "Explicação por LLM",
              "a explicação exibida passa a ser a determinística do backend",
              "APP4_FLAG_LLM"),
        Chave(ALTERACAO_PRIORIDADE, RECOMENDACAO, "Alteração de prioridade de aporte",
              "a ordem de prioridade dos aportes não é alterada por conjuntura",
              "APP4_FLAG_ALTERACAO_PRIORIDADE"),
        Chave(MODO_CRISE, CRISE, "Modo Crise",
              "os níveis 3 e 4 são registrados, mas a tela não muda de modo",
              "APP4_FLAG_MODO_CRISE"),
        Chave(ALERTAS_EXTERNOS, CRISE, "Alertas em canal externo",
              "os alertas ficam só no painel; nada sai do app",
              "APP4_FLAG_ALERTAS_EXTERNOS"),
        Chave(RECOMENDACAO_EMERGENCIAL, CRISE, "Recomendações emergenciais",
              "nenhuma recomendação de emergência é gerada",
              "APP4_FLAG_RECOMENDACAO_EMERGENCIAL"),
    )
}

#: Tudo desligado, menos o que a Fase 1 já permite e não afirma nada.
PADRAO: dict[str, bool] = {nome: nome in (COLETA, CLASSIFICACAO)
                           for nome in CHAVES}

VARIAVEL_FASE = "APP4_FASE"


def _texto_para_bool(valor: str, padrao: bool) -> bool:
    """Só ``true/1/sim/on`` liga. Qualquer outra coisa deixa como está.

    Valor ilegível **não** liga a flag: numa liberação gradual, o erro de
    digitação tem de cair para o lado seguro.
    """
    v = (valor or "").strip().lower()
    if v in ("true", "1", "sim", "yes", "on"):
        return True
    if v in ("false", "0", "nao", "não", "no", "off"):
        return False
    return padrao


@dataclass(frozen=True)
class Estado:
    """A fase corrente e o valor configurado de cada chave."""

    fase: int
    valores: dict[str, bool]

    def ativo(self, nome: str) -> bool:
        chave = CHAVES[nome]
        return bool(self.valores.get(nome, False)) and self.fase >= chave.fase_minima

    def motivo(self, nome: str) -> str:
        """Por que a funcionalidade está desligada. Vazio quando está ligada.

        Distinguir "a flag está desligada" de "a fase não alcança" é o que
        impede alguém de passar a tarde ligando um interruptor que a fase
        anula.
        """
        chave = CHAVES[nome]
        if not self.valores.get(nome, False):
            return f"a flag {chave.variavel} está desligada"
        if self.fase < chave.fase_minima:
            return (f"a fase corrente é {NOME_FASE[self.fase]} e esta "
                    f"funcionalidade exige {chave.rotulo_fase}")
        return ""

    @property
    def ligadas(self) -> tuple[str, ...]:
        return tuple(n for n in CHAVES if self.ativo(n))

    @property
    def barradas_pela_fase(self) -> tuple[str, ...]:
        """Ligadas na configuração e anuladas pela fase.

        É a lista que a tela de administração precisa mostrar em destaque:
        alguém quis ligar e não conseguiu, e o silêncio aqui viraria confusão.
        """
        return tuple(n for n in CHAVES
                     if self.valores.get(n, False) and not self.ativo(n))

    def resumo_auditoria(self) -> dict:
        return {
            "fase": self.fase,
            "fase_nome": NOME_FASE[self.fase],
            "ligadas": list(self.ligadas),
            "barradas_pela_fase": list(self.barradas_pela_fase),
            "desligadas": [n for n in CHAVES
                           if not self.valores.get(n, False)],
        }


def carregar(*, leitor=None) -> Estado:
    """Lê fase e flags da configuração. ``leitor`` existe para os testes.

    Fase ilegível ou fora de 1..4 cai na Fase 1 -- o lado seguro. Cair na Fase 4
    por causa de um typo liberaria decisão real por engano, que é o oposto do
    que o Prompt 5 pede.
    """
    if leitor is None:
        from core.config import _get_secret as leitor  # noqa: N813
    try:
        fase = int((leitor(VARIAVEL_FASE, "") or "").strip() or OBSERVACAO)
    except ValueError:
        fase = OBSERVACAO
    if fase not in NOME_FASE:
        fase = OBSERVACAO
    valores = {
        nome: _texto_para_bool(leitor(chave.variavel, ""), PADRAO[nome])
        for nome, chave in CHAVES.items()
    }
    return Estado(fase=fase, valores=valores)


def ativo(nome: str, *, estado: Estado | None = None) -> bool:
    """Atalho de leitura. Levanta ``KeyError`` para chave desconhecida.

    Falhar alto em nome errado é deliberado: uma flag inexistente que
    devolvesse ``False`` faria a funcionalidade sumir em silêncio, e um typo no
    nome pareceria uma decisão de produto.
    """
    if nome not in CHAVES:
        raise KeyError(f"flag desconhecida: {nome!r}")
    return (estado or carregar()).ativo(nome)


def motivo(nome: str, *, estado: Estado | None = None) -> str:
    if nome not in CHAVES:
        raise KeyError(f"flag desconhecida: {nome!r}")
    return (estado or carregar()).motivo(nome)
