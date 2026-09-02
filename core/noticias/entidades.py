"""Resolução de tickers, empresas, setores, países, moedas e ativos.

Regra central: **só é ticker o que está no universo conhecido.** Nada é
inferido de "palavra em maiúsculas com 4 letras" no texto. Sem essa trava,
``CEO``, ``PIB``, ``IPCA``, ``FED`` e o nome de qualquer sigla viram ativos, e
uma notícia macro passa a ser atribuída a uma empresa que ela nunca citou --
com o agravante de que o erro é silencioso e some no meio de dezenas de itens.

Ticker declarado pelo provedor entra com confiança maior do que ticker achado
no texto, mas ainda passa pelo universo: as APIs erram símbolo (este projeto já
gravou dados sob ticker errado vindo da brapi, ver ``market.ticker_alias``).

Empresa multi-classe expande para todas as classes de propósito. Notícia sobre
a Petrobras afeta PETR3 e PETR4; escolher uma só seria uma decisão de carteira
disfarçada de resolução de entidade.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.noticias.modelos import Entidades
from core.noticias.normalizacao import normalizar_texto

# Padrões estruturais. Servem para VALIDAR um candidato, nunca para descobrir
# um: todo candidato ainda precisa existir no universo.
_TICKER_B3 = re.compile(r"^[A-Z]{4}(?:3|4|5|6|11|34|39)$")
_TICKER_US = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")

MOEDAS: dict[str, tuple[str, ...]] = {
    "BRL": ("real", "reais", "brl"),
    "USD": ("dolar", "dolares", "usd"),
    "EUR": ("euro", "euros", "eur"),
    "CNY": ("yuan", "renminbi", "cny"),
    "JPY": ("iene", "ienes", "jpy"),
    "ARS": ("peso argentino", "ars"),
}

PAISES: dict[str, tuple[str, ...]] = {
    "BR": ("brasil", "brasileiro", "brasileira"),
    "US": ("estados unidos", "eua", "americano", "americana",
           "united states", "u s "),
    "CN": ("china", "chines", "chinesa"),
    "AR": ("argentina",),
    "EU": ("zona do euro", "uniao europeia", "european union"),
    "JP": ("japao", "japan", "japones"),
    "RU": ("russia", "russo"),
}

ATIVOS: dict[str, tuple[str, ...]] = {
    "petroleo": ("petroleo", "brent", "wti", "oil", "crude"),
    "minerio_de_ferro": ("minerio de ferro", "iron ore"),
    "ouro": ("ouro", "gold"),
    "soja": ("soja", "soybean"),
    "milho": ("milho", "corn"),
    "bitcoin": ("bitcoin", "btc"),
    "ibovespa": ("ibovespa", "ibov"),
    "sp500": ("s p 500", "sp500", "standard poor"),
    "nasdaq": ("nasdaq",),
    "selic": ("selic",),
    "juros_eua": ("fed funds", "federal reserve", "fomc"),
}


@dataclass(frozen=True)
class Universo:
    """Os ativos que o APP4 conhece. Fora daqui, ticker não é reconhecido.

    ``por_nome`` mapeia o nome normalizado da empresa para TODAS as classes
    dela. ``vazio`` é o caso legítimo de quem ainda não carregou a carteira --
    e nesse caso só entram os tickers que o provedor declarou.
    """

    tickers: frozenset[str] = frozenset()
    por_nome: dict[str, tuple[str, ...]] = field(default_factory=dict)
    setor_por_ticker: dict[str, str] = field(default_factory=dict)
    pais_por_ticker: dict[str, str] = field(default_factory=dict)

    @property
    def vazio(self) -> bool:
        return not self.tickers and not self.por_nome

    def conhece(self, ticker: str) -> bool:
        return ticker.upper() in self.tickers

    @classmethod
    def de_pares(cls, pares: dict[str, str],
                 setores: dict[str, str] | None = None,
                 paises: dict[str, str] | None = None) -> "Universo":
        """Constrói a partir de ``{ticker: nome da empresa}``.

        Nomes iguais em tickers diferentes agrupam -- é exatamente o caso das
        classes ON/PN, e agrupar é o comportamento correto.
        """
        por_nome: dict[str, list[str]] = {}
        for ticker, nome in pares.items():
            chave = normalizar_texto(nome)
            if not chave:
                continue
            por_nome.setdefault(chave, []).append(ticker.upper())
        return cls(
            tickers=frozenset(t.upper() for t in pares),
            por_nome={k: tuple(sorted(v)) for k, v in por_nome.items()},
            setor_por_ticker={k.upper(): v for k, v in (setores or {}).items()},
            pais_por_ticker={k.upper(): v for k, v in (paises or {}).items()},
        )


UNIVERSO_VAZIO = Universo()


def _valido(ticker: str) -> bool:
    return bool(_TICKER_B3.match(ticker) or _TICKER_US.match(ticker))


def resolver_tickers(declarados, texto: str,
                     universo: Universo = UNIVERSO_VAZIO) -> tuple[str, ...]:
    """Tickers da notícia, em ordem estável.

    Duas origens, nesta ordem de prioridade: o que o provedor declarou e o que
    o nome da empresa aponta. Nenhuma varredura por sigla solta no texto.

    Com universo vazio, aceita os declarados que ao menos têm forma de ticker.
    É um afrouxamento consciente e limitado: sem universo carregado a
    alternativa seria devolver nada e o motor ficaria cego justamente na
    primeira execução, antes de qualquer carteira existir.
    """
    achados: list[str] = []

    for bruto in declarados or ():
        simbolo = str(bruto or "").strip().upper()
        if not simbolo:
            continue
        if universo.vazio:
            if _valido(simbolo) and simbolo not in achados:
                achados.append(simbolo)
        elif universo.conhece(simbolo) and simbolo not in achados:
            achados.append(simbolo)

    if universo.por_nome:
        normalizado = normalizar_texto(texto)
        for nome, tickers in universo.por_nome.items():
            # Nome curto demais casaria dentro de outra palavra ("vale" em
            # "prevalece"); a fronteira de palavra resolve, o piso de tamanho
            # evita o resto.
            if len(nome) < 4:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(nome)}(?![a-z0-9])",
                         normalizado):
                for ticker in tickers:
                    if ticker not in achados:
                        achados.append(ticker)

    return tuple(achados)


def _casar(texto_normalizado: str, mapa: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    achados: list[str] = []
    for chave, termos in mapa.items():
        for termo in termos:
            alvo = normalizar_texto(termo)
            if alvo and re.search(
                rf"(?<![a-z0-9]){re.escape(alvo)}(?![a-z0-9])",
                texto_normalizado,
            ):
                if chave not in achados:
                    achados.append(chave)
                break
    return tuple(achados)


def resolver(
    titulo: str,
    resumo: str | None = None,
    *,
    tickers_declarados=(),
    empresas_declaradas=(),
    setores_declarados=(),
    pais_declarado: str | None = None,
    universo: Universo = UNIVERSO_VAZIO,
) -> Entidades:
    """Monta o conjunto de entidades de uma notícia.

    Devolve tuplas vazias onde nada foi identificado. Vazio não é erro: notícia
    macro sem ticker é legítima e vai ser avaliada como macro, não descartada.
    """
    texto = f"{titulo or ''} {resumo or ''}"
    normalizado = normalizar_texto(texto)

    tickers = resolver_tickers(tickers_declarados, texto, universo)

    empresas = []
    for nome in empresas_declaradas or ():
        limpo = str(nome or "").strip()
        if limpo and limpo not in empresas:
            empresas.append(limpo)

    setores = []
    for setor in setores_declarados or ():
        limpo = str(setor or "").strip()
        if limpo and limpo not in setores:
            setores.append(limpo)
    for ticker in tickers:
        setor = universo.setor_por_ticker.get(ticker)
        if setor and setor not in setores:
            setores.append(setor)

    paises = list(_casar(normalizado, PAISES))
    for ticker in tickers:
        pais = universo.pais_por_ticker.get(ticker)
        if pais and pais not in paises:
            paises.append(pais)
    if pais_declarado:
        codigo = str(pais_declarado).strip().upper()
        if codigo and codigo not in paises:
            paises.insert(0, codigo)

    return Entidades(
        tickers=tickers,
        empresas=tuple(empresas),
        setores=tuple(setores),
        paises=tuple(paises),
        moedas=_casar(normalizado, MOEDAS),
        ativos=_casar(normalizado, ATIVOS),
    )
