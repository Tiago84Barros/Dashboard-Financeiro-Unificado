"""
core/inteligencia_ativos/pares.py
Seleção de pares comparáveis e a tabela Ativo | Métrica | Valor | Mediana dos
pares | Diferença | Interpretação. Puro, sem I/O.

Par não é sorteio nem "os maiores do índice". A seleção é determinística e
declara cada critério:

1. filtros duros -- mesma classe (``tipo``) e mesmo mercado; fora o próprio
   ativo e as outras classes da mesma empresa (ITUB3 não é par de ITUB4);
2. modelo de negócio -- a taxonomia do mercado, do nível mais estreito para
   o mais largo (B3: segmento → subsetor → setor; EUA: SIC de 4 → 3 → 2
   dígitos; FII: tipo+segmento → tipo+mandato → tipo; Tesouro: mesmo papel
   → mesma família). Sobe de nível só se o estreito não tem pares mínimos;
3. tamanho e risco -- dentro do nível, primeiro só candidatos com porte
   entre 1/10x e 10x e risco entre 1/2x e 2x do ativo; afrouxa o risco,
   depois o porte, e registra o afrouxamento;
4. ordem -- distância |ln porte| + |ln risco| em relação ao ativo, empate
   pelo ticker. Até ``N_MAXIMO`` pares.

Com menos de ``N_MINIMO`` pares mesmo no nível mais largo, não há grupo:
mediana de dois não é referência.

A comparação é insumo, não conclusão: indicador acima da mediana não quer
dizer investimento melhor. Ela vai estruturada em ``ComparacaoPares`` para o
Portfolio Fit usar depois.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from core.inteligencia_ativos.fundamentos import (
    ACAO,
    ANOS,
    ETF,
    FII,
    MOEDA,
    NAO_DISPONIVEL,
    PCT,
    RENDA_FIXA,
    ROTULO_TIPO,
    Metrica,
    X,
    _tem_valor,
    formatar,
)
from core.inteligencia_ativos.valuation import (
    ABAIXO,
    ACIMA,
    EM_LINHA,
    SEM_DADO,
    TOLERANCIA,
    TOLERANCIA_EM_LINHA,
    posicao,
)

N_MINIMO = 3
N_MAXIMO = 10
BANDA_PORTE = 10.0     # 1/10x a 10x
BANDA_RISCO = 2.0      # 1/2x a 2x

RODAPE = ("Indicador melhor que o dos pares não implica investimento melhor: "
          "a comparação é insumo para o Portfolio Fit, não recomendação.")

# métricas comparadas por classe, com a leitura neutra dos dois lados
METRICAS: dict[str, tuple[Metrica, ...]] = {
    ACAO: (
        Metrica("p_l", "P/L", X),
        Metrica("p_vp", "P/VP", X),
        Metrica("dividend_yield", "Dividend yield", PCT),
        Metrica("ev_ebit", "EV/EBIT", X),
        Metrica("roe", "ROE", PCT),
        Metrica("margem_liquida", "Margem líquida", PCT),
        Metrica("volatilidade", "Volatilidade anualizada", PCT),
        Metrica("porte", "Valor de mercado", MOEDA),
    ),
    FII: (
        Metrica("p_vp", "P/VP", X),
        Metrica("dividend_yield", "Dividend yield (12 meses)", PCT),
        Metrica("cap_rate", "Cap rate implícito", PCT),
        Metrica("alavancagem", "Alavancagem", PCT),
        Metrica("max_drawdown", "Maior queda (drawdown)", PCT),
        Metrica("liquidez_diaria", "Liquidez diária", MOEDA),
        Metrica("porte", "Patrimônio líquido", MOEDA),
    ),
    RENDA_FIXA: (
        Metrica("taxa_mercado", "Taxa de mercado (venda)", PCT),
        Metrica("prazo", "Prazo até o vencimento", ANOS),
    ),
    ETF: (),
}

LEITURA: dict[str, str] = {
    "p_l": "o mercado paga mais (acima) ou menos (abaixo) por unidade de "
           "lucro; pode refletir crescimento esperado, lucro fora do normal "
           "ou risco percebido",
    "p_vp": "o preço se afasta mais (acima) ou menos (abaixo) do patrimônio "
            "contábil; pode refletir rentabilidade, qualidade dos ativos ou "
            "risco",
    "dividend_yield": "distribui mais (acima) ou menos (abaixo) sobre o "
                      "preço; pode refletir payout, preço deprimido ou "
                      "reinvestimento",
    "ev_ebit": "a firma vale mais (acima) ou menos (abaixo) por unidade de "
               "resultado operacional",
    "roe": "retorno sobre o patrimônio maior (acima) ou menor (abaixo); "
           "alavancagem também eleva o ROE",
    "margem_liquida": "fica com parte maior (acima) ou menor (abaixo) da "
                      "receita; depende do modelo de negócio",
    "volatilidade": "o preço oscilou mais (acima) ou menos (abaixo) nos "
                    "últimos 36 meses",
    "porte": "é maior (acima) ou menor (abaixo) que o par típico",
    "cap_rate": "a renda imobiliária implícita no preço é maior (acima) ou "
                "menor (abaixo)",
    "alavancagem": "usa mais (acima) ou menos (abaixo) dívida",
    "max_drawdown": "a maior queda foi mais funda (acima) ou mais rasa "
                    "(abaixo)",
    "liquidez_diaria": "negocia mais (acima) ou menos (abaixo) por dia",
    "taxa_mercado": "o mercado exige taxa maior (acima) ou menor (abaixo) "
                    "que nos títulos comparáveis; reflete prazo e indexador",
    "prazo": "vence depois (acima) ou antes (abaixo) dos comparáveis",
}


@dataclass(frozen=True)
class Candidato:
    """Um ativo do universo, com o que a seleção e a comparação precisam.

    ``taxonomia``: ((rótulo do nível, valor), ...) do mais estreito para o
    mais largo. ``raiz``: identidade da empresa (4 letras na B3, CIK nos
    EUA) para não pôr duas classes da mesma empresa no grupo.
    """
    ticker: str
    nome: str | None
    tipo: str
    mercado: str
    taxonomia: tuple[tuple[str, str | None], ...] = ()
    porte: float | None = None
    risco: float | None = None
    rotulo_risco: str | None = None
    metricas: dict = field(default_factory=dict)
    raiz: str | None = None
    ano_ref: int | None = None        # exercício do dado (balanço/múltiplo)


@dataclass(frozen=True)
class Par:
    ticker: str
    nome: str | None
    distancia: float
    motivo: str
    metricas: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GrupoPares:
    nivel: str | None = None          # rótulo do nível da taxonomia usado
    valor: str | None = None          # valor do nível (o segmento, o SIC...)
    pares: tuple[Par, ...] = ()
    criterios: tuple[str, ...] = ()
    relaxamentos: tuple[str, ...] = ()
    motivo: str | None = None         # por que não há grupo

    @property
    def descricao(self) -> str | None:
        if not self.pares:
            return None
        return (f"{len(self.pares)} {'par' if len(self.pares) == 1 else 'pares'}"
                f" do mesmo {self.nivel} ({self.valor})")

    def valores(self, chave: str) -> tuple[float, ...]:
        return tuple(float(p.metricas[chave]) for p in self.pares
                     if _tem_valor(p.metricas.get(chave)))

    def como_dict(self) -> dict:
        return {"nivel": self.nivel, "valor": self.valor,
                "criterios": list(self.criterios),
                "relaxamentos": list(self.relaxamentos),
                "motivo": self.motivo, "descricao": self.descricao,
                "pares": [{"ticker": p.ticker, "nome": p.nome,
                           "distancia": p.distancia, "motivo": p.motivo,
                           "metricas": dict(p.metricas)} for p in self.pares]}

    @classmethod
    def de_dict(cls, d: dict | None) -> "GrupoPares":
        d = d or {}
        return cls(d.get("nivel"), d.get("valor"), tuple(
            Par(p["ticker"], p.get("nome"), p.get("distancia") or 0.0,
                p.get("motivo") or "", dict(p.get("metricas") or {}))
            for p in d.get("pares") or ()),
            tuple(d.get("criterios") or ()), tuple(d.get("relaxamentos") or ()),
            d.get("motivo"))


# -- seleção -------------------------------------------------------------------------

def _razao(a, b) -> float | None:
    if not _tem_valor(a) or not _tem_valor(b) or float(a) <= 0 or float(b) <= 0:
        return None
    return float(b) / float(a)


def _defasado(alvo: Candidato, c: Candidato) -> bool:
    """Par com dado de exercício mais de um ano anterior ao do ativo: Fitbit
    com balanço de 2019 não é par da Apple de 2025. Sem ano, não exclui."""
    return (alvo.ano_ref is not None and c.ano_ref is not None
            and c.ano_ref < alvo.ano_ref - 1)


def _dentro(razao: float | None, banda: float) -> bool:
    return razao is not None and 1 / banda <= razao <= banda


def _distancia(alvo: Candidato, c: Candidato) -> float:
    total = 0.0
    for r in (_razao(alvo.porte, c.porte), _razao(alvo.risco, c.risco)):
        total += abs(math.log(r)) if r is not None else 1.0
    return round(total, 4)


def _x(v: float | None) -> str:
    return "sem dado" if v is None else f"{v:.2f}x".replace(".", ",")


def _motivo_par(alvo: Candidato, c: Candidato, nivel: str, valor: str) -> str:
    partes = [f"mesmo {nivel} ({valor})",
              f"porte {_x(_razao(alvo.porte, c.porte))} o do ativo"]
    if alvo.rotulo_risco:
        partes.append(f"{alvo.rotulo_risco} {_x(_razao(alvo.risco, c.risco))}")
    return "; ".join(partes)


def selecionar(alvo: Candidato, universo, *, n_maximo: int = N_MAXIMO,
               n_minimo: int = N_MINIMO) -> GrupoPares:
    base = [c for c in universo
            if c.tipo == alvo.tipo and c.mercado == alvo.mercado
            and c.ticker != alvo.ticker
            and not (alvo.raiz and c.raiz == alvo.raiz)
            and not _defasado(alvo, c)]
    criterios = [f"Mesma classe ({ROTULO_TIPO.get(alvo.tipo, alvo.tipo)}) e "
                 f"mesmo mercado ({alvo.mercado}).",
                 "Excluídas outras classes da mesma empresa; uma classe por "
                 "empresa no grupo."]
    if alvo.ano_ref is not None:
        criterios.append(f"Dado contemporâneo: exercício de {alvo.ano_ref - 1} "
                         "em diante (exclui empresa sem balanço recente, "
                         "como a que saiu da bolsa).")
    relaxamentos: list[str] = []
    tem_porte = _tem_valor(alvo.porte) and float(alvo.porte) > 0
    tem_risco = _tem_valor(alvo.risco) and float(alvo.risco) > 0
    if not tem_porte:
        relaxamentos.append("Ativo sem porte conhecido: tamanho não filtra.")
    if not tem_risco and alvo.rotulo_risco:
        relaxamentos.append(f"Ativo sem {alvo.rotulo_risco}: risco não filtra.")
    bandas = []
    if tem_porte and tem_risco:
        bandas.append((True, True))
    if tem_porte:
        bandas.append((True, False))
    if tem_risco:
        bandas.append((False, True))
    bandas.append((False, False))

    for i, (nivel, valor) in enumerate(alvo.taxonomia):
        if not valor:
            relaxamentos.append(f"Ativo sem {nivel}: nível ignorado.")
            continue
        mesmo = [c for c in base if len(c.taxonomia) > i
                 and c.taxonomia[i][1] == valor]
        for usa_porte, usa_risco in bandas:
            filtrados = [c for c in mesmo
                         if (not usa_porte or _dentro(_razao(alvo.porte, c.porte),
                                                      BANDA_PORTE))
                         and (not usa_risco or _dentro(_razao(alvo.risco, c.risco),
                                                       BANDA_RISCO))]
            ordenados = sorted(filtrados,
                               key=lambda c: (_distancia(alvo, c), c.ticker))
            escolhidos, raizes = [], set()
            for c in ordenados:
                if c.raiz and c.raiz in raizes:
                    continue
                if c.raiz:
                    raizes.add(c.raiz)
                escolhidos.append(c)
            if len(escolhidos) >= n_minimo:
                crit = list(criterios)
                crit.append(f"Modelo de negócio: mesmo {nivel} ({valor}).")
                if usa_porte:
                    crit.append("Tamanho: porte entre 1/10x e 10x o do ativo.")
                if usa_risco:
                    crit.append(f"Risco: {alvo.rotulo_risco} entre 1/2x e 2x "
                                "o do ativo.")
                if tem_porte and not usa_porte:
                    relaxamentos.append(f"Menos de {n_minimo} pares no porte "
                                        "do ativo: tamanho não filtra.")
                if tem_risco and not usa_risco:
                    relaxamentos.append(f"Menos de {n_minimo} pares com "
                                        f"{alvo.rotulo_risco} próximo: risco "
                                        "não filtra.")
                crit.append(f"Ordem: menor distância de porte e risco; até "
                            f"{n_maximo} pares.")
                return GrupoPares(nivel, valor, tuple(
                    Par(c.ticker, c.nome, _distancia(alvo, c),
                        _motivo_par(alvo, c, nivel, valor), dict(c.metricas))
                    for c in escolhidos[:n_maximo]), tuple(crit),
                    tuple(relaxamentos))
        relaxamentos.append(f"Menos de {n_minimo} pares no mesmo {nivel} "
                            f"({valor}): nível mais largo.")
    return GrupoPares(criterios=tuple(criterios), relaxamentos=tuple(relaxamentos),
                      motivo=(f"Menos de {n_minimo} pares comparáveis mesmo no "
                              "nível mais largo da classificação; sem grupo, "
                              "para não comparar com ativos de outro negócio."))


# -- comparação ----------------------------------------------------------------------

@dataclass(frozen=True)
class LinhaComparacao:
    ativo: str
    chave: str
    metrica: str
    unidade: str
    valor: float | None
    mediana_pares: float | None
    n_pares: int
    diferenca: float | None
    diferenca_pct: float | None
    posicao: str
    interpretacao: str

    def texto_valor(self, moeda: str = "BRL") -> str:
        return formatar(self.valor, self.unidade, moeda)

    def texto_mediana(self, moeda: str = "BRL") -> str:
        return formatar(self.mediana_pares, self.unidade, moeda)

    def texto_diferenca(self, moeda: str = "BRL") -> str:
        if self.diferenca is None:
            return NAO_DISPONIVEL
        sinal = "+" if self.diferenca >= 0 else "−"
        if self.unidade == PCT:
            base = f"{abs(self.diferenca):.1f}".replace(".", ",") + " p.p."
        else:
            base = formatar(abs(self.diferenca), self.unidade, moeda)
        if self.diferenca_pct is not None and self.unidade != PCT:
            base += f" ({sinal}{abs(self.diferenca_pct):.0f}%)"
        return f"{sinal}{base}"


@dataclass(frozen=True)
class ComparacaoPares:
    ativo: str
    tipo: str | None
    moeda: str
    grupo: GrupoPares = field(default_factory=GrupoPares)
    linhas: tuple[LinhaComparacao, ...] = ()
    motivo: str | None = None

    @property
    def com_dado(self) -> tuple[LinhaComparacao, ...]:
        return tuple(linha for linha in self.linhas if linha.posicao != SEM_DADO)

    def linha(self, chave: str) -> LinhaComparacao | None:
        return next((linha for linha in self.linhas if linha.chave == chave), None)

    def como_dict(self) -> dict:
        return {"ativo": self.ativo, "tipo": self.tipo, "moeda": self.moeda,
                "motivo": self.motivo, "rodape": RODAPE,
                "grupo": self.grupo.como_dict(),
                "linhas": [{
                    "ativo": linha.ativo, "chave": linha.chave, "metrica": linha.metrica,
                    "unidade": linha.unidade, "valor": linha.valor,
                    "mediana_pares": linha.mediana_pares, "n_pares": linha.n_pares,
                    "diferenca": linha.diferenca, "diferenca_pct": linha.diferenca_pct,
                    "posicao": linha.posicao, "interpretacao": linha.interpretacao,
                    "texto_valor": linha.texto_valor(self.moeda),
                    "texto_mediana": linha.texto_mediana(self.moeda),
                    "texto_diferenca": linha.texto_diferenca(self.moeda)}
                    for linha in self.linhas]}

    @classmethod
    def de_dict(cls, d: dict | None) -> "ComparacaoPares":
        d = d or {}
        return cls(d.get("ativo") or "", d.get("tipo"), d.get("moeda") or "BRL",
                   GrupoPares.de_dict(d.get("grupo")), tuple(
                       LinhaComparacao(linha["ativo"], linha["chave"], linha["metrica"],
                                       linha["unidade"], linha.get("valor"),
                                       linha.get("mediana_pares"),
                                       int(linha.get("n_pares") or 0),
                                       linha.get("diferenca"), linha.get("diferenca_pct"),
                                       linha.get("posicao") or SEM_DADO,
                                       linha.get("interpretacao") or NAO_DISPONIVEL)
                       for linha in d.get("linhas") or ()), d.get("motivo"))


def _interpretacao(mt: Metrica, pos: str, n: int) -> str:
    if pos == SEM_DADO:
        return NAO_DISPONIVEL if n else "Nenhum par com este dado."
    if pos == EM_LINHA:
        return (f"Em linha com a mediana dos pares (diferença de até "
                f"{TOLERANCIA.get(mt.chave, TOLERANCIA_EM_LINHA) * 100:.0f}%).")
    lado = "Acima" if pos == ACIMA else "Abaixo"
    return (f"{lado} da mediana dos pares: {LEITURA[mt.chave]}. "
            "Não indica, sozinho, investimento melhor ou pior.")


def comparar(alvo: Candidato, grupo: GrupoPares, *,
             moeda: str = "BRL") -> ComparacaoPares:
    catalogo = METRICAS.get(alvo.tipo) or ()
    if not catalogo:
        return ComparacaoPares(alvo.ticker, alvo.tipo, moeda, grupo, (),
                               "Classe sem métricas de comparação com pares.")
    linhas = []
    for mt in catalogo:
        valor = alvo.metricas.get(mt.chave)
        valor = float(valor) if _tem_valor(valor) else None
        amostra = grupo.valores(mt.chave)
        mediana = statistics.median(amostra) if amostra else None
        pos = (posicao(valor, mediana, TOLERANCIA.get(mt.chave,
                                                      TOLERANCIA_EM_LINHA))
               if grupo.pares else SEM_DADO)
        dif = (valor - mediana) if valor is not None and mediana is not None \
            else None
        dif_pct = (100.0 * dif / abs(mediana)) if dif is not None and mediana \
            else None
        interp = (_interpretacao(mt, pos, len(amostra)) if valor is not None
                  else NAO_DISPONIVEL)
        linhas.append(LinhaComparacao(
            alvo.ticker, mt.chave, mt.rotulo, mt.unidade, valor,
            mediana, len(amostra),
            round(dif, 6) if dif is not None else None,
            round(dif_pct, 2) if dif_pct is not None else None, pos, interp))
    return ComparacaoPares(alvo.ticker, alvo.tipo, moeda, grupo, tuple(linhas),
                           None if grupo.pares else grupo.motivo)


# -- textos --------------------------------------------------------------------------

def resumo(c: ComparacaoPares) -> str:
    if not c.grupo.pares:
        return c.motivo or c.grupo.motivo or "Sem grupo de pares comparáveis."
    acima = [linha.metrica for linha in c.com_dado if linha.posicao == ACIMA]
    abaixo = [linha.metrica for linha in c.com_dado if linha.posicao == ABAIXO]
    partes = [c.grupo.descricao or ""]
    if acima:
        partes.append("acima da mediana em " + ", ".join(acima[:3]))
    if abaixo:
        partes.append("abaixo em " + ", ".join(abaixo[:3]))
    return "; ".join(p for p in partes if p) + ". Insumo, não veredito."


def texto(c: ComparacaoPares, ticker: str) -> str:
    linhas = [f"=== COMPARAÇÃO COM PARES: {ticker} ==="]
    if not c.grupo.pares:
        linhas.append(c.motivo or c.grupo.motivo or NAO_DISPONIVEL)
        return "\n".join(linhas)
    linhas.append("[DADO — fornecido pelo sistema; não invente valores "
                  f"ausentes: onde estiver \"{NAO_DISPONIVEL}\", diga isso]")
    linhas.append(f"Grupo: {c.grupo.descricao}.")
    linhas += [f"- Critério: {x}" for x in c.grupo.criterios]
    linhas += [f"- Afrouxamento: {x}" for x in c.grupo.relaxamentos]
    linhas.append("Pares: " + ", ".join(
        f"{p.ticker}" + (f" ({p.nome})" if p.nome else "") for p in c.grupo.pares))
    linhas.append("Ativo | Métrica | Valor | Mediana dos pares | Diferença")
    for linha in c.linhas:
        linhas.append(f"{linha.ativo} | {linha.metrica} | {linha.texto_valor(c.moeda)} | "
                      f"{linha.texto_mediana(c.moeda)}"
                      f"{f' ({linha.n_pares})' if linha.n_pares else ''} | "
                      f"{linha.texto_diferenca(c.moeda)}")
    linhas.append("[INTERPRETAÇÃO — sua tarefa, usando só o bloco DADO]")
    linhas.append(f"- {RODAPE}")
    linhas.append("- As diferenças se explicam por modelo de negócio, "
                  "crescimento, rentabilidade ou risco? Diga qual, se o DADO "
                  "permitir; senão, diga que não permite.")
    return "\n".join(linhas)
