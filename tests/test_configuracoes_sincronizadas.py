"""Configurações descreve o próprio app: três abas acompanham a estrutura.

Regra do usuário de 29/10/2026 (``CLAUDE.md``, "Mudou a estrutura do app4,
mudam os informes de Configurações"): tela, aba ou subaba que entra, sai ou
muda de nome leva junto, no mesmo PR, 🎯 Grau de Confiança, 🗺️ Mapa do App e
📚 Documentação. Informe que descreve um app que não existe mais não quebra
nada na tela -- só engana quem confia nele --, por isso a guarda mora aqui.

O que este arquivo lê é estrutura: o menu de ``app.py``, as abas de
Configurações e as subabas de cada tela, sempre da fonte e por AST (contar na
fonte crua contaria também o comentário que explica a aba). O texto das
explicações ele não lê; esse fica com quem muda a tela.

O menu lateral e as abas de Configurações dentro do Mapa já têm guarda em
``test_configuracoes_mapa_do_app.py``; aqui ficam as subabas e as outras duas
abas.
"""
from __future__ import annotations

import ast
import json
import re
import unicodedata
from pathlib import Path

import pytest

from core import confianca_secao
from design import mapa_app
from design.market_companies import NAV_ITEMS, NAV_ITEMS_B3
from views import documentacao as doc
from views import inteligencia_ativos

RAIZ = Path(__file__).resolve().parents[1]

_EXTENSO = {2: "duas", 3: "três", 4: "quatro", 5: "cinco", 6: "seis",
            7: "sete", 8: "oito", 9: "nove", 10: "dez", 11: "onze", 12: "doze"}
_ORDINAL = ("primeira", "segunda", "terceira", "quarta", "quinta", "sexta",
            "sétima", "oitava", "nona", "décima")


def _dobra(texto: str) -> str:
    """Sem acento e sem caixa: a medição gravada antes de 29/10/2026 traz
    "Selecao de FIIs", e um rótulo com ou sem acento é a mesma tela."""
    base = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in base if not unicodedata.combining(c)).casefold().strip()


def _sem_emoji(rotulo: str) -> str:
    """``"📊  Dashboard"`` → ``"Dashboard"``; rótulo sem ícone volta igual."""
    cabeca, _, resto = rotulo.partition(" ")
    if resto and not any(c.isalnum() for c in cabeca):
        return resto.strip()
    return rotulo.strip()


def _arvore(caminho: str) -> ast.Module:
    return ast.parse((RAIZ / caminho).read_text(encoding="utf-8"))


def _constantes(no: ast.AST) -> list[str]:
    return [e.value for e in getattr(no, "elts", ())
            if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _menu() -> list[str]:
    """As telas do menu lateral, na ordem de ``app.py::_ROTAS``."""
    rotas = next(n.value for n in ast.walk(_arvore("app.py"))
                 if isinstance(n, ast.AnnAssign)
                 and getattr(n.target, "id", "") == "_ROTAS")
    return [_sem_emoji(c.value) for c in rotas.keys]


def _abas_de_configuracoes() -> list[str]:
    """Os rótulos do primeiro ``st.tabs`` de ``configuracoes.render``."""
    render = next(n for n in _arvore("views/configuracoes.py").body
                  if isinstance(n, ast.FunctionDef) and n.name == "render")
    chamada = next(n for n in ast.walk(render)
                   if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "tabs")
    return [e.value for e in chamada.args[0].elts]


def _atribuicao(caminho: str, nome: str) -> ast.AST:
    for no in ast.walk(_arvore(caminho)):
        if isinstance(no, ast.Assign) and any(
                getattr(alvo, "id", "") == nome for alvo in no.targets):
            return no.value
    raise AssertionError(f"{caminho} não define {nome}; o teste precisa acompanhar")


def _abas_de_investimentos() -> list[str]:
    """O ``st.tabs`` de topo de Investimentos (o que guarda a aba na sessão).

    O quarto rótulo é calculado -- 🧠 ou 🔒 conforme a estratégia libera --, e
    o nome dele vem de ``inteligencia_ativos.ROTULO``.
    """
    for no in ast.walk(_arvore("views/investimentos.py")):
        if (isinstance(no, ast.Call) and getattr(no.func, "attr", "") == "tabs"
                and any(k.arg == "key" and getattr(k.value, "id", "") == "ABA_INVESTIMENTOS_KEY"
                        for k in no.keywords)):
            return [_sem_emoji(e.value) if isinstance(e, ast.Constant) else inteligencia_ativos.ROTULO
                    for e in no.args[0].elts]
    raise AssertionError("st.tabs de topo de Investimentos sumiu; o teste precisa acompanhar")


#: Subabas de cada tela do menu, lidas de onde a tela as desenha.
SUBABAS = {
    "Controle Financeiro": lambda: [
        _sem_emoji(r) for r in _constantes(_atribuicao("views/controle_financeiro.py", "_SECOES"))],
    "Investimentos": _abas_de_investimentos,
    "Empresas B3": lambda: [nome for _icone, nome in NAV_ITEMS_B3],
    "Empresas Americanas": lambda: [nome for _icone, nome in NAV_ITEMS],
    "Seleção de FIIs": lambda: [
        _sem_emoji(r) for r in _constantes(_atribuicao("views/fiis.py", "_TABS"))],
    "Configurações": lambda: [_sem_emoji(r) for r in _abas_de_configuracoes()],
}


def _subsequencia(procuradas: list[str], lista: list[str]) -> bool:
    restante = iter(lista)
    return all(any(p == item for item in restante) for p in procuradas)


def test_toda_tela_com_subabas_esta_na_tabela_do_teste():
    """Tela nova no menu precisa dizer aqui se tem subabas -- ou ganhar uma linha."""
    sem_subabas = {"Dashboard Geral", "Portfólio Global"}
    assert set(SUBABAS) | sem_subabas == set(_menu())


# -- 🎯 Grau de Confiança -----------------------------------------------------

def test_grau_de_confianca_mede_ou_declara_cada_tela_do_menu():
    """Toda tela tem nota ou declaração de "não se aplica" -- e nenhuma a mais.

    Seção ausente parece seção sem problema; seção de tela que saiu do menu
    mede uma tela que ninguém mais abre.
    """
    medidas = [nome for nome, _fn in confianca_secao._SECOES]
    declaradas = list(confianca_secao.SECOES_SEM_AFIRMACAO)
    assert sorted(map(_dobra, medidas + declaradas)) == sorted(map(_dobra, _menu()))


def test_grau_de_confianca_segue_a_ordem_do_menu():
    """``relatorio`` promete "a ordem da barra lateral" na docstring."""
    medidas = [_dobra(nome) for nome, _fn in confianca_secao._SECOES]
    menu = [_dobra(t) for t in _menu() if _dobra(t) in medidas]
    assert medidas == menu


def test_subaba_fora_da_nota_existe_na_tela():
    """O card diz "Fora desta nota: X"; X tem de ser subaba que a tela desenha
    hoje, de tela que tem nota. Subaba renomeada deixaria a declaração falando
    de uma aba que não existe -- e a nova, calada, herdaria o percentual."""
    medidas = {_dobra(nome) for nome, _fn in confianca_secao._SECOES}
    for tela, abas in confianca_secao.SUBABAS_FORA_DA_NOTA.items():
        assert _dobra(tela) in medidas, f"{tela} não tem nota no Grau de Confiança"
        faltam = [a for a in abas if a not in SUBABAS[tela]()]
        assert not faltam, f"{tela}: {faltam} não são subabas da tela"


# -- 🗺️ Mapa do App -----------------------------------------------------------

def _area_do_mapa(tela: str) -> dict:
    mapa = json.loads((mapa_app.PASTA / "mapa.json").read_text(encoding="utf-8"))
    return next(a for a in mapa["filhos"] if a["nome"] == tela)


@pytest.mark.parametrize("tela", sorted(SUBABAS))
def test_mapa_lista_as_subabas_de_cada_tela(tela):
    """Cada subaba é um ramo da área, na ordem da tela.

    A área pode ter ramos que não são subaba (o filtro de período do Controle
    Financeiro, por exemplo); por isso subsequência, não igualdade.
    """
    ramos = [f["nome"] for f in _area_do_mapa(tela)["filhos"]]
    subabas = SUBABAS[tela]()
    faltam = [s for s in subabas if s not in ramos]
    assert not faltam, f"Mapa › {tela} sem os ramos {faltam}"
    assert _subsequencia(subabas, ramos), f"Mapa › {tela}: ordem diferente da tela"


def _abas_da_documentacao() -> list[str]:
    """Os rótulos de ``tab_labels`` em ``documentacao.render_corpo``."""
    corpo = next(n for n in _arvore("views/documentacao.py").body
                 if isinstance(n, ast.FunctionDef) and n.name == "render_corpo")
    return _constantes(next(n.value for n in ast.walk(corpo)
                            if isinstance(n, ast.Assign)
                            and getattr(n.targets[0], "id", "") == "tab_labels"))


def test_mapa_da_documentacao_lista_as_abas_dela():
    """O nó 📚 Documentação do Mapa tinha quatro abas da B3 num ramo só e dizia
    "12 sub-abas"; ramo por aba, na ordem dela, e a contagem da explicação junto."""
    no = next(f for f in _area_do_mapa("Configurações")["filhos"]
              if f["nome"] == "Documentação")
    abas = _abas_da_documentacao()
    assert [f["nome"] for f in no["filhos"]] == abas
    for numero in re.findall(r"\b(\d+) sub-abas\b", no["explicacao"]):
        assert int(numero) == len(abas), f"Mapa diz {numero} sub-abas, a tela tem {len(abas)}"


# -- 📚 Documentação ----------------------------------------------------------

def test_documentacao_tem_fluxo_para_cada_tela_do_menu():
    """Todo ``FlowSpec`` diz de que tela é; tela sem fluxo é tela sem documentação.

    ``tela=""`` é fluxo transversal (Qualidade dos dados), que não pertence a
    uma tela só.
    """
    telas = {flow.tela for flow in doc.FLOWS}
    assert telas - {""} <= set(_menu()), f"fluxo de tela fora do menu: {telas - set(_menu())}"
    faltam = [t for t in _menu() if t not in telas]
    assert not faltam, f"Documentação sem fluxo para: {faltam}"


@pytest.mark.parametrize("tela", sorted(set(SUBABAS) - {"Configurações"}))
def test_documentacao_cobre_cada_subaba(tela):
    """Os fluxos da tela declaram, em ``abas``, as subabas que explicam.

    Declarar não basta para o leitor, mas é o que a máquina consegue cobrar:
    subaba nova sem fluxo que a declare falha aqui, e quem a declara escreve o
    nó junto.
    """
    declaradas = {a for flow in doc.FLOWS if flow.tela == tela for a in flow.abas}
    faltam = [s for s in SUBABAS[tela]() if s not in declaradas]
    assert not faltam, f"Documentação › {tela} sem as subabas {faltam}"
    sobram = declaradas - set(SUBABAS[tela]())
    assert not sobram, f"Documentação › {tela} declara subabas que a tela não tem: {sobram}"


def _textos(flow) -> list[str]:
    textos = [flow.title, flow.subtitle, *flow.notes]
    for no in flow.nodes.values():
        textos += [no.title, no.summary, no.why, *no.contains]
    return textos


def _textos_de_configuracoes() -> list[str]:
    return _textos(doc.FLOW_CONFIGURACOES)


@pytest.mark.parametrize("flow", doc.FLOWS, ids=lambda f: f.key)
def test_subaba_declarada_e_nomeada_no_texto_do_fluxo(flow):
    """Declarar ``abas`` sem escrever sobre elas é cobertura de fachada."""
    corpo = _dobra(" ".join(_textos(flow)))
    caladas = [a for a in flow.abas if _dobra(a) not in corpo]
    assert not caladas, f"{flow.key} declara {caladas} sem nomeá-las em nó nenhum"


def test_documentacao_de_configuracoes_aponta_cada_aba():
    """Cada aba de Configurações é endereço de pelo menos um nó ("Aba 🧭 Restrições…")."""
    itens = [i for no in doc.FLOW_CONFIGURACOES.nodes.values() for i in no.contains]
    faltam = [r for r in _abas_de_configuracoes()
              if not any(i.startswith(f"Aba {r}") for i in itens)]
    assert not faltam, f"Documentação › Configurações sem nó para: {faltam}"


def test_contagem_e_posicao_das_abas_de_configuracoes_batem_com_a_tela():
    """"as oito abas" com nove na tela, "a primeira da barra" sendo a segunda:
    os dois defeitos que a auditoria de 29/10/2026 achou."""
    abas = _abas_de_configuracoes()
    fontes = {
        "Documentação": " ".join(_textos_de_configuracoes()),
        "docstring de configuracoes.py": ast.get_docstring(_arvore("views/configuracoes.py")),
    }
    for origem, texto in fontes.items():
        for palavra in re.findall(r"\b(\w+) abas\b", texto):
            n = next((k for k, v in _EXTENSO.items() if v == palavra.lower()), None)
            if n is not None:
                assert n == len(abas), f"{origem}: '{palavra} abas', a tela tem {len(abas)}"
    for item in (i for no in doc.FLOW_CONFIGURACOES.nodes.values() for i in no.contains):
        achado = re.match(r"Aba (.+?), a (\w+) da barra", item)
        if achado:
            rotulo, ordinal = achado.groups()
            assert rotulo in abas, item
            assert _ORDINAL[abas.index(rotulo)] == ordinal, item
