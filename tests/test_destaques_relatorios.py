"""Destaques dos relatórios (frases do emissor com fato e número)."""
from __future__ import annotations

from core.inteligencia_ativos import destaques_relatorios as dr

BOA = ("A receita líquida totalizou R$ 1,2 bilhão no trimestre, com "
       "crescimento de 15% sobre o mesmo período do ano anterior.")
TABELA = "Receita 1.234 2.345 3.456 4.567 5.678 6.789 7.890 8.901"
INGLES = ("Net revenue was R$ 1.2 billion and the margin reached 15% in the "
          "quarter compared with last year.")
SEM_NUMERO = ("A companhia registrou aumento relevante da receita líquida "
              "no período, com margens melhores em todas as regiões.")


def test_pontuar_aceita_prosa_com_fato_e_numero():
    assert dr.pontuar(BOA) > 0
    for ruim in (TABELA, INGLES, SEM_NUMERO, BOA.upper(), "receita " + BOA):
        assert dr.pontuar(ruim) == 0, ruim


def test_destaques_agrupa_por_documento_e_ignora_politica():
    lucro = ("O lucro líquido do semestre foi de R$ 300 milhões, alta de 8% "
             "puxada pela expansão das vendas nas lojas novas.")
    chunks = [
        (f"{TABELA}.\n{BOA}", "2026-08-10", "Release", "Release 2T26", 1),
        (lucro, "2026-08-10", "Release", "Release 2T26", 1),
        (BOA, "2026-07-01", "Política de Divulgação", "Política de divulgação", 2),
        (TABELA, "2026-06-01", "ITR", "ITR 1T26", 3),
    ]
    out = dr.destaques(chunks)
    assert len(out) == 1
    d = out[0]
    assert d.titulo == "Release 2T26" and d.data == "2026-08-10"
    assert d.frases == (BOA, lucro)                    # ordem do documento
    assert dr.mesmo_documento("RELEASE 2T26", "2026-08-10T00:00", d)
    assert not dr.mesmo_documento("Release 2T26", "2026-08-11", d)


def test_destaques_respeita_os_limites():
    chunks = [(BOA, f"2026-0{i}-01", "Release", f"Doc {i}", i)
              for i in range(1, 6)]
    assert len(dr.destaques(chunks, n_docs=2)) == 2
    assert dr.destaques(()) == ()
