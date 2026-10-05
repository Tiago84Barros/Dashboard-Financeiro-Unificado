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


def test_tema_pelo_assunto_da_frase():
    assert dr.tema("O lucro líquido foi de R$ 3 bi e a margem subiu.") == "resultado"
    assert dr.tema("O payout de dividendos foi de 100% do lucro.") == "proventos"
    assert dr.tema("A dívida líquida subiu e o caixa caiu 10%.") == "divida"
    # termo de mais palavras vence: "custo de capital" não é custo operacional
    assert dr.tema("Passou a considerar custo de capital de 14,75%.") == "projecoes"
    assert dr.tema("A capacidade de produção cresceu 5%.") == "operacao"
    assert dr.tema("O conselho se reuniu em 3 de maio.") == "outros"


def test_por_tema_agrupa_ordena_e_nao_repete_o_fato():
    lucro = "O lucro líquido foi de R$ 3,1 bi, alta de 8,2% no trimestre."
    lucro_rep = "Registramos lucro líquido de R$ 3,1 bi, alta de 8,2% no 2T."
    caixa = "O caixa ficou em R$ 500 MM, queda de 10% no trimestre."
    dest = (
        dr.Destaque("Release 2T26", "2026-08-10", "Release", (caixa, lucro)),
        dr.Destaque("Transcrição 2T26", "2026-08-12", "Transcrição",
                    (lucro_rep,)),
    )
    out = dr.por_tema(dest)
    assert [t.tema for t in out] == ["resultado", "divida"]
    assert out[0].frase == lucro and out[0].titulo == "Release 2T26"
    assert len(dr.por_tema(dest * 1, n_por_tema=1)) == 2
    assert dr.por_tema(()) == ()


def test_por_tema_respeita_o_limite_por_tema():
    frases = tuple(f"O lucro foi de R$ {i},0 bi, alta de {i}% no ano."
                   for i in range(1, 6))
    out = dr.por_tema((dr.Destaque("R", "2026-01-01", "R", frases),),
                      n_por_tema=2)
    assert len(out) == 2


def _sem_acento_por_frase(t: str) -> str:
    """A versão anterior: NFKD da frase inteira, laço por caractere."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower())
                   if not unicodedata.combining(c))


def test_sem_acento_igual_a_normalizar_a_frase_inteira():
    textos = [
        "Ação ordinária — dívida líquida caiu 12,5% no 3º trimestre",
        "PROJEÇÕES: CAPEX de R$ 1,2 bi; EBITDA ajustado ½",
        "ﬁnanciamento ﬂuxo ℃ ² Ǆ İstanbul ß ẞ",
        "é ạ̀ ñ",  # marcas já decompostas
        "texto ascii puro 123",
        "",
    ]
    for t in textos:
        assert dr._sem_acento(t) == _sem_acento_por_frase(t), t


def test_ler_trechos_le_uma_vez_por_ativo(monkeypatch):
    chamadas: list[str] = []

    def ler(ticker, *a, **k):
        chamadas.append(ticker)
        return ()

    monkeypatch.setattr(dr, "ler", ler)
    assert dr.ler_trechos("petr4") == ()
    assert dr.ler_trechos("PETR4") == ()
    assert dr.ler_trechos("VALE3") == ()
    assert chamadas == ["PETR4", "VALE3"]
