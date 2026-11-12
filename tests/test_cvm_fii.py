import io
import zipfile

import core.cvm_fii as cf


def test_only_digits():
    assert cf.only_digits("11.728.688/0001-47") == "11728688000147"
    assert cf.only_digits(None) == ""


def test_num_aceita_virgula_e_nan():
    assert cf._num("166,00") == 166.0
    assert cf._num("7570056166.40") == 7570056166.40
    assert cf._num("") is None and cf._num("nan") is None
    assert cf._num(float("nan")) is None


def test_s_trata_nan():
    assert cf._s(float("nan")) == ""
    assert cf._s(None) == ""
    assert cf._s("  Logística ") == "Logística"


def test_composition_percentuais():
    row = {"Valor_Ativo": "1000", "Imoveis_Renda_Acabados": "800",
           "CRI": "150", "Disponibilidades": "50"}
    comp = cf._composition(row)
    assert comp["pct_imoveis"] == 0.8
    assert comp["pct_papel"] == 0.15
    assert comp["pct_caixa"] == 0.05


def test_composition_sem_total():
    assert cf._composition({})["pct_imoveis"] is None


def test_classify_tipo():
    assert cf.classify_tipo({"pct_imoveis": 0.98, "pct_papel": 0.0, "pct_fundos": 0.0}) == "tijolo"
    assert cf.classify_tipo({"pct_imoveis": 0.1, "pct_papel": 0.85, "pct_fundos": 0.0}) == "papel"
    assert cf.classify_tipo({"pct_imoveis": 0.1, "pct_papel": 0.1, "pct_fundos": 0.7}) == "fof"
    assert cf.classify_tipo({"pct_imoveis": 0.4, "pct_papel": 0.4, "pct_fundos": 0.1}) == "hibrido"
    assert cf.classify_tipo(
        {"pct_imoveis": None, "pct_papel": None, "pct_fundos": None}
    ) is None
    assert cf.classify_tipo(
        {"pct_imoveis": 0.0, "pct_papel": 0.0, "pct_fundos": 0.0}
    ) is None


def test_ref_month():
    assert cf._ref_month("2024-02-29") == "2024-02-01"
    assert cf._ref_month("2024-02") == "2024-02-01"
    assert cf._ref_month("") is None
    assert cf._ref_month("lixo") is None


def _build_informe_zip(year: int) -> bytes:
    """Zip sintético com complemento (2 meses) + ativo_passivo p/ 1 CNPJ."""
    cnpj = "11.728.688/0001-47"
    complemento = (
        "CNPJ_Fundo_Classe;Data_Referencia;Patrimonio_Liquido;"
        "Valor_Patrimonial_Cotas;Total_Numero_Cotistas;Percentual_Dividend_Yield_Mes\n"
        f"{cnpj};{year}-01-31;1000000,00;100,50;5000;0.008\n"
        f"{cnpj};{year}-02-29;1010000,00;101,00;5100;0.0075\n"
    )
    ativo = (
        "CNPJ_Fundo_Classe;Data_Referencia;Valor_Ativo;"
        "Imoveis_Renda_Acabados;CRI;Disponibilidades\n"
        f"{cnpj};{year}-01-31;1000;900;50;50\n"
        f"{cnpj};{year}-02-29;1000;800;150;50\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"inf_mensal_fii_complemento_{year}.csv", complemento.encode("latin-1"))
        zf.writestr(f"inf_mensal_fii_ativo_passivo_{year}.csv", ativo.encode("latin-1"))
    return buf.getvalue()


def test_parse_informe_monthly():
    data = _build_informe_zip(2024)
    out = cf.parse_informe_monthly(data, 2024)
    cnpj = "11728688000147"
    assert cnpj in out
    serie = out[cnpj]
    assert len(serie) == 2
    # ordenado por mês asc
    assert [r["ref_month"] for r in serie] == ["2024-01-01", "2024-02-01"]
    jan, fev = serie
    assert jan["vpa"] == 100.50
    assert fev["vpa"] == 101.00
    assert jan["num_cotistas"] == 5000
    # a CVM entrega fracao (0.008 = 0,8% ao mes), nao percentual
    assert jan["dy_patrimonial_mes"] == 0.008
    assert fev["dy_patrimonial_mes"] == 0.0075
    # composição muda mês a mês (imóveis 900 -> 800)
    assert jan["pct_imoveis"] == 0.9
    assert fev["pct_imoveis"] == 0.8


def test_rendimento_do_informe_e_fracao_e_nao_e_dividido_por_100():
    # Valores do arquivo real da CVM (HGLG11, 2026): 0.007052 = 0,7052% ao mes.
    # De 07 a 11/2026 o leitor dividia por 100 e gravava 0,0000705.
    assert cf._bounded_ratio("0.007052") == 0.007052
    assert cf._bounded_ratio("0,007052") == 0.007052
    assert cf._bounded_ratio(0.2) == 0.2
    # fora da faixa nao e dado: negativo, acima de 20% ao mes, ou o fundo que
    # preenche em percentual (0,80), que nao da para distinguir de 80%
    assert cf._bounded_ratio("-0.000976") is None
    assert cf._bounded_ratio("0,80") is None
    assert cf._bounded_ratio("10.19") is None
    assert cf._bounded_ratio("") is None and cf._bounded_ratio(None) is None


def test_foto_e_serie_mensal_leem_o_rendimento_na_mesma_unidade():
    # parse_informe (foto do ultimo mes) e parse_informe_monthly (serie) sao
    # gemeos: a mesma coluna, a mesma unidade.
    buf = io.BytesIO(_build_informe_zip(2024))
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("inf_mensal_fii_geral_2024.csv", (
            "CNPJ_Fundo_Classe;Data_Referencia;Nome_Fundo_Classe\n"
            "11.728.688/0001-47;2024-01-31;FUNDO TESTE\n"
            "11.728.688/0001-47;2024-02-29;FUNDO TESTE\n").encode("latin-1"))
    data = buf.getvalue()
    foto = cf.parse_informe(data, 2024)["11728688000147"]
    serie = cf.parse_informe_monthly(data, 2024)["11728688000147"]
    assert foto["dy_patrimonial_mes"] == serie[-1]["dy_patrimonial_mes"] == 0.0075


def _df(rows):
    import pandas as pd
    return pd.DataFrame(rows, dtype=str)


def test_reapresentacao_na_mesma_competencia_vence_pela_entrega_em_qualquer_ordem():
    v1 = {"CNPJ_Fundo_Classe": "36.501.128/0001-86", "Data_Referencia": "2026-09-01",
          "Versao": "1", "Data_Entrega": "2026-10-15", "Valor_Patrimonial_Cotas": "195.67"}
    v3 = {**v1, "Versao": "3", "Data_Entrega": "2026-11-04", "Valor_Patrimonial_Cotas": "100.30"}
    for linhas in ([v1, v3], [v3, v1]):
        got = cf._latest_per_cnpj(_df(linhas))["36501128000186"]
        assert got["Valor_Patrimonial_Cotas"] == "100.30"
        got_m = cf._by_cnpj_month(_df(linhas))[("36501128000186", "2026-09-01")]
        assert got_m["Valor_Patrimonial_Cotas"] == "100.30"


def test_desempate_sem_versao_usa_data_de_entrega():
    a = {"CNPJ_Fundo_Classe": "1", "Data_Referencia": "2026-09-01", "Data_Entrega": "2026-10-15", "x": "velha"}
    b = {**a, "Data_Entrega": "2026-11-04", "x": "nova"}
    assert cf._latest_per_cnpj(_df([b, a]))["1"]["x"] == "nova"
    assert cf._latest_per_cnpj(_df([a, b]))["1"]["x"] == "nova"


def test_competencia_mais_nova_vence_versao_maior_da_antiga():
    old = {"CNPJ_Fundo_Classe": "1", "Data_Referencia": "2026-08-01", "Versao": "9", "x": "ago"}
    new = {**old, "Data_Referencia": "2026-09-01", "Versao": "1", "x": "set"}
    assert cf._latest_per_cnpj(_df([new, old]))["1"]["x"] == "set"
