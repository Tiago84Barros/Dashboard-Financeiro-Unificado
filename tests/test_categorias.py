# -*- coding: utf-8 -*-
"""As categorias do Controle Financeiro vêm do banco, e "é investimento" é uma
definição só.

Quatro riscos, e três já morderam este repositório:

1. **A definição de investimento voltar a se duplicar.** Eram três: duas cópias
   byte-a-byte de um literal SQL e um ``frozenset`` que não batia com elas
   (``memoria: guarda-duplicada-diverge``). A unicidade se testa lendo o
   código-fonte, não o comportamento — duas cópias idênticas HOJE passam em
   qualquer teste de comportamento e divergem amanhã.
2. **A unificação estreitar a definição em silêncio.** Se um nome que era
   classificado como aporte deixar de ser, o total investido do histórico muda
   sem ninguém pedir. Os dois pisos abaixo travam isso.
3. **Resgate virar aporte.** ``Resgate de Investimento`` mora no mesmo canto do
   banco que ``Aporte em Investimento`` e tem nome parecido; somá-los dá zero
   com cara de número (``memoria: convencao-nao-pode-apagar-o-observado``).
4. **O seletor oferecer o que o banco não tem.** Era o estado ANTERIOR: três
   nomes do literal não existiam como categoria, e escolhê-los gravava o
   lançamento sem categoria nenhuma, calado.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import categorias as cat

RAIZ = Path(__file__).parents[1]
MIGRATION = RAIZ / "supabase_unificado/schema/072_categorias.sql"
CONTROLE_VIEW = RAIZ / "views/controle_financeiro.py"

# Pisos: o que a definição classificava como aporte ANTES da unificação. A
# lista nova pode crescer; encolher é regressão silenciosa no histórico.
_SQL_ANTIGO = (
    "Investimento", "Investimentos", "Aporte em Investimento",
    "Renda Fixa", "Renda Variavel", "Renda Variável", "Exterior",
    "Reserva de Despesa", "Tesouro Direto", "Ações", "Acoes", "FIIs", "FII",
    "Fundos Imobiliários", "Fundos Imobiliarios", "Cripto", "Criptoativos",
    "Criptomoedas",
)
_CHAVES_ANTIGAS = frozenset({
    "investimento", "investimentos", "aporte investimento",
    "aporte em investimento", "renda fixa", "renda variavel", "exterior",
    "reserva de despesa", "tesouro direto", "acoes", "acao", "fiis", "fii",
    "fundos imobiliarios", "fundo imobiliario", "cripto", "criptoativos",
    "criptomoedas",
})


# -- uma definição só ---------------------------------------------------------

def _fontes_do_repositorio():
    """Os ``.py`` do repositório, sem os deste teste nem os do módulo dono.

    O caminho é relativizado ANTES de filtrar: rodando de um worktree
    (``.claude/worktrees/...``), ``caminho.parts`` carrega o prefixo do worktree
    e um filtro sobre ele esconde o repositório INTEIRO — o teste passa local e
    reprova no CI, sem nada a ver com o diff
    (``memoria: worktree-sem-env-passa-por-limpo``).
    """
    for caminho in RAIZ.rglob("*.py"):
        relativo = caminho.relative_to(RAIZ)
        if relativo.parts[0] in {".claude", ".venv", "venv", "build"}:
            continue
        if caminho.name in {"categorias.py", "test_categorias.py"}:
            continue
        yield relativo.as_posix(), caminho.read_text(
            encoding="utf-8", errors="ignore")


def test_o_literal_de_investimento_nao_tem_segunda_copia():
    """Um nome solto é coincidência — ``core/portfolio_valuations.py`` rotula
    uma classe de ativo de ``'Tesouro Direto'`` e não tem nada com isto. Cinco
    nomes da mesma lista no mesmo arquivo é recolagem."""
    culpados = {
        relativo: presentes
        for relativo, fonte in _fontes_do_repositorio()
        if len(presentes := [n for n in cat.NOMES_DE_INVESTIMENTO
                             if f"'{n}'" in fonte or f'"{n}"' in fonte]) >= 5
    }
    assert not culpados, f"segunda cópia da lista de investimento: {culpados}"


def test_as_duas_formas_derivam_da_mesma_lista():
    assert cat.CHAVES_DE_INVESTIMENTO == {
        cat.normalizar(n) for n in cat.NOMES_DE_INVESTIMENTO}
    for nome in cat.NOMES_DE_INVESTIMENTO:
        assert f"'{nome}'" in cat.SQL_INVESTIMENTO


@pytest.mark.parametrize("nome", _SQL_ANTIGO)
def test_nenhum_nome_perdeu_a_classificacao_de_aporte(nome):
    assert f"'{nome}'" in cat.SQL_INVESTIMENTO


def test_nenhuma_chave_normalizada_se_perdeu():
    assert _CHAVES_ANTIGAS <= cat.CHAVES_DE_INVESTIMENTO


def test_resgate_nao_e_aporte():
    from core.controle import is_investment_category

    assert not is_investment_category("Resgate de Investimento")
    assert "'Resgate de Investimento'" not in cat.SQL_INVESTIMENTO
    assert is_investment_category("Aporte em Investimento")


def test_controle_continua_usando_a_definicao_unificada():
    from core.controle import is_investment_category

    assert is_investment_category("Renda Variável")
    assert is_investment_category("fundo imobiliario")
    assert not is_investment_category("Dividendos")
    assert not is_investment_category("")


# -- listar -------------------------------------------------------------------

@pytest.fixture
def banco(monkeypatch):
    """``_consultar`` dublado: os testes de listagem não tocam em banco."""
    estado = {"linhas": []}
    monkeypatch.setattr(cat, "_consultar",
                        lambda sql, tipo_db: estado["linhas"])
    return estado


def test_listar_devolve_o_que_o_banco_tem(banco):
    banco["linhas"] = [{"id": "1", "nome": "Renda Fixa", "minha": False}]
    nomes = [c["nome"] for c in cat.listar("investimento")]
    assert "Renda Fixa" in nomes
    assert nomes.count("Renda Fixa") == 1  # o SEED não recria o que já existe


def test_nome_do_seed_ausente_do_banco_vem_marcado(banco):
    """Este é o defeito que a mudança conserta: o seletor oferecia o nome e o
    lançamento ia para o banco sem categoria. Agora o ``id`` é ``None`` e a
    tela diz isso."""
    banco["linhas"] = []
    faltando = {c["nome"] for c in cat.listar("entrada") if c["id"] is None}
    assert "Dividendos" in faltando
    assert all(not c["minha"] for c in cat.listar("entrada") if c["id"] is None)


def test_seed_nao_duplica_por_acento(banco):
    banco["linhas"] = [{"id": "1", "nome": "Renda Variavel", "minha": False}]
    nomes = [cat.normalizar(c["nome"]) for c in cat.listar("investimento")]
    assert nomes.count("renda variavel") == 1


def test_leitura_que_falha_de_todo_ainda_devolve_o_seed(monkeypatch):
    monkeypatch.setattr(cat, "_consultar", lambda sql, tipo_db: None)
    lista = cat.listar("saida")
    assert {c["nome"] for c in lista} == set(cat.SEED["saida"])
    assert all(c["id"] is None for c in lista)


def test_tipo_desconhecido_estoura():
    with pytest.raises(ValueError):
        cat.listar("transferencia")


# -- criar / arquivar ---------------------------------------------------------

def test_criar_recusa_nome_vazio(banco):
    ok, msg = cat.criar("   ", "entrada")
    assert not ok and "nome" in msg.lower()


def test_criar_recusa_tipo_desconhecido(banco):
    ok, _ = cat.criar("Previdência", "transferencia")
    assert not ok


def test_criar_recusa_duplicata_ignorando_acento_e_caixa(banco):
    banco["linhas"] = [{"id": "1", "nome": "Renda Variável", "minha": False}]
    ok, msg = cat.criar("renda variavel", "investimento")
    assert not ok and "existe" in msg.lower()


def test_criar_grava_quando_o_nome_e_novo(banco, monkeypatch):
    banco["linhas"] = []
    gravados = []

    class _Conexao:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, _sql, params):
            gravados.append(params)

    monkeypatch.setattr(cat, "_engine", lambda: type(
        "E", (), {"begin": lambda self: _Conexao()})())
    monkeypatch.setattr(cat, "_uid", lambda: "u1")
    ok, _ = cat.criar("  Previdência  ", "investimento")
    assert ok
    assert gravados == [{"uid": "u1", "nome": "Previdência",
                         "tipo": "investment"}]


def test_arquivar_recusa_categoria_que_nao_e_do_usuario(monkeypatch):
    class _Conexao:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_a, **_k):
            return type("R", (), {"rowcount": 0})()

    monkeypatch.setattr(cat, "_engine", lambda: type(
        "E", (), {"begin": lambda self: _Conexao()})())
    monkeypatch.setattr(cat, "_uid", lambda: "u1")
    ok, msg = cat.arquivar("abc")
    assert not ok and "você" in msg.lower()


# -- retirar do seletor ------------------------------------------------------
#
# A maioria das categorias é de sistema e de todos: retirar mexe só na
# preferência de quem pediu. Os riscos abaixo são os que tirariam isso do lugar:
# a retirada voltar pela porta do SEED (sem id, gravando lançamento sem
# categoria), o seletor ficar vazio, e "criar" duplicar o que foi retirado.

_SISTEMA = [
    {"id": "s1", "nome": "Aluguel Recebido", "minha": False},
    {"id": "s2", "nome": "Dividendos", "minha": False},
    {"id": "s3", "nome": "Outros", "minha": False},
    {"id": "s4", "nome": "Reembolso", "minha": False},
    {"id": "s5", "nome": "Renda Extra", "minha": False},
    {"id": "s6", "nome": "Salário", "minha": False},
    {"id": "m1", "nome": "Freelance", "minha": True},
]


@pytest.fixture
def preferencia(monkeypatch):
    """Banco dublado com a lista de retiradas da conta, como a consulta real
    devolve: a coluna ``retirada`` vem calculada junto de cada categoria."""
    estado = {"linhas": [dict(linha) for linha in _SISTEMA], "retiradas": set()}

    def _consultar(sql, tipo_db):
        return [{**linha, "retirada": linha["id"] in estado["retiradas"]}
                for linha in estado["linhas"]]

    def _gravar(sql, ids):
        if sql is cat._SQL_RETIRAR:
            estado["retiradas"].update(ids)
        else:
            assert sql is cat._SQL_DEVOLVER
            estado["retiradas"].difference_update(ids)

    monkeypatch.setattr(cat, "_consultar", _consultar)
    monkeypatch.setattr(cat, "_gravar_retiradas", _gravar)
    return estado


def test_categoria_de_sistema_pode_ser_retirada(preferencia):
    ok, msg = cat.retirar(["s1", "s2"], "entrada")
    assert ok, msg
    assert preferencia["retiradas"] == {"s1", "s2"}
    nomes = {c["nome"] for c in cat.listar("entrada")}
    assert not nomes & {"Aluguel Recebido", "Dividendos"}
    assert "saíram" in msg


def test_retirada_continua_visivel_para_quem_pode_devolver(preferencia):
    preferencia["retiradas"] = {"s2"}
    marcadas = {c["nome"] for c in cat.listar("entrada", com_retiradas=True)
                if c["retirada"]}
    assert marcadas == {"Dividendos"}


def test_retirada_do_seed_nao_volta_sem_id(preferencia):
    """``Dividendos`` está no SEED. Se a retirada fosse filtrada antes de
    comparar com o SEED, ela voltaria ao seletor com ``id=None`` — e o
    lançamento iria para o banco sem categoria, o defeito de origem."""
    preferencia["retiradas"] = {"s2"}
    lista = cat.listar("entrada")
    assert all(c["nome"] != "Dividendos" for c in lista)
    assert all(c["id"] is not None for c in lista)


def test_categoria_criada_pela_pessoa_tambem_se_retira(preferencia):
    ok, _ = cat.retirar(["m1"], "entrada")
    assert ok and preferencia["retiradas"] == {"m1"}


def test_nao_deixa_o_seletor_vazio(preferencia):
    todas = [linha["id"] for linha in _SISTEMA]
    ok, msg = cat.retirar(todas, "entrada")
    assert not ok and "ao menos uma" in msg
    assert preferencia["retiradas"] == set()


def test_nao_deixa_o_seletor_vazio_somando_com_as_ja_retiradas(preferencia):
    preferencia["retiradas"] = {"s1", "s2", "s3", "s4", "s5", "m1"}
    ok, _ = cat.retirar(["s6"], "entrada")
    assert not ok


def test_retirar_de_novo_nao_grava(preferencia):
    preferencia["retiradas"] = {"s2"}
    ok, msg = cat.retirar(["s2"], "entrada")
    assert ok and "já estavam" in msg


def test_retirar_recusa_id_de_outro_tipo_ou_inexistente(preferencia):
    ok, _ = cat.retirar(["nao-existe"], "entrada")
    assert not ok
    assert preferencia["retiradas"] == set()


def test_retirar_sem_escolha_recusa(preferencia):
    ok, _ = cat.retirar([], "entrada")
    assert not ok


def test_criar_aponta_para_devolver_quando_o_nome_foi_retirado(preferencia):
    """Criar de novo faria uma segunda ``Dividendos`` e partiria o histórico."""
    preferencia["retiradas"] = {"s2"}
    ok, msg = cat.criar("dividendos", "entrada")
    assert not ok and "Devolva" in msg


def test_devolver_poe_de_volta(preferencia):
    preferencia["retiradas"] = {"s1", "s2"}
    ok, _ = cat.devolver(["s2"])
    assert ok and preferencia["retiradas"] == {"s1"}
    assert "Dividendos" in {c["nome"] for c in cat.listar("entrada")}


def test_preferencia_ilegivel_nao_derruba_o_seletor(monkeypatch):
    """Se só a parte nova da consulta quebra, o seletor volta a ser o de antes
    das retiradas -- com id. Cair no SEED sem id gravaria sem categoria."""
    def _consultar(sql, tipo_db):
        if "user_settings" in sql:
            return None
        return [{"id": "s2", "nome": "Dividendos", "minha": False}]

    monkeypatch.setattr(cat, "_consultar", _consultar)
    dividendos = next(c for c in cat.listar("entrada") if c["nome"] == "Dividendos")
    assert dividendos["id"] == "s2" and not dividendos["retirada"]


def test_retiradas_moram_na_preferencia_e_nao_na_tabela_de_categorias():
    """Categoria de sistema é de todos: um UPDATE em ``categories`` tiraria a
    opção do seletor de todas as contas."""
    for sql in (cat._SQL_RETIRAR, cat._SQL_DEVOLVER):
        assert "user_settings" in sql and "categories" not in sql
        assert "jsonb_set" in sql  # escrita pontual, sem trazer o documento


def _app_categorias():
    from views.configuracoes_geral import _render_categorias
    _render_categorias()


def test_tela_retira_e_devolve(preferencia):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_app_categorias).run(timeout=30)
    assert not app.exception
    assert "7 categoria(s) no seletor de Entrada" in app.caption[0].value
    retirar = next(m for m in app.multiselect if m.label == "Retirar do seletor")
    retirar.set_value(["s1", "s2"]).run(timeout=30)
    next(b for b in app.button if "Retirar" in b.label).click().run(timeout=30)
    assert not app.exception
    assert preferencia["retiradas"] == {"s1", "s2"}
    assert any("saíram do seu seletor" in s.value for s in app.success)
    assert "5 categoria(s) no seletor de Entrada" in app.caption[0].value

    devolver = next(m for m in app.multiselect if m.label.startswith("Retiradas"))
    devolver.set_value(["s1"]).run(timeout=30)
    next(b for b in app.button if "Devolver" in b.label).click().run(timeout=30)
    assert not app.exception
    assert preferencia["retiradas"] == {"s2"}


def test_tela_nao_oferece_retirar_todas(preferencia):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_app_categorias).run(timeout=30)
    retirar = next(m for m in app.multiselect if m.label == "Retirar do seletor")
    retirar.set_value([linha["id"] for linha in _SISTEMA]).run(timeout=30)
    botao = next(b for b in app.button if "Retirar" in b.label)
    assert botao.disabled
    assert any("ao menos uma" in c.value for c in app.caption)


# -- a tela não tem mais lista própria ----------------------------------------

def test_a_view_nao_carrega_mais_lista_de_categoria():
    fonte = CONTROLE_VIEW.read_text(encoding="utf-8")
    for morto in ("_CAT_ENTRADA", "_CAT_SAIDA", "_CAT_INVESTIMENTO"):
        assert morto not in fonte, f"{morto} sobreviveu em controle_financeiro"
    chamadas = {no.func.id for no in ast.walk(ast.parse(fonte))
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)}
    assert "listar_categorias" in chamadas


# -- a migration diz o que o código pressupõe ---------------------------------

def test_a_migration_nao_retipa_o_resgate():
    sql = MIGRATION.read_text(encoding="utf-8")
    trecho = sql[sql.index("SET type = 'investment'"):sql.index("-- 3 ──")]
    assert "Resgate de Investimento" not in trecho
    assert "Aporte em Investimento" in trecho


def test_os_checks_descrevem_o_banco_real():
    """Os CHECKs declarados em ``002`` rejeitavam valores que o banco CONTÉM
    (``memoria: verificador-e-escritor-listas-diferentes``)."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'investment'" in sql
    assert "'csv_migration'" in sql


def test_a_migration_e_o_seed_falam_dos_mesmos_nomes():
    sql = MIGRATION.read_text(encoding="utf-8")
    inseridos = sql[sql.index("AS novas(nome, tipo)") - 900:
                    sql.index("AS novas(nome, tipo)")]
    todos = {n for nomes in cat.SEED.values() for n in nomes}
    for nome in ("Dividendos", "Restaurante", "Reserva de Despesa"):
        assert f"'{nome}'" in inseridos
        assert nome in todos
