"""Monta o Score Conjuntural por ativo a partir das fontes que existem hoje.

O contrato de :func:`carregar` é estreito de propósito: ele **coleta e encaixa**,
não julga. A régua que transforma componentes em ação continua sendo
:func:`core.memoria_mercado.scores.conjuntural` seguida de
:func:`~core.memoria_mercado.scores.avaliar`, e os limiares continuam morando lá.

Point-in-time, e por que os dois lados têm honestidade diferente
---------------------------------------------------------------

O corte é ``as_of``, e cada fonte é filtrada pelo carimbo que diz *quando aquilo
passou a ser sabido* — não pelo período a que se refere:

* **notícias** usam ``coletado_em`` e ``publicado_em``, e a avaliação usa
  ``avaliado_em``. Os três são gravados no momento em que o fato entra, então o
  PIT das notícias é honesto por construção. Sem o filtro em ``avaliado_em``,
  uma reavaliação feita depois vazaria para trás.
* **macro** delega a :func:`~core.macro_data.portfolio_context.load_portfolio_macro_snapshot`,
  que oferece ``strict`` e ``reconstructed``. Em 04/09/2026 o acervo macro tinha
  68.647 observações e apenas **5 dias distintos** de ``retrieved_at``, com
  68.644 delas carimbadas no backfill de 03–04/09 — ``released_at`` existia em 3
  linhas. Consequência medida: em ``strict``, qualquer ``as_of`` anterior a
  03/09/2026 devolve cobertura zero. Peso histórico com macro só é possível em
  ``reconstructed``, que é rotulado como ``histórico reconstruído ex post``. Este
  módulo propaga esse rótulo para :attr:`ContextoConjuntural.limitacoes` em vez
  de escondê-lo: um número reconstruído não vale o mesmo que um número que
  estava na tela no dia, e quem lê o backtest precisa ver a diferença.

Os quatro componentes e o que cada um tem hoje
----------------------------------------------

``scores.PESOS_CONJUNTURAIS_PRIOR`` pesa notícias 0,35, memória de mercado 0,30,
macro 0,20 e técnico 0,15. Só o macro tem fonte ligada aqui. Isso deixa a
cobertura em 0,20, abaixo de ``scores.COBERTURA_MINIMA`` (0,50) — e portanto
``avaliar`` devolve ``MANTER`` sem alterar prioridade. **É o resultado correto**,
e ele é declarado, não silencioso: os componentes ausentes saem nomeados em
:attr:`ContextoConjuntural.componentes_ausentes`. No dia em que o coletor de
notícias rodar, o mesmo código passa a mover prioridade sem alteração nenhuma.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.memoria_mercado import scores as sc

logger = logging.getLogger(__name__)

#: Janela do noticiário que compõe a leitura de hoje. Trinta dias é a mesma
#: janela que ``core.homologacao.medicoes`` usa para cobrança de frescor, e a
#: igualdade é intencional: o critério que cobra o acervo e o motor que o
#: consome não podem discordar sobre o que é "recente".
JANELA_NOTICIAS_DIAS = 30

#: Mínimo de itens por ativo para que o noticiário vire componente. Abaixo
#: disso não sai leitura — sai ``None``.
#:
#: Duas notícias não são uma conjuntura: são uma matéria e sua republicação. E o
#: caso de uma notícia só é o mais enviesado de todos, porque a única que
#: apareceu costuma ser a mais extrema — é a mesma recusa que abre
#: ``core.memoria_mercado``. O piso é menor que ``N_MINIMO_EXPERIMENTAL`` (8)
#: porque aquele conta *eventos históricos comparáveis* para uma estimativa
#: estatística, e este conta *itens correntes* que formam uma leitura; exigir 8
#: matérias em 30 dias silenciaria o noticiário de quase toda a carteira.
MINIMO_ITENS_ATIVO = 3

#: Escala do sentimento bruto quando a avaliação não fixou direção. O sentimento
#: vive em −1..+1 e o componente em −100..+100.
ESCALA_SENTIMENTO = 100.0

_TABELAS_NOTICIAS = ("noticias_itens", "noticias_avaliacoes")

_SQL_NOTICIAS = """
SELECT upper(t.ticker)      AS simbolo,
       i.id_dedup           AS id_dedup,
       i.titulo             AS titulo,
       i.veiculo            AS veiculo,
       i.url                AS url,
       i.publicado_em       AS publicado_em,
       i.tipo_evento        AS tipo_evento,
       i.sentimento_app4    AS sentimento_app4,
       i.sentimento_api     AS sentimento_api,
       a.nota               AS nota,
       a.direcao            AS direcao,
       a.confianca          AS confianca
  FROM noticias_itens i
  CROSS JOIN LATERAL jsonb_array_elements_text(
         COALESCE(i.entidades -> 'tickers', '[]'::jsonb)) AS t(ticker)
  LEFT JOIN LATERAL (
         SELECT v.nota, v.direcao, v.confianca
           FROM noticias_avaliacoes v
          WHERE v.id_dedup = i.id_dedup
            AND v.avaliado_em <= :as_of
          ORDER BY v.avaliado_em DESC
          LIMIT 1) a ON TRUE
 WHERE upper(t.ticker) = ANY(:tickers)
   AND i.coletado_em <= :as_of
   AND i.publicado_em IS NOT NULL
   AND i.publicado_em <= :as_of
   AND i.publicado_em >= :inicio
 ORDER BY i.publicado_em DESC
"""


class AcervoIndisponivel(RuntimeError):
    """O acervo de notícias não pôde ser lido.

    Distinta de "não há notícias". Sem esta separação, o banco fora do ar
    publicaria a mesma leitura que um mês tranquilo.
    """


@dataclass(frozen=True)
class ItemNoticiaBruto:
    """Um item já resolvido para um ativo, com procedência para citar."""

    simbolo: str
    titulo: str
    veiculo: str | None
    url: str | None
    publicado_em: datetime | None
    tipo_evento: str | None
    nota: float | None
    direcao: str | None
    confianca: float | None
    sentimento: float | None

    @property
    def procedencia(self) -> str:
        """Fonte, data e hora — exigência de exibição, não enfeite."""
        quando = (self.publicado_em.strftime("%d/%m/%Y %H:%M")
                  if self.publicado_em is not None else "data desconhecida")
        return f"{self.veiculo or 'veículo não informado'}, {quando}"


@dataclass(frozen=True)
class LeituraNoticias:
    """O componente de notícias de um ativo, com o tamanho da amostra ao lado."""

    simbolo: str
    valor: float | None
    n_itens: int
    itens: tuple[ItemNoticiaBruto, ...] = ()
    motivo: str = ""

    @property
    def medida(self) -> bool:
        return self.valor is not None


@dataclass(frozen=True)
class ContextoConjuntural:
    """O que a conjuntura autoriza, e tudo o que ela não pôde medir."""

    as_of: datetime
    knowledge_mode: str
    asset_class: str
    decisoes: tuple[sc.Decisao, ...]
    leituras: Mapping[str, LeituraNoticias] = field(default_factory=dict)
    impactos_macro: Mapping[str, float] = field(default_factory=dict)
    componentes_disponiveis: tuple[str, ...] = ()
    componentes_ausentes: tuple[str, ...] = ()
    cobertura_macro: float = 0.0
    limitacoes: tuple[str, ...] = ()
    #: A leitura do acervo falhou (distinto de acervo vazio). Sem este campo, a
    #: tela e o prompt escreveriam "não houve notícias" para um banco fora do ar.
    acervo_falhou: bool = False

    @property
    def move_prioridade(self) -> bool:
        """Alguma decisão altera bloqueio ou prioridade de aporte?

        Falso é o estado esperado enquanto a cobertura de componentes estiver
        abaixo do mínimo, e a tela deve dizer isso em vez de omitir a seção.
        """
        return any(d.bloqueia_aporte or d.fator_prioridade != 1.0
                   for d in self.decisoes)

    @property
    def bloqueios(self) -> dict[str, str]:
        return sc.para_aporte(self.decisoes)[0]

    @property
    def prioridades(self) -> dict[str, float]:
        return sc.para_aporte(self.decisoes)[1]


def _num(valor) -> float | None:
    try:
        if valor is None:
            return None
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero and abs(numero) != float("inf") else None


def _agora(as_of: datetime | None) -> datetime:
    momento = as_of or datetime.now(timezone.utc)
    return momento.replace(tzinfo=timezone.utc) if momento.tzinfo is None else momento


def _sinal_do_item(item: ItemNoticiaBruto) -> float | None:
    """Converte direção declarada, ou sentimento, num valor de −100 a +100.

    A direção da avaliação tem precedência sobre o sentimento do texto porque
    ela já passou pela taxonomia de eventos: "aprovou aumento de capital" pode
    ser escrita em tom positivo e ainda assim diluir o acionista. Sem direção e
    sem sentimento, o item entra na contagem de amostra mas não no valor — ele
    existe, apenas não diz para que lado.
    """
    direcao = (item.direcao or "").strip().lower()
    if direcao in {"alta", "positiva", "positivo"}:
        base = 1.0
    elif direcao in {"baixa", "negativa", "negativo"}:
        base = -1.0
    elif direcao in {"neutra", "neutro"}:
        base = 0.0
    else:
        sentimento = _num(item.sentimento)
        if sentimento is None:
            return None
        return max(-100.0, min(100.0, sentimento * ESCALA_SENTIMENTO))
    nota = _num(item.nota)
    intensidade = 100.0 if nota is None else max(0.0, min(100.0, nota))
    return base * intensidade


def _ler_noticias(engine, *, simbolos: Sequence[str], as_of: datetime,
                  janela_dias: int) -> dict[str, LeituraNoticias]:
    """Agrega o noticiário por ativo. Levanta em falha; devolve vazio em vazio."""
    inicio = as_of - timedelta(days=janela_dias)
    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text(_SQL_NOTICIAS),
                {"tickers": list(simbolos), "as_of": as_of, "inicio": inicio},
            ).mappings().all()
    except SQLAlchemyError as exc:
        # Só a primeira linha do erro: o SQL inteiro no texto tornaria a
        # limitação ilegível justamente na tela e no prompt, que são os dois
        # lugares onde alguém precisaria lê-la.
        causa = str(exc).splitlines()[0].strip()
        raise AcervoIndisponivel(
            f"acervo de notícias não pôde ser lido "
            f"({', '.join(_TABELAS_NOTICIAS)}): {causa}") from exc

    por_ativo: dict[str, list[ItemNoticiaBruto]] = {s: [] for s in simbolos}
    for linha in linhas:
        simbolo = str(linha["simbolo"])
        if simbolo not in por_ativo:
            continue
        sentimento = _num(linha["sentimento_app4"])
        if sentimento is None:
            sentimento = _num(linha["sentimento_api"])
        por_ativo[simbolo].append(ItemNoticiaBruto(
            simbolo=simbolo,
            titulo=str(linha["titulo"] or ""),
            veiculo=(str(linha["veiculo"]).strip() or None
                     if linha["veiculo"] is not None else None),
            url=(str(linha["url"]) if linha["url"] else None),
            publicado_em=linha["publicado_em"],
            tipo_evento=(str(linha["tipo_evento"]) if linha["tipo_evento"] else None),
            nota=_num(linha["nota"]),
            direcao=(str(linha["direcao"]) if linha["direcao"] else None),
            confianca=_num(linha["confianca"]),
            sentimento=sentimento,
        ))

    leituras: dict[str, LeituraNoticias] = {}
    for simbolo, itens in por_ativo.items():
        n = len(itens)
        if n < MINIMO_ITENS_ATIVO:
            leituras[simbolo] = LeituraNoticias(
                simbolo=simbolo, valor=None, n_itens=n, itens=tuple(itens),
                motivo=(f"amostra insuficiente: {n} item(ns) em "
                        f"{janela_dias} dias, mínimo de {MINIMO_ITENS_ATIVO}"))
            continue
        numerador = 0.0
        denominador = 0.0
        for item in itens:
            valor = _sinal_do_item(item)
            if valor is None:
                continue
            # Relevância pondera; confiança desconta. Item sem nota pesa 1,0
            # porque ausência de nota é ausência de julgamento, não julgamento
            # de irrelevância.
            nota = _num(item.nota)
            confianca = _num(item.confianca)
            peso = (1.0 if nota is None else max(0.0, min(100.0, nota)) / 100.0)
            if confianca is not None:
                peso *= max(0.0, min(1.0, confianca))
            if peso <= 0:
                continue
            numerador += valor * peso
            denominador += peso
        if denominador <= 0:
            leituras[simbolo] = LeituraNoticias(
                simbolo=simbolo, valor=None, n_itens=n, itens=tuple(itens),
                motivo=(f"{n} item(ns) no acervo, nenhum com direção ou "
                        "sentimento aproveitável"))
            continue
        leituras[simbolo] = LeituraNoticias(
            simbolo=simbolo,
            valor=round(max(-100.0, min(100.0, numerador / denominador)), 2),
            n_itens=n, itens=tuple(itens),
            motivo=f"{n} item(ns) em {janela_dias} dias")
    return leituras


def carregar(
    *,
    asset_class: str,
    ativos: Mapping[str, str],
    estruturais: Mapping[str, float] | None = None,
    as_of: datetime | None = None,
    knowledge_mode: str = "strict",
    macro_engine=None,
    noticias_engine=None,
    quedas: Mapping[str, float] | None = None,
    fundamentos_deteriorados: Mapping[str, bool] | None = None,
    janela_noticias_dias: int = JANELA_NOTICIAS_DIAS,
) -> ContextoConjuntural:
    """Reúne os componentes conjunturais e devolve as decisões que eles autorizam.

    ``ativos`` é ``{símbolo: setor}`` — a mesma forma que o carregador macro
    consome. ``estruturais`` é ``{símbolo: nota 0-100}`` do motor fundamentalista
    da tela chamadora; sem ele, o score estrutural fica não medido e a leitura de
    oportunidade não é liberada, que é o comportamento seguro
    (``memoria: fallback-nunca-contradiz``).

    Nenhuma exceção de infraestrutura escapa: cada fonte que falha vira uma
    limitação nomeada, e o componente correspondente fica de fora do
    denominador em vez de entrar como zero.
    """
    momento = _agora(as_of)
    simbolos = [str(s).strip().upper() for s in ativos if str(s).strip()]
    limitacoes: list[str] = []
    disponiveis: list[str] = []
    ausentes: list[str] = []

    # ── macro ────────────────────────────────────────────────────────────────
    impactos: dict[str, float] = {}
    cobertura_macro = 0.0
    if macro_engine is not None and simbolos:
        try:
            from core.macro_data.portfolio_context import (
                load_portfolio_macro_snapshot,
            )

            snapshot = load_portfolio_macro_snapshot(
                macro_engine, asset_class=asset_class,
                assets={s: str(ativos.get(s) or ativos.get(s.upper()) or "")
                        for s in simbolos},
                as_of=momento, knowledge_mode=knowledge_mode,
            )
            impactos = dict(snapshot.impacts)
            cobertura_macro = snapshot.coverage
            limitacoes.extend(snapshot.limitations)
        except (SQLAlchemyError, ValueError) as exc:
            limitacoes.append(f"contexto macro indisponível: {exc}")
            logger.warning("contexto macro indisponível em %s", momento)
    elif macro_engine is None:
        limitacoes.append("banco macro local não configurado")

    if impactos:
        disponiveis.append("macro")
    else:
        ausentes.append("macro")

    # ── notícias ─────────────────────────────────────────────────────────────
    leituras: dict[str, LeituraNoticias] = {}
    acervo_falhou = False
    if noticias_engine is not None and simbolos:
        try:
            leituras = _ler_noticias(
                noticias_engine, simbolos=simbolos, as_of=momento,
                janela_dias=janela_noticias_dias)
        except AcervoIndisponivel as exc:
            acervo_falhou = True
            limitacoes.append(str(exc))
            logger.warning("acervo de notícias indisponível: %s", exc)
    elif noticias_engine is None:
        limitacoes.append("acervo de notícias não consultado: engine ausente")

    medidas = [lt for lt in leituras.values() if lt.medida]
    if medidas:
        disponiveis.append("noticias")
        sem_amostra = len(simbolos) - len(medidas)
        if sem_amostra > 0:
            limitacoes.append(
                f"{sem_amostra} de {len(simbolos)} ativo(s) sem amostra de "
                f"notícias no corte de {janela_noticias_dias} dias")
    else:
        ausentes.append("noticias")
        if leituras:
            limitacoes.append(
                f"nenhum dos {len(simbolos)} ativo(s) alcançou o mínimo de "
                f"{MINIMO_ITENS_ATIVO} notícias em {janela_noticias_dias} dias")

    # ── memória de mercado e técnico ─────────────────────────────────────────
    # Sem estimativa de evento e sem série técnica ligadas aqui, os dois ficam
    # fora do denominador. Nomeados, para que a cobertura baixa tenha causa
    # legível na tela em vez de virar um número sem explicação.
    ausentes.extend(["memoria_mercado", "tecnico"])
    limitacoes.append(
        "componentes 'memoria_mercado' e 'tecnico' não têm fonte ligada nesta "
        "porta de entrada: ficam fora do denominador, não entram como neutros")

    # ── encaixe nos motores que já existem ───────────────────────────────────
    notas_estruturais = {str(k).strip().upper(): _num(v)
                         for k, v in (estruturais or {}).items()}
    quedas = {str(k).strip().upper(): _num(v) for k, v in (quedas or {}).items()}
    deterioracao = {str(k).strip().upper(): v
                    for k, v in (fundamentos_deteriorados or {}).items()}

    decisoes: list[sc.Decisao] = []
    for simbolo in simbolos:
        componentes: dict[str, float | None] = {
            "macro": _num(impactos.get(simbolo)),
            "noticias": (leituras[simbolo].valor if simbolo in leituras else None),
            "memoria_mercado": None,
            "tecnico": None,
        }
        conj = sc.conjuntural(componentes)
        nota = notas_estruturais.get(simbolo)
        estrut = sc.estrutural({"fundamentos": nota} if nota is not None else {})
        decisoes.append(sc.avaliar(
            estrut, conj, simbolo=simbolo,
            queda_recente=quedas.get(simbolo),
            fundamentos_deteriorados=deterioracao.get(simbolo),
        ))

    return ContextoConjuntural(
        as_of=momento,
        knowledge_mode=knowledge_mode,
        asset_class=asset_class,
        decisoes=tuple(decisoes),
        leituras=leituras,
        impactos_macro=impactos,
        componentes_disponiveis=tuple(dict.fromkeys(disponiveis)),
        componentes_ausentes=tuple(dict.fromkeys(ausentes)),
        cobertura_macro=cobertura_macro,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
        acervo_falhou=acervo_falhou,
    )


class GraoIncompativel(ValueError):
    """As chaves da conjuntura e as do plano falam de coisas diferentes."""


def para_plano_de_aporte(contexto: ContextoConjuntural,
                         universo: Iterable[str] | None = None) -> dict[str, object]:
    """Argumentos prontos para :func:`core.aporte.plano_de_aporte`.

    Existe para que a view nao monte os dois dicionarios a mao e erre a chave:
    ``bloqueios_conjunturais`` e ``prioridades`` sao coisas diferentes no plano
    (uma retira do rateio, a outra reordena quem recebe) e troca-las passaria
    despercebido.

    ``universo`` e o conjunto de chaves do plano que vai receber estes
    argumentos, e existe por causa de um erro que este projeto ja cometeu em
    outra forma: o unico ``plano_de_aporte`` em producao hoje trabalha por
    CLASSE de ativo (``renda variavel BR``, ``FIIs``), enquanto esta ponte
    decide por TICKER. Passar um ao outro nao levantaria erro -- as chaves
    simplesmente nunca se encontrariam, o bloqueio viraria no-op e a tela
    mostraria um plano "com conjuntura" que ignora a conjuntura inteira
    (``memoria: dedup-pela-coluna-que-diverge``). Com ``universo``, o
    descasamento de grao vira :class:`GraoIncompativel` na hora.
    """
    bloqueios, prioridades = sc.para_aporte(contexto.decisoes)
    if universo is not None:
        chaves = {str(k).strip().upper() for k in universo}
        nossas = {str(k).strip().upper() for k in (*bloqueios, *prioridades)}
        if nossas and not (nossas & chaves):
            raise GraoIncompativel(
                "nenhuma das " + str(len(nossas)) + " chaves da conjuntura "
                "aparece no plano (" + str(len(chaves)) + " chaves): grao "
                "diferente. Agregue a conjuntura ao grao do plano antes de "
                "passa-la, em vez de deixar o bloqueio virar no-op.")
    return {"bloqueios_conjunturais": bloqueios, "prioridades": prioridades}

def para_llm(contexto: ContextoConjuntural, *, max_itens: int = 12) -> str:
    """Bloco de texto para o prompt, com procedência em toda notícia citada.

    A LLM explica; ela não calcula. Todo número aqui já saiu dos motores, e toda
    notícia sai com veículo e data — sem isso a resposta ficaria impossível de
    conferir, que é o mesmo que ficar impossível de contestar. Componente que
    não foi medido aparece como não medido: bloco que some é indistinguível de
    bloco que está tudo bem.
    """
    linhas: list[str] = [
        "CONTEXTO CONJUNTURAL (calculado pelo backend; não recalcule nem altere)",
        f"  Corte: {contexto.as_of:%d/%m/%Y %H:%M} UTC | "
        f"modo de conhecimento: {contexto.knowledge_mode} | "
        f"classe: {contexto.asset_class}",
        f"  Componentes com fonte: "
        f"{', '.join(contexto.componentes_disponiveis) or 'nenhum'}",
        f"  Componentes NÃO medidos: "
        f"{', '.join(contexto.componentes_ausentes) or 'nenhum'}",
    ]
    if not contexto.move_prioridade:
        linhas.append(
            "  Efeito na carteira: NENHUM. A cobertura de componentes está "
            "abaixo do mínimo para alterar prioridade de aporte. Não afirme "
            "que a conjuntura está favorável nem desfavorável.")

    decisoes_ativas = [d for d in contexto.decisoes
                       if d.bloqueia_aporte or d.fator_prioridade != 1.0]
    if decisoes_ativas:
        linhas.append("  Decisões conjunturais (nenhuma delas vende):")
        for d in decisoes_ativas[:max_itens]:
            linhas.append(
                f"    - {d.simbolo}: {', '.join(d.acoes)} "
                f"(prioridade {d.fator_prioridade:.2f}"
                f"{', aporte novo bloqueado' if d.bloqueia_aporte else ''}) "
                f"— {d.motivo}")

    citaveis = [lt for lt in contexto.leituras.values() if lt.itens]
    if citaveis:
        linhas.append("  Notícias no corte (cite sempre veículo e data):")
        mostrados = 0
        for leitura in citaveis:
            if mostrados >= max_itens:
                break
            marca = (f"{leitura.valor:+.0f}" if leitura.medida
                     else f"não medido — {leitura.motivo}")
            linhas.append(f"    {leitura.simbolo} [{marca}]:")
            for item in leitura.itens[:3]:
                if mostrados >= max_itens:
                    break
                linhas.append(f"      • {item.titulo} ({item.procedencia})")
                mostrados += 1
    elif contexto.acervo_falhou:
        linhas.append(
            "  Notícias: o acervo NÃO PÔDE SER LIDO. Não há informação sobre "
            "o noticiário destes ativos — nem a favor, nem contra. Diga que a "
            "leitura falhou; não escreva que não houve notícias.")
    else:
        linhas.append(
            "  Notícias: acervo sem itens para estes ativos no corte. Isso "
            "NÃO significa ausência de fatos relevantes — significa ausência "
            "de coleta. Não conclua calmaria a partir disto.")

    if contexto.limitacoes:
        linhas.append("  Limitações declaradas:")
        linhas.extend(f"    - {lim}" for lim in contexto.limitacoes)
    return "\n".join(linhas)


def _engines() -> tuple[object | None, object | None]:
    """Resolve os dois bancos, cada falha virando ausência declarada.

    São bancos diferentes de propósito: o macro mora no armazém local
    (``macro_staging``, porta 5433) e o acervo de notícias mora no Supabase, que
    é o único que o app publicado alcança. No deploy o primeiro não existe — e
    isso precisa aparecer como componente ausente, não como conjuntura calma.
    """
    macro = noticias = None
    try:
        from core.macro_data.database import get_local_macro_engine

        macro = get_local_macro_engine()
    except Exception as exc:  # noqa: BLE001 - ausência declarada, não silêncio
        logger.info("banco macro local indisponível: %s", exc)
    try:
        from core.database import get_engine

        noticias = get_engine()
    except Exception as exc:  # noqa: BLE001
        logger.info("banco de notícias indisponível: %s", exc)
    return macro, noticias


def bloco_para_prompt(
    *,
    asset_class: str,
    ativos: Mapping[str, str],
    as_of: datetime | None = None,
    knowledge_mode: str = "strict",
    estruturais: Mapping[str, float] | None = None,
    max_itens: int = 12,
) -> str:
    """Atalho para as telas: resolve os bancos, carrega e formata em um passo.

    Devolve ``""`` só quando não há ativo para consultar. Qualquer outra falha
    vira texto dizendo o que faltou — montar prompt não pode derrubar a tela,
    mas também não pode calar a ausência e deixar a LLM supor que mediu.
    """
    if not ativos:
        return ""
    macro, noticias = _engines()
    try:
        contexto = carregar(
            asset_class=asset_class, ativos=ativos, estruturais=estruturais,
            as_of=as_of, knowledge_mode=knowledge_mode,
            macro_engine=macro, noticias_engine=noticias)
    except Exception as exc:  # noqa: BLE001
        logger.exception("contexto conjuntural indisponível")
        return ("CONTEXTO CONJUNTURAL: não foi possível montá-lo "
                f"({str(exc).splitlines()[0].strip()}). Não trate isso como "
                "ausência de notícias nem como conjuntura neutra.")
    finally:
        # O engine macro nasce nesta funcao a cada chamada
        # (``get_local_macro_engine`` nao e cacheado) e um prompt pode monta-lo
        # varias vezes por turno. Sem este dispose, cada montagem deixa um pool
        # aberto no Postgres local. O engine do Supabase vem de
        # ``st.cache_resource`` e e compartilhado: fecha-lo derrubaria o app.
        if macro is not None:
            macro.dispose()
    return para_llm(contexto, max_itens=max_itens)
