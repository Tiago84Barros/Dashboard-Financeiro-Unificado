"""
core/inteligencia_ativos/calculos.py
Camada determinística da análise de carteira.

Tudo o que é número sai daqui: pesos, alocação atual contra alocação-alvo,
faixas, desvios, concentração (com HHI) e alertas objetivos. A LLM recebe
esses números prontos e só os interpreta; ela não soma, não divide e não
decide se algo passou do limite.

Convenções (todas em % da base, 0 a 100, exceto o HHI, que é 0 a 1):

- **peso do ativo** = valor de mercado do ativo ÷ valor de mercado total. Se
  a carteira não tem valor de mercado nenhum, usa o valor investido, e
  ``Calculos.base_valor`` diz qual foi. O ``pct_carteira`` que vem da carteira
  não é reaproveitado: o peso é recalculado aqui a partir dos valores.
- **faixa** = mínimo / alvo / máximo, cada limite com a sua origem
  (``estrategia``, ``usuario`` ou ``sistema``). Faixa só com alvo é um alvo
  pontual: mínimo e máximo passam a ser o próprio alvo.
- **diferenca_para_alvo** = atual − alvo (pp). Positivo = acima do alvo.
- **overweight** = quanto o atual passa do máximo da faixa (pp, ≥ 0).
- **underweight** = quanto o atual fica abaixo do mínimo da faixa (pp, ≥ 0).
- **renda variável** = ações BR + FIIs, a mesma definição da política
  (``politica.valores``: ``variable_income_target``).

Faixas por classe: a estratégia dá o alvo; a faixa em volta é o alvo ±
``TOLERANCIA_CLASSE_PP`` (convenção do sistema, e o alerta diz isso), cortada
pelo limite máximo da classe quando a estratégia o define. Alvo 0% não tem
tolerância: a estratégia exclui a classe.

Faixas por ativo, setor e subclasse: a estratégia só tem limite por ativo e
por setor (máximos). Mínimo e alvo individuais só existem se o usuário os
informar em ``faixas``; nenhum é presumido. Quando usuário e estratégia dão
um máximo, vale o mais restritivo.

Puro: sem banco, Streamlit ou LLM. Coberto por
tests/test_inteligencia_ativos_calculos.py.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import pandas as pd

from core.global_portfolio.concentration import hhi as _hhi

# -- convenções do sistema -----------------------------------------------------

# Desvio da classe em relação ao alvo que ainda conta como "na faixa".
TOLERANCIA_CLASSE_PP = 5.0
# Peso de um setor dentro da renda variável a partir do qual há alerta de
# concentração. Convenção do sistema: a estratégia só limita setor em % da
# carteira inteira, e o alerta diz de onde veio o número.
LIMIAR_SETOR_NA_RV_PCT = 40.0

RENDA_VARIAVEL = ("acoes_br", "fiis")
NAO_IDENTIFICADO = "não identificado"
TESOURO_NACIONAL = "Tesouro Nacional"

# origens de um limite de faixa
ESTRATEGIA = "estrategia"
USUARIO = "usuario"
SISTEMA = "sistema"

# status de uma linha de alocação
DENTRO = "DENTRO"
ACIMA = "ACIMA"
ABAIXO = "ABAIXO"
SEM_REFERENCIA = "SEM_REFERENCIA"

# severidade dos alertas
ALTA = "alta"         # passou de um limite máximo da estratégia
MEDIA = "media"       # fora de uma faixa
INFO = "info"         # fato objetivo que merece atenção

_ORDEM_SEVERIDADE = {ALTA: 0, MEDIA: 1, INFO: 2}
_EPS = 1e-9

DIM_ATIVO = "ativo"
DIM_SETOR = "setor"
DIM_SUBCLASSE = "subclasse"
DIM_CLASSE = "classe"
DIM_GEOGRAFIA = "geografia"
DIM_EMISSOR = "emissor"
DIM_INDEXADOR = "indexador"

DIMENSOES_ALOCACAO = (DIM_CLASSE, DIM_SUBCLASSE, DIM_SETOR, DIM_ATIVO)
DIMENSOES_CONCENTRACAO = (DIM_ATIVO, DIM_SETOR, DIM_CLASSE, DIM_GEOGRAFIA,
                          DIM_EMISSOR, DIM_INDEXADOR)

ROTULO_DIMENSAO = {
    DIM_ATIVO: "Ativo", DIM_SETOR: "Setor", DIM_SUBCLASSE: "Subclasse",
    DIM_CLASSE: "Classe", DIM_GEOGRAFIA: "Geografia", DIM_EMISSOR: "Emissor",
    DIM_INDEXADOR: "Indexador",
}

# Rótulos das classes da política, repetidos aqui para o módulo não depender
# da política inteira (core.estrategia.politica.CLASSES).
ROTULO_CLASSE = {"renda_fixa": "Renda fixa", "acoes_br": "Ações Brasil",
                 "fiis": "Fundos imobiliários", "exterior": "Exterior"}

# Rótulo de classe da carteira (core/investimentos._CLASS_LABEL) → classe da
# política. O que não está aqui (Cripto, Outros) fica fora das classes que a
# política cobre.
CLASSE_POLITICA: dict[str, str] = {
    "Renda Fixa": "renda_fixa",
    "Tesouro Direto": "renda_fixa",
    "Fundo RF": "renda_fixa",
    "FII": "fiis",
    "Ações BR": "acoes_br",
    "ETF Brasil": "acoes_br",
    "ETF Internacional": "exterior",
    "Ações EUA": "exterior",
    "BDR": "exterior",
}

_PREFIXOS_RF = ("CDB", "LCI", "LCA", "CRI", "CRA", "DEBENTURE")
# Tickers que o importador da XP dá ao Tesouro (xp_consolidado._tesouro_ticker)
_TESOURO_TICKER = re.compile(r"^T(SELIC|IPCA|PRE|EDUCA|RENDA|ESOUR)\d*X?$")
# Raiz de ticker da B3: 4 letras + 1 ou 2 dígitos (PETR4, TAEE11, AAPL34).
_TICKER_B3 = re.compile(r"^([A-Z]{4})\d{1,2}F?$")


# -- modelos -------------------------------------------------------------------

@dataclass(frozen=True)
class Faixa:
    """Mínimo, alvo e máximo aceitáveis, em % da base, com a origem de cada."""
    minimo: float | None = None
    alvo: float | None = None
    maximo: float | None = None
    origem_minimo: str | None = None
    origem_alvo: str | None = None
    origem_maximo: str | None = None

    @property
    def vazia(self) -> bool:
        return self.minimo is None and self.alvo is None and self.maximo is None

    @property
    def piso(self) -> float | None:
        if self.minimo is not None:
            return self.minimo
        return self.alvo if self.maximo is None else None

    @property
    def teto(self) -> float | None:
        if self.maximo is not None:
            return self.maximo
        return self.alvo if self.minimo is None else None


@dataclass(frozen=True)
class PesoAtivo:
    ticker: str
    nome: str
    classe: str                    # rótulo da carteira
    classe_politica: str | None
    subclasse: str | None
    setor: str | None
    emissor: str
    indexador: str | None          # None = não se aplica (fora da renda fixa)
    geografia: str
    valor: float                   # valor usado no peso (base_valor)
    peso: float                    # % da carteira


@dataclass(frozen=True)
class Alocacao:
    """Uma linha de "atual vs alvo" para classe, subclasse, setor ou ativo."""
    dimensao: str
    chave: str
    rotulo: str
    atual: float                   # % da carteira
    faixa: Faixa
    diferenca_para_alvo: float | None
    overweight: float
    underweight: float
    status: str


@dataclass(frozen=True)
class Grupo:
    chave: str
    peso: float                    # % da base da dimensão
    n_ativos: int
    tickers: tuple[str, ...]


@dataclass(frozen=True)
class Concentracao:
    """Distribuição de uma dimensão e o HHI dela.

    O HHI é calculado sobre os grupos identificados, renormalizados para
    somar 1; ``cobertura`` diz quanto da base foi identificado. Um
    "não identificado" entraria no índice como se fosse um grupo só e
    inventaria concentração.
    """
    dimensao: str
    base: str                      # "carteira" | "renda variável" | "renda fixa"
    peso_da_base: float            # % da carteira que a base representa
    grupos: tuple[Grupo, ...]      # do maior para o menor, identificados
    nao_identificado: float        # % da base sem identificação
    cobertura: float               # % da base identificado
    hhi: float | None              # 0 a 1; None se nada identificado
    numero_efetivo: float | None   # 1 / HHI
    top3: float                    # % da base nos 3 maiores grupos

    @property
    def maior(self) -> Grupo | None:
        return self.grupos[0] if self.grupos else None


@dataclass(frozen=True)
class Alerta:
    codigo: str
    severidade: str
    dimensao: str
    chave: str
    mensagem: str
    valor: float
    referencia: float | None


@dataclass(frozen=True)
class Calculos:
    total: float
    base_valor: str                            # "valor_mercado" | "total_investido"
    pesos: tuple[PesoAtivo, ...]               # do maior para o menor
    alocacao: dict[str, tuple[Alocacao, ...]]  # dimensão → linhas
    concentracao: dict[str, Concentracao]
    peso_renda_variavel: float
    alertas: tuple[Alerta, ...]

    def peso(self, ticker: str) -> PesoAtivo | None:
        alvo = str(ticker or "").strip().upper()
        return next((p for p in self.pesos if p.ticker == alvo), None)

    def linha(self, dimensao: str, chave: str | None) -> Alocacao | None:
        return next((a for a in self.alocacao.get(dimensao, ())
                     if a.chave == chave), None)

    def peso_por(self, dimensao: str) -> dict[str, float]:
        return {a.chave: a.atual for a in self.alocacao.get(dimensao, ())}

    def como_dict(self) -> dict:
        return asdict(self)


# -- identificação da posição ---------------------------------------------------

def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0     # NaN → 0


def _texto(posicao: dict) -> str:
    return f"{posicao.get('ticker') or ''} {posicao.get('nome') or ''}".upper()


def classe_politica(posicao: dict) -> str | None:
    """Classe da política, ou None. A moeda vence o rótulo: ação cotada fora
    do real é exposição internacional, seja qual for o rótulo."""
    moeda = str(posicao.get("moeda") or "BRL").upper()
    if moeda not in ("", "BRL"):
        return "exterior"
    return CLASSE_POLITICA.get(str(posicao.get("classe") or ""))


def _eh_tesouro(posicao: dict) -> bool:
    ticker = str(posicao.get("ticker") or "").strip().upper()
    nome = str(posicao.get("nome") or "").strip().upper()
    return (posicao.get("classe") == "Tesouro Direto"
            or bool(_TESOURO_TICKER.match(ticker))
            or nome.startswith("TESOURO") or ticker.startswith("TESOURO"))


def subclasse(posicao: dict) -> str | None:
    classe = posicao.get("classe")
    ticker = str(posicao.get("ticker") or "").upper()
    if _eh_tesouro(posicao):
        return {"IPCA": "Tesouro IPCA+", "Selic": "Tesouro Selic",
                "Prefixado": "Tesouro Prefixado"}.get(indexador(posicao) or "")
    if classe == "Renda Fixa":
        return next((p for p in _PREFIXOS_RF if ticker.startswith(p)), None)
    if classe in ("BDR", "ETF Internacional", "ETF Brasil", "Fundo RF"):
        return classe
    return None


def emissor(posicao: dict) -> str:
    """Quem responde pelo ativo. PETR3 e PETR4 são o mesmo emissor.

    Só identifica o que o ticker ou o nome dizem sem ambiguidade: Tesouro,
    raiz de ticker da B3 e ticker estrangeiro. O banco de um CDB não está na
    carteira e fica ``NAO_IDENTIFICADO``.
    """
    if _eh_tesouro(posicao):
        return TESOURO_NACIONAL
    ticker = str(posicao.get("ticker") or "").strip().upper()
    if classe_politica(posicao) == "renda_fixa" or not ticker:
        return NAO_IDENTIFICADO
    raiz = _TICKER_B3.match(ticker)
    if raiz:
        return raiz.group(1)
    moeda = str(posicao.get("moeda") or "BRL").upper()
    if moeda not in ("", "BRL") and re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", ticker):
        return ticker
    return NAO_IDENTIFICADO


_INDEXADORES = (
    ("IPCA", re.compile(r"IPCA|\bTIPCA|\bTRENDA|\bTEDUCA|RENDA\+|EDUCA\+")),
    ("IGP-M", re.compile(r"IGP-?M")),
    ("Selic", re.compile(r"SELIC")),
    ("CDI", re.compile(r"\bCDI\b|\bDI\b")),
    ("Prefixado", re.compile(r"PREFIXAD|\bPR[EÉ]\b|\bTPRE")),
)


def indexador(posicao: dict) -> str | None:
    """Indexador de um título de renda fixa; None fora da renda fixa.

    Lê ticker e nome. Se nada casa, ou se casam dois indexadores diferentes,
    fica ``NAO_IDENTIFICADO``: escolher um seria adivinhar.
    """
    if classe_politica(posicao) != "renda_fixa" and not _eh_tesouro(posicao):
        return None
    texto = _texto(posicao)
    achados = {nome for nome, padrao in _INDEXADORES if padrao.search(texto)}
    return achados.pop() if len(achados) == 1 else NAO_IDENTIFICADO


def geografia(posicao: dict) -> str:
    classe = classe_politica(posicao)
    if classe is None:
        return NAO_IDENTIFICADO
    if classe != "exterior":
        return "Brasil"
    pais = str(posicao.get("pais") or "").strip()
    if pais:
        return pais
    moeda = str(posicao.get("moeda") or "").upper()
    return f"Exterior ({moeda})" if moeda not in ("", "BRL") else \
        f"Exterior ({posicao.get('classe') or 'BRL'})"


# -- pesos ---------------------------------------------------------------------

def pesos(posicoes) -> tuple[tuple[PesoAtivo, ...], float, str]:
    """(pesos do maior para o menor, total, base_valor).

    Valor negativo não é posição e não entra no total: vira peso 0, e o
    alerta ``valor_invalido`` diz qual ativo foi.
    """
    posicoes = list(posicoes or [])
    base = "valor_mercado"
    valores = [max(_num(p.get("valor_mercado")), 0.0) for p in posicoes]
    if sum(valores) <= 0:
        base = "total_investido"
        valores = [max(_num(p.get("total_investido")), 0.0) for p in posicoes]
    total = sum(valores)
    saida = []
    for p, v in zip(posicoes, valores):
        ticker = str(p.get("ticker") or "").strip().upper()
        saida.append(PesoAtivo(
            ticker=ticker,
            nome=str(p.get("nome") or ticker),
            classe=str(p.get("classe") or "Outros"),
            classe_politica=classe_politica(p),
            subclasse=subclasse(p),
            setor=p.get("setor") or None,
            emissor=emissor(p),
            indexador=indexador(p),
            geografia=geografia(p),
            valor=v,
            peso=(v / total * 100.0) if total > 0 else 0.0,
        ))
    saida.sort(key=lambda x: (-x.peso, x.ticker))
    return tuple(saida), total, base


def _somar(pesos_: tuple[PesoAtivo, ...], chave) -> dict[str, list[PesoAtivo]]:
    grupos: dict[str, list[PesoAtivo]] = {}
    for p in pesos_:
        k = chave(p)
        if k is not None:
            grupos.setdefault(k, []).append(p)
    return grupos


# -- faixas --------------------------------------------------------------------

def faixa_do_usuario(bruta) -> Faixa:
    """Normaliza ``{"min": 2, "alvo": 3.5, "max": 5}`` (ou Faixa) e valida.

    Levanta ``ValueError`` para limites fora de 0–100 ou fora de ordem: uma
    faixa invertida não tem leitura possível.
    """
    if isinstance(bruta, Faixa):
        minimo, alvo, maximo = bruta.minimo, bruta.alvo, bruta.maximo
    else:
        d = dict(bruta or {})
        minimo = d.get("min", d.get("minimo"))
        alvo = d.get("alvo")
        maximo = d.get("max", d.get("maximo"))
    vals = [None if v is None else float(v) for v in (minimo, alvo, maximo)]
    for v in vals:
        if v is not None and not 0.0 <= v <= 100.0:
            raise ValueError(f"Limite de faixa fora de 0–100%: {v}")
    definidos = [v for v in vals if v is not None]
    if definidos != sorted(definidos):
        raise ValueError("Faixa fora de ordem: esperado mínimo ≤ alvo ≤ máximo.")
    minimo, alvo, maximo = vals
    return Faixa(minimo, alvo, maximo,
                 USUARIO if minimo is not None else None,
                 USUARIO if alvo is not None else None,
                 USUARIO if maximo is not None else None)


def _com_teto(faixa: Faixa, teto: float | None, origem: str) -> Faixa:
    """Aplica um máximo externo; vale o mais restritivo."""
    if teto is None:
        return faixa
    maximo, origem_max = faixa.maximo, faixa.origem_maximo
    if faixa.maximo is None and faixa.minimo is None and faixa.alvo is not None:
        maximo, origem_max = faixa.alvo, faixa.origem_alvo   # alvo pontual
    if maximo is None or teto < maximo:
        maximo, origem_max = teto, origem
    return Faixa(faixa.minimo, faixa.alvo, maximo, faixa.origem_minimo,
                 faixa.origem_alvo, origem_max)


def faixa_da_classe(alvo: float | None, limite: float | None,
                    usuario: Faixa | None = None) -> Faixa:
    """Alvo da estratégia ± tolerância do sistema, cortado pelo limite."""
    if usuario is not None and not usuario.vazia:
        f = usuario
        if f.alvo is None and alvo is not None:
            f = Faixa(f.minimo, float(alvo), f.maximo, f.origem_minimo,
                      ESTRATEGIA, f.origem_maximo)
    elif alvo is None:
        f = Faixa()
    elif alvo == 0:
        f = Faixa(0.0, 0.0, 0.0, ESTRATEGIA, ESTRATEGIA, ESTRATEGIA)
    else:
        alvo = float(alvo)
        f = Faixa(max(0.0, alvo - TOLERANCIA_CLASSE_PP), alvo,
                  min(100.0, alvo + TOLERANCIA_CLASSE_PP),
                  SISTEMA, ESTRATEGIA, SISTEMA)
    return _com_teto(f, None if limite is None else float(limite), ESTRATEGIA)


# -- comparação ----------------------------------------------------------------

def comparar(dimensao: str, chave: str, rotulo: str, atual: float,
             faixa: Faixa) -> Alocacao:
    """Atual contra faixa: diferença para o alvo, overweight e underweight."""
    piso, teto = faixa.piso, faixa.teto
    diferenca = None if faixa.alvo is None else atual - faixa.alvo
    over = max(0.0, atual - teto) if teto is not None else 0.0
    under = max(0.0, piso - atual) if piso is not None else 0.0
    if faixa.vazia:
        status = SEM_REFERENCIA
    elif over > _EPS:
        status = ACIMA
    elif under > _EPS:
        status = ABAIXO
    else:
        status = DENTRO
        over = under = 0.0
    return Alocacao(dimensao, chave, rotulo, atual, faixa, diferenca, over,
                    under, status)


def alocacao(pesos_: tuple[PesoAtivo, ...], politica: dict,
             faixas: dict | None = None) -> dict[str, tuple[Alocacao, ...]]:
    """Atual vs alvo por classe, subclasse, setor e ativo.

    ``politica`` é o dict de ``politica.valores``. ``faixas`` é opcional:
    ``{"ativo": {"PETR4": {"min": 2, "max": 5}}, "setor": {...},
    "subclasse": {...}, "classe": {...}}``.
    """
    faixas = faixas or {}
    usuario = {dim: {str(k).strip() if dim != DIM_ATIVO else str(k).strip().upper():
                     faixa_do_usuario(v) for k, v in (faixas.get(dim) or {}).items()}
               for dim in DIMENSOES_ALOCACAO}
    alvos = dict(politica.get("asset_class_targets") or {})
    limites = dict(politica.get("asset_class_limits") or {})
    lim_ativo = politica.get("single_asset_limit_pct")
    lim_setor = politica.get("sector_limit_pct")

    saida: dict[str, tuple[Alocacao, ...]] = {}

    por_classe = _somar(pesos_, lambda p: p.classe_politica)
    linhas = []
    for classe, rotulo in ROTULO_CLASSE.items():
        atual = sum(p.peso for p in por_classe.get(classe, []))
        f = faixa_da_classe(alvos.get(classe), limites.get(classe),
                            usuario[DIM_CLASSE].get(classe))
        linhas.append(comparar(DIM_CLASSE, classe, rotulo, atual, f))
    saida[DIM_CLASSE] = tuple(linhas)

    def _linhas(dim, grupos, teto_estrategia=None):
        chaves = set(grupos) | set(usuario[dim])
        out = []
        for k in chaves:
            atual = sum(p.peso for p in grupos.get(k, []))
            f = _com_teto(usuario[dim].get(k, Faixa()),
                          None if teto_estrategia is None else float(teto_estrategia),
                          ESTRATEGIA)
            out.append(comparar(dim, k, k, atual, f))
        return tuple(sorted(out, key=lambda a: (-a.atual, a.chave)))

    saida[DIM_SUBCLASSE] = _linhas(DIM_SUBCLASSE,
                                   _somar(pesos_, lambda p: p.subclasse))
    saida[DIM_SETOR] = _linhas(DIM_SETOR, _somar(pesos_, lambda p: p.setor),
                               lim_setor)
    saida[DIM_ATIVO] = _linhas(DIM_ATIVO, _somar(pesos_, lambda p: p.ticker),
                               lim_ativo)
    return saida


# -- concentração ----------------------------------------------------------------

def concentracao(pesos_: tuple[PesoAtivo, ...], dimensao: str, chave, *,
                 base: str = "carteira", filtro=None) -> Concentracao:
    """Distribuição e HHI de uma dimensão sobre uma base da carteira."""
    membros = [p for p in pesos_ if filtro is None or filtro(p)]
    total_base = sum(p.peso for p in membros)
    grupos: dict[str, list[PesoAtivo]] = {}
    for p in membros:
        grupos.setdefault(chave(p) or NAO_IDENTIFICADO, []).append(p)
    nao_id = grupos.pop(NAO_IDENTIFICADO, [])

    def _pct(x):
        return x / total_base * 100.0 if total_base > 0 else 0.0

    lista = sorted(
        (Grupo(k, _pct(sum(p.peso for p in v)), len(v),
               tuple(p.ticker for p in v)) for k, v in grupos.items()),
        key=lambda g: (-g.peso, g.chave))
    identificado = sum(g.peso for g in lista)
    if identificado > 0:
        indice = _hhi(pd.Series([g.peso / identificado for g in lista]))
        efetivo = 1.0 / indice if indice > 0 else None
    else:
        indice = efetivo = None
    return Concentracao(
        dimensao=dimensao, base=base, peso_da_base=total_base,
        grupos=tuple(lista),
        nao_identificado=_pct(sum(p.peso for p in nao_id)),
        cobertura=identificado if total_base > 0 else 0.0,
        hhi=indice, numero_efetivo=efetivo,
        top3=sum(g.peso for g in lista[:3]))


def _rotulo_classe(p: PesoAtivo) -> str:
    return ROTULO_CLASSE.get(p.classe_politica or "", p.classe)


def concentracoes(pesos_: tuple[PesoAtivo, ...]) -> dict[str, Concentracao]:
    """As seis dimensões. Setor é medido dentro da renda variável (onde setor
    se aplica) e indexador dentro da renda fixa."""
    return {
        DIM_ATIVO: concentracao(pesos_, DIM_ATIVO, lambda p: p.ticker),
        DIM_SETOR: concentracao(pesos_, DIM_SETOR, lambda p: p.setor,
                                base="renda variável",
                                filtro=lambda p: p.classe_politica in RENDA_VARIAVEL),
        DIM_CLASSE: concentracao(pesos_, DIM_CLASSE, _rotulo_classe),
        DIM_GEOGRAFIA: concentracao(pesos_, DIM_GEOGRAFIA,
                                    lambda p: p.geografia),
        DIM_EMISSOR: concentracao(pesos_, DIM_EMISSOR, lambda p: p.emissor),
        DIM_INDEXADOR: concentracao(pesos_, DIM_INDEXADOR,
                                    lambda p: p.indexador, base="renda fixa",
                                    filtro=lambda p: p.classe_politica == "renda_fixa"),
    }


# -- alertas --------------------------------------------------------------------

def fmt_pct(x: float | None, casas: int = 1) -> str:
    """18.7 → "18,7%"; inteiros sem casas (10 → "10%")."""
    if x is None:
        return "—"
    if abs(x - round(x)) < _EPS:
        return f"{round(x):d}%"
    return f"{x:.{casas}f}%".replace(".", ",")


def fmt_pp(x: float) -> str:
    return f"{x:.1f} pp".replace(".", ",")


_DE_ONDE = {ESTRATEGIA: "da sua estratégia", USUARIO: "da faixa que você definiu",
            SISTEMA: f"tolerância de {TOLERANCIA_CLASSE_PP:g} pp adotada pelo sistema"}


def _descreve_faixa(f: Faixa) -> str:
    if f.minimo is not None and f.maximo is not None and f.minimo != f.maximo:
        txt = f"{fmt_pct(f.minimo)} a {fmt_pct(f.maximo)}"
    elif f.teto is not None and f.piso is None:
        txt = f"até {fmt_pct(f.teto)}"
    elif f.piso is not None and f.teto is None:
        txt = f"a partir de {fmt_pct(f.piso)}"
    else:
        txt = fmt_pct(f.alvo if f.alvo is not None else f.teto)
    if f.alvo is not None and f.minimo != f.maximo:
        txt += f", alvo {fmt_pct(f.alvo)}"
    return txt


def _alerta_linha(a: Alocacao, contexto: str) -> Alerta | None:
    f = a.faixa
    if a.status == ACIMA:
        limite = f.teto
        so_limite = f.piso is None and f.alvo is None
        if so_limite or f.origem_maximo == ESTRATEGIA and f.maximo != f.alvo:
            nome = {DIM_ATIVO: "o limite por ativo",
                    DIM_SETOR: "o limite por setor",
                    DIM_CLASSE: "o limite máximo da classe"}.get(a.dimensao, "o limite")
            if f.origem_maximo != ESTRATEGIA:
                nome = "o máximo da faixa que você definiu"
            msg = (f"{contexto} representa {fmt_pct(a.atual)} da carteira e "
                   f"excede {nome} de {fmt_pct(limite)}.")
            sev = ALTA if f.origem_maximo == ESTRATEGIA else MEDIA
        else:
            msg = (f"{contexto} está acima da faixa-alvo: {fmt_pct(a.atual)} "
                   f"contra {_descreve_faixa(f)} "
                   f"({_DE_ONDE[f.origem_maximo or SISTEMA]}).")
            sev = MEDIA
        return Alerta(f"{a.dimensao}_acima", sev, a.dimensao, a.chave, msg,
                      a.atual, limite)
    if a.status == ABAIXO:
        msg = (f"{contexto} está abaixo da faixa-alvo: {fmt_pct(a.atual)} "
               f"contra {_descreve_faixa(f)} "
               f"({_DE_ONDE[f.origem_minimo or f.origem_alvo or SISTEMA]}).")
        return Alerta(f"{a.dimensao}_abaixo", MEDIA, a.dimensao, a.chave, msg,
                      a.atual, f.piso)
    return None


def alertas(pesos_: tuple[PesoAtivo, ...],
            aloc: dict[str, tuple[Alocacao, ...]],
            conc: dict[str, Concentracao], politica: dict,
            posicoes=()) -> tuple[Alerta, ...]:
    """Regras objetivas; nenhuma interpretação."""
    saida: list[Alerta] = []
    contexto = {DIM_CLASSE: lambda a: a.rotulo,
                DIM_SUBCLASSE: lambda a: f"A subclasse {a.rotulo}",
                DIM_SETOR: lambda a: f"O setor {a.rotulo}",
                DIM_ATIVO: lambda a: a.rotulo}
    for dim in DIMENSOES_ALOCACAO:
        for a in aloc.get(dim, ()):
            al = _alerta_linha(a, contexto[dim](a))
            if al:
                saida.append(al)

    setor = conc[DIM_SETOR]
    for g in setor.grupos:
        if g.peso >= LIMIAR_SETOR_NA_RV_PCT - _EPS:
            saida.append(Alerta(
                "setor_na_renda_variavel", INFO, DIM_SETOR, g.chave,
                f"O setor {g.chave} representa {fmt_pct(g.peso)} da renda "
                f"variável (limiar de {fmt_pct(LIMIAR_SETOR_NA_RV_PCT)} "
                "adotado pelo sistema).", g.peso, LIMIAR_SETOR_NA_RV_PCT))

    lim_ativo = politica.get("single_asset_limit_pct")
    if lim_ativo is not None:
        for g in conc[DIM_EMISSOR].grupos:
            # o limite por ativo alcança o emissor quando ele tem mais de um
            # papel; dívida soberana fica de fora por ser o risco-base.
            if (g.n_ativos > 1 and g.chave != TESOURO_NACIONAL
                    and g.peso > lim_ativo + _EPS):
                saida.append(Alerta(
                    "emissor_acima", ALTA, DIM_EMISSOR, g.chave,
                    f"O emissor {g.chave} soma {fmt_pct(g.peso)} da carteira "
                    f"({', '.join(g.tickers)}) e excede o limite por ativo de "
                    f"{fmt_pct(lim_ativo)}.", g.peso, float(lim_ativo)))

    fora = [p for p in pesos_ if p.classe_politica is None and p.peso > 0]
    if fora:
        soma = sum(p.peso for p in fora)
        classes = sorted({p.classe for p in fora})
        saida.append(Alerta(
            "fora_da_politica", INFO, DIM_CLASSE, "fora_da_politica",
            f"{fmt_pct(soma)} da carteira está em classes fora da sua "
            f"alocação-alvo ({', '.join(classes)}).", soma, None))

    for p in posicoes or ():
        if _num(p.get("valor_mercado")) < 0 or _num(p.get("total_investido")) < 0:
            t = str(p.get("ticker") or "").strip().upper()
            saida.append(Alerta(
                "valor_invalido", INFO, DIM_ATIVO, t,
                f"{t} tem valor negativo na carteira e entrou com peso 0.",
                0.0, None))

    saida.sort(key=lambda a: (_ORDEM_SEVERIDADE[a.severidade], -a.valor,
                              a.chave))
    return tuple(saida)


# -- tudo junto -------------------------------------------------------------------

def calcular(posicoes, politica: dict, faixas: dict | None = None) -> Calculos:
    """Ponto de entrada: posições da carteira + ``politica.valores``."""
    posicoes = list(posicoes or [])
    pesos_, total, base = pesos(posicoes)
    aloc = alocacao(pesos_, politica, faixas)
    conc = concentracoes(pesos_)
    return Calculos(
        total=total, base_valor=base, pesos=pesos_, alocacao=aloc,
        concentracao=conc,
        peso_renda_variavel=sum(p.peso for p in pesos_
                                if p.classe_politica in RENDA_VARIAVEL),
        alertas=alertas(pesos_, aloc, conc, politica, posicoes))


def texto(calc: Calculos) -> str:
    """Os números como a LLM vai lê-los, marcados como calculados pelo código."""
    linhas = ["=== CÁLCULOS DETERMINÍSTICOS DA CARTEIRA ===",
              "Calculados pelo código a partir dos valores da carteira. Use "
              "estes números como estão; não recalcule pesos, desvios, "
              "concentrações nem limites."]
    linhas.append(f"Base do peso: {calc.base_valor}. Renda variável (ações BR "
                  f"+ FIIs): {fmt_pct(calc.peso_renda_variavel)} da carteira.")
    for dim in DIMENSOES_ALOCACAO:
        linhas_dim = [a for a in calc.alocacao.get(dim, ())
                      if a.status != SEM_REFERENCIA or dim == DIM_CLASSE]
        if not linhas_dim:
            continue
        linhas.append(f"\n[Alocação atual vs alvo · {ROTULO_DIMENSAO[dim]}]")
        for a in linhas_dim:
            partes = [f"- {a.rotulo}: atual {fmt_pct(a.atual, 2)}"]
            if not a.faixa.vazia:
                partes.append(f"faixa {_descreve_faixa(a.faixa)}")
            if a.diferenca_para_alvo is not None:
                partes.append(f"diferença para o alvo {a.diferenca_para_alvo:+.2f} pp"
                              .replace(".", ","))
            partes.append(f"overweight {fmt_pp(a.overweight)}")
            partes.append(f"underweight {fmt_pp(a.underweight)}")
            partes.append(f"status {a.status}")
            linhas.append(" · ".join(partes))
    linhas.append("\n[Concentração]")
    for dim in DIMENSOES_CONCENTRACAO:
        c = calc.concentracao[dim]
        if c.peso_da_base <= 0:
            linhas.append(f"- {ROTULO_DIMENSAO[dim]} (base {c.base}): sem posições.")
            continue
        maior = (f"maior {c.maior.chave} {fmt_pct(c.maior.peso)}"
                 if c.maior else "nenhum grupo identificado")
        hhi = ("HHI —" if c.hhi is None
               else f"HHI {c.hhi:.4f} (≈ {c.numero_efetivo:.1f} grupos iguais)"
               .replace(".", ","))
        linhas.append(
            f"- {ROTULO_DIMENSAO[dim]} (base {c.base}, "
            f"{fmt_pct(c.peso_da_base)} da carteira): {maior}; top 3 "
            f"{fmt_pct(c.top3)}; {hhi}; identificado {fmt_pct(c.cobertura)}")
    linhas.append("\n[Alertas objetivos]")
    if not calc.alertas:
        linhas.append("- Nenhum.")
    linhas += [f"- ({a.severidade}) {a.mensagem}" for a in calc.alertas]
    return "\n".join(linhas)
