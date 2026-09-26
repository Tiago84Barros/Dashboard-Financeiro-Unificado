"""
core/inteligencia_ativos/modelos.py
Modelos da análise individual de um ativo.

Puro: dataclasses congeladas, sem banco, Streamlit ou LLM. A análise é um
``AnaliseAtivo`` montado em ``analise.py``; a tela só lê.

A ordem dos campos de ``AnaliseAtivo`` é a ordem da tela::

    ativo → papel → peso atual → peso/faixa desejada → fundamentos →
    valuation → pares → cenário → notícias → relatórios → próximos eventos →
    impacto na carteira → ação a considerar

As quatro perguntas que a análise separa (``Questao``) não são uma nota
única: fundamentos bons e valuation caro, ou ativo excelente que não serve a
ESTE investidor, são respostas diferentes e ficam visíveis como tais.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from core.inteligencia_ativos.calculos import Calculos

# -- estados de seção ----------------------------------------------------------

DISPONIVEL = "DISPONIVEL"
PENDENTE = "PENDENTE"      # a etapa ainda não foi implementada
SEM_DADOS = "SEM_DADOS"    # implementada, mas sem dado para este ativo

# -- papéis -------------------------------------------------------------------

PAPEIS: dict[str, str] = {
    "growth": "crescimento do patrimônio",
    "income": "geração de renda",
    "dividend": "dividendos",
    "inflation_protection": "proteção contra a inflação",
    "capital_preservation": "preservação do capital",
    "liquidity": "liquidez",
    "international_diversification": "diversificação internacional",
    "sector_exposure": "exposição setorial",
    "defensive": "posição defensiva",
    "opportunity": "oportunidade",
    "emergency_reserve": "reserva de emergência",
    "fixed_income_core": "núcleo de renda fixa",
    "real_estate_income": "renda imobiliária",
}

# -- ações a considerar ---------------------------------------------------------
# Nenhuma é ordem de compra ou venda: são leituras de adequação.

MANTER = "MANTER"
APORTE_COMPATIVEL = "APORTE_COMPATIVEL_COM_ESTRATEGIA"
EXPOSICAO_ADEQUADA = "EXPOSICAO_ADEQUADA"
REAVALIAR_APORTES = "REAVALIAR_NOVOS_APORTES"
REDUZIR_CONCENTRACAO = "CONSIDERAR_REDUCAO_DE_CONCENTRACAO"
REAVALIAR_TESE = "REAVALIAR_TESE"
COMPARAR_ALTERNATIVAS = "COMPARAR_COM_ALTERNATIVAS"

ROTULO_ACAO: dict[str, str] = {
    MANTER: "Manter",
    APORTE_COMPATIVEL: "Aporte compatível com a estratégia",
    EXPOSICAO_ADEQUADA: "Exposição adequada",
    REAVALIAR_APORTES: "Reavaliar novos aportes",
    REDUZIR_CONCENTRACAO: "Considerar redução de concentração",
    REAVALIAR_TESE: "Reavaliar tese",
    COMPARAR_ALTERNATIVAS: "Comparar com alternativas",
}

# Quanto mais alto, mais a ação pede atenção. Quando várias regras disparam,
# vale a de maior prioridade e as outras viram justificativa.
PRIORIDADE_ACAO: dict[str, int] = {
    REAVALIAR_TESE: 60,
    REDUZIR_CONCENTRACAO: 50,
    COMPARAR_ALTERNATIVAS: 40,
    REAVALIAR_APORTES: 30,
    APORTE_COMPATIVEL: 20,
    EXPOSICAO_ADEQUADA: 10,
    MANTER: 0,
}

# -- as quatro perguntas --------------------------------------------------------

Q_FUNDAMENTOS = "fundamentos"
Q_VALUATION = "valuation"
Q_FUNCAO = "funcao"
Q_ADEQUACAO = "adequacao"

PERGUNTAS: dict[str, str] = {
    Q_FUNDAMENTOS: "O ativo possui bons fundamentos?",
    Q_VALUATION: "O valuation está interessante?",
    Q_FUNCAO: "O ativo possui uma função útil em um portfólio?",
    Q_ADEQUACAO: "Faz sentido para você, nesta carteira e para o seu objetivo?",
}


@dataclass(frozen=True)
class ContextoInvestidor:
    """Tudo o que uma análise de ativo precisa saber do investidor.

    Só nasce de uma política concluída (``contexto.montar``). Não existe
    análise sem ele: é o parâmetro obrigatório de ``analise.analisar``.
    """
    versao_politica: int
    objetivo: str | None
    horizonte: str | None
    perfil_risco: str | None
    capacidade_risco: str | None
    estrategia: str | None
    necessidade_liquidez: str | None
    alocacao_alvo: dict[str, float]          # classe da política → %
    limites_classe: dict[str, float]         # classe da política → teto %
    limite_por_ativo: float | None
    limite_por_setor: float | None
    restricoes: tuple[str, ...]
    texto_politica: str                      # como a LLM lê a política
    # carteira completa
    total_mercado: float
    posicoes: tuple[dict, ...]
    peso_por_classe: dict[str, float]        # classe da política → % atual
    peso_por_setor: dict[str, float]         # setor → % atual
    peso_fora_da_politica: float             # % em classes que a política não cobre
    # pesos, faixas, desvios, concentração e alertas (calculos.py)
    calculos: Calculos | None = None


@dataclass(frozen=True)
class InfoBasica:
    ticker: str
    nome: str
    classe: str                   # rótulo da carteira ("FII", "Ações BR"...)
    classe_politica: str | None   # renda_fixa | acoes_br | fiis | exterior
    subclasse: str | None
    setor: str | None
    moeda: str
    valor_investido: float | None
    valor_mercado: float | None
    peso_atual: float             # % da carteira


@dataclass(frozen=True)
class FaixaAlvo:
    """O que a política diz sobre o peso deste ativo.

    A política não tem alvo por ativo; tem alvo por classe e, se a pessoa
    respondeu, teto por ativo e por setor. Um alvo individual inventado
    (alvo da classe ÷ número de ativos) seria um número que ninguém escolheu
    apresentado como escolha — por isso não existe aqui.
    """
    alvo_classe: float | None          # % da carteira desejado para a classe
    peso_classe: float | None          # % atual da classe
    desvio_classe: float | None        # peso_classe − alvo_classe, em pp
    teto_ativo: float | None           # limite por ativo, se respondido
    folga_ativo: float | None          # teto_ativo − peso_atual, em pp
    teto_setor: float | None
    peso_setor: float | None
    # só existem se o usuário informar uma faixa para o ativo
    piso_ativo: float | None = None
    alvo_ativo: float | None = None
    diferenca_para_alvo_ativo: float | None = None
    overweight_ativo: float = 0.0
    underweight_ativo: float = 0.0
    status_ativo: str | None = None     # calculos.DENTRO/ACIMA/ABAIXO/SEM_REFERENCIA
    emissor: str | None = None
    indexador: str | None = None


@dataclass(frozen=True)
class Papel:
    codigo: str
    principal: bool
    motivo: str        # por que este papel foi atribuído
    fonte: str         # "classe" | "setor" | "nome" | "usuario"

    @property
    def rotulo(self) -> str:
        return PAPEIS.get(self.codigo, self.codigo)


@dataclass(frozen=True)
class Gatilho:
    """Um motivo possível para a tese deixar de valer."""
    codigo: str
    descricao: str
    estado: str               # DISPONIVEL (avaliado) | PENDENTE
    disparado: bool | None    # None = ainda não avaliado
    detalhe: str = ""


@dataclass(frozen=True)
class Tese:
    por_que_esta_na_carteira: str
    alinhamento: str               # como o papel conversa com a política
    valida: bool | None            # None = não dá para dizer ainda
    gatilhos: tuple[Gatilho, ...]


@dataclass(frozen=True)
class Secao:
    """Um bloco de dados externos (fundamentos, valuation, notícias...).

    As seções que ainda não têm implementação saem com ``estado=PENDENTE`` e
    dizem o que vão trazer; cada uma tem um provedor em
    ``secoes.PROVEDORES`` que será trocado pelo real.
    """
    chave: str
    titulo: str
    estado: str
    resumo: str
    dados: dict = field(default_factory=dict)
    fonte: str | None = None


@dataclass(frozen=True)
class Impacto:
    peso_atual: float
    peso_classe: float | None
    alvo_classe: float | None
    desvio_classe: float | None
    peso_setor: float | None
    teto_setor: float | None
    observacoes: tuple[str, ...]


@dataclass(frozen=True)
class Acao:
    estado: str
    justificativas: tuple[str, ...]
    completa: bool      # False = fundamentos/valuation ainda não entraram

    @property
    def rotulo(self) -> str:
        return ROTULO_ACAO[self.estado]


@dataclass(frozen=True)
class Resposta:
    """Resposta a uma das quatro perguntas."""
    pergunta: str
    estado: str      # DISPONIVEL | PENDENTE | SEM_DADOS
    resposta: str


@dataclass(frozen=True)
class AnaliseAtivo:
    ativo: InfoBasica
    papeis: tuple[Papel, ...]
    faixa: FaixaAlvo
    tese: Tese
    fundamentos: Secao
    valuation: Secao
    pares: Secao
    cenario: Secao
    noticias: Secao
    relatorios: Secao
    eventos: Secao
    impacto: Impacto
    acao: Acao
    questoes: dict[str, Resposta]
    versao_politica: int

    @property
    def papel_principal(self) -> Papel | None:
        return next((p for p in self.papeis if p.principal), None)

    @property
    def secoes_externas(self) -> tuple[Secao, ...]:
        return (self.fundamentos, self.valuation, self.pares, self.cenario,
                self.noticias, self.relatorios, self.eventos)

    def como_dict(self) -> dict:
        return asdict(self)
