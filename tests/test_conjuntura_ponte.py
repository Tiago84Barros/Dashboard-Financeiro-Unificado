"""Testes da porta de entrada conjuntural, contra Postgres de verdade.

Por que não dublê: o que ``_ler_noticias`` faz é quase tudo SQL — um
``CROSS JOIN LATERAL jsonb_array_elements_text`` para resolver o ticker dentro
do JSONB, um ``LEFT JOIN LATERAL`` que escolhe a avaliação mais recente **até o
corte**, e três filtros point-in-time (``coletado_em``, ``publicado_em``,
``avaliado_em``). Um dublê validaria o piso de amostra e a média ponderada, e
deixaria passar exatamente a metade que decide quais linhas chegam à média.

O schema é descartável e o arquivo **pula** se o armazém não estiver no ar,
porque pular é dizer que não foi medido — o oposto de passar sem ter testado.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from core.conjuntura import ponte as P

SCHEMA = "app4_conjuntura_teste"

CORTE = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.noticias_itens (
        id_dedup TEXT PRIMARY KEY,
        titulo TEXT NOT NULL,
        veiculo TEXT,
        url TEXT,
        publicado_em TIMESTAMPTZ,
        coletado_em TIMESTAMPTZ NOT NULL,
        entidades JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        tipo_evento TEXT,
        sentimento_api NUMERIC(5,4),
        sentimento_app4 NUMERIC(5,4)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.noticias_avaliacoes (
        id_dedup TEXT NOT NULL,
        versao_metodologia TEXT NOT NULL,
        nota NUMERIC(5,2),
        direcao TEXT,
        confianca NUMERIC(5,4),
        avaliado_em TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (id_dedup, versao_metodologia)
    )
    """,
]


@pytest.fixture(scope="module")
def engine():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(
            _warehouse_url(),
            connect_args={"options": f"-csearch_path={SCHEMA},public"})
        with motor.begin() as conn:
            for ddl in _DDL:
                conn.execute(text(ddl))
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


@pytest.fixture(autouse=True)
def _limpar(engine):
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {SCHEMA}.noticias_avaliacoes"))
        conn.execute(text(f"TRUNCATE {SCHEMA}.noticias_itens"))
    yield


def _inserir(engine, id_dedup, ticker, *, publicado=None, coletado=None,
             veiculo="Valor Econômico", sentimento=None, titulo=None):
    with engine.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {SCHEMA}.noticias_itens
                (id_dedup, titulo, veiculo, url, publicado_em, coletado_em,
                 entidades, tipo_evento, sentimento_app4)
            VALUES (:i, :t, :v, :u, :p, :c, CAST(:e AS jsonb), :te, :s)
        """), {
            "i": id_dedup, "t": titulo or f"Notícia {id_dedup}", "v": veiculo,
            "u": f"https://exemplo/{id_dedup}",
            "p": publicado or (CORTE - timedelta(days=2)),
            "c": coletado or (CORTE - timedelta(days=2)),
            "e": '{"tickers": ["%s"], "setores": []}' % ticker,
            "te": "resultado_trimestral", "s": sentimento,
        })


def _avaliar(engine, id_dedup, *, nota=80.0, direcao="baixa", confianca=0.9,
             avaliado=None, versao="v1"):
    with engine.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO {SCHEMA}.noticias_avaliacoes
                (id_dedup, versao_metodologia, nota, direcao, confianca, avaliado_em)
            VALUES (:i, :vm, :n, :d, :c, :a)
        """), {"i": id_dedup, "vm": versao, "n": nota, "d": direcao,
               "c": confianca, "a": avaliado or (CORTE - timedelta(days=1))})


# ── o piso de amostra ────────────────────────────────────────────────────────

def test_duas_noticias_nao_formam_leitura(engine):
    """Abaixo do piso é ausência, não leitura fraca: sai None com o tamanho."""
    for i in range(2):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}")
    leituras = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                               janela_dias=30)
    leitura = leituras["PETR4"]
    assert leitura.valor is None
    assert leitura.n_itens == 2
    assert "mínimo de 3" in leitura.motivo


def test_tres_noticias_formam_leitura(engine):
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}", direcao="baixa", nota=80.0)
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert leitura.n_itens == 3
    assert leitura.valor == pytest.approx(-80.0)


def test_ativo_sem_nenhuma_noticia_sai_none_e_nao_zero(engine):
    """Zero é uma leitura ("noticiário equilibrado"); vazio não é leitura."""
    leitura = P._ler_noticias(engine, simbolos=["VALE3"], as_of=CORTE,
                              janela_dias=30)["VALE3"]
    assert leitura.valor is None
    assert leitura.n_itens == 0


# ── os filtros point-in-time ─────────────────────────────────────────────────

def test_item_coletado_depois_do_corte_nao_entra(engine):
    """Publicado antes, coletado depois: no dia do corte ninguém o tinha."""
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}")
    _inserir(engine, "futuro", "PETR4",
             publicado=CORTE - timedelta(days=1),
             coletado=CORTE + timedelta(days=5))
    _avaliar(engine, "futuro")
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert leitura.n_itens == 3


def test_item_fora_da_janela_nao_entra(engine):
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4",
                 publicado=CORTE - timedelta(days=90),
                 coletado=CORTE - timedelta(days=90))
        _avaliar(engine, f"n{i}")
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert leitura.n_itens == 0


def test_reavaliacao_posterior_ao_corte_nao_vaza_para_tras(engine):
    """A avaliação vigente é a mais recente ATÉ o corte, não a mais recente."""
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}", direcao="baixa", nota=80.0, versao="v1",
                 avaliado=CORTE - timedelta(days=1))
        # Revisão feita depois do corte inverteria o sinal, se vazasse.
        _avaliar(engine, f"n{i}", direcao="alta", nota=90.0, versao="v2",
                 avaliado=CORTE + timedelta(days=10))
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert leitura.valor == pytest.approx(-80.0), "a revisão futura vazou"


# ── como o valor é formado ───────────────────────────────────────────────────

def test_direcao_avaliada_tem_precedencia_sobre_sentimento(engine):
    """Texto otimista sobre fato diluidor não pode virar componente positivo."""
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4", sentimento=0.9)
        _avaliar(engine, f"n{i}", direcao="baixa", nota=100.0, confianca=1.0)
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert leitura.valor == pytest.approx(-100.0)


def test_item_sem_direcao_e_sem_sentimento_conta_amostra_mas_nao_valor(engine):
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4", sentimento=None)
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert leitura.n_itens == 3
    assert leitura.valor is None
    assert "nenhum com direção" in leitura.motivo


def test_um_ticker_nao_contamina_o_outro(engine):
    for i in range(3):
        _inserir(engine, f"p{i}", "PETR4")
        _avaliar(engine, f"p{i}", direcao="baixa")
    for i in range(3):
        _inserir(engine, f"v{i}", "VALE3")
        _avaliar(engine, f"v{i}", direcao="alta")
    leituras = P._ler_noticias(engine, simbolos=["PETR4", "VALE3"],
                               as_of=CORTE, janela_dias=30)
    assert leituras["PETR4"].valor < 0 < leituras["VALE3"].valor


def test_procedencia_traz_veiculo_e_hora(engine):
    _inserir(engine, "n0", "PETR4", veiculo="Reuters",
             publicado=datetime(2026, 6, 28, 9, 30, tzinfo=timezone.utc))
    leitura = P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE,
                              janela_dias=30)["PETR4"]
    assert "Reuters" in leitura.itens[0].procedencia
    assert "28/06/2026" in leitura.itens[0].procedencia


# ── falha tem que parecer falha ──────────────────────────────────────────────

def test_tabela_ausente_levanta_em_vez_de_devolver_vazio(engine, monkeypatch):
    monkeypatch.setattr(P, "_SQL_NOTICIAS", "SELECT * FROM tabela_que_nao_existe")
    with pytest.raises(P.AcervoIndisponivel):
        P._ler_noticias(engine, simbolos=["PETR4"], as_of=CORTE, janela_dias=30)


def test_falha_de_leitura_nao_vira_acervo_vazio(engine, monkeypatch):
    """Banco fora do ar não pode publicar a mesma leitura que um mês calmo."""
    monkeypatch.setattr(P, "_SQL_NOTICIAS", "SELECT * FROM tabela_que_nao_existe")
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, noticias_engine=engine)
    assert ctx.acervo_falhou is True
    assert any("não pôde ser lido" in lim for lim in ctx.limitacoes)
    texto = P.para_llm(ctx)
    assert "NÃO PÔDE SER LIDO" in texto
    # A única ocorrência permitida da frase é a instrução que a proíbe.
    assert "não escreva que não houve notícias" in texto
    assert texto.count("não houve notícias") == 1


def test_limitacao_de_falha_nao_despeja_o_sql_inteiro(engine, monkeypatch):
    monkeypatch.setattr(P, "_SQL_NOTICIAS", "SELECT * FROM tabela_que_nao_existe")
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, noticias_engine=engine)
    falha = next(lim for lim in ctx.limitacoes if "não pôde ser lido" in lim)
    assert "\n" not in falha and "SELECT" not in falha


def test_acervo_vazio_e_falha_produzem_textos_diferentes(engine, monkeypatch):
    vazio = P.para_llm(P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                                  as_of=CORTE, noticias_engine=engine))
    monkeypatch.setattr(P, "_SQL_NOTICIAS", "SELECT * FROM tabela_que_nao_existe")
    falho = P.para_llm(P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                                  as_of=CORTE, noticias_engine=engine))
    assert vazio != falho
    assert "ausência de coleta" in vazio
    assert "ausência de coleta" not in falho


# ── o encaixe nos motores que já existiam ────────────────────────────────────

def test_cobertura_rala_nao_move_prioridade(engine, monkeypatch):
    """Só macro (peso 0,20) fica abaixo de COBERTURA_MINIMA (0,50)."""
    from core.macro_data import portfolio_context as pc

    def _falso(*_a, **_k):
        return pc.PortfolioMacroSnapshot(
            impacts={"PETR4": -90.0}, details=(), as_of=CORTE,
            asset_count=1, covered_assets=1, source_count=5)

    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot", _falso)
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, macro_engine=object(), noticias_engine=engine)
    assert ctx.impactos_macro == {"PETR4": -90.0}
    assert ctx.move_prioridade is False
    assert ctx.bloqueios == {} and ctx.prioridades == {}
    assert "noticias" in ctx.componentes_ausentes
    assert "memoria_mercado" in ctx.componentes_ausentes


def test_com_macro_e_noticias_a_cobertura_passa_e_a_prioridade_anda(engine,
                                                                    monkeypatch):
    """Com dois componentes (0,55) o motor volta a decidir — sem mudar código."""
    from core.macro_data import portfolio_context as pc

    def _falso(*_a, **_k):
        return pc.PortfolioMacroSnapshot(
            impacts={"PETR4": -95.0}, details=(), as_of=CORTE,
            asset_count=1, covered_assets=1, source_count=5)

    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot", _falso)
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}", direcao="baixa", nota=100.0, confianca=1.0)
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, macro_engine=object(), noticias_engine=engine)
    assert "noticias" in ctx.componentes_disponiveis
    assert ctx.move_prioridade is True
    assert "PETR4" in ctx.bloqueios


def test_nenhuma_decisao_reduz_posicao(engine, monkeypatch):
    """O teto do pacote. Vale mesmo com a conjuntura no pior valor possível."""
    from core.macro_data import portfolio_context as pc

    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot", lambda *a, **k:
                        pc.PortfolioMacroSnapshot(
                            impacts={"PETR4": -100.0}, details=(), as_of=CORTE,
                            asset_count=1, covered_assets=1, source_count=5))
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}", direcao="baixa", nota=100.0, confianca=1.0)
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, macro_engine=object(), noticias_engine=engine)
    assert all(not d.altera_posicao_existente for d in ctx.decisoes)


def test_chaves_do_plano_batem_com_a_assinatura_de_plano_de_aporte(engine):
    """Trocar bloqueio por prioridade passaria despercebido; aqui não passa."""
    from core.aporte import plano_de_aporte

    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, noticias_engine=engine)
    argumentos = P.para_plano_de_aporte(ctx)
    parametros = inspect.signature(plano_de_aporte).parameters
    assert set(argumentos) <= set(parametros)
    assert "bloqueios_conjunturais" in parametros
    assert "prioridades" in parametros


def test_modo_reconstruido_carrega_o_selo_ex_post(engine, monkeypatch):
    """Peso histórico é possível, mas não pode passar por captura do dia."""
    from core.macro_data import portfolio_context as pc

    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot", lambda *a, **k:
                        pc.PortfolioMacroSnapshot(
                            impacts={"PETR4": 10.0}, details=(), as_of=CORTE,
                            asset_count=1, covered_assets=1, source_count=5,
                            limitations=("histórico reconstruído ex post; não "
                                         "equivale a captura disponível no dia",),
                            knowledge_mode="reconstructed"))
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, knowledge_mode="reconstructed",
                     macro_engine=object(), noticias_engine=engine)
    assert any("ex post" in lim for lim in ctx.limitacoes)
    assert "ex post" in P.para_llm(ctx)


def test_grao_incompativel_e_erro_e_nao_no_op(engine, monkeypatch):
    """Conjuntura por ticker contra plano por classe: tem que doer, nao calar."""
    from core.macro_data import portfolio_context as pc

    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot", lambda *a, **k:
                        pc.PortfolioMacroSnapshot(
                            impacts={"PETR4": -95.0}, details=(), as_of=CORTE,
                            asset_count=1, covered_assets=1, source_count=5))
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}", direcao="baixa", nota=100.0, confianca=1.0)
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, macro_engine=object(), noticias_engine=engine)
    assert ctx.bloqueios, "sem decisão ativa o teste não exercita o guarda"
    with pytest.raises(P.GraoIncompativel):
        P.para_plano_de_aporte(ctx, universo=["Renda variável BR", "FIIs"])


def test_grao_compativel_passa(engine, monkeypatch):
    from core.macro_data import portfolio_context as pc

    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot", lambda *a, **k:
                        pc.PortfolioMacroSnapshot(
                            impacts={"PETR4": -95.0}, details=(), as_of=CORTE,
                            asset_count=1, covered_assets=1, source_count=5))
    for i in range(3):
        _inserir(engine, f"n{i}", "PETR4")
        _avaliar(engine, f"n{i}", direcao="baixa", nota=100.0, confianca=1.0)
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, macro_engine=object(), noticias_engine=engine)
    saida = P.para_plano_de_aporte(ctx, universo=["PETR4", "VALE3"])
    assert "PETR4" in saida["bloqueios_conjunturais"]


def test_sem_decisao_ativa_o_guarda_nao_reclama(engine):
    """Conjuntura vazia contra qualquer universo é compatível por vacuidade."""
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"},
                     as_of=CORTE, noticias_engine=engine)
    assert P.para_plano_de_aporte(ctx, universo=["Renda fixa"]) == {
        "bloqueios_conjunturais": {}, "prioridades": {}}
