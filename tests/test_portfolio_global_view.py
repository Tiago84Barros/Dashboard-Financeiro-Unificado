"""Secao Portfolio Global: roteamento, estado vazio e montagem."""
import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from views import portfolio_global


def test_a_rota_esta_registrada_no_app():
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert '"portfolio_global"' in fonte, "modulo nao registrado em _ROTAS"
    assert "Portfólio Global" in fonte, "rotulo ausente na sidebar"


def test_o_modulo_expoe_render_sem_argumentos_obrigatorios():
    import inspect
    assinatura = inspect.signature(portfolio_global.render)
    obrigatorios = [p for p in assinatura.parameters.values()
                    if p.default is inspect.Parameter.empty]
    assert obrigatorios == []


def test_estado_vazio_sem_snapshot_orienta_o_backfill():
    msg = portfolio_global.estado_vazio({}, {})
    assert "049" in msg and "backfill_portfolio_snapshots" in msg


def test_estado_vazio_sem_alocacao_pede_o_alvo():
    snaps = {"b3": {"PETR4": {"identity": {"symbol": "PETR4"}}}}
    msg = portfolio_global.estado_vazio(snaps, {})
    assert "alocação-alvo" in msg


def test_sem_estado_vazio_quando_ha_snapshot_e_alvo():
    snaps = {"b3": {"PETR4": {"identity": {"symbol": "PETR4"}}}}
    assert portfolio_global.estado_vazio(snaps, {"b3": 1.0}) is None


def test_classe_com_dicionario_vazio_conta_como_sem_snapshot():
    assert portfolio_global.estado_vazio({"b3": {}, "us": {}}, {"b3": 1.0}) is not None


def test_detalhe_cobertura_avisa_quando_abaixo_do_minimo():
    from core.global_portfolio.metrics import MetricaAgregada
    baixa = MetricaAgregada(valor=10.0, cobertura=0.30, n_ativos=1)
    texto = portfolio_global.detalhe_cobertura(baixa)
    assert "⚠️" in texto and "30%" in texto


def test_detalhe_cobertura_sem_valor_diz_que_nao_ha_dado():
    from core.global_portfolio.metrics import MetricaAgregada
    vazia = MetricaAgregada(valor=None, cobertura=0.0, n_ativos=0)
    assert "sem dado" in portfolio_global.detalhe_cobertura(vazia)


def test_a_view_nao_reimplementa_o_card_do_projeto():
    """Regressao: card_metrica ja existe em design/componentes.py.

    _kpi_html ja esta duplicado entre dashboard_geral.py e fiis.py com
    assinaturas divergentes; uma terceira copia pioraria o problema.
    """
    import inspect
    fonte = inspect.getsource(portfolio_global)
    assert "_kpi_html" not in fonte
    assert "card_metrica" in fonte


def test_valor_inicial_do_total_usa_o_total_salvo_em_vez_de_zero():
    """Regressao: o total salvo nao pode sumir a cada reabertura do formulario.

    _editor_de_alocacao so recebia `alvos`, nunca `alocacao["total_brl"]`, e o
    campo do formulario vinha hardcoded em `value=0.0`. Resultado: reabrir o
    formulario para ajustar uma classe e salvar apagava o total ja persistido
    (`total or None` mandava None). O valor inicial do widget precisa ser o
    total carregado, nao zero.
    """
    assert portfolio_global._valor_inicial_total(15000.0) == 15000.0
    assert portfolio_global._valor_inicial_total(None) == 0.0


def test_carregar_snapshots_usa_o_modelo_ativo_de_cada_classe(monkeypatch):
    chamadas = []

    def fake(classe, *, engine=None, owner_id=None):
        chamadas.append(classe)
        return {"X": {"identity": {"symbol": "X"}}} if classe == "b3" else {}

    monkeypatch.setattr(portfolio_global, "load_active_snapshots", fake)
    saida = portfolio_global.carregar_snapshots()
    assert sorted(chamadas) == ["b3", "fii", "us"]
    assert set(saida) == {"b3", "fii", "us"}
    assert set(saida["b3"]) == {"X"}


def test_erro_de_tabela_ausente_no_postgres_vira_orientacao_de_backfill():
    """Regressao: schema 049 nao aplicado nao pode aparecer como erro cru.

    load_allocation_targets consulta portfolio_allocation_targets; se o
    schema 049 nao foi rodado no Supabase, a tabela nao existe e o Postgres
    levanta ProgrammingError. Isso e o primeiro-uso esperado, nao uma falha
    de verdade: o usuario deve ver a orientacao do backfill, nao "relation
    does not exist".
    """
    exc = ProgrammingError(
        "SELECT 1", {},
        Exception('relation "portfolio_allocation_targets" does not exist'),
    )
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg == portfolio_global.MSG_SEM_SNAPSHOT


def test_erro_de_tabela_ausente_no_sqlite_vira_orientacao_de_backfill():
    """Mesmo caso do teste acima, mas para o driver SQLite (usado em testes)."""
    exc = OperationalError(
        "SELECT 1", {}, Exception("no such table: portfolio_allocation_targets"),
    )
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg == portfolio_global.MSG_SEM_SNAPSHOT


def test_erro_generico_mantem_a_mensagem_crua():
    """Uma falha de conexao real precisa continuar visivel, nao disfarcada."""
    exc = RuntimeError("connection refused")
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg != portfolio_global.MSG_SEM_SNAPSHOT
    assert "connection refused" in msg


def test_load_allocation_targets_falhando_por_schema_ausente_aciona_a_orientacao(monkeypatch):
    """Ponta a ponta da decisao (sem Streamlit): a falha real que o primeiro
    acesso provoca — load_allocation_targets contra uma tabela que o schema
    049 ainda nao criou — precisa resultar na mensagem de backfill, nao na
    excecao crua.
    """
    def fake_load(*, engine=None, owner_id=None):
        raise ProgrammingError(
            "SELECT 1", {},
            Exception('relation "portfolio_allocation_targets" does not exist'),
        )

    monkeypatch.setattr(portfolio_global, "load_allocation_targets", fake_load)
    try:
        portfolio_global.load_allocation_targets()
        assert False, "deveria ter levantado ProgrammingError"
    except Exception as exc:  # noqa: BLE001 - espelha o except da render()
        msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg == portfolio_global.MSG_SEM_SNAPSHOT
