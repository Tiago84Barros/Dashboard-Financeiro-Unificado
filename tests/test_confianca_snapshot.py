# -*- coding: utf-8 -*-
"""A medição de confiança é gravada, e só o clique do usuário a refaz.

Dois riscos separados, e os dois já morderam este repositório:

1. **O portão não existir de verdade.** ``st.tabs`` executa o corpo de todas as
   abas em toda execução do script, então esconder a chamada cara atrás de uma
   posição não adia nada. Quem prova que ela foi adiada é o teste estrutural
   abaixo, que exige que ``relatorio()`` só apareça dentro da função do botão.
2. **O não medido voltar como zero.** ``pct=None`` significa NÃO MEDIDO; um
   ``0.0`` na volta do JSON puniria o app por uma falha que ninguém observou —
   o defeito de "medição que pune a evidência".
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from core import confianca_snapshot as snap
from core.confianca_secao import Componente, ConfiancaSecao
from core.universo_decisao import Universo

RAIZ = Path(__file__).parents[1]
MIGRATION = RAIZ / "supabase_unificado/schema/071_confianca_snapshots.sql"
VIEW = RAIZ / "views/confianca.py"


# -- banco de teste -----------------------------------------------------------

def _sqlite_da_migration() -> str:
    """Traduz a migration real para SQLite, em vez de reescrever o DDL à mão.

    Uma cópia escrita à mão deixaria o teste passar contra uma tabela que a
    produção não tem — verificador e escritor lendo estruturas diferentes é o
    modo de falha que já deixou uma migration registrada e nunca executada.
    Aqui só os TIPOS mudam; os nomes de coluna continuam vindo do arquivo.
    """
    bruto = MIGRATION.read_text(encoding="utf-8")
    for de, para in (("BIGSERIAL    PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"),
                     ("BIGSERIAL", "INTEGER"),
                     ("TIMESTAMPTZ", "TIMESTAMP"),
                     ("JSONB", "TEXT"),
                     ("NOW()", "CURRENT_TIMESTAMP")):
        bruto = bruto.replace(de, para)
    return "\n".join(linha for linha in bruto.splitlines()
                     if not linha.lstrip().startswith("--"))


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        for comando in _sqlite_da_migration().split(";"):
            if comando.strip() and not comando.strip().startswith("--"):
                conn.exec_driver_sql(comando)
    yield eng
    eng.dispose()


def _secoes():
    return [
        ConfiancaSecao(
            "Empresas B3",
            (Componente("Integridade", 91.5, 0.35, "3 checagens"),
             Componente("Frescor", None, 0.25, "banco fora do ar"),
             Componente("Metodologia validada", 50.0, 0.25, "1/2 portoes")),
            universo=Universo("b3", 400, 380, 210, ("LUXM3",), ("nota",), 30),
            notas=("preco ajustado por split",),
        ),
        ConfiancaSecao(
            "Configuracoes",
            (Componente("Medicao", None, 1.0, "nao se aplica"),),
            aplicavel=False,
        ),
    ]


# -- serialização -------------------------------------------------------------

def test_ida_e_volta_preserva_o_numero_e_a_faixa():
    secoes = _secoes()
    voltou, rigor = snap.desserializar(snap.serializar(secoes, {"motores": {}}))
    assert [s.secao for s in voltou] == [s.secao for s in secoes]
    for antes, depois in zip(secoes, voltou):
        if antes.pct is None:
            assert depois.pct is None
        else:
            assert depois.pct == pytest.approx(antes.pct)
        assert depois.faixa == antes.faixa
        assert depois.cobertura_da_medicao == pytest.approx(
            antes.cobertura_da_medicao)
        assert depois.notas == antes.notas
        assert depois.aplicavel == antes.aplicavel
    assert rigor == {"motores": {}}


def test_nao_medido_volta_none_e_nunca_zero():
    """0.0 entraria na média ponderada acusando um defeito não observado."""
    payload = snap.serializar(_secoes(), None)
    voltou, _ = snap.desserializar(payload)
    frescor = next(c for c in voltou[0].componentes if c.nome == "Frescor")
    assert frescor.pct is None
    assert frescor.medido is False
    # E a cobertura continua denunciando que o peso 0.25 do Frescor ficou fora.
    assert voltou[0].cobertura_da_medicao == pytest.approx(0.60 / 0.85)


def test_universo_sobrevive_a_ida_e_volta():
    voltou, _ = snap.desserializar(snap.serializar(_secoes(), None))
    u = voltou[0].universo
    assert (u.modulo, u.nominal, u.investivel, u.apto) == ("b3", 400, 380, 210)
    assert u.exemplos_descartados == ("LUXM3",)


def test_payload_carrega_versao():
    """Sem versão, um formato novo lido por código velho falha em silêncio."""
    assert snap.serializar([], None)["versao"] == snap.VERSAO


# -- persistência -------------------------------------------------------------

def test_grava_e_carrega_a_ultima_medicao(engine):
    agora = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    snap.gravar(_secoes(), {"motores": {"FII": {}}}, engine=engine, agora=agora)
    secoes, rigor, medido_em = snap.carregar_ultimo(engine=engine)
    assert [s.secao for s in secoes] == ["Empresas B3", "Configuracoes"]
    assert rigor == {"motores": {"FII": {}}}
    assert medido_em == agora


def test_sem_medicao_nenhuma_o_retorno_e_none(engine):
    assert snap.carregar_ultimo(engine=engine) is None


def test_carrega_a_mais_recente_e_nao_a_ultima_inserida(engine):
    velha = datetime(2026, 9, 1, tzinfo=timezone.utc)
    nova = datetime(2026, 9, 20, tzinfo=timezone.utc)
    snap.gravar(_secoes(), None, engine=engine, agora=nova)
    snap.gravar([], None, engine=engine, agora=velha)
    _, _, medido_em = snap.carregar_ultimo(engine=engine)
    assert medido_em == nova


def test_podar_deixa_exatamente_o_numero_pedido(engine):
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(12):
        snap.gravar([], None, engine=engine, agora=base + timedelta(days=i))
    with engine.begin() as conn:
        snap.podar(conn, manter=5)
        total = conn.execute(text("SELECT COUNT(*) FROM confianca_snapshots")).scalar()
        restantes = [r[0] for r in conn.execute(text(
            "SELECT medido_em FROM confianca_snapshots ORDER BY medido_em DESC"))]
    assert total == 5
    # E são as CINCO MAIS RECENTES, não cinco quaisquer.
    assert snap._aware(restantes[0]).date() == (base + timedelta(days=11)).date()
    assert snap._aware(restantes[-1]).date() == (base + timedelta(days=7)).date()


def test_tabela_ausente_levanta_em_vez_de_falhar_calado():
    """O usuário pagaria a medição inteira e voltaria para a tela vazia."""
    vazio = create_engine("sqlite://")
    with pytest.raises(snap.TabelaAusente) as erro:
        snap.gravar(_secoes(), None, engine=vazio)
    assert snap.MIGRATION in str(erro.value)
    vazio.dispose()


def test_leitura_sem_tabela_e_silenciosa_porque_o_vazio_e_legitimo():
    vazio = create_engine("sqlite://")
    assert snap.carregar_ultimo(engine=vazio) is None
    vazio.dispose()


def test_payload_ilegivel_nao_derruba_a_tela(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO confianca_snapshots (medido_em, payload) "
            "VALUES ('2026-09-21 10:00:00', :p)"), {"p": json.dumps([1, 2, 3])})
    assert snap.carregar_ultimo(engine=engine) is None


# -- idade --------------------------------------------------------------------

def test_medicao_velha_se_declara_velha():
    agora = datetime(2026, 9, 21, tzinfo=timezone.utc)
    assert snap.vencida(agora - timedelta(days=snap.VALIDADE_DIAS + 1), agora)
    assert not snap.vencida(agora - timedelta(days=1), agora)


def test_rotulo_de_idade_sai_da_medicao_e_nao_de_frase_fixa():
    agora = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    assert snap.rotulo_idade(agora - timedelta(seconds=30), agora) == "agora ha pouco"
    assert snap.rotulo_idade(agora - timedelta(minutes=1, seconds=40), agora) == "ha 1 minuto"
    assert snap.rotulo_idade(agora - timedelta(hours=3), agora) == "ha 3 horas"
    assert snap.rotulo_idade(agora - timedelta(days=1), agora) == "ha 1 dia"
    assert snap.rotulo_idade(agora - timedelta(days=22), agora) == "ha 22 dias"


def test_medido_em_ingenuo_nao_quebra_a_conta_da_idade():
    """Postgres pode devolver o instante sem fuso; a idade não pode levantar."""
    agora = datetime(2026, 9, 21, tzinfo=timezone.utc)
    assert snap.idade(datetime(2026, 9, 20), agora) == timedelta(days=1)


# -- o portão -----------------------------------------------------------------

def _funcoes_da_view() -> dict[str, ast.FunctionDef]:
    arvore = ast.parse(VIEW.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef)}


def _chamadas(no: ast.AST) -> set[str]:
    nomes = set()
    for filho in ast.walk(no):
        if isinstance(filho, ast.Call):
            alvo = filho.func
            if isinstance(alvo, ast.Name):
                nomes.add(alvo.id)
            elif isinstance(alvo, ast.Attribute):
                nomes.add(alvo.attr)
    return nomes


def test_abrir_a_aba_nao_dispara_a_medicao():
    """O teste que prova o pedido.

    Estrutural de propósito: o custo não está num valor de retorno que dê para
    observar, está em QUEM chama. Um teste de comportamento que passasse com
    ``relatorio`` mockado não distinguiria a tela que mede da tela que lê.
    """
    funcoes = _funcoes_da_view()
    assert "relatorio" not in _chamadas(funcoes["render_corpo"]), (
        "render_corpo voltou a medir ao ser aberta; a medicao so pode sair "
        "de _medir_e_gravar, atras do botao"
    )
    assert "relatorio" in _chamadas(funcoes["_medir_e_gravar"])
    assert "carregar_ultimo" in _chamadas(funcoes["render_corpo"])


def test_a_medicao_so_acontece_dentro_de_um_if_com_botao():
    """Chamar ``_medir_e_gravar`` fora de um ``if st.button`` reabriria o buraco."""
    render = _funcoes_da_view()["render_corpo"]
    guardados = [no for no in ast.walk(render)
                 if isinstance(no, ast.If) and "_medir_e_gravar" in _chamadas(no)
                 and "button" in _chamadas(no.test)]
    assert guardados, "a medicao precisa estar atras de um st.button"
    total = sum(1 for no in ast.walk(render)
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id == "_medir_e_gravar")
    assert total == sum(
        1 for guarda in guardados for no in ast.walk(guarda)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
        and no.func.id == "_medir_e_gravar"), (
        "ha chamada de _medir_e_gravar fora do if do botao")
