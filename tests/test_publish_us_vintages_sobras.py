# -*- coding: utf-8 -*-
"""A vitrine guardava safra que o local ja nao tem, e a conferencia nao via.

`publicar()` so escrevia: upsert sem remocao. A empresa que saia do universo --
FGN, `excluded` por nao ser acao ordinaria -- deixava as safras de 2024 e 2025
na vitrine, e o app publicado seguia rankeando com elas. A vitrine desfazia em
silencio a decisao de um filtro.

A conferencia final tambem nao pegava, porque perguntava `remotas < len(safras)`.
Sobra e o caso oposto, e uma checagem assimetrica e cega para metade dos
defeitos possiveis. Estes testes travam as duas coisas.
"""
from __future__ import annotations

import scripts.publish_us_score_vintages as pub


class _ConnFake:
    def __init__(self, remotas):
        self._remotas = remotas
        self.apagados = []

    def execute(self, sql, params=None):
        texto = str(sql)
        if texto.strip().startswith("DELETE"):
            self.apagados.append((params["s"], params["d"]))
            return self
        return list(self._remotas)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _EngineFake:
    def __init__(self, remotas):
        self.conn = _ConnFake(remotas)

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


def _safra(symbol, data):
    """(symbol, score_version, as_of_date, ...) -- a ordem de COLS_VINTAGE."""
    return (symbol, "0.8.0", data, "fundamental", 70.0, 90.0, 80.0)


def test_safra_que_saiu_do_local_e_removida_da_vitrine():
    engine = _EngineFake([("AAPL", "2025-06-30"), ("FGN", "2024-06-30"),
                          ("FGN", "2025-06-30")])
    n = pub._remover_sobras(engine, [_safra("AAPL", "2025-06-30")], "0.8.0")
    assert n == 2
    assert engine.conn.apagados == [("FGN", "2024-06-30"), ("FGN", "2025-06-30")]


def test_vitrine_igual_ao_local_nao_apaga_nada():
    """Reconciliacao nao pode ser destrutiva no caminho feliz."""
    engine = _EngineFake([("AAPL", "2025-06-30")])
    assert pub._remover_sobras(engine, [_safra("AAPL", "2025-06-30")], "0.8.0") == 0
    assert engine.conn.apagados == []


def test_conferencia_final_exige_igualdade_e_nao_apenas_minimo():
    """`remotas < local` deixava passar sobra; a compa­racao tem de ser simetrica."""
    fonte = pub.__file__
    with open(fonte, encoding="utf-8") as fh:
        texto = fh.read()
    assert "if remotas != len(safras):" in texto
    assert "if remotas < len(safras):" not in texto
