"""O Tesouro da carteira é marcado pela curva oficial, não pelo preço da foto.

A foto "Posição" da B3 carrega, para os títulos públicos, um PU de dias antes
da data do relatório. Em 26/10/2026 os prefixados e os IPCA+ caíram de 2 a 10%
num pregão, e a foto do dia seguinte continuou com o PU da semana anterior: a
carteira passou a afirmar um ganho que o próprio painel do Tesouro, marcado
pela curva, não reconhecia — e a distância crescia com o prazo do título.

A curva oficial (`tesouro_market_rates`) é o preço do emissor. Quem já tem o
título vende ao Tesouro pela ponta de **recompra**, a mesma que a Evolução e o
painel do Tesouro já usam. Os números abaixo são sintéticos.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import partial
from types import SimpleNamespace

import pytest

from core import investimentos, tesouro_curva
from core.investimentos import _montar_carteira_snapshot
from core.market_freshness import MAX_QUOTE_AGE_DAYS, classificar_cotacao

_HOJE = date(2026, 10, 30)


@pytest.fixture(autouse=True)
def _hoje_fixo(monkeypatch):
    # As curvas daqui são do fim de outubro de 2026, e o frescor é medido
    # contra hoje: sem um hoje fixo, o teste apodrece com o calendário.
    monkeypatch.setattr(investimentos, "classificar_cotacao",
                        partial(classificar_cotacao, agora=_HOJE))


def _row(ticker, *, qty, vm, invested, tipo="tesouro", report=date(2026, 10, 27),
         nome=None):
    return SimpleNamespace(
        ticker=ticker, quantity=qty, market_value=vm, invested_value=invested,
        asset_type=tipo, asset_name=nome or ticker, sector="other", currency="BRL",
        country="BR", report_date=report, live_price=None, live_timestamp=None,
        usd_brl_rate=None, pp_quantity=0, pp_total_invested=0,
        pp_average_price=0, b3_avg_price=0, b3_quantity=0, b3_report_date=None,
        is_loaned=False, market_price=None,
    )


def _pos(rows, ticker, curva):
    carteira = _montar_carteira_snapshot(rows, curva_tesouro=curva)
    return carteira, next(p for p in carteira["posicoes"] if p["ticker"] == ticker)


def test_curva_mais_nova_que_a_foto_remarca_o_titulo():
    # Foto de 27/10 com o PU de antes do choque (866,67); curva de 28/10 a 850.
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]
    curva = {"TPRE2030": (850.0, date(2026, 10, 28))}

    carteira, p = _pos(rows, "TPRE2030", curva)

    assert p["preco_atual"] == 850.0
    assert p["valor_mercado"] == 2550.0
    assert p["rentab_pct"] == 27.5
    assert p["cotacao_fonte"] == "curva_tesouro"
    assert p["data_referencia"] == date(2026, 10, 28)
    # A foto continua dizendo de quando é a quantidade.
    assert p["snapshot_report_date"] == date(2026, 10, 27)
    assert carteira["total_mercado"] == 2550.0
    assert carteira["rentabilidade_total_pct"] == 27.5


def test_curva_do_mesmo_dia_da_foto_tambem_vale():
    # A foto da B3 é de 27/10 mas o PU dela é de antes; a curva de 27/10 é o
    # preço do próprio dia.
    rows = [_row("TIPCA2035", qty=2.0, vm=7000.0, invested=6000.0)]
    curva = {"TIPCA2035": (3300.0, date(2026, 10, 27))}

    _, p = _pos(rows, "TIPCA2035", curva)

    assert p["valor_mercado"] == 6600.0
    assert p["cotacao_fonte"] == "curva_tesouro"


def test_curva_mais_velha_que_a_foto_nao_desfaz_a_foto():
    # Job da curva parado: a foto é o número mais recente que existe.
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]
    curva = {"TPRE2030": (850.0, date(2026, 10, 20))}

    _, p = _pos(rows, "TPRE2030", curva)

    assert p["valor_mercado"] == 2600.0
    assert p["cotacao_fonte"] == "snapshot"
    assert p["data_referencia"] == date(2026, 10, 27)


def test_quantidade_zerada_pela_corretora_fica_com_o_valor_da_foto():
    # A XP já entregou título com quantidade arredondada para zero e valor
    # cheio. Quantidade vezes PU daria zero.
    rows = [_row("TSELIC2029", qty=0.0, vm=5400.0, invested=5000.0)]
    curva = {"TSELIC2029": (18000.0, date(2026, 10, 28))}

    _, p = _pos(rows, "TSELIC2029", curva)

    assert p["valor_mercado"] == 5400.0
    assert p["cotacao_fonte"] == "snapshot"


def test_papel_fora_da_curva_e_acao_nao_mudam():
    rows = [
        _row("CDB-BANCO-X", qty=1.0, vm=1100.0, invested=1000.0, tipo="fixed_income"),
        _row("PETR4", qty=10.0, vm=380.0, invested=300.0, tipo="stock"),
    ]
    # Mesmo que alguém ponha um ticker de ação no dicionário, ação não é
    # título público.
    curva = {"PETR4": (50.0, date(2026, 10, 28))}

    carteira = _montar_carteira_snapshot(rows, curva_tesouro=curva)
    por = {p["ticker"]: p for p in carteira["posicoes"]}

    assert por["CDB-BANCO-X"]["valor_mercado"] == 1100.0
    assert por["PETR4"]["valor_mercado"] == 380.0
    assert por["PETR4"]["cotacao_fonte"] == "snapshot"


def test_sem_curva_a_montagem_e_a_de_antes():
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]

    carteira = _montar_carteira_snapshot(rows)

    assert carteira["posicoes"][0]["valor_mercado"] == 2600.0


# ── leitura da curva ─────────────────────────────────────────────────────────

class _Savepoint:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.savepoints += 1
        return self

    def __exit__(self, *exc):
        return False


class _Conexao:
    def __init__(self, linhas=None, erro=None):
        self.linhas, self.erro = linhas or [], erro
        self.savepoints = 0
        self.params = None

    def begin_nested(self):
        return _Savepoint(self)

    def execute(self, stmt, params=None):
        if self.erro:
            raise self.erro
        self.params = params
        return SimpleNamespace(fetchall=lambda: list(self.linhas))


def test_pu_de_recompra_le_a_ponta_de_venda_dentro_de_savepoint():
    conn = _Conexao(linhas=[
        SimpleNamespace(security_key="TPRE2030", base_date=date(2026, 10, 28), pu=850.0),
    ])

    pus = tesouro_curva.pu_de_recompra(conn, ["TPRE2030", "TPRE2030", "TIPCA2035"])

    assert pus == {"TPRE2030": (850.0, date(2026, 10, 28))}
    assert conn.savepoints == 1
    assert sorted(conn.params["chaves"]) == ["TIPCA2035", "TPRE2030"]


def test_pu_de_recompra_sem_tabela_devolve_vazio():
    # Tabela ausente não pode derrubar a carteira: o SAVEPOINT impede que a
    # transação do chamador fique abortada, e a função devolve vazio.
    conn = _Conexao(erro=RuntimeError("relation tesouro_market_rates does not exist"))

    assert tesouro_curva.pu_de_recompra(conn, ["TPRE2030"]) == {}
    assert tesouro_curva.pu_de_recompra(conn, []) == {}


def test_sql_da_curva_usa_a_ponta_de_recompra():
    sql = " ".join(tesouro_curva.SQL_PU_DE_RECOMPRA.split())

    assert "COALESCE(sell_pu, base_pu)" in sql
    assert "DISTINCT ON (security_key)" in sql
    assert "base_date DESC" in sql


def test_carteira_consolidada_passa_a_curva_para_a_montagem(monkeypatch):
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0),
            _row("PETR4", qty=10.0, vm=380.0, invested=300.0, tipo="stock")]
    pedidas = []

    def _curva(conn, chaves):
        pedidas.append(sorted(chaves))
        return {"TPRE2030": (850.0, date(2026, 10, 28))}

    class _Conn:
        def execute(self, stmt, params=None):
            return SimpleNamespace(fetchall=lambda: [], scalar=lambda: True)

    monkeypatch.setattr(tesouro_curva, "pu_de_recompra", _curva)
    monkeypatch.setattr(investimentos, "_calcular_custos_transacoes", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "cambio_medio_de_aquisicao", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "listar_precos_manuais", lambda owner, conn: {})
    monkeypatch.setattr(investimentos.cotacao_viva, "buscar", lambda simbolos: {})

    carteira = investimentos._carteira_snapshot_consolidada(_Conn(), "u1", rows)

    # Só os títulos públicos vão à curva.
    assert pedidas == [["TPRE2030"]]
    p = next(p for p in carteira["posicoes"] if p["ticker"] == "TPRE2030")
    assert p["valor_mercado"] == 2550.0
    assert p["cotacao_fonte"] == "curva_tesouro"


def test_card_rotula_a_cotacao_pela_curva():
    from views.investimentos import _card_ativo

    pos = {
        "ticker": "TPRE2030", "nome": "Tesouro Prefixado 2030", "classe": "Tesouro Direto",
        "setor": "Tesouro Direto", "moeda": "BRL", "pais": "BR", "quantidade": 3.0,
        "preco_medio": 666.67, "total_investido": 2000.0, "custo_fonte": "snapshot",
        "cotacao_fonte": "curva_tesouro", "preco_atual": 850.0, "valor_mercado": 2550.0,
        "diferenca_reais": 550.0, "rentab_pct": 27.5, "rentab_moeda": "BRL",
        "pct_carteira": 100.0, "cor": "#000000", "data_referencia": date(2026, 10, 28),
    }

    html = _card_ativo(pos, 0.0)

    assert "curva do Tesouro" in html
    assert "snapshot" not in html.split("Cotação", 1)[1].split("</", 1)[0]


class _ConnEscalar:
    def __init__(self, valor):
        self.valor = valor

    def execute(self, *_a, **_k):
        return SimpleNamespace(scalar=lambda: self.valor)


@pytest.mark.parametrize("dias, esperado", [(2, 100.0), (15, 0.0), (30, 0.0)])
def test_grau_de_confianca_mede_a_idade_da_curva(dias, esperado):
    from datetime import timedelta

    from core.confianca_secao import _curva_do_tesouro

    comp = _curva_do_tesouro(_ConnEscalar(date.today() - timedelta(days=dias)))

    assert comp.nome == "Curva do Tesouro"
    assert comp.pct == pytest.approx(esperado)


def test_grau_de_confianca_sem_curva_vale_zero_e_nao_some():
    """Sem curva o número da tela continua de pé, só que velho: zero, não None."""
    from core.confianca_secao import _curva_do_tesouro

    comp = _curva_do_tesouro(_ConnEscalar(None))

    assert comp.pct == 0.0
    assert "foto da B3" in comp.evidencia


def test_grau_de_confianca_falha_na_leitura_vira_nao_medido():
    from core.confianca_secao import confianca_investimentos

    class _Quebrado:
        def connect(self):
            raise RuntimeError("sem banco")

    secao = confianca_investimentos(_Quebrado())

    curva = next(c for c in secao.componentes if c.nome == "Curva do Tesouro")
    assert curva.pct is None


# ── título de cupom: o ticker da foto não é a chave da curva ────────────────
#
# A foto junta "IPCA+ com Juros Semestrais 2032" e "IPCA+ 2032" no mesmo
# TIPCA2032; a curva os guarda separados (TIPCAJ2032 e TIPCA2032). Pedir o
# ticker da foto deixava o título de cupom na foto — ou, havendo o principal
# do mesmo ano, marcava-o pelo PU de outro título.

@pytest.mark.parametrize("ticker, nome, chave", [
    ("TIPCA2032", "Tesouro IPCA+ com Juros Semestrais 2032", "TIPCAJ2032"),
    ("TPRE2035", "Tesouro Prefixado com Juros Semestrais 2035", "TPREJ2035"),
    ("TIPCA2045", "NTN-B 2045", "TIPCAJ2045"),
    ("TIPCA2032", "Tesouro IPCA+ 2032", "TIPCA2032"),
    ("TIPCA2045", "NTN-B Principal 2045", "TIPCA2045"),
    ("TPRE2028", "Tesouro Prefixado 2028", "TPRE2028"),
    # O nome do Educa+ traz o ano da primeira parcela; a chave é a do ticker.
    ("TEDUCA2030", "Tesouro Educa+ 2026", "TEDUCA2030"),
    ("TSELIC2029", "Tesouro Selic 2029", "TSELIC2029"),
    ("TIPCA2032", None, "TIPCA2032"),
    # fixed_income também cobre CDB: o nome nunca o leva para a curva.
    ("CDB-BANCO-X", "CDB Banco X Pré 2030", "CDB-BANCO-X"),
])
def test_chave_da_curva_separa_o_cupom_pelo_nome(ticker, nome, chave):
    assert tesouro_curva.chave_da_curva(ticker, nome) == chave


def _curva_que_responde(pedidas, curva):
    def _pu(conn, chaves):
        pedidas.append(sorted(chaves))
        return {k: v for k, v in curva.items() if k in chaves}
    return _pu


_CURVA_DOS_DOIS = {
    "TIPCA2032": (4000.0, date(2026, 10, 28)),
    "TIPCAJ2032": (3900.0, date(2026, 10, 28)),
}


def test_titulo_de_cupom_e_marcado_pela_sua_chave(monkeypatch):
    pedidas = []
    monkeypatch.setattr(tesouro_curva, "pu_de_recompra",
                        _curva_que_responde(pedidas, _CURVA_DOS_DOIS))
    rows = [_row("TIPCA2032", qty=1.0, vm=4100.0, invested=3500.0,
                 nome="Tesouro IPCA+ com Juros Semestrais 2032")]

    curva = investimentos._marcacao_pela_curva(object(), rows)

    assert pedidas == [["TIPCAJ2032"]]
    # A carteira continua chaveada pelo ticker da foto.
    assert curva == {"TIPCA2032": (3900.0, date(2026, 10, 28))}
    _, p = _pos(rows, "TIPCA2032", curva)
    assert p["valor_mercado"] == 3900.0
    assert p["cotacao_fonte"] == "curva_tesouro"


def test_grupo_que_junta_cupom_e_principal_fica_na_foto(monkeypatch):
    # Mesmo ticker, dois títulos: um PU só marcaria parte da quantidade pelo
    # preço do outro.
    pedidas = []
    monkeypatch.setattr(tesouro_curva, "pu_de_recompra",
                        _curva_que_responde(pedidas, _CURVA_DOS_DOIS))
    rows = [
        _row("TIPCA2032", qty=1.0, vm=4100.0, invested=3500.0,
             nome="Tesouro IPCA+ 2032"),
        _row("TIPCA2032", qty=1.0, vm=4000.0, invested=3500.0,
             nome="Tesouro IPCA+ com Juros Semestrais 2032"),
        _row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0),
    ]

    curva = investimentos._marcacao_pela_curva(object(), rows)

    assert pedidas == [["TPRE2030"]]
    assert "TIPCA2032" not in curva
    _, p = _pos(rows, "TIPCA2032", curva)
    assert p["cotacao_fonte"] == "snapshot"


def test_carteira_consolidada_pede_a_chave_do_cupom(monkeypatch):
    rows = [_row("TIPCA2032", qty=1.0, vm=4100.0, invested=3500.0,
                 nome="Tesouro IPCA+ com Juros Semestrais 2032")]
    pedidas = []

    class _Conn:
        def execute(self, stmt, params=None):
            return SimpleNamespace(fetchall=lambda: [], scalar=lambda: True)

    monkeypatch.setattr(tesouro_curva, "pu_de_recompra",
                        _curva_que_responde(pedidas, _CURVA_DOS_DOIS))
    monkeypatch.setattr(investimentos, "_calcular_custos_transacoes", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "cambio_medio_de_aquisicao", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "listar_precos_manuais", lambda owner, conn: {})
    monkeypatch.setattr(investimentos.cotacao_viva, "buscar", lambda simbolos: {})

    carteira = investimentos._carteira_snapshot_consolidada(_Conn(), "u1", rows)

    assert pedidas == [["TIPCAJ2032"]]
    assert carteira["posicoes"][0]["valor_mercado"] == 3900.0


# ── a curva é cotação do dia, não a foto ─────────────────────────────────────

def test_carteira_so_pela_curva_tem_cotacoes():
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]

    carteira = _montar_carteira_snapshot(
        rows, curva_tesouro={"TPRE2030": (850.0, date(2026, 10, 28))})

    assert carteira["cotacoes_disponiveis"] is True
    assert carteira["n_cotacoes_curva"] == 1
    assert carteira["n_cotacoes_live"] == 0


def test_carteira_so_pela_foto_continua_sem_cotacoes():
    carteira = _montar_carteira_snapshot(
        [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)])

    assert carteira["cotacoes_disponiveis"] is False
    assert carteira["n_cotacoes_curva"] == 0


def test_recalculo_dos_totais_conta_a_curva():
    carteira = {"posicoes": [
        {"ticker": "TPRE2030", "valor_mercado": 2550.0, "total_investido": 2000.0,
         "cotacao_fonte": "curva_tesouro", "classe": "Tesouro Direto",
         "setor": "Tesouro Direto", "moeda": "BRL", "cor": "#000000"},
    ]}

    investimentos._recalcular_totais_carteira(carteira)

    assert carteira["n_cotacoes_curva"] == 1


def test_papel_pela_curva_nao_e_sem_marcacao():
    assert not investimentos.sem_marcacao_renda_fixa(
        "renda_fixa", "snapshot", "curva_tesouro", 1000.0, 1000.0)
    assert investimentos.sem_marcacao_renda_fixa(
        "renda_fixa", "snapshot", "snapshot", 1000.0, 1000.0)


def test_visao_geral_e_selo_nao_chamam_a_curva_de_foto():
    import inspect

    from views import investimentos as inv

    corpo = inspect.getsource(inv._tab_analise)
    visao_geral = corpo[corpo.index("with ta:"):corpo.index("with tb:")]
    assert "n_cotacoes_curva" in visao_geral
    assert "pela curva do Tesouro" in visao_geral
    assert 'carteira.get("n_cotacoes_curva", 0)' in inspect.getsource(inv)


def test_contexto_da_llm_diz_quantos_titulos_vieram_da_curva():
    from core.llm_context_carteira import build_carteira_geral_context

    texto = build_carteira_geral_context({
        "posicoes": [], "por_classe": [], "por_setor": [],
        "cotacoes_disponiveis": True, "n_cotacoes_curva": 2,
    })

    assert "Cotações do dia: disponíveis (2 título(s) do Tesouro pela curva oficial)" in texto


# ── curva parada é preço antigo, não do dia ──────────────────────────────────

def test_curva_parada_continua_marcando_mas_como_antiga():
    # O job da curva parou em 02/08 e a última foto é de 01/08: o PU da curva
    # ainda é o mais novo que existe, mas no fim de outubro não é preço do dia.
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0,
                 report=date(2026, 8, 1))]

    carteira, p = _pos(rows, "TPRE2030", {"TPRE2030": (850.0, date(2026, 8, 2))})

    assert p["valor_mercado"] == 2550.0
    assert p["cotacao_fonte"] == "curva_tesouro_antiga"
    assert p["data_referencia"] == date(2026, 8, 2)
    assert carteira["n_cotacoes_curva"] == 0
    assert carteira["n_cotacoes_curva_antiga"] == 1
    assert carteira["n_cotacoes_stale"] == 1
    assert carteira["cotacoes_disponiveis"] is False


@pytest.mark.parametrize("idade, fonte", [
    (MAX_QUOTE_AGE_DAYS, "curva_tesouro"),
    (MAX_QUOTE_AGE_DAYS + 1, "curva_tesouro_antiga"),
])
def test_curva_usa_o_mesmo_limite_de_frescor_da_cotacao_viva(idade, fonte):
    dia = _HOJE - timedelta(days=idade)
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0, report=dia)]

    _, p = _pos(rows, "TPRE2030", {"TPRE2030": (850.0, dia)})

    assert p["cotacao_fonte"] == fonte


def test_recalculo_dos_totais_conta_a_curva_antiga_como_antiga():
    carteira = {"posicoes": [
        {"ticker": "TPRE2030", "valor_mercado": 2550.0, "total_investido": 2000.0,
         "cotacao_fonte": "curva_tesouro_antiga", "classe": "Tesouro Direto",
         "setor": "Tesouro Direto", "moeda": "BRL", "cor": "#000000"},
    ]}

    investimentos._recalcular_totais_carteira(carteira)

    assert carteira["n_cotacoes_curva"] == 0
    assert carteira["n_cotacoes_curva_antiga"] == 1
    assert carteira["n_cotacoes_stale"] == 1


def test_papel_pela_curva_antiga_nao_e_sem_marcacao():
    assert not investimentos.sem_marcacao_renda_fixa(
        "renda_fixa", "snapshot", "curva_tesouro_antiga", 1000.0, 1000.0)


def test_tela_diz_que_a_curva_esta_desatualizada():
    import inspect

    from views import investimentos as inv

    assert "curva do Tesouro desatualizada" in inspect.getsource(inv._card_ativo)
    corpo = inspect.getsource(inv._tab_analise)
    visao_geral = corpo[corpo.index("with ta:"):corpo.index("with tb:")]
    assert "n_cotacoes_curva_antiga" in visao_geral
    assert "pela curva do Tesouro desatualizada" in visao_geral


def test_contexto_da_llm_nao_chama_a_curva_parada_de_cotacao_do_dia():
    from core.llm_context_carteira import build_carteira_geral_context

    texto = build_carteira_geral_context({
        "posicoes": [], "por_classe": [], "por_setor": [],
        "cotacoes_disponiveis": False, "n_cotacoes_curva_antiga": 1,
    })

    assert "Cotações do dia: indisponíveis" in texto
    assert "1 título(s) do Tesouro pela última curva oficial, desatualizada" in texto
