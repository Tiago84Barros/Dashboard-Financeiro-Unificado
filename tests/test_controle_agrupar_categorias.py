from views.controle_financeiro import _agrupar_categorias_pequenas as agrupar


def _cats(pares):
    return [{"nome": n, "gasto": g} for n, g in pares]


def test_pequenas_viram_um_so_outros_e_soma_se_mantem():
    cats = _cats([("A", 50), ("B", 30), ("Outros", 8), ("C", 7), ("D", 2), ("E", 1), ("F", 2)])
    r = agrupar(cats)
    assert [c["nome"] for c in r] == ["A", "B", "C", "Outros"]
    assert sum(c["gasto"] for c in r) == sum(c["gasto"] for c in cats)
    assert r[-1]["gasto"] == 13  # Outros(8) + D + E + F


def test_sem_categoria_pequena_nao_cria_outros():
    r = agrupar(_cats([("A", 60), ("B", 40)]))
    assert [c["nome"] for c in r] == ["A", "B"]


def test_total_zero_devolve_como_veio():
    cats = _cats([("A", 0)])
    assert agrupar(cats) == cats
