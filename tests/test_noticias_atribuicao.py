"""Quando o nome aparece no texto sem ser o assunto dele.

O defeito, medido
-----------------
Com o acervo em 391 itens (06/09/2026), o ticker **mais citado** passou a ser
``GETY`` -- 24 itens, à frente de ``VALE3`` com 20. Num acervo majoritariamente
brasileiro isso é impossível, e o impossível no resultado é assinatura de bug
(``memoria: juncao-por-posicao-em-quadro-ordenado``). Os 24 eram o **crédito da
foto** que o Valor Investe cola no fim da ``description`` do RSS.

O mesmo defeito tem três mecanismos, e por isso dois arquivos são tocados:

1. **Crédito de imagem** (``provedores/rss.py``): rodapé editorial repetido item
   a item. Some antes de o texto virar evidência.
2. **Crédito de fornecedor de dado** e 3. **chave que é palavra comum**
   (``entidades.py``): ``QUANTUM CORP /DE/`` virou a chave ``quantum`` e casou
   com "Dados da **Quantum** Finance"; ``NEWS CORP`` virou ``news`` e casou com
   "Fox **News** host". Ambas são a poda de sufixo societário do PR #214
   voltando como falso positivo.

O que este arquivo cobra é a **fronteira** dos dois remédios: que eles matem o
crédito e a palavra comum, e que não matem a empresa quando ela é o assunto.
"""
from __future__ import annotations

from core.noticias.entidades import Universo, resolver_tickers
from core.noticias.provedores.rss import (
    _credito_plausivel,
    _rodapes_repetidos,
)

_UNIVERSO = Universo(
    tickers=frozenset({"VALE3", "GETY", "NWS", "QMCO", "ADBE", "EQTL3"}),
    por_nome={
        "vale": ("VALE3",),
        "getty images": ("GETY",),
        "news": ("NWS",),
        "quantum": ("QMCO",),
        "adobe": ("ADBE",),
        "equatorial": ("EQTL3",),
    },
)


def _resolver(texto: str) -> set[str]:
    return set(resolver_tickers((), texto, _UNIVERSO))


# --------------------------------------------------------------------------
# 1. O crédito de imagem, no coletor


def test_cauda_repetida_e_reconhecida_como_credito():
    """O que se repete é a cauda **como valor**, não como sufixo universal.

    A primeira versão procurava o maior sufixo comum a todos os itens e não
    achava nada: o crédito varia por foto e nem toda matéria tem um. Este teste
    fixa a forma que funciona -- cauda que reaparece em parte do lote.
    """
    resumos = [
        "Minerio sobe no porto de Tubarao Getty Images",
        "Bolsa fecha em alta pelo terceiro pregao Getty Images",
        "Juros futuros cedem apos o IPCA Getty Images",
        "Analise do setor de shoppings Reproducao/site",
        "Balanco do trimestre supera o consenso",
        "Cambio opera perto da estabilidade",
    ]
    assert "Getty Images" in _rodapes_repetidos(resumos)


def test_lote_pequeno_nao_gera_rodape():
    """Três itens iguais não são padrão editorial, são coincidência.

    Sem o piso de lote, um feed curto sobre a própria Getty Images perderia o
    assunto -- e o remédio viraria o defeito espelhado.
    """
    assert _rodapes_repetidos(["Alta Getty Images"] * 3) == ()


def test_frase_terminada_em_ponto_nao_e_credito():
    """Crédito não tem pontuação final nem caixa de frase.

    São as duas marcas baratas que separam "Getty Images" de "as acoes
    subiram." -- e sem elas o detector cortaria o fim de qualquer texto que o
    feed repetisse por acaso.
    """
    assert not _credito_plausivel("as acoes subiram.")
    assert not _credito_plausivel("mostram que o setor caiu")
    assert _credito_plausivel("Getty Images")


# --------------------------------------------------------------------------
# 2 e 3. A chave que é palavra comum, no resolvedor


def test_palavra_comum_colada_a_nome_maior_nao_resolve():
    """"Fox News host" não é notícia da News Corp, e "Quantum Finance" não é
    da Quantum Corp -- é a casa de dados brasileira."""
    assert "NWS" not in _resolver("Longtime Fox News host disputes reports")
    assert "QMCO" not in _resolver(
        "Dados da Quantum Finance mostram que shoppings caem 1,70%")


def test_empresa_como_assunto_sobrevive_a_guarda():
    """A guarda tem de custar zero quando a empresa É o assunto.

    Os quatro casos são os que quebraram durante o desenvolvimento: início de
    frase ("A Vale"), conjunção capitalizada ("Mas Vale"), vírgula separando
    dois nomes ("Reuters, Vale") e sufixo societário logo depois do nome
    ("Adobe Inc") -- este último porque a chave ``adobe`` existe justamente
    por a poda ter removido o ``inc`` que o texto traz de volta.
    """
    assert "VALE3" in _resolver("A Vale anunciou dividendos nesta sexta")
    assert "VALE3" in _resolver("Mas Vale subiu 3% no pregao de hoje")
    assert "VALE3" in _resolver("Segundo a Reuters, Vale negocia ativo")
    assert "ADBE" in _resolver("Adobe Inc (ADBE) Stock Down 6.7% Now What")


def test_title_case_desarma_a_vizinhanca():
    """Em manchete inglesa toda palavra é maiúscula, e vizinho maiúsculo
    deixa de ser prova de nome maior.

    Sem esta exceção a guarda cortaria a empresa em qualquer manchete
    americana, que é a forma dominante do provedor ``alphavantage``.
    """
    assert "ADBE" in _resolver(
        "Maren Capital LLC Sells Shares And Adobe Rises In Late Trading Today")


def test_nome_geografico_nao_vira_empresa():
    """Um acerto que a guarda entregou sem ter sido projetada para ele:
    "Margem Equatorial" é bacia de petróleo, não a Equatorial Energia."""
    assert "EQTL3" not in _resolver(
        "Lula citara petroleo na Margem Equatorial durante o discurso")
