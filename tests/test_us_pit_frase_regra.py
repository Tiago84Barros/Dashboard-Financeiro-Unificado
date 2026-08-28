# -*- coding: utf-8 -*-
"""A tela nao pode afirmar rigor que a base nao tem (A-159).

`core.us_pit` sabe aplicar a regra por campo, mas linha sem `filed_at` cai no
fallback e continua sob a regra antiga. Enquanto a base esta pela metade, dizer
"point-in-time por campo" e declaracao nao verificada -- a mesma armadilha que
`core.us_survivorship` ja documenta.
"""
from core import us_pit


class _Res:
    def __init__(self, par): self._par = par
    def first(self): return self._par


class _Conn:
    def __init__(self, par): self._par = par
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): return _Res(self._par)


class _Engine:
    def __init__(self, par): self._par = par
    def connect(self): return _Conn(self._par)


def test_base_inteira_com_procedencia_e_regra_por_campo():
    c = us_pit.cobertura_procedencia(_Engine((100, 100)))
    assert c["regra"] == us_pit.REGRA_CAMPO and c["fracao"] == 1.0


def test_base_pela_metade_nao_vira_afirmacao_de_rigor():
    c = us_pit.cobertura_procedencia(_Engine((100, 40)))
    assert c["regra"] == "mista" and abs(c["fracao"] - 0.4) < 1e-9


def test_sem_procedencia_nenhuma_continua_a_regra_antiga():
    assert us_pit.cobertura_procedencia(_Engine((100, 0)))["regra"] == us_pit.REGRA_LINHA


def test_vitrine_sem_a_coluna_nao_quebra_a_tela():
    class Quebrada:
        def connect(self): raise RuntimeError("column filed_at does not exist")
    assert us_pit.cobertura_procedencia(Quebrada())["regra"] == "indisponivel"
