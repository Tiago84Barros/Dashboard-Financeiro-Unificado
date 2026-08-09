"""Backfill de snapshots das carteiras ja salvas."""
import pytest
from sqlalchemy import create_engine, text

from scripts import backfill_portfolio_snapshots as bf

OWNER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE portfolio_asset_snapshots (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, asset_class TEXT NOT NULL,
                model_id TEXT NOT NULL, symbol TEXT NOT NULL, schema_version INTEGER NOT NULL,
                as_of_date TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (asset_class, model_id, symbol)
            )
        """))
        conn.execute(text("""
            CREATE TABLE b3_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT, params_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE b3_portfolio_model_items (
                model_id TEXT, ticker TEXT, nome TEXT, setor TEXT, subsetor TEXT,
                segmento TEXT, weight REAL, score REAL, alpha_selic REAL, alpha_ew REAL,
                rank_score INTEGER, ano_lider INTEGER,
                motivos_json TEXT, meta_json TEXT
            )
        """))
        conn.execute(text("INSERT INTO b3_portfolio_models (id, user_id, status, params_json) "
                          "VALUES ('m01', :u, 'active', '{\"top_n\": 2}')"), {"u": OWNER})
        for tk, nome, peso in [("PETR4", "Petrobras", 0.6), ("VALE3", "Vale", 0.4)]:
            conn.execute(
                text("INSERT INTO b3_portfolio_model_items "
                     "(model_id, ticker, nome, weight, score, motivos_json, meta_json) "
                     "VALUES ('m01', :t, :n, :w, 70, :mot, :meta)"),
                {"t": tk, "n": nome, "w": peso,
                 "mot": '["Lider de score"]',
                 "meta": '{"classificacao": "aprovada", "motivo": "governanca ok"}'},
            )
    return eng


def test_le_os_itens_do_modelo(engine):
    itens = bf.read_model_items("b3", "m01", engine=engine)
    assert [i["ticker"] for i in itens] == ["PETR4", "VALE3"]
    assert itens[0]["nome"] == "Petrobras"


def test_lista_apenas_o_modelo_ativo_do_dono(engine):
    modelos = bf.active_models("b3", engine=engine, owner_id=OWNER)
    assert [m["id"] for m in modelos] == ["m01"]
    assert modelos[0]["params_json"]["top_n"] == 2


def test_simulacao_nao_grava_nada(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=False, classes=["b3"])
    assert resumo["b3"] == 2

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM portfolio_asset_snapshots")).scalar() == 0


def test_apply_grava_e_marca_backfilled(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=True, classes=["b3"])
    assert resumo["b3"] == 2

    from core.portfolio.repository import load_snapshots
    lidos = load_snapshots("b3", "m01", engine=engine)
    assert set(lidos) == {"PETR4", "VALE3"}
    assert lidos["PETR4"]["provenance"]["backfilled"] is True


def test_classe_sem_carteira_nao_quebra(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=False, classes=["b3", "us"])
    assert resumo["us"] == 0


def test_falha_real_na_consulta_e_logada_e_nao_apenas_tolerada(caplog):
    """Distingue, no log, uma consulta quebrada de uma classe legitimamente sem carteira.

    A tabela existe mas nao tem a coluna 'status' exigida pela query: e um erro
    de verdade (SQL invalido/permissao/conexao), nao a ausencia esperada da
    tabela. active_models() deve devolver [] sem propagar, mas tem que deixar
    rastro no log — do contrario "0 carteiras" fica indistinguivel de "a
    consulta falhou".
    """
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE b3_portfolio_models (id TEXT PRIMARY KEY, user_id TEXT)"))

    with caplog.at_level("WARNING"):
        modelos = bf.active_models("b3", engine=eng, owner_id=OWNER)

    assert modelos == []
    avisos = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("b3" in r.getMessage() for r in avisos)
    assert any(r.exc_info is not None for r in avisos)


def _neutralizar_leituras_de_mercado(monkeypatch):
    """Deixa o adaptador B3 real rodar sem tocar em market_read.

    Substitui _default_loaders no proprio adaptador em vez de remendar funcoes
    de core.market_read: importar aquele modulo puxa Streamlit, que polui a
    saida dos testes com avisos de runtime ausente. O adaptador nao importa
    market_read no topo justamente para permitir isso.
    """
    from core.portfolio.adapters import b3 as adaptador_b3
    monkeypatch.setattr(
        adaptador_b3, "_default_loaders",
        lambda: {"multiplos": lambda tks: {}, "demonstracoes": lambda tks: {}},
    )


def test_normalizar_itens_traduz_colunas_cruas_do_b3(engine):
    """motivos_json/meta_json (colunas) -> motivos/quali (o que o adaptador procura)."""
    cru = bf.read_model_items("b3", "m01", engine=engine)
    assert "motivos" not in cru[0] and "quali" not in cru[0]   # a linha crua nao tem

    itens = bf.normalizar_itens("b3", cru)
    assert itens[0]["motivos"] == ["Lider de score"]
    assert itens[0]["quali"]["classificacao"] == "aprovada"


def test_normalizar_itens_devolve_a_linha_intacta_para_classe_sem_traducao():
    linhas = [{"symbol": "AAPL", "entry_score": 71.0}]
    assert bf.normalizar_itens("us", linhas) == linhas


def test_backfill_com_adaptador_REAL_preserva_motivos_e_quali(engine, monkeypatch):
    """Regressao do defeito que os fakes escondiam.

    Todos os outros testes deste arquivo fazem monkeypatch de load_adapter, entao
    a costura entre read_model_items (colunas cruas) e o adaptador (formato em
    memoria) nunca era exercitada — e o desalinhamento motivos_json/motivos e
    meta_json/quali passava despercebido, gravando classification vazia.

    Aqui o adaptador B3 real roda; so as leituras de mercado sao neutralizadas,
    porque o que esta sob teste e a traducao do item, nao o enriquecimento.
    """
    _neutralizar_leituras_de_mercado(monkeypatch)

    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=True, classes=["b3"])
    assert resumo["b3"] == 2

    from core.portfolio.repository import load_snapshots
    cls = load_snapshots("b3", "m01", engine=engine)["PETR4"]["classification"]
    assert cls["motivos"] == ["Lider de score"]
    assert cls["quali"]["classificacao"] == "aprovada"


def test_backfill_com_adaptador_REAL_preserva_metricas_point_in_time(engine, monkeypatch):
    """Score e peso gravados na selecao sao historico verdadeiro, nao valor de hoje."""
    _neutralizar_leituras_de_mercado(monkeypatch)

    bf.backfill(engine=engine, owner_id=OWNER, apply=True, classes=["b3"])

    from core.portfolio.repository import load_snapshots
    metrics = load_snapshots("b3", "m01", engine=engine)["PETR4"]["metrics"]
    assert metrics["score"] == 70
    assert metrics["weight"] == 0.6


class _FakeAdapter:
    """Adaptador sem acesso a rede: monta payload minimo a partir do item."""

    @staticmethod
    def build_snapshots(items, *, model_id, params, as_of, loaders=None):
        from core.portfolio.models import AssetSnapshot
        return [
            AssetSnapshot.from_blocks(
                asset_class="b3", model_id=model_id, symbol=i["ticker"],
                as_of_date=as_of,
                blocks={"identity": {"symbol": i["ticker"]},
                        "provenance": {"backfilled": True}},
            )
            for i in items
        ]
