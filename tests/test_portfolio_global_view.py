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


class _OrigFake(Exception):
    """Substituto do erro do driver DBAPI (psycopg2), com pgcode como o real.

    psycopg2.errors.UndefinedTable so tem pgcode preenchido quando o erro vem
    de uma conexao de verdade (o atributo e escrito pela extensao C); nos
    testes o substituto precisa carregar o mesmo dado — SQLSTATE — do jeito
    que o codigo de producao vai le-lo: `exc.orig.pgcode`.
    """

    def __init__(self, msg: str, pgcode: str | None = None):
        super().__init__(msg)
        self.pgcode = pgcode


def test_erro_de_tabela_ausente_no_postgres_por_pgcode_vira_orientacao():
    """Regressao: schema 049 nao aplicado nao pode aparecer como erro cru.

    load_allocation_targets consulta portfolio_allocation_targets; se o
    schema 049 nao foi rodado no Supabase, a tabela nao existe e o Postgres
    levanta ProgrammingError com pgcode 42P01 (undefined_table). Isso e o
    primeiro-uso esperado, nao uma falha de verdade: o usuario deve ver a
    orientacao do backfill.
    """
    exc = ProgrammingError(
        "SELECT 1", {},
        _OrigFake('relation "portfolio_allocation_targets" does not exist', "42P01"),
    )
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg == portfolio_global.MSG_SEM_SNAPSHOT


def test_erro_de_tabela_ausente_no_sqlite_vira_orientacao_de_backfill():
    """SQLite nao tem pgcode; o sinal e so a mensagem 'no such table'."""
    exc = OperationalError(
        "SELECT 1", {}, Exception("no such table: portfolio_allocation_targets"),
    )
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg == portfolio_global.MSG_SEM_SNAPSHOT


def test_operational_error_de_conexao_recusada_mantem_a_mensagem_crua():
    """Regressao do re-review: no psycopg2, OperationalError e a categoria de
    falha de CONEXAO, nao de tabela ausente. Um Supabase fora do ar nao pode
    virar "rode o schema 049" — isso seria pior que o erro cru, porque
    parece uma resposta confiante e esta errada.
    """
    exc = OperationalError(
        "SELECT 1", {},
        Exception(
            'connection to server at "db.supabase.co" (1.2.3.4), port 5432 '
            "failed: Connection refused"
        ),
    )
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg != portfolio_global.MSG_SEM_SNAPSHOT
    assert "Connection refused" in msg


def test_operational_error_de_autenticacao_mantem_a_mensagem_crua():
    """Mesma regressao do re-review, para credencial invalida."""
    exc = OperationalError(
        "SELECT 1", {},
        Exception('FATAL: password authentication failed for user "postgres"'),
    )
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg != portfolio_global.MSG_SEM_SNAPSHOT
    assert "password authentication failed" in msg


def test_erro_generico_mantem_a_mensagem_crua():
    """Uma excecao qualquer, sem relacao com o banco, tambem fica crua."""
    exc = RuntimeError("algo inesperado")
    msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg != portfolio_global.MSG_SEM_SNAPSHOT
    assert "algo inesperado" in msg


def test_rotulo_maior_traduz_setor_via_rotulos():
    assert portfolio_global.rotulo_maior("sector", "consumer") == "Consumo Cíclico"


def test_rotulo_maior_traduz_classe_via_registry():
    """Regressao: 'Classes efetivas' mostrava a chave crua ('b3'), nao o
    rotulo de exibicao ('Empresas B3') que o resto do app usa.
    """
    assert portfolio_global.rotulo_maior("asset_class", "b3") == "Empresas B3"
    assert portfolio_global.rotulo_maior("asset_class", "us") == "Empresas Americanas"
    assert portfolio_global.rotulo_maior("asset_class", "fii") == "FIIs"


def test_rotulo_maior_classe_desconhecida_nao_propaga_keyerror():
    """get_spec levanta KeyError para chave desconhecida; a exibicao nao pode quebrar."""
    assert portfolio_global.rotulo_maior("asset_class", "cripto") == "cripto"


def test_rotulo_maior_pais_e_moeda_ficam_como_estao():
    assert portfolio_global.rotulo_maior("country", "BR") == "BR"
    assert portfolio_global.rotulo_maior("currency", "USD") == "USD"


def test_rotulo_maior_com_chave_none_mostra_travessao():
    """maior_nome e None quando o frame de origem esta vazio."""
    assert portfolio_global.rotulo_maior("sector", None) == "—"
    assert portfolio_global.rotulo_maior("asset_class", None) == "—"


def test_rotulo_maior_setor_sem_mapa_mantem_a_chave_crua():
    assert portfolio_global.rotulo_maior("sector", "algo_nao_mapeado") == "algo_nao_mapeado"


def test_top_ns_a_exibir_carteira_de_um_ativo_mostra_so_top1():
    assert portfolio_global.top_ns_a_exibir(1) == [1]


def test_top_ns_a_exibir_carteira_de_quatro_ativos_omite_top5_e_top10():
    """Top 5 e Top 10 saturariam no mesmo valor (100%) que Top 4 posicoes
    inteiras somariam — mostrar os dois seria redundante.
    """
    assert portfolio_global.top_ns_a_exibir(4) == [1, 3]


def test_top_ns_a_exibir_carteira_de_trinta_ativos_mostra_os_quatro():
    assert portfolio_global.top_ns_a_exibir(30) == [1, 3, 5, 10]


def test_top_ns_a_exibir_carteira_vazia_nao_quebra():
    assert portfolio_global.top_ns_a_exibir(0) == [1]


def test_qualidade_gini_e_top_n_sao_de_fato_usados_na_view():
    """Regressao: concentration.top_n e concentration.gini existiam e eram
    testados no modulo de calculo mas nunca chamados pela tela.
    """
    import inspect
    fonte = inspect.getsource(portfolio_global)
    assert "concentration.top_n(" in fonte
    assert "concentration.gini(" in fonte


def test_load_allocation_targets_falhando_por_schema_ausente_aciona_a_orientacao(monkeypatch):
    """Ponta a ponta da decisao (sem Streamlit): a falha real que o primeiro
    acesso provoca — load_allocation_targets contra uma tabela que o schema
    049 ainda nao criou — precisa resultar na mensagem de backfill, nao na
    excecao crua.
    """
    def fake_load(*, engine=None, owner_id=None):
        raise ProgrammingError(
            "SELECT 1", {},
            _OrigFake('relation "portfolio_allocation_targets" does not exist', "42P01"),
        )

    monkeypatch.setattr(portfolio_global, "load_allocation_targets", fake_load)
    try:
        portfolio_global.load_allocation_targets()
        assert False, "deveria ter levantado ProgrammingError"
    except Exception as exc:  # noqa: BLE001 - espelha o except da render()
        msg = portfolio_global.mensagem_de_erro_ao_carregar(exc)
    assert msg == portfolio_global.MSG_SEM_SNAPSHOT
