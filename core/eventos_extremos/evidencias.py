"""As três classes de evidência, cada uma com a sua cobertura.

A especificação separa evidência **informacional** (o que foi dito, por quem, há
quanto tempo), **de mercado** (o que os preços fizeram) e **de carteira** (o
quanto isso alcança quem investiu). A separação não é organizacional: é a única
forma de a regra "divergência entre manchete e mercado reduz a confiança" ser
escrita, porque ela compara duas das três.

Três decisões que este módulo toma, e que mudam o resultado
-----------------------------------------------------------
**Não medido é ``None``, nunca ``0,0``.** Numa média renormalizada, ``None`` é
neutro e ``0,0`` é punitivo -- este projeto já publicou um índice em que quem
conciliou tirava nota menor que quem nunca conciliou. Aqui, componente sem
medição sai da média e entra na cobertura.

**Cobertura viaja junto, sempre.** É o contrapeso do parágrafo acima. Renormalizar
sobre o que foi medido, sozinho, produz o defeito oposto: uma classe com um único
componente medido em 1,0 devolve severidade 1,0 -- e quem pergunta menos tira nota
maior. Por isso :class:`Evidencia` nunca expõe ``severidade`` sem ``cobertura``, e
:mod:`core.eventos_extremos.transicao` exige cobertura mínima para escalar.

**Os limiares são tabela, não adjetivo.** "Queda expressiva" e "volatilidade
elevada" não são critérios; ``-8%`` e ``2,5x a volatilidade de referência`` são.
Todo corte deste módulo é constante nomeada, no topo, para ser lido sem ler
lógica -- e para que mudá-lo obrigue a subir ``EVENTOS_EXTREMOS_VERSAO``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from core.eventos_extremos import niveis

# ── Classes de evidência ──────────────────────────────────────────────────────
CLASSE_INFORMACIONAL = "informacional"
CLASSE_MERCADO = "mercado"
CLASSE_CARTEIRA = "carteira"

CLASSES = (CLASSE_INFORMACIONAL, CLASSE_MERCADO, CLASSE_CARTEIRA)


# ── Peça elementar ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Componente:
    """Um pedaço de evidência, com o número cru que o justificou.

    ``valor`` é severidade normalizada em 0..1, ou ``None`` quando o componente
    não pôde ser medido. ``evidencia`` carrega a medição original em unidade
    legível -- sem ela, "0,72" não é auditável, é opinião com casas decimais.

    ``bruto`` guarda essa mesma medição como número, na unidade natural do
    indicador (fração, ponto percentual, bps, razão). Ele existe porque
    comparar limiar contra o *normalizado* é armadilha: tudo abaixo do corte
    brando normaliza para exatamente 0,0, e uma regra que pergunte "isto é
    desprezível?" ao valor normalizado responde "sim" para 4,9% de exposição
    direta a um banco que quebrou. Quem pergunta pela grandeza pergunta a
    ``bruto``; quem pergunta pela gravidade pergunta a ``valor``.
    """

    chave: str
    rotulo: str
    valor: float | None
    peso: float
    evidencia: str
    fonte: str | None = None
    bruto: float | None = None

    @property
    def medido(self) -> bool:
        return self.valor is not None

    def __post_init__(self) -> None:
        if self.peso <= 0:
            raise ValueError(f"peso deve ser positivo: {self.chave}={self.peso}")
        if self.valor is not None and not (0.0 <= float(self.valor) <= 1.0):
            raise ValueError(
                f"severidade fora de 0..1: {self.chave}={self.valor}")


@dataclass(frozen=True)
class Evidencia:
    """Uma classe de evidência inteira: componentes, severidade e cobertura.

    ``severidade`` é ``None`` quando nada foi medido. Devolver 0,0 nesse caso
    diria "está calmo" onde o correto é "não sei" -- e é justamente a leitura
    que faz um motor de crise dormir durante a crise.
    """

    classe: str
    componentes: tuple[Componente, ...] = ()
    limitacoes: tuple[str, ...] = ()

    @property
    def severidade(self) -> float | None:
        peso = sum(c.peso for c in self.componentes if c.medido)
        if peso <= 0:
            return None
        return sum(c.valor * c.peso for c in self.componentes if c.medido) / peso

    @property
    def cobertura(self) -> float:
        total = sum(c.peso for c in self.componentes)
        if total <= 0:
            return 0.0
        return sum(c.peso for c in self.componentes if c.medido) / total

    @property
    def nao_medidos(self) -> tuple[str, ...]:
        return tuple(c.chave for c in self.componentes if not c.medido)

    def componente(self, chave: str) -> Componente | None:
        for c in self.componentes:
            if c.chave == chave:
                return c
        return None

    def valor_de(self, chave: str) -> float | None:
        c = self.componente(chave)
        return None if c is None else c.valor

    def bruto_de(self, chave: str) -> float | None:
        """A medição na unidade natural do indicador, não a severidade.

        Use este quando a pergunta for de grandeza ("2% ou 40% do patrimônio?")
        e não de gravidade. Ver a nota em :class:`Componente`.
        """
        c = self.componente(chave)
        return None if c is None else c.bruto

    def descrever(self) -> str:
        sev = "não medida" if self.severidade is None else f"{self.severidade:.2f}"
        return (f"{self.classe}: severidade {sev} | "
                f"cobertura {self.cobertura:.0%} | "
                f"{len(self.componentes) - len(self.nao_medidos)}/"
                f"{len(self.componentes)} componentes")


def _num(valor) -> float | None:
    """Float finito, ou ``None``. Bool não é número aqui, é engano de chamada."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if isfinite(f) else None


def _faixa(valor, brando: float, grave: float) -> float | None:
    """Mapeia uma medição para 0..1 entre dois cortes declarados.

    Abaixo de ``brando`` é 0 (medido e calmo -- diferente de não medido).
    Em ``grave`` ou acima é 1. Entre os dois, interpola linearmente. Usa o
    módulo do valor, porque para quase todo indicador de estresse o que importa
    é o tamanho do movimento, não o sinal; quem precisa do sinal (queda de
    índice, por exemplo) passa o valor já orientado.
    """
    v = _num(valor)
    if v is None:
        return None
    v = abs(v)
    if v <= brando:
        return 0.0
    if v >= grave:
        return 1.0
    return (v - brando) / (grave - brando)


# ══════════════════════════════════════════════════════════════════════════════
# Evidência informacional
# ══════════════════════════════════════════════════════════════════════════════

#: Fontes independentes -> severidade. Uma fonte só nunca chega perto do topo:
#: a regra é textual ("duas ou mais fontes independentes confiáveis poderão
#: elevar a severidade"), e este degrau é onde ela vira número. Contar fonte
#: independente é trabalho de ``core.noticias.eventos``, que agrupa por cluster
#: de quase-duplicata: cinco portais republicando uma agência valem 1.
SEVERIDADE_POR_N_FONTES: dict[int, float] = {0: 0.0, 1: 0.25, 2: 0.60, 3: 0.85}
SEVERIDADE_N_FONTES_MAXIMA = 1.0

#: Recência: até este ponto o fato é "agora"; a partir do outro, é histórico.
RECENCIA_FRESCA_HORAS = 6.0
RECENCIA_VELHA_HORAS = 120.0

#: Abrangência -> severidade. Mesma escala de ``niveis.ABRANGENCIAS``.
SEVERIDADE_POR_ABRANGENCIA: dict[str, float] = {
    niveis.ABRANGENCIA_ATIVO: 0.25,
    niveis.ABRANGENCIA_SETOR: 0.50,
    niveis.ABRANGENCIA_PAIS: 0.70,
    niveis.ABRANGENCIA_REGIONAL: 0.90,
    niveis.ABRANGENCIA_GLOBAL: 1.00,
}

PESOS_INFORMACIONAIS: dict[str, float] = {
    "fonte_oficial": 1.4,
    "fontes_independentes": 1.3,
    "confiabilidade": 1.2,
    "concordancia": 1.0,
    "recencia": 0.8,
    "materialidade": 1.3,
    "abrangencia": 1.0,
}


def informacional(
    *,
    fonte_oficial: bool | None = None,
    n_fontes_independentes: int | None = None,
    confiabilidade_maxima: float | None = None,
    concordancia: float | None = None,
    horas_desde_publicacao: float | None = None,
    materialidade: float | None = None,
    abrangencia: str | None = None,
    limitacoes: tuple[str, ...] = (),
) -> Evidencia:
    """Monta a evidência informacional a partir do que ``core.noticias`` apurou.

    Todos os argumentos aceitam ``None`` -- e ``None`` significa "não apurei",
    não "não tem". ``fonte_oficial=False`` (apurei, e não é oficial) e
    ``fonte_oficial=None`` (não sei de onde veio) produzem coisas diferentes: o
    primeiro entra na média puxando para baixo, o segundo sai da média e entra
    na cobertura.
    """
    comps: list[Componente] = []

    if fonte_oficial is None:
        comps.append(Componente("fonte_oficial", "Fonte oficial ou primária",
                                None, PESOS_INFORMACIONAIS["fonte_oficial"],
                                "classe da fonte não apurada"))
    else:
        comps.append(Componente(
            "fonte_oficial", "Fonte oficial ou primária",
            1.0 if fonte_oficial else 0.35,
            PESOS_INFORMACIONAIS["fonte_oficial"],
            "confirmado por regulador ou pela própria companhia" if fonte_oficial
            else "apenas imprensa ou fonte secundária"))

    n = None if n_fontes_independentes is None else int(n_fontes_independentes)
    comps.append(Componente(
        "fontes_independentes", "Fontes independentes",
        None if n is None else SEVERIDADE_POR_N_FONTES.get(
            n, SEVERIDADE_N_FONTES_MAXIMA),
        PESOS_INFORMACIONAIS["fontes_independentes"],
        "não contadas" if n is None else f"{n} cluster(es) de domínio distinto"))

    conf = _num(confiabilidade_maxima)
    comps.append(Componente(
        "confiabilidade", "Confiabilidade do veículo",
        None if conf is None else max(0.0, min(1.0, conf)),
        PESOS_INFORMACIONAIS["confiabilidade"],
        "não classificada" if conf is None
        else f"confiabilidade {conf:.2f} (core.noticias.fontes)"))

    conc = _num(concordancia)
    comps.append(Componente(
        "concordancia", "Concordância entre fontes",
        None if conc is None else max(0.0, min(1.0, conc)),
        PESOS_INFORMACIONAIS["concordancia"],
        "fonte única ou direção não comparada" if conc is None
        else f"{conc:.0%} das fontes na mesma direção"))

    horas = _num(horas_desde_publicacao)
    if horas is None:
        rec = None
        texto = "sem data de publicação"
    elif horas <= RECENCIA_FRESCA_HORAS:
        rec, texto = 1.0, f"publicado há {horas:.1f}h"
    elif horas >= RECENCIA_VELHA_HORAS:
        rec, texto = 0.0, f"publicado há {horas / 24:.1f} dias"
    else:
        rec = 1.0 - (horas - RECENCIA_FRESCA_HORAS) / (
            RECENCIA_VELHA_HORAS - RECENCIA_FRESCA_HORAS)
        texto = f"publicado há {horas:.1f}h"
    comps.append(Componente("recencia", "Recência", rec,
                            PESOS_INFORMACIONAIS["recencia"], texto))

    mat = _num(materialidade)
    comps.append(Componente(
        "materialidade", "Materialidade do tipo de evento",
        None if mat is None else max(0.0, min(1.0, mat)),
        PESOS_INFORMACIONAIS["materialidade"],
        "tipo de evento não classificado" if mat is None
        else f"materialidade {mat:.2f} (core.noticias.taxonomia)"))

    chave_abr = str(abrangencia or "").strip().lower()
    comps.append(Componente(
        "abrangencia", "Abrangência geográfica ou setorial",
        SEVERIDADE_POR_ABRANGENCIA.get(chave_abr),
        PESOS_INFORMACIONAIS["abrangencia"],
        "abrangência não declarada" if chave_abr not in SEVERIDADE_POR_ABRANGENCIA
        else f"abrangência {chave_abr}"))

    return Evidencia(CLASSE_INFORMACIONAL, tuple(comps), tuple(limitacoes))


# ══════════════════════════════════════════════════════════════════════════════
# Evidência de mercado
# ══════════════════════════════════════════════════════════════════════════════

#: (corte brando, corte grave) por indicador, na unidade natural de cada um.
#: Esta tabela é a configuração que a especificação pede -- mudar um número aqui
#: muda o comportamento do motor, e por isso obriga a subir a versão.
CORTES_DE_MERCADO: dict[str, tuple[float, float]] = {
    # razão entre a volatilidade realizada curta e a de referência
    "volatilidade": (1.5, 3.0),
    # queda acumulada do índice de referência na janela (fração, já orientada)
    "indices": (0.04, 0.15),
    # variação do câmbio na janela (fração)
    "cambio": (0.03, 0.12),
    # variação da taxa de juros de referência (pontos percentuais)
    "juros": (0.50, 2.00),
    # variação do ouro (fração) -- alta forte é fuga para segurança
    "ouro": (0.03, 0.10),
    # variação do petróleo (fração)
    "petroleo": (0.06, 0.25),
    # variação de uma cesta de commodities (fração)
    "commodities": (0.05, 0.20),
    # abertura do spread de crédito (pontos-base)
    "spread_credito": (50.0, 250.0),
    # queda do volume negociado contra a mediana (fração; 1,0 = secou)
    "liquidez": (0.30, 0.70),
    # aumento da correlação média entre ativos (pontos de correlação)
    "correlacao": (0.10, 0.35),
    # dispersão do movimento entre ativos relacionados (fração)
    "relacionados": (0.05, 0.20),
}

ROTULOS_DE_MERCADO: dict[str, str] = {
    "volatilidade": "Volatilidade contra a de referência",
    "indices": "Queda do índice de referência",
    "cambio": "Câmbio",
    "juros": "Juros",
    "ouro": "Ouro",
    "petroleo": "Petróleo",
    "commodities": "Commodities",
    "spread_credito": "Spread de crédito",
    "liquidez": "Liquidez e volume",
    "correlacao": "Correlação entre ativos",
    "relacionados": "Ativos relacionados",
}

UNIDADES_DE_MERCADO: dict[str, str] = {
    "volatilidade": "x", "indices": "%", "cambio": "%", "juros": "p.p.",
    "ouro": "%", "petroleo": "%", "commodities": "%", "spread_credito": "bps",
    "liquidez": "%", "correlacao": "pts", "relacionados": "%",
}

PESOS_DE_MERCADO: dict[str, float] = {
    "volatilidade": 1.4, "indices": 1.4, "cambio": 1.0, "juros": 1.0,
    "ouro": 0.6, "petroleo": 0.7, "commodities": 0.7, "spread_credito": 1.2,
    "liquidez": 1.1, "correlacao": 1.2, "relacionados": 0.8,
}

#: Indicadores que este projeto **hoje** consegue medir a partir das séries que
#: possui (preço diário da B3 e dos EUA, com volume). Os demais entram como
#: ``None`` até existir fonte -- e a cobertura mostra o buraco em vez de escondê-lo.
MEDIVEIS_HOJE = frozenset({"volatilidade", "indices", "liquidez", "correlacao",
                           "relacionados"})


def mercado(medicoes: dict[str, float | None] | None = None, *,
            fontes: dict[str, str] | None = None,
            limitacoes: tuple[str, ...] = ()) -> Evidencia:
    """Monta a evidência de mercado a partir de medições já calculadas.

    Recebe medições, não séries: quem calcula volatilidade realizada, correlação
    média ou queda de índice é :mod:`core.eventos_extremos.mercado`, e manter
    esta fronteira é o que deixa a tabela de cortes testável sem banco.

    Indicador ausente do dicionário e indicador com valor ``None`` são a mesma
    coisa -- "não medido" --, e nenhum dos dois vira zero.
    """
    med = dict(medicoes or {})
    fon = dict(fontes or {})
    desconhecidos = sorted(set(med) - set(CORTES_DE_MERCADO))
    if desconhecidos:
        raise KeyError(f"indicador de mercado desconhecido: {desconhecidos}")

    comps: list[Componente] = []
    for chave, (brando, grave) in CORTES_DE_MERCADO.items():
        bruto = med.get(chave)
        valor = _faixa(bruto, brando, grave)
        unidade = UNIDADES_DE_MERCADO[chave]
        if valor is None:
            texto = "não medido"
        elif unidade == "%":
            texto = f"{abs(_num(bruto)) * 100:.1f}% (brando {brando * 100:.0f}%, grave {grave * 100:.0f}%)"
        else:
            texto = f"{abs(_num(bruto)):.2f} {unidade} (brando {brando:g}, grave {grave:g})"
        comps.append(Componente(chave, ROTULOS_DE_MERCADO[chave], valor,
                                PESOS_DE_MERCADO[chave], texto, fon.get(chave),
                                bruto=_num(bruto)))

    return Evidencia(CLASSE_MERCADO, tuple(comps), tuple(limitacoes))


# ══════════════════════════════════════════════════════════════════════════════
# Evidência de carteira
# ══════════════════════════════════════════════════════════════════════════════

#: Cortes da exposição, em fração do patrimônio. O corte grave da exposição
#: direta é 25% porque abaixo disso o evento não decide o resultado da carteira,
#: e acima disso ele decide sozinho.
CORTES_DE_CARTEIRA: dict[str, tuple[float, float]] = {
    "exposicao_direta": (0.05, 0.25),
    "exposicao_indireta": (0.10, 0.40),
    "concentracao": (0.15, 0.35),          # HHI
    "risco_credito": (0.10, 0.35),
    "risco_cambial": (0.15, 0.50),
    "dependencia_geografica": (0.60, 0.95),
    "perda_simulada": (0.10, 0.35),        # perda no pior cenário aplicável
}

ROTULOS_DE_CARTEIRA: dict[str, str] = {
    "exposicao_direta": "Exposição direta ao evento",
    "exposicao_indireta": "Exposição indireta (setor, cadeia, país)",
    "concentracao": "Concentração (HHI)",
    "liquidez_disponivel": "Liquidez disponível",
    "risco_credito": "Risco de crédito",
    "risco_cambial": "Risco cambial",
    "dependencia_geografica": "Dependência geográfica",
    "perda_simulada": "Perda no cenário simulado",
}

PESOS_DE_CARTEIRA: dict[str, float] = {
    "exposicao_direta": 1.6, "exposicao_indireta": 1.2, "concentracao": 1.0,
    "liquidez_disponivel": 1.1, "risco_credito": 0.9, "risco_cambial": 0.9,
    "dependencia_geografica": 0.8, "perda_simulada": 1.3,
}


def carteira(
    *,
    exposicao_direta: float | None = None,
    exposicao_indireta: float | None = None,
    concentracao_hhi: float | None = None,
    liquidez_disponivel: float | None = None,
    risco_credito: float | None = None,
    risco_cambial: float | None = None,
    dependencia_geografica: float | None = None,
    perda_simulada: float | None = None,
    limitacoes: tuple[str, ...] = (),
) -> Evidencia:
    """Monta a evidência de carteira.

    ``liquidez_disponivel`` entra **invertida**: ela é a única medição desta
    classe em que mais é melhor. Uma carteira com 30% em caixa aguenta o choque;
    a severidade dela é 0,70, não 0,30. Trocar o sentido aqui produziria o pior
    tipo de defeito -- o que não quebra nada e inverte a conclusão.
    """
    comps: list[Componente] = []

    def _add(chave: str, bruto, *, texto_unidade: str = "%") -> None:
        brando, grave = CORTES_DE_CARTEIRA[chave]
        valor = _faixa(bruto, brando, grave)
        if valor is None:
            texto = "não medido"
        elif texto_unidade == "%":
            texto = f"{abs(_num(bruto)) * 100:.1f}% do patrimônio"
        else:
            texto = f"{abs(_num(bruto)):.3f}"
        comps.append(Componente(chave, ROTULOS_DE_CARTEIRA[chave], valor,
                                PESOS_DE_CARTEIRA[chave], texto,
                                bruto=_num(bruto)))

    _add("exposicao_direta", exposicao_direta)
    _add("exposicao_indireta", exposicao_indireta)
    _add("concentracao", concentracao_hhi, texto_unidade="hhi")

    liq = _num(liquidez_disponivel)
    comps.append(Componente(
        "liquidez_disponivel", ROTULOS_DE_CARTEIRA["liquidez_disponivel"],
        None if liq is None else max(0.0, min(1.0, 1.0 - liq)),
        PESOS_DE_CARTEIRA["liquidez_disponivel"],
        "não medida" if liq is None
        else f"{liq * 100:.1f}% do patrimônio em ativos líquidos (invertida)",
        bruto=liq))

    _add("risco_credito", risco_credito)
    _add("risco_cambial", risco_cambial)
    _add("dependencia_geografica", dependencia_geografica)
    _add("perda_simulada", perda_simulada)

    return Evidencia(CLASSE_CARTEIRA, tuple(comps), tuple(limitacoes))


# ══════════════════════════════════════════════════════════════════════════════
# Conjunto das três
# ══════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Conjunto:
    """As três classes juntas, do jeito que a transição as consome."""

    informacional: Evidencia = field(
        default_factory=lambda: Evidencia(CLASSE_INFORMACIONAL))
    mercado: Evidencia = field(
        default_factory=lambda: Evidencia(CLASSE_MERCADO))
    carteira: Evidencia = field(
        default_factory=lambda: Evidencia(CLASSE_CARTEIRA))

    def por_classe(self) -> dict[str, Evidencia]:
        return {CLASSE_INFORMACIONAL: self.informacional,
                CLASSE_MERCADO: self.mercado,
                CLASSE_CARTEIRA: self.carteira}

    def descrever(self) -> tuple[str, ...]:
        return tuple(e.descrever() for e in self.por_classe().values())
