"""Provento sem quantidade fica sem quantidade -- e a fonte que tem completa.

Regra do AGENTS.md ("Dados ausentes permanecem ausentes"). Até 06/11/2026 a
Movimentação da B3 sem quantidade e TODO provento do Consolidado da XP eram
gravados como 1 cota com o pagamento inteiro por cota, e a deduplicação
canônica impedia a fonte posterior de corrigir.

O teste central é o de EQUIVALÊNCIA ENTRE AS FONTES (regra dos gêmeos do
CLAUDE.md): o mesmo pagamento, chegando pela B3, pela XP e pela Rico em
qualquer ordem, com ou sem quantidade, termina na mesma linha -- igual à de
quem só importou a fonte que tem a quantidade. Os importadores rodam de
verdade (parse do arquivo + ``common.gravar_proventos``) num SQLite com o
schema da 083; só o cadastro de ativo e de conta é trocado por dublê.
"""
from __future__ import annotations

import io
from datetime import date
from itertools import permutations

import pytest

openpyxl = pytest.importorskip("openpyxl")
from sqlalchemy import create_engine, text  # noqa: E402

from core import proventos as prov  # noqa: E402
from data_pipeline.importers.investments import b3_movimentacao as b3  # noqa: E402
from data_pipeline.importers.investments import common  # noqa: E402
from data_pipeline.importers.investments import rico_extrato_conta as rico  # noqa: E402
from data_pipeline.importers.investments import xp_consolidado as xp  # noqa: E402

UID = "00000000-0000-0000-0000-000000000001"
PAGAMENTO = date(2026, 6, 12)
TOTAL = 35.20
QTD = 32.0


# ─────────────────────────────────────────────────────────────────────────────
# Banco sintético
# ─────────────────────────────────────────────────────────────────────────────

def _engine(*, anulavel: bool = True):
    """``dividends`` com (083) ou sem (antes da 083) o vazio permitido."""
    nulo = "" if anulavel else " NOT NULL"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE dividends (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                type TEXT NOT NULL,
                amount_per_unit NUMERIC{nulo}, quantity NUMERIC{nulo},
                total_amount NUMERIC NOT NULL,
                ex_date DATE, payment_date DATE NOT NULL, external_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX ux_div_ext ON dividends(external_id)
            WHERE external_id IS NOT NULL
        """))
    return engine


def _linhas(engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            (r.asset_id, r.type, str(r.payment_date)[:10],
             None if r.quantity is None else float(r.quantity),
             None if r.amount_per_unit is None else float(r.amount_per_unit),
             round(float(r.total_amount), 2))
            for r in conn.execute(text(
                "SELECT * FROM dividends ORDER BY asset_id, payment_date"))
        ]


@pytest.fixture
def sem_cadastro(monkeypatch):
    """Ativo e conta viram dublês; a gravação do provento é a real."""
    for mod in (b3, xp, rico):
        monkeypatch.setattr(mod.settings, "OWNER_USER_ID", UID)
        monkeypatch.setattr(mod, "ensure_external_id_columns", lambda e: None)
    monkeypatch.setattr(b3, "get_or_create_b3_account", lambda c, u: 1)
    monkeypatch.setattr(b3, "batch_get_or_create_assets",
                        lambda c, items: {tk: f"asset-{tk}" for tk, _n, _c in items})
    monkeypatch.setattr(b3, "ensure_movement_events_table", lambda e: False)

    def _existentes(conn, tabela, ids):  # o original usa ANY(), só Postgres
        marcas = ", ".join(f":i{n}" for n in range(len(ids)))
        return {r[0] for r in conn.execute(
            text(f"SELECT external_id FROM {tabela} WHERE external_id IN ({marcas})"),
            {f"i{n}": v for n, v in enumerate(ids)})}

    monkeypatch.setattr(b3, "batch_filter_existing_external_ids", _existentes)
    monkeypatch.setattr(xp, "get_or_create_asset",
                        lambda conn, ticker, name, asset_class: f"asset-{ticker}")
    monkeypatch.setattr(rico, "get_or_create_asset",
                        lambda conn, ticker, name=None, asset_class=None: f"asset-{ticker}")


# ─────────────────────────────────────────────────────────────────────────────
# Arquivos sintéticos: o mesmo rendimento de KNCR11 em cada fonte
# ─────────────────────────────────────────────────────────────────────────────

def _xlsx(titulo: str, linhas: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = titulo
    for linha in linhas:
        ws.append(linha)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _b3(com_qtd: bool) -> bytes:
    return _xlsx("Movimentação", [
        ["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição",
         "Quantidade", "Preço unitário", "Valor da Operação"],
        ["Credito", "12/06/2026", "Rendimento", "KNCR11 - KINEA RENDIMENTOS",
         "XP INVESTIMENTOS", QTD if com_qtd else "-", "-", TOTAL],
    ])


def _xp_aba(com_qtd: bool):
    cab = ["Produto", "Pagamento", "Tipo de Evento", "Instituição",
           "Quantidade" if com_qtd else "Coluna sem nome", "Preço unitário",
           "Valor líquido"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Proventos Recebidos"
    ws.append(cab)
    ws.append(["KNCR11 - KINEA RENDIMENTOS", "12/06/2026", "Rendimento",
               "XP INVESTIMENTOS", QTD, 1.10, TOTAL])
    return ws


def _rico(com_qtd: bool) -> bytes:
    historico = (f"RENDIMENTOS DE CLIENTES KNCR11 S/ {int(QTD)}" if com_qtd
                 else "RENDIMENTO FUNDO FECHADO BALCÃO KNCR11 - KINEA")
    return _xlsx("Planilha1", [
        ["Movimentação", "Liquidação", "Lançamento", "Valor (R$)", "Saldo (R$)"],
        ["12/06/2026", "12/06/2026", historico, TOTAL, 1000.0],
    ])


def _importar(fonte: str, com_qtd: bool, engine) -> dict:
    if fonte == "b3":
        return b3.parse(_b3(com_qtd), engine)
    if fonte == "rico":
        return rico.parse(_rico(com_qtd), engine)
    resumo = common.make_summary("xp_consolidado")
    with engine.connect() as conn, conn.begin():
        xp._parse_proventos(_xp_aba(com_qtd), conn, UID, resumo, PAGAMENTO)
    return common.finalize_summary(resumo)


ESPERADO_COM_QTD = [("asset-KNCR11", "reit_income", "2026-06-12",
                     QTD, round(TOTAL / QTD, 6), TOTAL)]
ESPERADO_SEM_QTD = [("asset-KNCR11", "reit_income", "2026-06-12", None, None, TOTAL)]


# ─────────────────────────────────────────────────────────────────────────────
# Regra única de quantidade
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("qtd", [None, 0, -5, float("nan"), "abc"])
def test_quantidade_ausente_nao_vira_um(qtd):
    assert common.quantidade_provento(44.16, qtd) == (None, None)


def test_quantidade_informada_da_o_valor_por_unidade():
    assert common.quantidade_provento(44.16, 368) == (368.0, 0.12)


def test_b3_sem_quantidade_nao_inventa_uma_cota():
    linha = ["Credito", "12/06/2026", "Rendimento", "KNCR11 - KINEA",
             "XP", None, None, "35,20"]
    p = b3._parse_row(linha)
    assert (p["quantity"], p["amount_per_unit"], p["total_amount"]) == (None, None, 35.20)


def test_b3_com_quantidade_mantem_a_conta():
    linha = ["Credito", "12/06/2026", "Rendimento", "KNCR11 - KINEA",
             "XP", 32, None, "35,20"]
    p = b3._parse_row(linha)
    assert (p["quantity"], p["amount_per_unit"]) == (32.0, 1.1)


def test_gravar_proventos_exige_quantidade_e_unitario_juntos():
    engine = _engine()
    linha = {"user_id": UID, "asset_id": "a", "type": "dividend",
             "quantity": 10.0, "amount_per_unit": None, "total_amount": 5.0,
             "ex_date": None, "payment_date": PAGAMENTO, "external_id": "x"}
    with engine.begin() as conn, pytest.raises(ValueError):
        common.gravar_proventos(conn, [linha])


# ─────────────────────────────────────────────────────────────────────────────
# Equivalência entre as fontes
# ─────────────────────────────────────────────────────────────────────────────

_CHEGADAS = [
    # (fonte, traz quantidade?)
    ("b3", True), ("b3", False),
    ("xp", True), ("xp", False),
    ("rico", True), ("rico", False),
]


@pytest.mark.parametrize("fonte,com_qtd", _CHEGADAS)
def test_cada_fonte_sozinha(sem_cadastro, fonte, com_qtd):
    engine = _engine()
    resumo = _importar(fonte, com_qtd, engine)
    assert resumo["incomes_imported"] == 1, resumo
    assert _linhas(engine) == (ESPERADO_COM_QTD if com_qtd else ESPERADO_SEM_QTD)


@pytest.mark.parametrize("ordem", list(permutations(
    [("b3", True), ("xp", False), ("rico", False)])) + list(permutations(
    [("xp", True), ("b3", False), ("rico", False)])) + list(permutations(
    [("rico", True), ("b3", False), ("xp", False)])))
def test_qualquer_ordem_termina_igual_a_fonte_com_quantidade(sem_cadastro, ordem):
    """Uma fonte com a quantidade e duas sem, em qualquer ordem: uma linha só,
    com a quantidade -- a mesma de quem importou só a fonte que a tem."""
    engine = _engine()
    resumos = [_importar(f, q, engine) for f, q in ordem]

    assert _linhas(engine) == ESPERADO_COM_QTD
    assert sum(r["incomes_imported"] for r in resumos) == 1
    # Quem chegou depois de uma linha vazia e trouxe a quantidade, completou.
    pos_com = next(i for i, (_f, q) in enumerate(ordem) if q)
    esperado_completado = 1 if pos_com > 0 else 0
    assert sum(r.get("incomes_enriched", 0) for r in resumos) == esperado_completado
    assert all(not r["errors"] for r in resumos), [r["errors"] for r in resumos]


@pytest.mark.parametrize("ordem", list(permutations(
    [("b3", False), ("xp", False), ("rico", False)])))
def test_nenhuma_fonte_com_quantidade_fica_vazio(sem_cadastro, ordem):
    engine = _engine()
    for f, q in ordem:
        _importar(f, q, engine)
    assert _linhas(engine) == ESPERADO_SEM_QTD


def test_quantidade_ja_dita_nao_e_trocada(sem_cadastro):
    """Duas fontes com quantidades diferentes: a primeira a dizer fica."""
    engine = _engine()
    _importar("b3", True, engine)
    with engine.begin() as conn:
        res = common.gravar_proventos(conn, [{
            "user_id": UID, "asset_id": "asset-KNCR11", "type": "reit_income",
            "quantity": 99.0, "amount_per_unit": round(TOTAL / 99, 6),
            "total_amount": TOTAL, "ex_date": None, "payment_date": PAGAMENTO,
            "external_id": "outra-fonte"}])
    assert (res.inseridos, res.enriquecidos, res.duplicados) == (0, 0, 1)
    assert _linhas(engine) == ESPERADO_COM_QTD


def test_reimportar_a_mesma_fonte_completa_a_si_mesma(sem_cadastro):
    """A linha da XP gravada vazia (arquivo antigo sem a coluna) é completada
    pelo mesmo relatório reexportado com "Quantidade" -- mesmo external_id."""
    engine = _engine()
    _importar("xp", False, engine)
    resumo = _importar("xp", True, engine)
    assert resumo["incomes_enriched"] == 1
    assert _linhas(engine) == ESPERADO_COM_QTD


@pytest.mark.parametrize("fonte", ["b3", "xp", "rico"])
def test_sem_a_083_recusa_e_nomeia_em_vez_de_inventar(sem_cadastro, fonte):
    """Banco com quantity NOT NULL: nada de 1 cota; o provento sai em errors."""
    engine = _engine(anulavel=False)
    resumo = _importar(fonte, False, engine)
    assert _linhas(engine) == []
    assert resumo["incomes_imported"] == 0
    assert any("KNCR11" in e and "35.20" in e and "083" in e
               for e in resumo["errors"]), resumo["errors"]


@pytest.mark.parametrize("fonte", ["b3", "xp", "rico"])
def test_sem_a_083_quem_tem_quantidade_grava_normal(sem_cadastro, fonte):
    engine = _engine(anulavel=False)
    resumo = _importar(fonte, True, engine)
    assert resumo["errors"] == []
    assert _linhas(engine) == ESPERADO_COM_QTD


# ─────────────────────────────────────────────────────────────────────────────
# Leitura: a tela não transforma o vazio em número
# ─────────────────────────────────────────────────────────────────────────────

def _evento(ticker, ext, quantidade):
    return {
        "id": ext, "ticker": ticker, "nome": ticker, "classe": "Ações BR",
        "cor": "#000", "tipo": "dividend", "label_tipo": "Dividendo",
        "amount_per_unit": None if quantidade is None else round(62.0 / quantidade, 6),
        "quantity": quantidade, "total_amount": 62.0, "ex_date": None,
        "payment_date": PAGAMENTO, "external_id": ext,
    }


def test_fracionario_completa_a_quantidade_da_linha_descartada():
    """BBAS3 pela B3 sem quantidade e BBAS3F pela Rico com ela: a linha que
    fica é a da B3 (fonte mais confiável), com a quantidade da Rico."""
    d = prov._montar_dict([
        _evento("BBAS3", "b3mov-1", None),
        _evento("BBAS3F", "ricocc-1", 100.0),
    ], PAGAMENTO)
    assert len(d["eventos"]) == 1
    e = d["eventos"][0]
    assert (e["external_id"], e["quantity"], e["amount_per_unit"]) == ("b3mov-1", 100.0, 0.62)


def test_totais_nao_dependem_da_quantidade():
    sem = prov._montar_dict([_evento("BBAS3", "b3mov-1", None)], PAGAMENTO)
    com = prov._montar_dict([_evento("BBAS3", "b3mov-1", 100.0)], PAGAMENTO)
    for chave in ("total_historico", "total_ano", "num_eventos", "por_ativo"):
        assert sem[chave] == com[chave]


def test_tela_mostra_traco_para_quantidade_ausente():
    from views import proventos as tela

    assert tela._fmt_qtd(None) == tela.SEM_QUANTIDADE == "—"
    assert tela._fmt_unitario(None) == "—"
    assert tela._fmt_qtd(368.0) == "368"
    assert tela._fmt_unitario(0.12) == "R$ 0,1200"
