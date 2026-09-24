"""Movimentação da B3: fato cru no importador, interpretação em core."""
from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import date

import openpyxl
import pytest

from core import movimentacao_b3 as mv
from core import rentabilidade as rt

D = date(2024, 3, 1)


def _ev(mov, tk, sentido, q, valor=None, d=D):
    return {"data": d, "ticker": tk, "movimento": mov, "sentido": sentido,
            "quantidade": q, "valor": valor}


# ── Interpretação ─────────────────────────────────────────────────────────────


def test_eventos_sem_caixa_seguem_o_sentido():
    r = mv.interpretar([
        _ev("bonificação em ativos", "ITSA4", "Credito", 10),
        _ev("desdobro", "WEGE3", "Credito", 100),
        _ev("grupamento", "MGLU3", "Debito", 90),
        _ev("fração em ativos", "ITSA4", "Debito", 0.4),
    ])
    assert [(a["ticker"], a["delta_qtd"], a["caixa"]) for a in r["ajustes"]] == [
        ("ITSA4", 10.0, 0.0), ("WEGE3", 100.0, 0.0), ("MGLU3", -90.0, 0.0),
        ("ITSA4", -0.4, 0.0),
    ]
    assert r["bloqueados"] == {}


def test_recibo_de_fii_vira_cota_com_caixa():
    r = mv.interpretar([_ev("recibo de subscrição", "HGLG13", "Credito", 12, 1200.0)])
    assert r["ajustes"] == [{"data": D, "ticker": "HGLG11", "delta_qtd": 12.0, "caixa": -1200.0}]
    assert r["subscritos"] == {"HGLG11"}
    assert r["bloqueados"] == {}


def test_recibo_sem_valor_bloqueia_em_vez_de_entrar_de_graca():
    r = mv.interpretar([_ev("recibo de subscrição", "HGLG13", "Credito", 12, None)])
    assert r["bloqueados"] == {"HGLG11": mv.MOTIVO_SUBSCRICAO_SEM_VALOR}


def test_troca_de_corretora_soma_zero_e_entrada_de_fora_bloqueia():
    r = mv.interpretar([
        _ev("transferência", "BBAS3", "Debito", 50),
        _ev("transferência", "BBAS3", "Credito", 50),
        _ev("transferência", "TAEE11", "Credito", 30),
    ])
    assert r["bloqueados"] == {"TAEE11": mv.MOTIVO_TRANSFERENCIA}


def test_negociacao_proventos_direitos_e_atualizacao_sao_ignorados():
    r = mv.interpretar([
        _ev("compra", "PETR4", "Credito", 10, 300.0),
        _ev("transferência - liquidação", "PETR4", "Credito", 10, 300.0),
        _ev("dividendo", "PETR4", "Credito", 10, 5.0),
        _ev("direito de subscrição", "HGLG12", "Credito", 12),
        _ev("atualização", "HGLG11", "Credito", 12),
    ])
    assert r == {"ajustes": [], "bloqueados": {}, "subscritos": set()}


def test_leilao_de_fracao_e_so_caixa_e_incorporacao_bloqueia_quem_recebe():
    r = mv.interpretar([
        _ev("leilão de fração", "ITSA4", "Credito", 0.4, 3.2),
        _ev("incorporação", "NOVA3", "Credito", 20),
        _ev("incorporação", "VELH3", "Debito", 40),
    ])
    assert {"data": D, "ticker": "ITSA4", "delta_qtd": 0.0, "caixa": 3.2} in r["ajustes"]
    assert r["bloqueados"] == {"NOVA3": mv.MOTIVO_TROCA_DE_CODIGO}


def test_ticker_da_cota_so_mexe_em_recibo_de_fii():
    assert mv.ticker_da_cota("ABCD13") == "ABCD11"
    assert mv.ticker_da_cota("SPG213") == "SPG211"
    assert mv.ticker_da_cota("PETR4") == "PETR4"
    assert mv.ticker_da_cota("ABCD11") == "ABCD11"


# ── Conciliação com a movimentação ────────────────────────────────────────────


def _tx(tk, tipo, q, p, d=D):
    return {"data": d, "ticker": tk, "tipo": tipo, "quantidade": q, "preco": p, "taxas": 0.0}


def test_subscricao_e_bonificacao_fecham_a_quantidade_e_o_caixa_entra():
    transacoes = [_tx("HGLG11", "buy", 88, 100.0), _tx("ITSA4", "buy", 100, 10.0)]
    posicoes = {
        "HGLG11": {"quantidade": 100, "valor_mercado": 11000.0},
        "ITSA4": {"quantidade": 110, "valor_mercado": 1200.0},
    }
    sem = rt.conciliar_universo(transacoes, [], posicoes)
    assert sem["incluidos"] == []

    mov = mv.interpretar([
        _ev("recibo de subscrição", "HGLG13", "Credito", 12, 1150.0, date(2024, 6, 1)),
        _ev("bonificação em ativos", "ITSA4", "Credito", 10),
    ])
    com = rt.conciliar_universo(transacoes, [], posicoes, movimentacao=mov)
    assert com["incluidos"] == ["HGLG11", "ITSA4"]
    # A subscrição é dinheiro que saiu; a bonificação não é fluxo nenhum.
    assert (date(2024, 6, 1), -1150.0) in com["fluxos"]
    assert sorted(v for _, v in com["fluxos"]) == [-8800.0, -1150.0, -1000.0]


def test_bloqueado_sai_com_o_motivo_da_movimentacao():
    mov = mv.interpretar([_ev("recibo de subscrição", "HGLG13", "Credito", 12, None)])
    u = rt.conciliar_universo(
        [_tx("HGLG11", "buy", 88, 100.0)], [],
        {"HGLG11": {"quantidade": 100, "valor_mercado": 11000.0}},
        movimentacao=mov,
    )
    assert u["incluidos"] == []
    assert u["excluidos"] == [{"ticker": "HGLG11", "motivo": mv.MOTIVO_SUBSCRICAO_SEM_VALOR}]


def test_interpretacao_que_conta_em_dobro_custa_cobertura_e_nao_numero():
    # Se a B3 publicasse a cota pela atualização E pelo recibo e as duas
    # contassem, a quantidade passaria da posição e o ativo sairia.
    mov = {"ajustes": [
        {"data": D, "ticker": "HGLG11", "delta_qtd": 12.0, "caixa": -1150.0},
        {"data": D, "ticker": "HGLG11", "delta_qtd": 12.0, "caixa": 0.0},
    ], "bloqueados": {}, "subscritos": {"HGLG11"}}
    u = rt.conciliar_universo(
        [_tx("HGLG11", "buy", 88, 100.0)], [],
        {"HGLG11": {"quantidade": 100, "valor_mercado": 11000.0}},
        movimentacao=mov,
    )
    assert u["incluidos"] == []
    assert u["excluidos"][0]["motivo"] == rt.MOTIVO_QTD_DIVERGE


# ── Importador ────────────────────────────────────────────────────────────────


def _xlsx(linhas) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Movimentação"
    ws.append(["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição",
               "Quantidade", "Preço unitário", "Valor da Operação"])
    for ln in linhas:
        ws.append(ln)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_LINHAS = [
    ["Credito", "01/03/2024", "Bonificação em Ativos", "ITSA4 - ITAUSA S.A.", "XP", 10, "-", "-"],
    ["Credito", "01/03/2024", "Bonificação em Ativos", "ITSA4 - ITAUSA S.A.", "XP", 10, "-", "-"],
    ["Credito", "15/03/2024", "Rendimento", "HGLG11 - CSHG LOGISTICA", "XP", 88, "1,10", "96,80"],
]


class _Conn:
    def __init__(self):
        self.executados = []

    @contextmanager
    def begin(self):
        yield

    @contextmanager
    def begin_nested(self):
        yield

    def execute(self, *a, **k):
        self.executados.append(a)


class _Engine:
    def __init__(self):
        self.conn = _Conn()

    @contextmanager
    def connect(self):
        yield self.conn


@pytest.fixture
def importador(monkeypatch):
    from data_pipeline.importers.investments import b3_movimentacao as imp

    monkeypatch.setattr(imp.settings, "OWNER_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(imp, "ensure_external_id_columns", lambda e: None)
    monkeypatch.setattr(imp, "get_or_create_b3_account", lambda c, u: 1)
    monkeypatch.setattr(imp, "batch_get_or_create_assets",
                        lambda c, items: {tk: i for i, (tk, _n, _c) in enumerate(items)})
    monkeypatch.setattr(imp, "batch_filter_existing_external_ids", lambda c, t, ids: set())
    monkeypatch.setattr(imp, "batch_insert_investment_transactions", lambda c, rows: len(rows))
    monkeypatch.setattr(imp, "batch_insert_dividends", lambda c, rows: len(rows))
    monkeypatch.setattr(imp, "ensure_movement_events_table", lambda e: True)
    return imp


def test_importador_guarda_toda_linha_crua_e_distingue_linhas_identicas(importador, monkeypatch):
    gravados = []
    monkeypatch.setattr(importador, "insert_movement_events",
                        lambda c, rows: gravados.extend(rows) or len(rows))
    s = importador.parse(_xlsx(_LINHAS), _Engine())
    assert s["status"] == "success"
    assert s["incomes_imported"] == 1
    assert s["events_recorded"] == 3
    assert [g["movement"] for g in gravados] == [
        "bonificação em ativos", "bonificação em ativos", "rendimento"]
    ids = [g["external_id"] for g in gravados]
    assert len(set(ids)) == 3 and all(i.startswith("b3evt-") for i in ids)
    assert gravados[0]["quantity"] == 10.0 and gravados[0]["unit_price"] is None

    # Subir o mesmo arquivo de novo gera os mesmos ids: o ON CONFLICT descarta.
    gravados_2 = []
    monkeypatch.setattr(importador, "insert_movement_events",
                        lambda c, rows: gravados_2.extend(rows) or len(rows))
    importador.parse(_xlsx(_LINHAS), _Engine())
    assert [g["external_id"] for g in gravados_2] == ids


def test_falha_nos_eventos_nao_derruba_os_proventos(importador, monkeypatch):
    def _explode(c, rows):
        raise RuntimeError("sem permissão")

    monkeypatch.setattr(importador, "insert_movement_events", _explode)
    s = importador.parse(_xlsx(_LINHAS), _Engine())
    assert s["incomes_imported"] == 1
    assert s["status"] == "partial_success"
    assert any("Eventos crus" in e for e in s["errors"])


def test_sem_tabela_de_eventos_o_upload_segue(importador, monkeypatch):
    monkeypatch.setattr(importador, "ensure_movement_events_table", lambda e: False)
    chamado = []
    monkeypatch.setattr(importador, "insert_movement_events",
                        lambda c, rows: chamado.append(1) or 0)
    s = importador.parse(_xlsx(_LINHAS), _Engine())
    assert s["status"] == "success" and s["incomes_imported"] == 1
    assert chamado == []


def test_leitura_dos_eventos_devolve_none_se_a_tabela_nao_existe():
    from core.investimentos import _ler_eventos_movimentacao

    class _Quebra:
        def connect(self):
            raise RuntimeError('relation "investment_movement_events" does not exist')

    assert _ler_eventos_movimentacao(_Quebra(), "u") is None
