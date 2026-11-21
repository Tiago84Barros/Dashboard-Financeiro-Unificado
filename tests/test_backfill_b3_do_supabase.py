"""O backfill da B3 preenche buraco e nunca traz fóssil de revisão."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from scripts import backfill_b3_precos_proventos_do_supabase as bf
from scripts.backfill_b3_precos_proventos_do_supabase import (
    precos_faltantes,
    proventos_faltantes,
)

D = dt.date


def test_preco_so_entra_onde_o_armazem_nao_tem_o_pregao():
    local = pd.DataFrame({"ticker": ["AAA3"], "date": [D(2026, 1, 2)]})
    nuvem = pd.DataFrame({
        "ticker": ["AAA3", "AAA3", "BBB3"],
        "date": [D(2026, 1, 2), D(2026, 1, 3), D(2026, 1, 2)],
        "close": [10.0, 11.0, 5.0],
    })
    falta = precos_faltantes(local, nuvem)
    assert sorted(zip(falta.ticker, falta.date)) == [
        ("AAA3", D(2026, 1, 3)), ("BBB3", D(2026, 1, 2))]


def test_provento_revisado_no_armazem_nao_volta_como_fossil():
    # O armazém tem o JCP de data-ex 26/12 já com o valor corrigido; a nuvem
    # guarda a versão antiga (outro valor) e um provento antigo sem par.
    local = pd.DataFrame({"ticker": ["BRSR6"], "ex_date": [D(2025, 12, 26)],
                          "payment_date": [D(2026, 1, 20)], "type": ["JCP"]})
    nuvem = pd.DataFrame({
        "ticker": ["BRSR6", "BRSR6"],
        "ex_date": [D(2025, 12, 26), D(1999, 4, 30)],
        "payment_date": [D(2026, 1, 20), D(1999, 5, 15)],
        "type": ["JCP", "DIVIDENDO"],
        "amount": [0.4625, 0.10],
    })
    falta = proventos_faltantes(local, nuvem)
    assert list(falta.ex_date) == [D(1999, 4, 30)]


def test_mesma_data_ex_com_outro_tipo_e_lacuna():
    local = pd.DataFrame({"ticker": ["X3"], "ex_date": [D(2024, 5, 1)],
                          "payment_date": [None], "type": ["JCP"]})
    nuvem = pd.DataFrame({"ticker": ["X3"], "ex_date": [D(2024, 5, 1)],
                          "payment_date": [None], "type": ["DIVIDENDO"],
                          "amount": [1.0]})
    assert len(proventos_faltantes(local, nuvem)) == 1


def test_nuvem_vazia_nao_quebra():
    vazio = pd.DataFrame(columns=["ticker", "date"])
    assert precos_faltantes(vazio, vazio).empty
    vaziod = pd.DataFrame(columns=["ticker", "ex_date", "payment_date", "type"])
    assert proventos_faltantes(vaziod, vaziod).empty


def test_provento_sem_data_ex_casa_pelo_pagamento():
    # Nulo não é par de nulo: o armazém tem um provento sem data-ex pago em
    # 10/03; a nuvem tem esse mesmo e outro, também sem data-ex, pago em
    # 10/06. Só o de junho é lacuna.
    local = pd.DataFrame({"ticker": ["Y3"], "ex_date": [None],
                          "payment_date": [D(2025, 3, 10)], "type": ["DIVIDENDO"]})
    nuvem = pd.DataFrame({
        "ticker": ["Y3", "Y3"],
        "ex_date": [None, None],
        "payment_date": [D(2025, 3, 10), D(2025, 6, 10)],
        "type": ["DIVIDENDO", "DIVIDENDO"],
        "amount": [0.5, 0.7],
    })
    falta = proventos_faltantes(local, nuvem)
    assert list(falta.payment_date) == [D(2025, 6, 10)]
    assert "_data_par" not in falta.columns


def test_provento_sem_data_ex_nao_some_quando_o_armazem_tem_outro_sem_data_ex():
    local = pd.DataFrame({"ticker": ["Y3"], "ex_date": [None],
                          "payment_date": [None], "type": ["JCP"]})
    nuvem = pd.DataFrame({"ticker": ["Y3"], "ex_date": [None],
                          "payment_date": [D(2025, 6, 10)], "type": ["JCP"],
                          "amount": [0.7]})
    assert len(proventos_faltantes(local, nuvem)) == 1


# ── --apply: o manifesto de desfazer é gravado antes do commit ─────────────

class _Res:
    def __init__(self, linhas):
        self._linhas = linhas

    def all(self):
        return self._linhas


class _Transacao:
    """Imita ``engine.begin()``: commit na saída limpa, rollback na exceção.

    ``ja_gravadas`` são chaves que outro processo gravou entre a varredura e o
    insert: o ``ON CONFLICT DO NOTHING`` as pula e o ``RETURNING`` não as traz.
    """

    def __init__(self, diario, ja_gravadas):
        self.diario = diario
        self.ja_gravadas = ja_gravadas
        self.lote: list[dict] = []

    def execute(self, sql, params=None):
        texto = str(sql)
        if texto.startswith("insert into _bf_lote"):
            self.lote = list(params)
        elif texto.startswith("insert into market."):
            tabela = texto.split()[2].split(".")[1]
            self.diario.append(("insert", tabela))
            if tabela == "historical_prices":
                chaves = [(r["ticker"], r["date"]) for r in self.lote]
            else:
                chaves = [(r["ticker"], r["payment_date"] or r["ex_date"],
                           r["ex_date"], r["type"], r["amount"]) for r in self.lote]
            return _Res([k for k in chaves if k[:2] not in self.ja_gravadas])
        return None

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_):
        self.diario.append(("rollback",) if tipo else ("commit",))
        return False


class _Eng:
    def __init__(self, ja_gravadas=()):
        self.diario = []
        self.ja_gravadas = set(ja_gravadas)

    def begin(self):
        return _Transacao(self.diario, self.ja_gravadas)


def _faltas():
    p = pd.DataFrame([{c: None for c in bf._COLS_PRECO} | {
        "ticker": "AAA3", "date": D(2026, 1, 3), "close": 11.0}])
    d = pd.DataFrame([{c: None for c in bf._COLS_PROVENTO} | {
        "ticker": "AAA3", "ex_date": D(2025, 12, 26), "event_date": D(2025, 12, 26),
        "type": "JCP", "amount": 0.5}])
    return p, d


def test_apply_grava_o_manifesto_antes_do_commit(tmp_path, monkeypatch):
    eng = _Eng()
    destino = tmp_path / "bk" / "m.json"
    original = bf.gravar_manifesto

    def _espia(dest, man):
        eng.diario.append(("manifesto",))
        original(dest, man)

    monkeypatch.setattr(bf, "gravar_manifesto", _espia)
    p, d = _faltas()
    assert bf.aplicar(eng, p, d, destino) == (1, 1)
    assert eng.diario[-2:] == [("manifesto",), ("commit",)]
    assert destino.exists()
    assert not destino.with_suffix(".json.tmp").exists()


def test_falha_ao_gravar_o_manifesto_desfaz_os_inserts(tmp_path, monkeypatch):
    eng = _Eng()
    destino = tmp_path / "bk" / "m.json"

    def _disco_cheio(*_a, **_k):
        raise OSError("disco cheio")

    monkeypatch.setattr(bf, "gravar_manifesto", _disco_cheio)
    p, d = _faltas()
    with pytest.raises(OSError, match="disco cheio"):
        bf.aplicar(eng, p, d, destino)
    assert eng.diario[-1] == ("rollback",)
    assert ("commit",) not in eng.diario
    assert not destino.exists()


def test_manifesto_relido_confere_com_o_gravado(tmp_path):
    man = bf.manifesto_de_desfazer(
        [("AAA3", D(2026, 1, 3))],
        [("AAA3", D(2025, 12, 26), D(2025, 12, 26), "JCP", 0.5)])
    bf.gravar_manifesto(tmp_path / "m.json", man)
    assert man["precos"] == [["AAA3", "2026-01-03"]]
    assert man["proventos"] == [["AAA3", "2025-12-26", "2025-12-26", "JCP", 0.5]]


def test_manifesto_so_lista_o_que_esta_rodada_gravou(tmp_path):
    """Candidata gravada por outro processo depois da varredura cai no
    ``ON CONFLICT DO NOTHING``; no manifesto, desfazer apagaria o registro
    alheio. O manifesto sai do ``RETURNING``, não das candidatas."""
    p, d = _faltas()
    p = pd.concat([p, p.assign(date=D(2026, 1, 4))], ignore_index=True)
    eng = _Eng(ja_gravadas={("AAA3", D(2026, 1, 4)), ("AAA3", D(2025, 12, 26))})
    destino = tmp_path / "m.json"
    assert bf.aplicar(eng, p, d, destino) == (1, 0)
    man = json.loads(destino.read_text(encoding="utf-8"))
    assert man == {"precos": [["AAA3", "2026-01-03"]], "proventos": []}
