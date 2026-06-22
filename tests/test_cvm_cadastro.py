import core.cvm_cadastro as cad

_CAD = (
    "CNPJ_CIA;DENOM_SOCIAL;DENOM_COMERC;DT_REG;SIT;CD_CVM;SETOR_ATIV;TP_MERC\n"
    "33.000.167/0001-01;PETROLEO BRASILEIRO S.A. PETROBRAS;PETROBRAS;2000-01-01;ATIVO;9512;Petróleo;Bolsa\n"
    "60.872.504/0001-23;ITAU UNIBANCO HOLDING S.A.;ITAU;2000-01-01;ATIVO;19348;Bancos;Bolsa\n"
    "00.000.000/0001-91;BCO BRASIL S.A.;BB;2000-01-01;ATIVO;1023;Bancos;Bolsa\n"
).encode("latin-1")

_FCA = (
    "CNPJ_Companhia;Data_Referencia;Versao;Nome_Empresarial;Valor_Mobiliario;"
    "Sigla_Classe_Acao_Preferencial;Classe_Acao_Preferencial;Codigo_Negociacao;"
    "Composicao_BDR_Unit;Mercado;Sigla_Entidade_Administradora;Entidade_Administradora;"
    "Data_Inicio_Negociacao;Data_Fim_Negociacao;Segmento\n"
    "33.000.167/0001-01;2026-01-01;1;PETROBRAS;Ações Preferenciais;PN;;PETR4;;Bolsa;B3;B3;;;Nível 2\n"
    "33.000.167/0001-01;2026-01-01;1;PETROBRAS;Ações Ordinárias;;;PETR3;;Bolsa;B3;B3;;;Nível 2\n"
    "60.872.504/0001-23;2026-01-01;1;ITAU;Ações Preferenciais;PN;;ITUB4;;Bolsa;B3;B3;;;Nível 1\n"
    "33.000.167/0001-01;2026-01-01;1;PETROBRAS;Debêntures;;;PETRDEB;;Balcão;B3;B3;;;\n"  # ignorar
).encode("latin-1")


def test_parse_cad():
    m = cad.parse_cad(_CAD)
    petr = m[cad.cnpj_digits("33.000.167/0001-01")]
    assert petr["codigo_cvm"] == 9512 and petr["sector"] == "Petróleo"


def test_parse_fca_valmob_only_shares():
    rows = cad.parse_fca_valmob(_FCA)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"PETR4", "PETR3", "ITUB4"}  # debênture (PETRDEB) ignorada


def test_build_map_joins_by_cnpj():
    cad_map = cad.parse_cad(_CAD)
    fca = cad.parse_fca_valmob(_FCA)
    t2c, companies = cad.build_map(cad_map, fca)
    assert t2c["PETR4"] == 9512 and t2c["PETR3"] == 9512 and t2c["ITUB4"] == 19348
    assert companies[9512]["name"].upper().startswith("PETROBRAS")
    assert companies[19348]["sector"] == "Bancos"


def test_cnpj_digits():
    assert cad.cnpj_digits("33.000.167/0001-01") == "33000167000101"
