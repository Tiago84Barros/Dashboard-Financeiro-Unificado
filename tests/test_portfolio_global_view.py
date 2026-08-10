"""Secao Portfolio Global: roteamento, estado vazio e montagem."""
import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from core.global_portfolio.aggregate import montar_posicoes
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


def test_fracao_para_percentual_converte_dy_para_pontos_percentuais():
    """Regressao: dy_consolidado devolve fracao (contrato de MetricaAgregada),
    e o cartao de DY exibia essa fracao direto com sufixo '%' — 0,1307 virava
    '0,13%' em vez de '13,07%'. A conversao precisa acontecer na borda de
    exibicao, sem tocar core/global_portfolio/metrics.py.
    """
    assert portfolio_global.fracao_para_percentual(0.1307) is not None
    valor = portfolio_global.fracao_para_percentual(0.1307)
    assert round(valor, 2) == 13.07
    assert portfolio_global._fmt(valor, "%", casas=2) == "13,07%"


def test_fracao_para_percentual_preserva_none():
    assert portfolio_global.fracao_para_percentual(None) is None


def test_cartao_de_dy_usa_fracao_para_percentual():
    """Regressao: garante que o call site do cartao de DY passou a converter
    a fracao antes de formatar, e nao voltou a exibir o valor cru.
    """
    import inspect
    fonte = inspect.getsource(portfolio_global._cards_de_metricas)
    assert "fracao_para_percentual(dy.valor)" in fonte


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


def test_texto_de_apoio_do_ativo_inclui_valor_quando_presente():
    texto = portfolio_global.texto_de_apoio_do_ativo(
        "Petrobras", "Empresas B3", "Energia", 12345.0,
    )
    assert texto == "Petrobras · Empresas B3 · Energia · R$ 12.345"


def test_texto_de_apoio_do_ativo_omite_valor_quando_none():
    """Regressao: valor_brl ausente e falta de patrimonio total informado, nao
    zero — o card nao pode mostrar travessao no lugar de um valor que nunca
    foi calculado.
    """
    texto = portfolio_global.texto_de_apoio_do_ativo(
        "Petrobras", "Empresas B3", "Energia", None,
    )
    assert texto == "Petrobras · Empresas B3 · Energia"
    assert "R$" not in texto


def test_texto_de_apoio_do_ativo_sem_nome_mostra_travessao():
    texto = portfolio_global.texto_de_apoio_do_ativo(None, "Empresas B3", "Energia", None)
    assert texto == "— · Empresas B3 · Energia"


def test_aba_por_ativo_usa_cartoes_em_vez_de_dataframe():
    """Regressao: informacao nunca solta em tabela — a regra do projeto e
    cartoes CSS (card_metrica). 'Por setor' e 'Por pais' continuam tabela
    porque sao agregados de verdade, nao uma lista de ativos individuais.
    """
    import inspect
    fonte_tabelas = inspect.getsource(portfolio_global._tabelas)
    assert "_cards_de_ativos(df)" in fonte_tabelas
    assert "st.dataframe" not in inspect.getsource(portfolio_global._cards_de_ativos)
    assert "card_metrica" in inspect.getsource(portfolio_global._cards_de_ativos)


def test_setores_sem_mapa_fica_silencioso_quando_tudo_mapeia():
    """Regressao do defeito: nao_mapeados espera o setor BRUTO (contrato da
    funcao), mas a view chamava com `sector` — a coluna JA canonica que
    montar_posicoes produz. `setor_canonico("b3", "consumer")` nao acha
    "consumer" entre os rotulos de origem da B3 e devolve 'other': quase todo
    ativo virava "sem mapeamento", mesmo mapeado com sucesso. Construir a
    carteira via montar_posicoes (nao a mao) e o que expõe o bug: um dict
    manual com "sector" ja no formato certo mascara a confusao raw/canonico.
    """
    snaps = {
        "b3": {
            "PETR4": {"identity": {"symbol": "PETR4", "name": "Petrobras",
                                   "sector": "Petróleo, Gás e Biocombustíveis"},
                      "metrics": {"weight": 1.0}},
        },
        "us": {
            "AAPL": {"identity": {"symbol": "AAPL", "name": "Apple",
                                  "sector": "Tecnologia"},
                     "metrics": {"weight": 1.0}},
        },
    }
    df = montar_posicoes(snaps, {"b3": 0.5, "us": 0.5})
    assert portfolio_global._setores_sem_mapa(df) == []


def test_setores_sem_mapa_reporta_setor_genuinamente_desconhecido():
    """Um setor que nao existe em nenhum vocabulario de origem precisa
    continuar aparecendo — o aviso nao pode virar um silencio cego.
    """
    snaps = {
        "b3": {
            "XYZ3": {"identity": {"symbol": "XYZ3", "name": "Fantasia",
                                  "sector": "Setor Inexistente"},
                     "metrics": {"weight": 1.0}},
        },
    }
    df = montar_posicoes(snaps, {"b3": 1.0})
    assert portfolio_global._setores_sem_mapa(df) == [("b3", "Setor Inexistente")]


def test_setores_sem_mapa_nao_acusa_o_fallback_deliberado_do_modulo_eua():
    """'Outros setores' e o proprio modulo EUA dizendo que a fonte nao tinha
    setor determinavel (core/market_companies.py::translate_us_sector,
    linha 302) — nao e uma lacuna do nosso mapa, e sim ausencia de dado na
    origem. Listar sob "sem mapeamento canonico" seria enganoso: o mapeamento
    funcionou, so nao havia o que mapear.
    """
    snaps = {
        "us": {
            "ZZZZ": {"identity": {"symbol": "ZZZZ", "name": "Desconhecida",
                                  "sector": "Outros setores"},
                     "metrics": {"weight": 1.0}},
        },
    }
    df = montar_posicoes(snaps, {"us": 1.0})
    assert portfolio_global._setores_sem_mapa(df) == []


def test_aviso_de_cobertura_nomeia_os_ativos_de_fora():
    from core.global_portfolio.returns import Cobertura
    cob = Cobertura(("PETR4",), ("AAPL", "MSFT"), 0.615, 60)
    msg = portfolio_global.aviso_de_cobertura(cob)
    assert "AAPL" in msg and "MSFT" in msg
    assert "38" in msg or "38,5" in msg


def test_aviso_de_cobertura_silencia_quando_tudo_coberto():
    from core.global_portfolio.returns import Cobertura
    assert portfolio_global.aviso_de_cobertura(Cobertura(("PETR4",), (), 1.0, 60)) is None


def test_aviso_de_cobertura_com_cobertura_zero_orienta():
    from core.global_portfolio.returns import Cobertura
    msg = portfolio_global.aviso_de_cobertura(Cobertura((), ("AAPL",), 0.0, 0))
    assert msg is not None and "AAPL" in msg


def test_tabela_de_pares_redundantes_preserva_a_ordem_e_arredonda():
    """A ordenacao ja vem de correlation.pares_redundantes; a funcao de
    exibicao nao pode reordenar, so formatar.
    """
    pares = [("PETR4", "VALE3", 0.912345), ("ITUB4", "BBDC4", 0.856)]
    tabela = portfolio_global._tabela_de_pares_redundantes(pares)
    assert list(tabela["Ativo 1"]) == ["PETR4", "ITUB4"]
    assert list(tabela["Ativo 2"]) == ["VALE3", "BBDC4"]
    assert tabela["Correlação"].iloc[0] == 0.91


def test_tabela_de_exposicoes_marca_significancia_visivelmente():
    """Beta fraco (erro-padrao grande) precisa ficar marcado como 'Não', nao
    ficar indistinguivel de um beta significativo so pela cor.
    """
    from core.global_portfolio.factors import Exposicao

    sig = Exposicao(fator="mercado_br", beta=0.527, erro_padrao=0.05, r2=0.90, n_obs=31)
    fraco = Exposicao(fator="small_cap", beta=0.227, erro_padrao=0.117, r2=0.10, n_obs=31)
    tabela = portfolio_global._tabela_de_exposicoes([sig, fraco])

    assert tabela.loc[tabela["Fator"] == "Mercado brasileiro", "Significativo"].iloc[0] == "✅ Sim"
    assert tabela.loc[tabela["Fator"] == "Small caps", "Significativo"].iloc[0] == "⚠️ Não"


def test_tabela_de_exposicoes_traduz_o_rotulo_do_fator():
    from core.global_portfolio.factors import Exposicao

    exp = Exposicao(fator="juros_nominais", beta=0.1, erro_padrao=0.2, r2=0.05, n_obs=25)
    tabela = portfolio_global._tabela_de_exposicoes([exp])
    assert tabela["Fator"].iloc[0] == "Juros nominais"


def test_paineis_de_risco_correlacao_e_fatores_sao_chamados_no_render():
    """Regressao: garante que os tres paineis desta fase realmente entram no
    fluxo de render() em vez de ficarem so definidos e nunca chamados —
    mesmo risco que motivou 'Diagnostico precisa de porta de entrada'.
    """
    import inspect
    fonte = inspect.getsource(portfolio_global.render)
    assert "_painel_correlacao(" in fonte
    assert "_painel_fatores(" in fonte
    assert "_painel_risco(" in fonte
    assert "retornos_mensais(" in fonte
    assert "aviso_de_cobertura(" in fonte


def test_render_chama_retornos_mensais_uma_unica_vez():
    """Regra do plano: 'uma unica chamada a retornos_mensais por render'."""
    import inspect
    fonte = inspect.getsource(portfolio_global.render)
    assert fonte.count("retornos_mensais(") == 1


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
