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
    #: Índices derivados, calculados uma vez. Fora da comparação e do ``repr``
    #: porque não são estado: são a mesma informação em outra forma.
    _indice: dict = field(default_factory=dict, compare=False, repr=False)

    def nomes_por_primeiro_termo(self) -> dict[str, tuple[str, ...]]:
        """``{primeiro token do nome: nomes que começam por ele}``.

        Existe por custo, não por elegância. Sem ele, casar nome de empresa era
        uma ``re.search`` por nome conhecido **por notícia**: com o cadastro
        real (~3 mil nomes) isso media 360 ms por item, o que faria um ciclo de
        cem notícias gastar meio minuto só resolvendo entidade. O índice reduz
        os candidatos aos nomes cujo primeiro termo aparece no texto; a regex
        que decide continua sendo a mesma, e o resultado também.
        """
        pronto = self._indice.get("por_termo")
        if pronto is None:
            pronto = {}
            for nome in self.por_nome:
                termo = nome.split(" ", 1)[0]
                pronto.setdefault(termo, []).append(nome)
            pronto = {k: tuple(v) for k, v in pronto.items()}
            self._indice["por_termo"] = pronto
        return pronto

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


_PALAVRA = re.compile(r"[^\W\d_]+", re.UNICODE)


def _nomes_proprios(texto: str) -> frozenset[str]:
    """Termos que o texto original apresenta como nome proprio.

    Um termo entra se comeca com maiuscula **e** nao esta colado por hifen a
    outro. Devolve a forma normalizada, para casar com as chaves de
    ``por_nome``.

    Existe por causa de "Vale". A empresa e uma das maiores da B3; a palavra e
    uma forma do verbo valer e metade dos compostos do portugues. Na coleta de
    05/09/2026, VALE3 foi atribuido a "Vale-refeicao entra em nova disputa",
    "se vale a pena" e "o guru do vale do silicio" -- tres itens, exatamente o
    piso de exibicao da vitrine, todos falsos.

    A trava nao e uma lista de palavras proibidas. Seria preciso adivinhar
    quais, a lista envelheceria calada, e barrar "vale" custaria toda noticia
    real da Vale. E uma exigencia de evidencia: em manchete, nome proprio vem
    capitalizado e solto. "Vale reduz producao" passa; "se vale a pena" nao tem
    a maiuscula e "Vale-refeicao" nao tem a soltura.

    Custo assumido, e declarado por ser real: manchete inteiramente em caixa
    alta fica permissiva, e manchete inteiramente em minusculas perde o nome de
    um termo. Nomes de dois termos ou mais nao passam por aqui -- a chance de
    "petroleo brasileiro" aparecer por acaso e de outra ordem.
    """
    bruto = str(texto or "")
    achados = set()
    for casa in _PALAVRA.finditer(bruto):
        termo = casa.group()
        if not termo[:1].isupper():
            continue
        antes = bruto[casa.start() - 1] if casa.start() else " "
        depois = bruto[casa.end()] if casa.end() < len(bruto) else " "
        if antes == "-" or depois == "-":
            continue
        normal = normalizar_texto(termo)
        if normal:
            achados.add(normal)
    return frozenset(achados)


#: Acima disto o trecho e titulo em Title Case e a capitalizacao dos vizinhos
#: deixa de significar qualquer coisa. Medido nas manchetes reais do acervo:
#: "Recent News From Nvidia and SK Hynix Reveals" tem 8 de 8 capitalizadas, e
#: "Dados da Quantum Finance mostram que shoppings caem" tem 2 de 8.
_FRACAO_TITLE_CASE = 0.7

_SEGMENTO = re.compile(r"[.!?|—–]")


def _fragmento_de_nome_maior(texto: str, nome: str, universo) -> bool:
    """O nome de um termo aparece so como pedaco de um nome maior?

    Chave de uma palavra e o ponto fraco do casamento por nome, e a poda de
    sufixo juridico cria muitas: ``NEWS CORP`` vira ``news``, ``QUANTUM CORP``
    vira ``quantum``. Medido no acervo em 06/09/2026, ``NWS`` apareceu em
    "Fox News host", "Breakfast News" e "Recent News From Nvidia" -- e ``QMCO``
    em "Dados da Quantum Finance", que e uma casa de dados brasileira sem
    relacao nenhuma com a Quantum Corp americana.

    O que separa esses casos de "Vale reduz producao" nao e o nome: e o
    **vizinho**. Quando o termo vem colado a outra palavra capitalizada e a
    dupla nao e nome conhecido, o texto esta nomeando outra coisa, e o casamento
    pegou um pedaco.

    A excecao e Title Case: em manchete inglesa tudo vem capitalizado e a
    vizinhanca deixa de informar. Ali a regra se cala de proposito -- perder
    "Recent News From Nvidia" para matar "Breakfast News" seria trocar um falso
    positivo por um falso negativo do mesmo tamanho.
    """
    ocorrencias = 0
    for casa in re.finditer(rf"(?<![^\W\d_]){re.escape(nome)}(?![^\W\d_])",
                            texto, re.IGNORECASE):
        ocorrencias += 1
        # Vizinho so e vizinho quando **so ha espaco** entre ele e o nome:
        # virgula, parentese ou ponto separam nomes distintos. Sem isso,
        # "Segundo a Reuters, Vale negocia" leria "Reuters Vale" como um nome
        # so e perderia a Vale -- falso negativo trocado por falso positivo.
        colado_atras = texto[:casa.start()].endswith(" ")
        antes = texto[:casa.start()].rstrip()
        anterior = antes.rsplit(" ", 1)[-1] if colado_atras and antes else ""
        depois = texto[casa.end():]
        seguinte = (depois.lstrip().split(" ", 1)[0]
                    if depois[:1] == " " else "")
        seguinte = seguinte.rstrip(".,;:!?")
        # Maiuscula no inicio de frase e gramatica, nao nome: em "A Vale
        # anunciou", o "A" nao indica que o nome continua para tras. E vizinho
        # de ate dois caracteres nao carrega nome de empresa ("De", "Na").
        if anterior and _inicio_de_segmento(texto, len(antes) - len(anterior)):
            anterior = ""
        colados = [v for v in (anterior, seguinte)
                   if len(v) >= 3 and v[:1].isupper() and v.isalpha()
                   and not _forma_juridica(v)]
        if not colados:
            return False
        # A dupla so isenta se ela mesma for nome conhecido -- "banco do brasil"
        # nao pode ser barrado por "banco" vir grudado em "do". Vizinho vazio
        # nao forma dupla: `" " + nome` normaliza para o proprio nome e a
        # checagem se auto-isentaria.
        duplas = ([f"{anterior} {nome}"] if anterior else []) +                  ([f"{nome} {seguinte}"] if seguinte else [])
        if any(normalizar_texto(d) in universo.por_nome for d in duplas):
            return False
        if _title_case(texto, casa.start()):
            return False
    return ocorrencias > 0


def _forma_juridica(palavra: str) -> bool:
    """O vizinho e sufixo societario, e nao outro nome?

    "Adobe Inc (ADBE)" e o caso: a chave e ``adobe`` porque a poda de sufixo
    removeu ``inc``, e o texto traz de volta exatamente o que foi podado. Sem
    esta isencao a regra de vizinhanca leria "Adobe Inc" como nome maior e
    perderia a propria empresa que a materia tem por assunto -- medido em 3
    itens do acervo em 06/09/2026, mais "Versant Corporation", "CVS Health
    Corporation" e "Teledyne Technologies Incorporated".

    A lista consultada e a mesma ``NOMES_GENERICOS`` que a poda usa, importada
    aqui dentro porque ``universo_entidades`` depende deste modulo. Duas copias
    divergiriam, e a divergencia seria muda.
    """
    from core.noticias.universo_entidades import NOMES_GENERICOS

    normal = normalizar_texto(palavra)
    return bool(normal) and (normal in NOMES_GENERICOS
                             or normal.rstrip("d") in NOMES_GENERICOS)


def _inicio_de_segmento(texto: str, posicao: int) -> bool:
    """Nao ha nada antes desta posicao alem de pontuacao de fim de frase."""
    antes = texto[:posicao].strip()
    return not antes or antes[-1] in ".!?|—–:;"


def _title_case(texto: str, posicao: int) -> bool:
    """O trecho em volta e titulo com todas as iniciais em maiuscula?"""
    ini = 0
    fim = len(texto)
    for casa in _SEGMENTO.finditer(texto):
        if casa.end() <= posicao:
            ini = casa.end()
        else:
            fim = casa.start()
            break
    palavras = [p for p in texto[ini:fim].split()
                if len(p) > 3 and p[:1].isalpha()]
    if len(palavras) < 4:
        return False
    maiusculas = sum(1 for p in palavras if p[:1].isupper())
    return maiusculas >= _FRACAO_TITLE_CASE * len(palavras)


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
        proprios = _nomes_proprios(texto)
        por_termo = universo.nomes_por_primeiro_termo()
        # Só os nomes cujo primeiro termo aparece no texto são candidatos. A
        # regex abaixo é a mesma de antes e continua sendo quem decide -- o
        # índice apenas evita percorrer o cadastro inteiro por notícia.
        candidatos: list[str] = []
        vistos: set[str] = set()
        for termo in normalizado.split():
            for nome in por_termo.get(termo, ()):
                if nome not in vistos:
                    vistos.add(nome)
                    candidatos.append(nome)
        for nome in candidatos:
            # Nome curto demais casaria dentro de outra palavra ("vale" em
            # "prevalece"); a fronteira de palavra resolve, o piso de tamanho
            # evita o resto.
            if len(nome) < 4:
                continue
            # Ver :func:`_nomes_proprios`: nome de um termo so exige evidencia
            # de nome proprio no original; dois ou mais dispensam.
            if " " not in nome:
                if nome not in proprios:
                    continue
                if _fragmento_de_nome_maior(texto, nome, universo):
                    continue
            if re.search(rf"(?<![a-z0-9]){re.escape(nome)}(?![a-z0-9])",
                         normalizado):
                for ticker in universo.por_nome[nome]:
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
