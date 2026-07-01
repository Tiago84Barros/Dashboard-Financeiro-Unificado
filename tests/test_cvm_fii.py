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
        f"{cnpj};{year}-01-31;1000000,00;100,50;5000;0,80\n"
        f"{cnpj};{year}-02-29;1010000,00;101,00;5100;0,75\n"
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
    # composição muda mês a mês (imóveis 900 -> 800)
    assert jan["pct_imoveis"] == 0.9
    assert fev["pct_imoveis"] == 0.8
