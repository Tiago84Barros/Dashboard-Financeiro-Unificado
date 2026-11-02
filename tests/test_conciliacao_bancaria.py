"""Conciliação bancária: o extrato casa com o lançamento manual e confere saldo.

Antes, cada linha do extrato virava lançamento novo. Quem lança à mão e
também importa o extrato ficava com a despesa em dobro, e o saldo da conta
inflava sem aviso. E as linhas de saldo do PDF -- a prova de que o app fecha
com o banco -- eram descartadas pelo parser.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import core.bank_statement_import as bsi
from core.bank_statement_import import parse_c6_bank_text
from core.conciliacao_bancaria import (
    conferir_saldo,
    extrair_saldos_extrato,
    impacto_no_saldo,
    similaridade_descricao,
    sugerir_vinculos,
)


def _mov(dia, valor, desc):
    return {"data_movimento": date(2026, 10, dia), "valor": valor, "descricao_original": desc}


def _tx(tid, dia, valor, desc, cat="cat-x"):
    return {"id": tid, "due_date": date(2026, 10, dia), "amount": valor,
            "description": desc, "category_id": cat}


# ── saldo do extrato ────────────────────────────────────────────────────────

def test_linhas_de_saldo_viram_saldo_inicial_e_final():
    texto = """
    Saldo anterior 30/09/2026 R$ 1.000,00
    01/10/2026 Saida PIX Pix enviado para FULANO R$ -80,00
    Saldo do dia 01/10/2026 R$ 920,00
    05/10/2026 Entrada PIX Pix recebido de CICLANO R$ 200,00
    Saldo do dia 05/10/2026 R$ 1.120,00
    """
    saldos = extrair_saldos_extrato(texto)
    assert saldos["inicial"] == {"data": date(2026, 9, 30), "valor": 1000.00}
    assert saldos["final"] == {"data": date(2026, 10, 5), "valor": 1120.00}


def test_saldo_final_e_o_de_data_mais_recente_mesmo_fora_de_ordem():
    texto = """
    Saldo do dia 31/10/2026 R$ 500,00
    Saldo do dia 15/10/2026 R$ 900,00
    """
    assert extrair_saldos_extrato(texto)["final"]["valor"] == 500.00


def test_saldo_devedor_fica_negativo():
    assert extrair_saldos_extrato("Saldo final 31/10/2026 R$ -250,00")["final"]["valor"] == -250.00
    assert extrair_saldos_extrato("Saldo final 31/10/2026 250,00 D")["final"]["valor"] == -250.00


def test_extrato_sem_saldo_nao_inventa_numero():
    assert extrair_saldos_extrato("01/10/2026 Pix enviado R$ -10,00") == {"inicial": None, "final": None}


def test_parser_entrega_o_saldo_e_continua_sem_virar_movimento():
    texto = """
    01/10/2026 Saida PIX Pix enviado para FULANO R$ -80,00
    Saldo do dia 01/10/2026 R$ 920,00
    """
    parsed = parse_c6_bank_text(texto, file_name="c6.pdf")
    assert parsed["summary"]["rows"] == 1
    assert parsed["saldos"]["final"] == {"data": date(2026, 10, 1), "valor": 920.00}


# ── vínculo com lançamento manual ───────────────────────────────────────────

def test_mesmo_valor_e_data_proxima_casam():
    v = sugerir_vinculos([_mov(10, -80.0, "Pix enviado para JOAO PADARIA")],
                         [_tx("t1", 9, -80.0, "Padaria do João")])
    assert v[0]["transaction_id"] == "t1"
    assert v[0]["dias"] == 1


def test_valor_diferente_ou_longe_demais_nao_casa():
    movs = [_mov(10, -80.0, "Padaria")]
    assert sugerir_vinculos(movs, [_tx("t1", 10, -80.01, "Padaria")]) == {}
    assert sugerir_vinculos(movs, [_tx("t1", 14, -80.0, "Padaria")]) == {}
    # Sinal conta: entrada de 80 não é a saída de 80.
    assert sugerir_vinculos(movs, [_tx("t1", 10, 80.0, "Padaria")]) == {}


def test_um_lancamento_nao_serve_a_dois_movimentos():
    movs = [_mov(10, -50.0, "Uber"), _mov(10, -50.0, "Uber")]
    v = sugerir_vinculos(movs, [_tx("t1", 10, -50.0, "Uber")])
    assert len(v) == 1


def test_descricao_desempata_entre_lancamentos_de_mesmo_valor():
    movs = [_mov(10, -100.0, "Pix enviado para ACADEMIA FORMA"),
            _mov(10, -100.0, "Pix enviado para DENTISTA SORRISO")]
    txs = [_tx("dentista", 10, -100.0, "Dentista Sorriso"),
           _tx("academia", 10, -100.0, "Academia Forma")]
    v = sugerir_vinculos(movs, txs)
    assert v[0]["transaction_id"] == "academia"
    assert v[1]["transaction_id"] == "dentista"


def test_palavras_do_banco_nao_contam_como_semelhanca():
    assert similaridade_descricao("Pix enviado para JOAO", "Pix enviado para MARIA") < 0.5


def test_forca_do_par():
    v = sugerir_vinculos([_mov(10, -30.0, "Pix XYZ")], [_tx("t", 12, -30.0, "Mercado")])
    assert v[0]["forca"] == "possivel"
    v = sugerir_vinculos([_mov(10, -30.0, "Pix XYZ")], [_tx("t", 10, -30.0, "Mercado")])
    assert v[0]["forca"] == "forte"


# ── conferência de saldo ────────────────────────────────────────────────────

def test_saldo_fecha_quando_app_mais_importacao_bate_com_banco():
    c = conferir_saldo(1120.0, saldo_app=1000.0, a_lancar=120.0)
    assert c["fecha"] and c["diferenca"] == 0.0


def test_diferenca_igual_aos_pendentes_e_nomeada():
    c = conferir_saldo(1000.0, saldo_app=1080.0, a_lancar=0.0, pendente=-80.0)
    assert not c["fecha"]
    assert c["explicada_por_pendentes"]


def test_impacto_ignora_vinculado_ja_importado_e_data_depois_do_saldo():
    rows = [
        {"data_movimento": date(2026, 10, 1), "valor": -80.0, "categoria_id": "c"},
        {"data_movimento": date(2026, 10, 1), "valor": -50.0, "categoria_id": "c",
         "vincular_transaction_id": "t1"},
        {"data_movimento": date(2026, 10, 1), "valor": -30.0, "categoria_id": "c",
         "ja_importado": True},
        {"data_movimento": date(2026, 10, 2), "valor": -20.0, "categoria_id": None},
        {"data_movimento": date(2026, 11, 1), "valor": -999.0, "categoria_id": "c"},
    ]
    assert impacto_no_saldo(rows, date(2026, 10, 31)) == (-80.0, -20.0)


# ── gravação: vínculo não cria lançamento, saldo fica guardado ──────────────

class _Conn:
    def __init__(self):
        self.movimentos: list[dict] = []
        self.saldos: list[dict] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "FOR UPDATE" in sql:
            ok = params["tx_id"] == "tx-manual"
            return SimpleNamespace(fetchone=lambda: (
                SimpleNamespace(id="tx-manual", category_id="cat-manual") if ok else None))
        if "INSERT INTO bank_statement_movements" in sql:
            self.movimentos.append(dict(params))
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(id=f"mov-{len(self.movimentos)}"))
        if "INSERT INTO bank_statement_balances" in sql:
            self.saldos.append(dict(params))
        return SimpleNamespace(fetchone=lambda: None, rowcount=1)


def test_linha_vinculada_nao_cria_lancamento_e_herda_categoria(monkeypatch):
    conn = _Conn()

    @contextmanager
    def _begin():
        yield conn

    criados: list[dict] = []
    monkeypatch.setattr(bsi.settings, "MOCK_MODE", False, raising=False)
    monkeypatch.setattr(bsi.settings, "OWNER_USER_ID", "owner-1", raising=False)
    monkeypatch.setattr(bsi, "get_engine", lambda: SimpleNamespace(begin=_begin))
    monkeypatch.setattr(bsi, "_schema_initialized", True)
    monkeypatch.setattr(bsi, "_resolve_bank_statement_account",
                        lambda *a, **k: SimpleNamespace(id="acc-1", name="C6", type="checking"))
    monkeypatch.setattr(bsi, "_fetch_categories", lambda *a: [{"id": "cat-x", "nome": "Mercado",
                                                             "tipo": "expense"}])
    monkeypatch.setattr(bsi, "acquire_transaction_import_lock", lambda *a, **k: None)
    monkeypatch.setattr(bsi, "_insert_transaction",
                        lambda c, o, row, acc, cat: criados.append(row) or "tx-novo")

    rows = [
        {"banco": "C6 Bank", "data_movimento": date(2026, 10, 1), "valor": -80.0,
         "descricao_original": "Pix padaria", "direcao": "saida", "categoria_id": "cat-x",
         "vincular_transaction_id": "tx-manual"},
        {"banco": "C6 Bank", "data_movimento": date(2026, 10, 2), "valor": -30.0,
         "descricao_original": "Pix mercado", "direcao": "saida", "categoria_id": "cat-x"},
    ]
    res = bsi.import_bank_statement_rows(
        rows, "c6.pdf", saldo_extrato={"data": date(2026, 10, 31), "valor": 890.0})

    assert res["ok"]
    assert res["summary"]["linked"] == 1 and res["summary"]["published"] == 1
    assert [r["descricao_original"] for r in criados] == ["Pix mercado"]
    vinculado = conn.movimentos[0]
    assert vinculado["transaction_id"] == "tx-manual"
    assert vinculado["conciliacao"] == "vinculada"
    assert vinculado["categoria_id"] == "cat-manual"
    assert conn.saldos == [{"uid": "owner-1", "account_id": "acc-1", "banco": "C6 Bank",
                            "data": date(2026, 10, 31), "valor": 890.0, "arquivo": "c6.pdf"}]


def test_vinculo_que_nao_vale_mais_segue_o_caminho_normal(monkeypatch):
    """Lançamento apagado ou já reclamado por outro movimento entre a prévia e
    o clique: a linha não pode sumir -- vira lançamento novo."""
    conn = _Conn()

    @contextmanager
    def _begin():
        yield conn

    criados: list[dict] = []
    monkeypatch.setattr(bsi.settings, "MOCK_MODE", False, raising=False)
    monkeypatch.setattr(bsi.settings, "OWNER_USER_ID", "owner-1", raising=False)
    monkeypatch.setattr(bsi, "get_engine", lambda: SimpleNamespace(begin=_begin))
    monkeypatch.setattr(bsi, "_schema_initialized", True)
    monkeypatch.setattr(bsi, "_resolve_bank_statement_account",
                        lambda *a, **k: SimpleNamespace(id="acc-1", name="C6", type="checking"))
    monkeypatch.setattr(bsi, "_fetch_categories", lambda *a: [])
    monkeypatch.setattr(bsi, "acquire_transaction_import_lock", lambda *a, **k: None)
    monkeypatch.setattr(bsi, "_insert_transaction",
                        lambda c, o, row, acc, cat: criados.append(row) or "tx-novo")

    rows = [{"banco": "C6 Bank", "data_movimento": date(2026, 10, 1), "valor": -80.0,
             "descricao_original": "Pix padaria", "direcao": "saida", "categoria_id": "cat-x",
             "vincular_transaction_id": "tx-sumiu"}]
    res = bsi.import_bank_statement_rows(rows, "c6.pdf")

    assert res["summary"]["linked"] == 0 and res["summary"]["published"] == 1
    assert len(criados) == 1
    assert conn.movimentos[0]["transaction_id"] is None


# ── Grau de Confiança: o saldo conferido entra como componente ──────────────

class _ConnConfianca:
    def __init__(self, saldos: int, diferenca: float, dias: int):
        from datetime import timedelta
        self.saldos, self.diferenca = saldos, diferenca
        self.data = date.today() - timedelta(days=dias)

    def execute(self, stmt, *a, **k):
        sql = " ".join(str(stmt).split()).lower()
        if "count(*) from bank_statement_balances" in sql:
            v = self.saldos
        elif "max(data_saldo)" in sql:
            v = self.data
        elif "saldo_extrato -" in sql:
            v = self.diferenca
        else:
            v = 0
        return SimpleNamespace(scalar=lambda: v)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _componente_saldo(saldos, diferenca, dias):
    from core.confianca_secao import _componente_saldo_conferido
    notas: list[str] = []
    eng = SimpleNamespace(connect=lambda: _ConnConfianca(saldos, diferenca, dias))
    return _componente_saldo_conferido(eng, notas), notas


def test_saldo_que_fecha_vale_cem_e_recente_pesa_cheio():
    (c,), notas = _componente_saldo(1, 0.0, 5)
    assert c.pct == 100.0 and c.peso == 0.10 and not notas


def test_saldo_que_nao_fecha_reprova_e_vira_nota():
    (c,), notas = _componente_saldo(1, -80.0, 5)
    assert c.pct == 0.0
    assert notas and "difere" in notas[0]


def test_sem_saldo_guardado_nao_entra_componente():
    assert _componente_saldo(0, 0.0, 5) == ([], [])
