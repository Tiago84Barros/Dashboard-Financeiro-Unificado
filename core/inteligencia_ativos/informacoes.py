"""
core/inteligencia_ativos/informacoes.py
Informações recentes do ativo: notícias, relatórios e próximos eventos.

Módulo puro. Quem lê o artefato e o banco é ``fontes_informacoes.py``; quem
monta o artefato é ``scripts/publish_informacoes_recentes.py``. As regras de
classificação moram aqui para as duas pontas usarem a mesma.

Nada aqui inventa conteúdo. Toda notícia, documento e evento vem de uma fonte
com ``source``, ``source_url``, ``retrieved_at`` e ``reference_date`` quando a
fonte os tem. Onde não há dado, o texto é ``Dado não disponível.``

Notícias: filtro de relevância, não só de menção
------------------------------------------------
Medido em 26/09/2026 no acervo local (60 dias, 22.774 itens): dos 13.837
vínculos notícia→ticker, 5.492 não tinham o ativo como assunto da manchete
("Vale do Silício" como VALE3, resumos de pregão com cinco empresas, 13F de
gestora). E o ``tipo_evento`` do acervo não serve para impacto: "Ibovespa
fecha em alta" saía como ``emissao_capital``. Por isso a classificação é
refeita aqui, pela manchete, com um léxico próprio, e o que não casa com
nenhuma categoria material é descartado com o motivo contado.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

NAO_DISPONIVEL = "Dado não disponível."

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
ROTULO_NIVEL = {HIGH: "Alto", MEDIUM: "Médio", LOW: "Baixo"}
_ORDEM_NIVEL = {HIGH: 0, MEDIUM: 1, LOW: 2}
_REBAIXA = {HIGH: MEDIUM, MEDIUM: LOW, LOW: LOW}

# Dimensões avaliadas (pedido do usuário, nesta ordem).
DIMENSOES = ("fundamentos", "valuation", "receita", "divida", "dividendos",
             "estrategia", "governanca", "risco", "operacao")
ROTULO_DIMENSAO = {
    "fundamentos": "fundamentos", "valuation": "valuation",
    "receita": "receita", "divida": "dívida", "dividendos": "dividendos",
    "estrategia": "estratégia", "governanca": "governança", "risco": "risco",
    "operacao": "operação",
}


def normalizar(texto: str | None) -> str:
    """Minúsculas, sem acento, espaços simples."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


# ---------------------------------------------------------------------------
# Léxico. Ordem = materialidade: a primeira categoria que casa é a principal.
# Cada entrada: (categoria, nível, dimensões, padrão, padrão que eleva o nível)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Categoria:
    chave: str
    rotulo: str
    nivel: str
    dimensoes: tuple[str, ...]
    padrao: re.Pattern
    eleva: re.Pattern | None = None   # casa → nível sobe para HIGH/MEDIUM


def _re(*termos: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(termos)
                      + r")(?:e?s)?(?![a-z0-9])")


# Agência de rating: sem ela, "rating"/"downgrade" é quase sempre analista
# ("Buy rating", "downgrades to Hold") e cai em recomendação.
_AGENCIA = r"(moody\w*|fitch|s ?& ?p|standard (& |and )?poor\w*)"
_NAO_BEM = (r"(?! (us\$|r\$|\$|\d|over \d|mais de \d|novos? bitcoins?|"
            r"bitcoins?|btc|gpus?|chips?|equipamentos?|tokens?|solana|"
            r"ether\w*|[\d.,]+ ?% (voting )?stake))")

CATEGORIAS: tuple[Categoria, ...] = (
    Categoria("recuperacao_judicial", "Recuperação judicial / insolvência",
              HIGH, ("divida", "risco", "fundamentos"),
              _re(r"recuperacao (judicial|extrajudicial)", r"falencia",
                  r"chapter 11", r"bankruptcy", r"insolven\w*")),
    Categoria("fraude", "Fraude / irregularidade contábil", HIGH,
              ("governanca", "risco"),
              _re(r"fraude", r"fraud\w*", r"irregularidade", r"inconsistencia",
                  r"restatement", r"reapresenta\w* (de )?(balanco|resultado)",
                  r"accounting (probe|scandal)", r"desvio de recursos")),
    Categoria("fusao_aquisicao", "Fusão, aquisição ou venda de ativo", HIGH,
              ("estrategia", "valuation"),
              # "adquire US$ 18 mi em GPUs", "acquires $2M in stock": compra
              # de bem ou de ação, não de empresa. O ``\w*+`` é possessivo:
              # sem isso "acquire" recua um "s" e o plural do _re o devolve,
              # driblando a exclusão ("acquires 1,000 bitcoin").
              _re(r"aquisic(ao|oes)(?! de (gas|energia|aeronaves?|"
                  r"equipamentos?|insumos|terras?|imoveis|materia))",
                  r"adquir\w*+" + _NAO_BEM, r"fus(ao|oes)",
                  r"incorpora\w*", r"merger", r"acqui\w*+" + _NAO_BEM,
                  r"takeover (bid|offer|deal|attempt|battle|target)",
                  r"hostile (bid|takeover|offer)", r"buyout",
                  r"oferta publica de aquisicao", r"opa",
                  r"venda de (ativo|participacao|controle|subsidiaria)",
                  r"desinvest\w*", r"alienacao", r"divestiture",
                  r"spin[- ]?off", r"cisao")),
    Categoria("divida", "Dívida e crédito", MEDIUM, ("divida", "risco"),
              _re(r"divida", r"debenture\w*", r"debt", r"bonds?", r"notes offering",
                  r"refinanc\w*", r"alavancagem", r"leverage", r"covenant\w*",
                  _AGENCIA, r"rating de credito", r"credit rating",
                  r"grau de investimento", r"investment grade",
                  r"credito", r"calote", r"default", r"cri", r"emprestimo",
                  r"loan", r"resgate antecipado", r"titulos externos"),
              eleva=_re(r"calote", r"default", r"vencimento antecipado",
                        r"quebra de covenant", r"covenant breach",
                        r"perde (o )?grau de investimento",
                        _AGENCIA + r".{0,40}(rebaix\w*|downgrad\w*|corta\w*|cuts?)",
                        r"(rebaix\w*|downgrad\w*|corta\w*|cuts?).{0,40}" + _AGENCIA)),
    Categoria("resultado", "Resultado / balanço", MEDIUM,
              ("fundamentos", "receita"),
              _re(r"lucro", r"prejuizo", r"receita", r"resultado", r"balanco",
                  r"ebitda", r"trimestre", r"earnings", r"revenue", r"profit",
                  r"quarterly", r"results", r"net income", r"[1-4]t\d{2}",
                  r"q[1-4]", r"eps", r"margem", r"margin")),
    Categoria("guidance", "Guidance / projeções", MEDIUM,
              ("fundamentos", "estrategia", "receita"),
              _re(r"guidance", r"projec(ao|oes)", r"projeta\w*", r"outlook",
                  r"forecast",
                  r"estimativa (de|para) (producao|vendas|capex)",
                  r"plano de negocios", r"plano estrategico", r"capex")),
    Categoria("capital", "Emissão, recompra ou oferta", MEDIUM,
              ("valuation", "estrategia"),
              _re(r"follow[- ]?on", r"oferta (de acoes|de cotas|primaria|"
                  r"secundaria|subsequente)", r"emissao de (acoes|cotas)",
                  r"\d+a emissao", r"subscricao", r"aumento de capital",
                  r"ipo", r"share offering", r"stock offering",
                  r"recompra", r"buyback", r"repurchase", r"desdobramento",
                  r"grupamento", r"stock split")),
    # Só MUDANÇA de gestão. "Diz diretora", "CEO afirma" é opinião, não fato.
    Categoria("gestao", "Troca de gestão / conselho", MEDIUM,
              ("governanca", "estrategia"),
              _re(r"renuncia\w*", r"deixa (o )?cargo", r"deixara (o )?cargo",
                  r"nomea\w*", r"nomeia\w*", r"demit\w*",
                  r"nov[oa] (ceo|cfo|presidente|diretor\w*|conselheir\w*)",
                  r"sucess(ao|or)", r"troca de (comando|gest\w*|ceo)",
                  r"nova gestora", r"resign\w*", r"steps? down",
                  r"appoint\w*", r"names? (new )?(ceo|cfo|chair\w*)",
                  r"new (ceo|cfo|chair\w*)", r"ousted",
                  r"(mudanca|troca) de auditor\w*")),
    Categoria("litigio", "Litígio / regulatório", MEDIUM,
              ("risco", "governanca"),
              _re(r"processo", r"multa", r"cade", r"aneel", r"anatel", r"anp",
                  r"antitrust", r"lawsuit", r"sued?", r"regulador\w*",
                  r"tcu", r"justica", r"court", r"judge", r"fined?",
                  r"investigac\w*", r"investigation", r"probe", r"sanc\w*",
                  r"acordo de leniencia", r"settlement", r"ftc", r"doj",
                  r"aciona\w*", r"mpf", r"ministerio publico", r"acao civil",
                  r"denuncia\w*")),
    Categoria("dividendo", "Dividendos / proventos", LOW, ("dividendos",),
              _re(r"dividend\w*", r"jcp", r"juros sobre (o )?capital",
                  r"provento\w*", r"rendimento\w*", r"payout",
                  r"distribui\w* (de )?(lucro|resultado|rendimento)"),
              eleva=_re(r"cort\w*", r"suspen\w*", r"reduz\w*", r"cuts?",
                        r"suspend\w*", r"slash\w*", r"sem dividendo")),
    Categoria("operacional", "Operação", LOW, ("operacao",),
              _re(r"producao", r"vendas", r"entregas?", r"descobert\w*",
                  r"contrato", r"expansao", r"fabrica", r"production",
                  r"deliveries", r"lanc\w*", r"launch\w*", r"contract",
                  r"leilao", r"vacancia", r"locacao", r"inquilino",
                  r"greve", r"strike", r"acidente", r"paralisa\w*",
                  r"recall", r"outage", r"plant",
                  r"preco d[oa] (diesel|gasolina|combustive\w*)", r"reajuste",
                  r"(?<!de )acordo", r"agreement", r"pact", r"licensing",
                  r"layoffs?", r"cutting jobs", r"job cuts", r"demissoes",
                  r"perfura\w*", r"pocos?", r"vazamento", r"rompimento",
                  r"extravasamento", r"incendio")),
    Categoria("recomendacao", "Recomendação de analista", LOW, ("valuation",),
              _re(r"recomenda\w*", r"preco[- ]alvo", r"price target",
                  r"target price", r"upgrade\w*", r"downgrad\w*",
                  r"rebaix\w*", r"eleva (a )?recomendacao",
                  r"overweight", r"underweight", r"outperform",
                  r"underperform", r"(buy|sell|hold|neutral) rating",
                  r"rating (de )?(compra|venda|neutro)",
                  r"(diz|segundo|avalia) (o |a )?(xp|btg|itau bba|safra|"
                  r"goldman|jpmorgan|morgan stanley|bank of america|citi|ubs)",
                  r"tira .{0,30}da carteira", r"inclui .{0,30}na carteira",
                  r"undervalued", r"overvalued",
                  r"predicts? .{0,40}(earnings|results|eps)",
                  r"estimate for .{0,40}(earnings|results|eps)")),
)
_POR_CHAVE = {c.chave: c for c in CATEGORIAS}

# Opinião de analista não é fato da empresa: quando o padrão de recomendação
# casa, ele manda — salvo se houver também fato HIGH (RJ, fraude, M&A).
_RECOMENDACAO = _POR_CHAVE["recomendacao"]

# Manchete em forma de pergunta ou aposta: o fato é hipotético.
_ESPECULATIVA = re.compile(
    r"\?|(?<![a-z])(pode|podem|poderia|could|should|would|might|may|"
    r"prediction|previsao:|aposta|will .{0,20} (soar|crash|double))(?![a-z])")

# Manchetes que falam do mercado, não do ativo. Descartadas antes do léxico.
_RUIDO = _re(r"day trade", r"ibovespa (hoje|fecha|abre|sobe|cai|avanca|recua)",
             r"fechamento do mercado", r"abertura do mercado",
             r"carteira recomendada", r"acoes para (comprar|investir|ficar)",
             r"agenda do dia", r"o que (esperar|move)", r"radar do mercado",
             r"maiores altas", r"maiores baixas", r"destaques do",
             r"destaques das empresas", r"stocks to (buy|watch)",
             r"top \d+ stocks", r"market wrap", r"stock market today",
             r"prediction", r"i'?d (choose|buy|pick)", r"bull of the day",
             r"bear of the day", r"better buy", r"stocks today",
             r"veja quem", r"veja (as|os) \d+", r"in the know", r"vs",
             # escritórios que anunciam class action em série
             r"faruqi", r"rosen law", r"pomerantz", r"levi (&|and) korsinsky",
             r"bragar", r"glancy", r"bronstein", r"schall law", r"kessler topaz",
             r"gross law", r"portnoy law", r"shareholder (action|alert|reminder)",
             r"investor (alert|reminder)", r"class action (reminder|deadline)")

# Relato de posição de terceiro (13F, Form 4, participação) — com ou sem o
# rótulo 13F do acervo.
_POSICAO_TERCEIRO = _re(
    r"[\d,.]+ shares of", r"shares .{0,60}(acquired|sold|bought|purchased) by",
    r"(buys|acquires|takes|builds|opens|initiates) (a )?(new )?(position|stake)",
    r"(stake|position|holdings) .{0,40}(raised|lowered|trimmed|boosted|cut) by",
    r"(raises|lowers|trims|boosts|cuts) (its |their )?(stake|position|holdings)",
    r"form 4", r"acquires \$[\d.,]+ .{0,20}(in|of) stock",
    r"sells \$[\d.,]+ .{0,20}(in|of) stock", r"insider (buying|selling|sells|buys)",
    r"13f", r"participacao (relevante|acionaria) .{0,30}(atinge|passa|reduz)",
    r"acquires? (over )?[\d,.]+ (million )?(derivative )?(securities|shares)",
    r"[\d.,]+ ?% (voting )?stake", r"voting stake")

# Nome da empresa que coincide com expressão comum. Só vale quando o vínculo
# veio pelo nome (sem o ticker literal na manchete).
_FALSO_NOME: dict[str, re.Pattern] = {
    "VALE": _re(r"vale do silicio", r"vale a pena", r"vale o (mesmo|preco)",
                r"vale mais", r"vale comprar", r"vale investir", r"o que vale",
                r"vale-(refeicao|alimentacao|transporte|gas)"),
    "TGT": _re(r"price target", r"target price", r"targets?"),
    # "growth strategy", "investment strategy": só é a empresa com bitcoin
    # ou Saylor por perto.
    "MSTR": re.compile(r"^(?!.*(bitcoin|btc|saylor|microstrategy|mstr)).*"
                       r"strateg"),
}

# Agências de rating aparecem como AUTORAS de rebaixamento de terceiros.
_AGENCIAS_TICKER = frozenset({"SPGI", "MCO"})
_ACAO_DE_RATING = _re(r"downgrad\w*", r"upgrad\w*", r"rebaix\w*",
                      r"affirm\w*", r"outlook", r"perspectiva", r"rates?")

# Bancos e corretoras: aparecem na manchete como AUTORES da recomendação
# sobre outra empresa, não como assunto.
_CORRETORAS = frozenset({"MS", "GS", "JPM", "BAC", "C", "WFC", "UBS", "STT",
                         "BLK", "SCHW", "RJF", "JEF", "DB", "BCS", "HSBC",
                         "ITUB", "BBDC", "BPAC", "SANB", "BBAS", "KEY",
                         "RY", "PNC", "TD", "BMO", "CM", "PIPR", "EVR",
                         "LAZ", "SF", "COWN", "BK"})

# Motivos de descarte (contados e publicados).
D_NAO_ASSUNTO = "o ativo não é o assunto da manchete"
D_13F = "movimento de carteira de terceiro (13F)"
D_RESUMO = "resumo de mercado com várias empresas"
D_RUIDO = "manchete de mercado, não do ativo"
D_SEM_CATEGORIA = "sem fato material identificável pela manchete"
D_DUPLICADA = "duplicada de outra fonte"


def raiz_b3(ticker: str) -> str | None:
    """PETR4 → PETR. Para casar classes irmãs (PETR3/PETR4, TAEE11)."""
    m = re.fullmatch(r"([A-Z]{4})\d{1,2}", str(ticker or "").upper())
    return m.group(1) if m else None


def _literal(ticker: str, titulo: str) -> bool:
    """O ticker (ou uma classe irmã na B3) aparece escrito na manchete."""
    t = str(titulo or "")
    raiz = raiz_b3(ticker)
    if raiz:
        return re.search(rf"(?<![A-Z0-9]){raiz}\d{{1,2}}(?![A-Z0-9])",
                         t.upper()) is not None
    # EUA: sensível a caixa, senão "Post Earnings" vira POST e "Life" vira
    # LIFE. Em manchete toda em maiúsculas, ou ticker de 1-2 letras, só vale
    # a forma marcada: "(AAPL)", "$AAPL", "NASDAQ: AAPL".
    tk = re.escape(ticker.upper())
    letras = [c for c in t if c.isalpha()]
    caixa_alta = bool(letras) and sum(c.isupper() for c in letras) > 0.8 * len(letras)
    if caixa_alta or len(ticker) <= 2:
        padrao = rf"(\(|\$|(NYSE|NASDAQ|AMEX|NYSEARCA|OTC)\s*:\s*){tk}(?![A-Za-z0-9])"
    else:
        padrao = rf"(?<![A-Za-z0-9$])\$?{tk}(?![A-Za-z0-9])"
    return re.search(padrao, t) is not None


def _mesma_empresa(a: str, b: str) -> bool:
    ra, rb = raiz_b3(a), raiz_b3(b)
    return a == b or (ra is not None and ra == rb)


@dataclass(frozen=True)
class Classificacao:
    categoria: str
    categorias: tuple[str, ...]
    nivel: str
    dimensoes: tuple[str, ...]
    motivo: str


def classificar_titulo(titulo: str) -> Classificacao | None:
    """Categoria material pela manchete, ou ``None`` se nenhuma casa. Puro.

    Regras, nesta ordem:

    * o nível de cada categoria é o nominal, elevado quando o padrão de
      agravamento casa (rebaixamento por agência, corte de dividendo);
    * recomendação de analista manda sobre o resto (é opinião sobre a
      empresa, não fato dela), salvo quando há também fato HIGH;
    * manchete em forma de pergunta ou aposta desce um grau;
    * a categoria principal é a primeira, na ordem de materialidade, que
      tem o nível final; as dimensões são a união das que casaram."""
    n = normalizar(titulo)
    achadas = [c for c in CATEGORIAS if c.padrao.search(n)]
    if not achadas:
        return None

    def efetivo(c: Categoria) -> str:
        if c.eleva is not None and c.eleva.search(n):
            return HIGH if c.nivel == MEDIUM else MEDIUM
        return c.nivel

    niveis = [efetivo(c) for c in achadas]
    motivo_extra = ""
    if _RECOMENDACAO in achadas and HIGH not in niveis:
        achadas, niveis = [_RECOMENDACAO], [_RECOMENDACAO.nivel]
    nivel = min(niveis, key=_ORDEM_NIVEL.__getitem__)
    principal = achadas[niveis.index(nivel)]
    if _ESPECULATIVA.search(n) and nivel != LOW:
        nivel = _REBAIXA[nivel]
        motivo_extra = (" (manchete especulativa ou em forma de pergunta: "
                        "nível reduzido um grau)")
    dims = tuple(d for d in DIMENSOES
                 if any(d in c.dimensoes for c in achadas))
    return Classificacao(categoria=principal.chave,
                         categorias=tuple(c.chave for c in achadas),
                         nivel=nivel, dimensoes=dims,
                         motivo=principal.rotulo + motivo_extra)


def avaliar_manchete(ticker: str, titulo: str, resolvidos: tuple[str, ...],
                     *, relato_13f: bool = False
                     ) -> Classificacao | str:
    """Decide se a notícia entra para ``ticker``. Puro.

    ``resolvidos`` são os tickers que o resolvedor de entidades acha **na
    manchete** (não no resumo). Devolve a classificação ou o motivo do
    descarte (uma das constantes ``D_*``)."""
    ticker = ticker.upper()
    literal = _literal(ticker, titulo)
    pelo_nome = any(_mesma_empresa(ticker, r) for r in resolvidos)
    if not (literal or pelo_nome):
        return D_NAO_ASSUNTO
    n = normalizar(titulo)
    raiz = raiz_b3(ticker) or ticker
    falso = _FALSO_NOME.get(raiz)
    if not literal and falso is not None and falso.search(n):
        return D_NAO_ASSUNTO
    bruto = str(titulo or "")
    citados = (tuple(resolvidos)
               + tuple(re.findall(r"(?<![A-Z0-9])[A-Z]{4}\d{1,2}(?![A-Z0-9])",
                                  bruto.upper()))
               + tuple(re.findall(r"(?:NYSE|NASDAQ|AMEX)\s*:\s*([A-Z.]{1,6})",
                                  bruto)))
    outras = {raiz_b3(r) or r for r in citados
              if not _mesma_empresa(ticker, r)}
    if (not literal and raiz in _AGENCIAS_TICKER
            and _ACAO_DE_RATING.search(n)):
        return D_NAO_ASSUNTO
    # Banco citado pelo nome numa manchete de recomendação sobre OUTRA
    # empresa é o autor dela ("Morgan Stanley eleva Nvidia"), não o assunto.
    if (not literal and outras and raiz in _CORRETORAS
            and _RECOMENDACAO.padrao.search(n)):
        return D_NAO_ASSUNTO
    if relato_13f or _POSICAO_TERCEIRO.search(n):
        return D_13F
    if _RUIDO.search(n):
        return D_RUIDO
    if len(outras) >= 2:
        return D_RESUMO
    cl = classificar_titulo(titulo)
    if cl is None:
        return D_SEM_CATEGORIA
    if outras and cl.nivel != LOW:
        cl = Classificacao(cl.categoria, cl.categorias, _REBAIXA[cl.nivel],
                           cl.dimensoes,
                           cl.motivo + " (manchete divide o assunto com "
                           "outra empresa: nível reduzido um grau)")
    return cl


def chave_de_duplicata(titulo: str) -> str:
    """Manchete normalizada sem pontuação nem ``(TICKER)``: a mesma notícia
    em duas fontes ("MPF aciona Vale (VALE3)…" e "MPF aciona Vale…")."""
    t = re.sub(r"\((?:[a-z]{4}\d{1,2}|[a-z]{1,5})\)", " ", normalizar(titulo))
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return " ".join(t.split())[:90]


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Noticia:
    headline: str
    date: str | None               # reference_date: publicação (ISO)
    source: str | None
    url: str | None
    impact_level: str
    affected_dimension: tuple[str, ...]
    summary: str | None            # resumo da própria fonte, truncado
    categoria: str
    motivo: str
    retrieved_at: str | None = None
    ticker: str | None = None      # só nas do segmento: de qual par é

    @property
    def rotulo_categoria(self) -> str:
        c = _POR_CHAVE.get(self.categoria)
        return c.rotulo if c else self.categoria


@dataclass(frozen=True)
class Noticias:
    itens: tuple[Noticia, ...] = ()
    descartadas: dict[str, int] = field(default_factory=dict)
    janela_dias: int | None = None
    base_ate: str | None = None    # notícia mais recente do acervo inteiro
    fonte: str | None = None
    retrieved_at: str | None = None
    motivo: str | None = None
    # Sem notícia própria, as dos pares do mesmo segmento (30/09/2026: "se
    # não houver informações relevantes do ativo, deve ao menos mostrar
    # informações sobre como está o setor").
    setor: tuple[Noticia, ...] = ()
    setor_rotulo: str | None = None

    def como_dict(self) -> dict:
        d = asdict(self)
        d["itens"] = [asdict(i) for i in self.itens]
        d["setor"] = [asdict(i) for i in self.setor]
        return d

    @classmethod
    def de_dict(cls, d: dict) -> "Noticias":
        def _itens(lista):
            return tuple(Noticia(**{**i, "affected_dimension": tuple(
                i.get("affected_dimension") or ())}) for i in lista or ())
        return cls(itens=_itens(d.get("itens")), **{k: d.get(k) for k in (
            "janela_dias", "base_ate", "fonte", "retrieved_at", "motivo",
            "setor_rotulo")},
            descartadas=dict(d.get("descartadas") or {}),
            setor=_itens(d.get("setor")))


# Documentos: categoria da fonte → tipo pedido pelo usuário.
BALANCO, RELEASE, APRESENTACAO, GERENCIAL = ("balanco", "release",
                                             "apresentacao", "gerencial")
FATO_RELEVANTE, COMUNICADO, GUIDANCE = ("fato_relevante", "comunicado",
                                        "guidance")
RATING, ASSEMBLEIA, OFERTA, OUTRO = "rating", "assembleia", "oferta", "outro"
ROTULO_DOC = {
    BALANCO: "Balanço", RELEASE: "Release", APRESENTACAO: "Apresentação",
    GERENCIAL: "Relatório gerencial", FATO_RELEVANTE: "Fato relevante",
    COMUNICADO: "Comunicado", GUIDANCE: "Guidance", RATING: "Rating",
    ASSEMBLEIA: "Assembleia", OFERTA: "Oferta / emissão", OUTRO: "Outro",
}
# Ordem de prioridade quando o teto por ativo corta.
PRIORIDADE_DOC = (FATO_RELEVANTE, BALANCO, RELEASE, GUIDANCE, GERENCIAL,
                  APRESENTACAO, RATING, OFERTA, ASSEMBLEIA, COMUNICADO, OUTRO)


def tipo_documento(categoria: str | None, tipo: str | None,
                   titulo: str | None = None) -> str | None:
    """Tipo do documento (``ROTULO_DOC``) a partir da CVM/IPE ou do FNET.
    ``None`` = não interessa à análise (política de divulgação, laudo...)."""
    c, t, ti = normalizar(categoria), normalizar(tipo), normalizar(titulo)
    if "guidance" in ti or "projec" in ti:
        return GUIDANCE
    if "fato relev" in t or "fato relev" in c:
        return FATO_RELEVANTE
    if "demonstra" in t or t in ("dfin", "df anual fii") or "relatorio anual" in t:
        return BALANCO
    if "press-release" in t or "press release" in t:
        return RELEASE
    if "apresenta" in t:
        return APRESENTACAO
    if "gerencial" in t:
        return GERENCIAL
    if "rating" in t:
        return RATING
    if c == "assembleia" or t.startswith(("ago", "age", "edital")) \
            or "assembleia" in t:
        return ASSEMBLEIA
    if "distribuicao publica" in c or "prosp" in t or "opa" in c \
            or "aumento de capital" in t or "debentures" in c:
        return OFERTA
    if "recuperacao judicial" in c:
        return FATO_RELEVANTE
    if c in ("comunicado ao mercado", "aviso aos acionistas") \
            or t in ("aviso mercado",) or "aquisicao/alienacao" in t:
        return COMUNICADO
    return None


@dataclass(frozen=True)
class Documento:
    tipo: str
    titulo: str
    reference_date: str | None
    source: str | None
    source_url: str | None
    retrieved_at: str | None = None
    dimensoes: tuple[str, ...] = ()     # pelo título, não pelo conteúdo

    @property
    def rotulo(self) -> str:
        return ROTULO_DOC.get(self.tipo, self.tipo)


# As sete perguntas de extração. Só algumas têm indício pelo título; as
# outras exigem ler o documento e ficam para a LLM (trechos do RAG).
PERGUNTAS = (
    ("mudou", "O que mudou"),
    ("melhorou", "O que melhorou"),
    ("piorou", "O que piorou"),
    ("riscos", "Novos riscos"),
    ("oportunidades", "Novas oportunidades"),
    ("estrategia", "Mudanças na estratégia"),
    ("proximos", "Próximos movimentos da administração"),
)


@dataclass(frozen=True)
class Relatorios:
    documentos: tuple[Documento, ...] = ()
    base_ate: str | None = None
    fonte: str | None = None
    retrieved_at: str | None = None
    motivo: str | None = None

    def indicios(self) -> dict[str, tuple[Documento, ...]]:
        """Documentos cujo **título** aponta para cada pergunta. Não é leitura
        do conteúdo: é onde procurar."""
        docs = self.documentos
        return {
            "mudou": tuple(d for d in docs if d.tipo == FATO_RELEVANTE),
            "melhorou": (),
            "piorou": (),
            "riscos": tuple(d for d in docs if {"risco", "divida"}
                            & set(d.dimensoes) or d.tipo == RATING),
            "oportunidades": (),
            "estrategia": tuple(d for d in docs if "estrategia" in d.dimensoes
                                or d.tipo == GUIDANCE),
            "proximos": tuple(d for d in docs if d.tipo in (ASSEMBLEIA, OFERTA)),
        }

    def como_dict(self) -> dict:
        d = asdict(self)
        d["documentos"] = [asdict(x) for x in self.documentos]
        return d

    @classmethod
    def de_dict(cls, d: dict) -> "Relatorios":
        docs = tuple(Documento(**{**x, "dimensoes": tuple(
            x.get("dimensoes") or ())}) for x in d.get("documentos") or ())
        return cls(documentos=docs, **{k: d.get(k) for k in (
            "base_ate", "fonte", "retrieved_at", "motivo")})


# Próximos eventos --------------------------------------------------------
TIPOS_EVENTO: dict[str, tuple[str, str, str]] = {
    # tipo: (rótulo, relevância, possível impacto)
    "earnings": ("Divulgação de resultado", HIGH,
                 "Pode mudar lucro, margens, dívida e múltiplos de uma vez."),
    "dividend": ("Provento", MEDIUM,
                 "Caixa ao investidor; a data-com define quem recebe."),
    "debt_maturity": ("Vencimento", HIGH,
                      "Resgate do principal: decide o reinvestimento."),
    "contract_expiration": ("Fim de contrato", MEDIUM,
                            "Receita contratada pode sair ou ser repactuada."),
    "guidance_update": ("Atualização de guidance", MEDIUM,
                        "Revisa a expectativa de resultado e investimento."),
    "shareholder_meeting": ("Assembleia", MEDIUM,
                            "Deliberações sobre resultado, proventos e "
                            "gestão."),
    "fund_emission": ("Emissão de cotas", MEDIUM,
                      "Pode diluir quem não acompanha e muda o caixa do "
                      "fundo."),
    "acquisition": ("Aquisição", HIGH,
                    "Muda o portfólio, a alavancagem e a estratégia."),
    "asset_sale": ("Venda de ativo", HIGH,
                   "Realiza valor e muda o perfil de receita."),
    "regulatory_event": ("Evento regulatório", MEDIUM,
                         "Regra nova pode mudar receita ou custo."),
}


@dataclass(frozen=True)
class Evento:
    tipo: str
    data: str                       # ISO
    descricao: str
    natureza: str                   # anunciado | prazo regulatório | contratual
    source: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    reference_date: str | None = None

    @property
    def rotulo(self) -> str:
        return TIPOS_EVENTO.get(self.tipo, (self.tipo,))[0]

    @property
    def relevancia(self) -> str:
        return TIPOS_EVENTO.get(self.tipo, ("", MEDIUM))[1]

    @property
    def impacto(self) -> str:
        return TIPOS_EVENTO.get(self.tipo, ("", "", NAO_DISPONIVEL))[2]


@dataclass(frozen=True)
class Eventos:
    itens: tuple[Evento, ...] = ()
    tipos_consultados: tuple[str, ...] = ()   # tipos com fonte para a classe
    motivo: str | None = None

    @property
    def sem_dado(self) -> tuple[str, ...]:
        com = {e.tipo for e in self.itens}
        return tuple(t for t in TIPOS_EVENTO if t not in com)

    def como_dict(self) -> dict:
        return {"itens": [asdict(e) for e in self.itens],
                "tipos_consultados": list(self.tipos_consultados),
                "motivo": self.motivo}

    @classmethod
    def de_dict(cls, d: dict) -> "Eventos":
        return cls(itens=tuple(Evento(**e) for e in d.get("itens") or ()),
                   tipos_consultados=tuple(d.get("tipos_consultados") or ()),
                   motivo=d.get("motivo"))


def ordenar_eventos(itens, hoje: date) -> tuple[Evento, ...]:
    """Só o futuro (inclui hoje), em ordem de data; sem repetição."""
    vistos, saida = set(), []
    for e in sorted(itens, key=lambda e: (e.data, e.tipo)):
        chave = (e.tipo, e.data, e.descricao)
        if e.data >= hoje.isoformat() and chave not in vistos:
            vistos.add(chave)
            saida.append(e)
    return tuple(saida)


def _fim_do_trimestre(d: date) -> date:
    mes = ((d.month - 1) // 3 + 1) * 3
    prox = date(d.year + (mes == 12), mes % 12 + 1, 1)
    return prox - timedelta(days=1)


def prazo_de_resultado(hoje: date) -> tuple[date, str]:
    """Próxima data-limite regulatória de divulgação (CVM 80/2022): ITR até
    45 dias após o 1º, 2º e 3º trimestres; DFP até 3 meses após o fim do
    exercício. É o **prazo**, não a data anunciada pela empresa. Puro."""
    periodo = date(hoje.year, (hoje.month - 1) // 3 * 3 + 1, 1) - timedelta(days=1)
    for _ in range(3):
        if periodo.month == 12:
            limite, nome = date(periodo.year + 1, 3, 31), f"DFP {periodo.year}"
        else:
            limite = periodo + timedelta(days=45)
            nome = f"ITR {periodo.month // 3}T{periodo.year % 100:02d}"
        if limite >= hoje:
            return limite, nome
        periodo = _fim_do_trimestre(periodo + timedelta(days=1))
    raise AssertionError("inalcançável")  # pragma: no cover


# ---------------------------------------------------------------------------
# Resumo (uma linha) e texto para a LLM
# ---------------------------------------------------------------------------
def _data_br(iso: str | None) -> str:
    if not iso:
        return "—"
    a, m, d = iso[:10].split("-")
    return f"{d}/{m}/{a}"


def resumo_noticias(n: Noticias) -> str:
    if not n.itens and n.setor:
        return (f"{n.motivo or NAO_DISPONIVEL} Do segmento"
                + (f" ({n.setor_rotulo})" if n.setor_rotulo else "")
                + f": {len(n.setor)} notícia(s) dos pares.")
    if not n.itens:
        return n.motivo or NAO_DISPONIVEL
    altos = sum(1 for i in n.itens if i.impact_level == HIGH)
    return (f"{len(n.itens)} notícia(s) relevante(s) em {n.janela_dias} dias"
            + (f", {altos} de impacto alto" if altos else "") + ".")


def resumo_relatorios(r: Relatorios) -> str:
    if not r.documentos:
        return r.motivo or NAO_DISPONIVEL
    return (f"{len(r.documentos)} documento(s) recente(s); o mais novo em "
            f"{_data_br(r.documentos[0].reference_date)}.")


def resumo_eventos(e: Eventos) -> str:
    if not e.itens:
        return e.motivo or NAO_DISPONIVEL
    p = e.itens[0]
    return (f"{len(e.itens)} evento(s) à frente; o próximo: {p.rotulo} em "
            f"{_data_br(p.data)}.")


def _fonte(*partes) -> str:
    return " · ".join(str(p) for p in partes if p)


def texto_noticias(n: Noticias, ticker: str) -> str:
    linhas = [f"[Notícias recentes de {ticker} — DADO: manchetes da fonte, "
              "classificadas pelo código]"]
    if not n.itens:
        linhas.append(f"- {n.motivo or NAO_DISPONIVEL}")
    for i in n.itens:
        dims = ", ".join(ROTULO_DIMENSAO.get(d, d) for d in i.affected_dimension)
        linhas.append(
            f"- {_data_br(i.date)} · impacto {i.impact_level} · {dims} · "
            f"\"{i.headline}\" ({_fonte(i.source, i.url)})"
            + (f"\n  Resumo da fonte: {i.summary}" if i.summary else ""))
    if n.descartadas:
        linhas.append("- Descartadas pelo filtro: " + "; ".join(
            f"{k}: {v}" for k, v in n.descartadas.items()))
    linhas.append("INTERPRETAÇÃO (sua): o que cada fato muda na tese. O nível "
                  "de impacto é regra de palavra-chave sobre a manchete, não "
                  "leitura da matéria; não cite notícia que não está acima.")
    return "\n".join(linhas)


def texto_relatorios(r: Relatorios, ticker: str) -> str:
    linhas = [f"[Relatórios e comunicados de {ticker} — DADO: documentos "
              "publicados, só metadados]"]
    if not r.documentos:
        linhas.append(f"- {r.motivo or NAO_DISPONIVEL}")
    for d in r.documentos:
        linhas.append(f"- {_data_br(d.reference_date)} · {d.rotulo} · "
                      f"\"{d.titulo}\" ({_fonte(d.source, d.source_url)})")
    ind = r.indicios()
    linhas.append("Indícios pelo título (onde procurar, não conclusão):")
    for chave, rotulo in PERGUNTAS:
        docs = ind[chave]
        linhas.append(f"- {rotulo}: " + (
            "; ".join(f"{x.titulo} ({_data_br(x.reference_date)})"
                      for x in docs) if docs else NAO_DISPONIVEL))
    linhas.append("INTERPRETAÇÃO (sua): responda às sete perguntas só com o "
                  "que os títulos e os trechos de documento recuperados "
                  "sustentam; o resto fica como \"Dado não disponível.\"")
    return "\n".join(linhas)


def texto_eventos(e: Eventos, ticker: str) -> str:
    linhas = [f"[Próximos eventos de {ticker} — DADO]"]
    if not e.itens:
        linhas.append(f"- {e.motivo or NAO_DISPONIVEL}")
    for x in e.itens:
        linhas.append(f"- {_data_br(x.data)} · {x.rotulo} · relevância "
                      f"{x.relevancia} · {x.descricao} [{x.natureza}] "
                      f"({_fonte(x.source, x.source_url)})")
    if e.sem_dado:
        linhas.append("- Sem fonte de data para: " + ", ".join(
            TIPOS_EVENTO[t][0] for t in e.sem_dado))
    return "\n".join(linhas)
