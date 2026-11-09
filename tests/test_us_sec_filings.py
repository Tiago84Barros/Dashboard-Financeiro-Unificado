"""Entregas à SEC (8-K, 10-Q, 10-K...) das ações dos EUA.

A tela de relatórios dizia em texto fixo que não havia base de filings da SEC.
Tudo aqui roda sem rede e sem Postgres: o submissions é um dublê e o armazém é
SQLite com o schema `market_us` anexado.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from core.inteligencia_ativos import fontes_informacoes as fi
from core.inteligencia_ativos import informacoes as inf
from data_pipeline.us import sec_filings as sf
from scripts import publish_informacoes_recentes as pub

HOJE = date(2026, 11, 8)


def _recentes():
    """Bloco `filings.recent` no formato da SEC: listas paralelas."""
    return {
        "accessionNumber": ["0000320193-26-000101", "0000320193-26-000090",
                            "0000320193-26-000080", "0000320193-26-000070",
                            "0000320193-24-000010"],
        "form": ["8-K", "10-Q", "4", "8-K/A", "10-K"],
        "filingDate": ["2026-10-30", "2026-10-31", "2026-11-01", "2026-09-15",
                       "2024-11-01"],
        "reportDate": ["2026-10-30", "2026-09-27", "", "2026-09-10", "2024-09-28"],
        # A SEC devolve `items` mais curto quando falta item no fim.
        "items": ["2.02,9.01", "", "", "5.02"],
        "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm", "form4.xml",
                            "aapl-8ka.htm", "aapl-10k.htm"],
        "primaryDocDescription": ["8-K", "10-Q", "4", "8-K/A", "10-K"],
    }


# ── classificação ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("forma, itens, tipo, trecho", [
    ("8-K", "2.02,9.01", inf.RELEASE, "2.02 divulgação de resultado"),
    # Item mais importante manda no tipo; o título lista todos.
    ("8-K", "2.02,5.02", inf.FATO_RELEVANTE, "2.02 divulgação de resultado; 5.02"),
    ("8-K", "9.01", inf.COMUNICADO, "Evento relevante (8-K)"),
    ("8-K/A", "4.02", inf.FATO_RELEVANTE, "retificação"),
    ("10-Q", None, inf.BALANCO, "Relatório trimestral (10-Q)"),
    ("10-K/A", None, inf.BALANCO, "Relatório anual (10-K) -- retificação"),
    ("DEF 14A", None, inf.ASSEMBLEIA, "Proxy"),
    ("20-F", None, inf.BALANCO, "20-F"),
])
def test_classifica_formulario_e_item_do_8k(forma, itens, tipo, trecho):
    obtido = sf.classificar(forma, itens)
    assert obtido is not None and obtido[0] == tipo and trecho in obtido[1]


@pytest.mark.parametrize("forma", ["4", "3", "SC 13G", "424B2", "FWP"])
def test_formulario_sem_interesse_fica_de_fora(forma):
    assert sf.classificar(forma, "") is None


def test_todo_item_mapeado_tem_tipo_conhecido_da_tela():
    for tipo, _ in sf.ITENS_8K.values():
        assert tipo in inf.PRIORIDADE_DOC


# ── leitura do submissions ────────────────────────────────────────────────────
def test_linhas_de_recentes_respeita_janela_e_listas_curtas():
    linhas = sf.linhas_de_recentes(_recentes(), "320193", desde=date(2025, 11, 1))
    formas = [linha["form"] for linha in linhas]
    # O Form 4 (insider) não interessa; o 10-K de 2024 está fora da janela.
    assert formas == ["8-K", "10-Q", "8-K/A"]
    oito_k = linhas[0]
    assert oito_k["items"] == "2.02,9.01" and oito_k["cik"] == "0000320193"
    assert oito_k["url"] == ("https://www.sec.gov/Archives/edgar/data/320193/"
                             "000032019326000101/aapl-8k.htm")
    assert linhas[1]["report_date"] == date(2026, 9, 27)
    assert linhas[2]["items"] == "5.02"


def test_um_cik_serve_todas_as_classes():
    mapa = sf.simbolos_por_cik([("GOOG", "1652044"), ("GOOGL", "1652044"),
                                ("AAPL", "320193"), ("XX", None), ("YY", "abc")])
    assert mapa == {"0001652044": ["GOOG", "GOOGL"], "0000320193": ["AAPL"]}
    assert sf.simbolos_por_cik([("AAPL", "320193"), ("MSFT", "789019")],
                               ["msft"]) == {"0000789019": ["MSFT"]}


# ── coleta: dublê da SEC + SQLite ─────────────────────────────────────────────
class _Sec:
    def __init__(self, respostas):
        self.respostas = respostas
        self.pedidos = []

    def submissions(self, cik):
        self.pedidos.append(cik)
        resposta = self.respostas.get(cik)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


@pytest.fixture()
def armazem():
    motor = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(motor, "connect")
    def _anexa(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market_us")

    with motor.begin() as conn:
        conn.execute(text(
            "CREATE TABLE market_us.company_snapshots (symbol TEXT, cik TEXT, "
            "is_active BOOLEAN)"))
        conn.execute(text(
            "INSERT INTO market_us.company_snapshots VALUES "
            "('AAPL', '0000320193', 1), ('GOOG', '0001652044', 1), "
            "('GOOGL', '0001652044', 1), ('DEAD', '0000000042', 0)"))
        conn.execute(text("""
            CREATE TABLE market_us.sec_filings (
                accession_number TEXT, symbol TEXT, cik TEXT, form TEXT,
                filing_date DATE, report_date DATE, items TEXT,
                primary_document TEXT, description TEXT, url TEXT,
                collected_at TIMESTAMP,
                PRIMARY KEY (accession_number, symbol))"""))
    yield motor
    motor.dispose()


def test_coleta_grava_uma_linha_por_simbolo_e_e_idempotente(armazem):
    sec = _Sec({"0000320193": {"filings": {"recent": _recentes()}},
                "0001652044": RuntimeError("HTTP 503")})
    for _ in range(2):
        res = sf.coletar(sec, armazem, hoje=HOJE, aplicar_migracao=False)
    # Inativo não é consultado; GOOG e GOOGL custam uma requisição só.
    assert sorted(set(sec.pedidos)) == ["0000320193", "0001652044"]
    assert res["ciks"] == 2 and res["falhas"] == 1 and res["ok"] is True
    with armazem.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM market_us.sec_filings WHERE symbol='AAPL'")).scalar()
    assert n == 3


def test_universo_vazio_e_erro_e_nao_sucesso(armazem):
    res = sf.coletar(_Sec({}), armazem, tickers=["ZZZZ"], hoje=HOJE,
                     aplicar_migracao=False)
    assert res["ok"] is False and res["ciks"] == 0


# ── vitrine e leitura ─────────────────────────────────────────────────────────
def test_vitrine_leva_os_documentos_da_sec_ao_ticker():
    linhas = [{"symbol": "AAPL", "form": "8-K", "items": "2.02,9.01",
               "filing_date": date(2026, 10, 30), "report_date": date(2026, 10, 30),
               "url": "https://www.sec.gov/x", "collected_at": None},
              {"symbol": "AAPL", "form": "10-Q", "items": None,
               "filing_date": date(2026, 10, 31), "report_date": date(2026, 9, 27),
               "url": "https://www.sec.gov/y", "collected_at": None},
              {"symbol": "AAPL", "form": "4", "items": None,
               "filing_date": date(2026, 11, 1), "report_date": None,
               "url": "https://www.sec.gov/z", "collected_at": None}]
    docs = pub.agrupar_documentos(pub.documentos_sec(linhas))["AAPL"]
    assert [d["tipo"] for d in docs] == [inf.BALANCO, inf.RELEASE]
    assert docs[0]["titulo"] == ("Relatório trimestral (10-Q) — período até "
                                 "27/09/2026")
    assert docs[1]["source"] == "SEC/EDGAR"


def test_tela_le_os_documentos_da_sec_e_nao_diz_mais_que_nao_ha_base():
    art = {"gerado_em": "2026-11-08T03:00:00+00:00", "relatorios": {
        "janela_dias": 180,
        "base_ate": {"b3": "2026-11-07", "eua": "2026-11-07"},
        "fonte": {"b3": "CVM/IPE", "eua": "SEC/EDGAR"},
        "por_ticker": {"GOOG": [{
            "tipo": inf.RELEASE, "titulo": "8-K: 2.02 divulgação de resultado",
            "reference_date": "2026-10-29", "source": "SEC/EDGAR",
            "source_url": "https://www.sec.gov/x", "retrieved_at": None,
            "dimensoes": []}]}}}
    r = fi.relatorios_de(art, "GOOGL", "acao")      # classe irmã
    assert r.documentos and r.documentos[0].tipo == inf.RELEASE
    assert r.fonte == "SEC/EDGAR" and r.base_ate == "2026-11-07"
    vazio = fi.relatorios_de(art, "MSFT", "acao")
    assert "Nenhum documento de MSFT" in vazio.motivo
    assert "07/11/2026" in vazio.motivo


def test_llm_dos_eua_recebe_os_documentos_da_sec(monkeypatch):
    from core import llm_context_ativo as ctx
    art = {"relatorios": {
        "janela_dias": 180, "base_ate": {"eua": "2026-11-07"},
        "fonte": {"eua": "SEC/EDGAR"},
        "por_ticker": {"AAPL": [{
            "tipo": inf.RELEASE, "titulo": "8-K: 2.02 divulgação de resultado",
            "reference_date": "2026-10-30", "source": "SEC/EDGAR",
            "source_url": "https://www.sec.gov/x", "retrieved_at": None,
            "dimensoes": []}]}}}
    monkeypatch.setattr(fi, "arquivo", lambda: art)
    bloco = ctx._bloco_documentos_sec("AAPL")
    assert "2026-10-30 | Release | 8-K: 2.02 divulgação de resultado" in bloco
    # Sem documento, a falta é nomeada.
    assert "Nenhum documento de MSFT" in ctx._bloco_documentos_sec("MSFT")


def test_dossie_b3_nao_mistura_documento_do_adr_americano(monkeypatch):
    from core import dossie_b3
    art = {"relatorios": {"por_ticker": {
        "VALE3": [{"tipo": inf.FATO_RELEVANTE, "titulo": "CVM",
                   "reference_date": "2026-11-01", "source": "CVM/IPE"}],
        "VALE": [{"tipo": inf.COMUNICADO, "titulo": "6-K",
                  "reference_date": "2026-11-02", "source": "SEC/EDGAR"}]}}}
    monkeypatch.setattr(fi, "arquivo", lambda: art)
    res = dossie_b3._eventos_do_artefato("VALE3", 10)
    assert [e["titulo"] for e in res["eventos"]] == ["CVM"]
