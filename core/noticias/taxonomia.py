"""Vocabulário fechado do Motor Conjuntural: tipos de evento, classes de fonte,
horizontes, direções e estados de verificação.

Fechado de propósito. Um campo de texto livre para "tipo de evento" vira, em
poucas semanas, quinze grafias do mesmo fato -- e aí o agrupamento por evento e
a materialidade por tipo passam a medir grafia, não fato. Cada tipo aqui carrega
a materialidade financeira e a persistência que o índice de relevância usa, para
que esses dois números tenham uma origem auditável em vez de sair de um `if`
espalhado pelo código.

``TAXONOMIA_VERSAO`` sobe quando qualquer peso muda **ou quando o vocabulário
muda**. Sem isso, um índice recalculado com pesos novos fica indistinguível de um
calculado com os antigos, e a comparação histórica passa a somar maçãs com
laranjas. Tipo novo também muda o resultado: uma notícia que antes caía em
``indefinido`` (materialidade 0,25) passa a cair no tipo certo, e o índice dela
muda sem que peso nenhum tenha sido tocado.

1.1.0 (03/09/2026) acrescentou ``pandemia``, ``quebra_bancaria`` e
``evento_climatico``. A calibração publicada em ``docs/calibracao_conjuntural.md``
continua carimbada com 1.0.0: ela **não** foi refeita, e os três tipos novos
carregam prior declarado, nunca medido. Subir a versão sem refazer a medição e
não dizer isso é o defeito de ``memoria: versao-de-metodologia-sem-safra``.
"""
from __future__ import annotations

from dataclasses import dataclass

TAXONOMIA_VERSAO = "1.1.0"


@dataclass(frozen=True)
class TipoEvento:
    """Um tipo de fato, com o que ele implica antes de olhar a notícia.

    ``materialidade`` é o quanto esse tipo de fato costuma mexer no valor da
    empresa; ``persistencia`` é o quanto o efeito costuma durar. São priores por
    categoria, não medições da notícia específica -- a notícia específica entra
    depois, pelos outros componentes do índice.
    """

    chave: str
    rotulo: str
    materialidade: float      # 0..1
    persistencia: float       # 0..1
    horizonte: str
    escopo: str               # ativo | setor | macro


# Escopos
ESCOPO_ATIVO = "ativo"
ESCOPO_SETOR = "setor"
ESCOPO_MACRO = "macro"

# Horizontes prováveis do efeito
HORIZONTE_INTRADIA = "intradia"
HORIZONTE_CURTO = "curto"          # até ~1 mês
HORIZONTE_MEDIO = "medio"          # 1 a 6 meses
HORIZONTE_LONGO = "longo"          # acima de 6 meses
HORIZONTE_INDETERMINADO = "indeterminado"

HORIZONTES = (
    HORIZONTE_INTRADIA,
    HORIZONTE_CURTO,
    HORIZONTE_MEDIO,
    HORIZONTE_LONGO,
    HORIZONTE_INDETERMINADO,
)

# Direção provável do efeito. "indefinida" não é meio-termo entre alta e baixa:
# é a ausência de leitura, e precisa ser distinguível de "neutra" na exibição.
DIRECAO_ALTA = "alta"
DIRECAO_BAIXA = "baixa"
DIRECAO_NEUTRA = "neutra"
DIRECAO_INDEFINIDA = "indefinida"

DIRECOES = (DIRECAO_ALTA, DIRECAO_BAIXA, DIRECAO_NEUTRA, DIRECAO_INDEFINIDA)

# Estado de verificação da notícia.
VERIF_NAO_VERIFICADA = "nao_verificada"
VERIF_FONTE_PRIMARIA = "confirmada_fonte_primaria"
VERIF_INDEPENDENTE = "confirmada_independente"
VERIF_CONTESTADA = "contestada"

ESTADOS_VERIFICACAO = (
    VERIF_NAO_VERIFICADA,
    VERIF_FONTE_PRIMARIA,
    VERIF_INDEPENDENTE,
    VERIF_CONTESTADA,
)

TIPOS: tuple[TipoEvento, ...] = (
    # ── Escopo do ativo ───────────────────────────────────────────────────────
    TipoEvento("resultado_trimestral", "Resultado trimestral", 0.75, 0.55,
               HORIZONTE_CURTO, ESCOPO_ATIVO),
    # Prior IDENTICO ao trimestral, e de proposito. A unica fonte historica
    # ponto-no-tempo do repositorio e a entrega de DFP a CVM (anual), e ela
    # nao autoriza afirmar que o resultado anual e mais material ou mais
    # persistente que o trimestral -- inventar a diferenca aqui daria a dois
    # numeros identicos a aparencia de terem sido medidos separadamente. O
    # tipo existe separado porque o EVENTO e outro: chamar DFP de resultado
    # trimestral trocaria o tipo do evento pelo tipo que havia.
    TipoEvento("resultado_anual", "Resultado anual (DFP)", 0.75, 0.55,
               HORIZONTE_CURTO, ESCOPO_ATIVO),
    TipoEvento("guidance", "Projeção da companhia", 0.70, 0.70,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("fato_relevante", "Fato relevante", 0.85, 0.70,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("dividendo", "Provento / dividendo", 0.55, 0.45,
               HORIZONTE_CURTO, ESCOPO_ATIVO),
    TipoEvento("fusao_aquisicao", "Fusão ou aquisição", 0.90, 0.90,
               HORIZONTE_LONGO, ESCOPO_ATIVO),
    TipoEvento("mudanca_gestao", "Mudança na gestão", 0.55, 0.70,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("emissao_capital", "Emissão / oferta", 0.70, 0.65,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("divida_rating", "Dívida ou rating de crédito", 0.80, 0.75,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("litigio_regulatorio", "Litígio ou sanção regulatória", 0.75, 0.70,
               HORIZONTE_LONGO, ESCOPO_ATIVO),
    TipoEvento("fraude_governanca", "Fraude ou falha de governança", 0.95, 0.90,
               HORIZONTE_LONGO, ESCOPO_ATIVO),
    TipoEvento("operacional", "Operação / produção / contrato", 0.50, 0.50,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("vacancia_locacao", "Vacância ou locação (FII)", 0.70, 0.75,
               HORIZONTE_MEDIO, ESCOPO_ATIVO),
    TipoEvento("recuperacao_judicial", "Recuperação judicial ou falência", 0.98, 0.95,
               HORIZONTE_LONGO, ESCOPO_ATIVO),
    TipoEvento("deslistagem", "Saída de bolsa", 0.90, 0.95,
               HORIZONTE_LONGO, ESCOPO_ATIVO),
    # ── Escopo do setor ───────────────────────────────────────────────────────
    TipoEvento("regulacao_setorial", "Regulação setorial", 0.70, 0.85,
               HORIZONTE_LONGO, ESCOPO_SETOR),
    TipoEvento("commodity", "Preço de commodity", 0.60, 0.50,
               HORIZONTE_MEDIO, ESCOPO_SETOR),
    TipoEvento("concorrencia", "Movimento competitivo", 0.50, 0.60,
               HORIZONTE_MEDIO, ESCOPO_SETOR),
    # Quebra de banco é de escopo setorial, não macro: ela pode parar no
    # próprio banco (Banco Master, 2025) ou virar crise sistêmica (2008). Quem
    # decide qual dos dois foi é o Motor de Eventos Extremos, olhando o
    # mercado -- não a taxonomia, olhando o título da notícia. Separar os dois
    # tipos é o que permite ao motor observar a escalada em vez de assumi-la.
    TipoEvento("quebra_bancaria", "Quebra de instituição financeira", 0.92, 0.80,
               HORIZONTE_MEDIO, ESCOPO_SETOR),
    TipoEvento("evento_climatico", "Evento climático ou desastre natural",
               0.65, 0.60, HORIZONTE_MEDIO, ESCOPO_SETOR),
    # ── Escopo macro ──────────────────────────────────────────────────────────
    TipoEvento("juros_politica_monetaria", "Juros / política monetária", 0.75, 0.70,
               HORIZONTE_MEDIO, ESCOPO_MACRO),
    TipoEvento("inflacao", "Inflação", 0.65, 0.65,
               HORIZONTE_MEDIO, ESCOPO_MACRO),
    TipoEvento("cambio", "Câmbio", 0.65, 0.50,
               HORIZONTE_CURTO, ESCOPO_MACRO),
    TipoEvento("fiscal_politico", "Fiscal ou político", 0.70, 0.75,
               HORIZONTE_LONGO, ESCOPO_MACRO),
    TipoEvento("atividade_emprego", "Atividade e emprego", 0.55, 0.60,
               HORIZONTE_MEDIO, ESCOPO_MACRO),
    TipoEvento("crise_sistemica", "Crise sistêmica", 0.95, 0.85,
               HORIZONTE_LONGO, ESCOPO_MACRO),
    TipoEvento("geopolitica", "Geopolítica / conflito", 0.75, 0.75,
               HORIZONTE_LONGO, ESCOPO_MACRO),
    # Persistência alta e horizonte longo com materialidade abaixo de
    # `crise_sistemica`: 2020 mostrou que o choque de preço se desfez em meses
    # e o de comportamento (trabalho remoto, cadeia de suprimentos, escritório
    # vago) durou anos. Um prior que só olhasse o crash de março diria o
    # contrário.
    TipoEvento("pandemia", "Pandemia ou emergência sanitária", 0.90, 0.85,
               HORIZONTE_LONGO, ESCOPO_MACRO),
    # ── Resíduo ───────────────────────────────────────────────────────────────
    TipoEvento("indefinido", "Não classificado", 0.25, 0.30,
               HORIZONTE_INDETERMINADO, ESCOPO_ATIVO),
)

POR_CHAVE: dict[str, TipoEvento] = {t.chave: t for t in TIPOS}

TIPO_INDEFINIDO = POR_CHAVE["indefinido"]

# Tipos que, sozinhos, indicam evento extraordinário e justificam encurtar a
# cadência de coleta. É a ponte para o Motor de Crise: ele lerá esta lista em
# vez de reimplementar a pergunta "isto é uma crise?".
TIPOS_EMERGENCIAIS: frozenset[str] = frozenset({
    "crise_sistemica",
    "geopolitica",
    "fraude_governanca",
    "recuperacao_judicial",
    "juros_politica_monetaria",
    "pandemia",
    "quebra_bancaria",
})

#: ``evento_climatico`` ficou **fora** de :data:`TIPOS_EMERGENCIAIS` de
#: propósito. Enchente, seca e furacão são frequentes e quase sempre locais: se
#: cada um encurtasse a cadência de coleta, o gatilho dispararia dezenas de
#: vezes por ano e o custo de mantê-lo seria pago em falso alarme -- o dano mais
#: caro deste sistema. Quando um evento climático for grande o bastante para
#: importar, ele aparece nas classes de evidência de mercado e da carteira, que
#: medem tamanho em vez de contar manchete.
CLIMATICO_NAO_E_EMERGENCIAL = (
    "frequente e local demais para encurtar cadencia; o tamanho, quando houver, "
    "aparece na evidencia de mercado")


def tipo(chave: str | None) -> TipoEvento:
    """Tipo pela chave; chave desconhecida devolve ``indefinido``.

    Devolver o resíduo em vez de levantar exceção é deliberado: um provedor novo
    trazendo uma categoria que ainda não mapeamos não pode derrubar a coleta
    inteira -- ele só não ganha prior de materialidade.
    """
    return POR_CHAVE.get((chave or "").strip().lower(), TIPO_INDEFINIDO)


# ── Faixas de classificação do índice de relevância ──────────────────────────
# Os limites vêm da especificação e são configuráveis por quem chama
# `relevancia.classificar_faixa`; estes são os padrões.
FAIXA_INFORMATIVA = "informativa"
FAIXA_OBSERVACAO = "observacao"
FAIXA_REVISAO = "revisao_estrategica"

LIMITE_OBSERVACAO = 60.0
LIMITE_REVISAO = 80.0

ROTULO_FAIXA = {
    FAIXA_INFORMATIVA: "Informativa",
    FAIXA_OBSERVACAO: "Observação",
    FAIXA_REVISAO: "Candidata à revisão estratégica",
}
