"""Regressões da classificação setorial exibida no universo de FIIs."""
from core.fii_taxonomy import categoria_fii


def test_casos_do_problema_reportado_usam_segmentos_conhecidos_do_mercado():
    assert categoria_fii("tijolo", ticker="GRUL11", segmento="Aeroporto",
                         nome="Icatu Vanguarda GRU Logístico") == "Logística"
    assert categoria_fii("tijolo", ticker="RZTR11", segmento="Agronegócio",
                         nome="Riza Terrax") == "Agronegócio"
    assert categoria_fii("tijolo", ticker="CXAG11", segmento="Agência Bancária",
                         nome="Caixa Agências") == "Renda Urbana"
    assert categoria_fii("fof", ticker="PATL11", segmento="Alimentício",
                         nome="Pátria Logística FII") == "Logística"
    assert categoria_fii("tijolo", ticker="BTML11", segmento="Alimentação",
                         nome="Barra Malls FII") == "Shoppings"


def test_setor_de_locatario_nao_supera_nome_inequivoco_do_fundo():
    assert categoria_fii("hibrido", segmento="Alimentos",
                         nome="Bluemacaw Logística FII") == "Logística"
    assert categoria_fii("tijolo", segmento="Tecnologia",
                         nome="Edifício Corporate Offices FII") == "Lajes Corporativas"


def test_tipo_estrutural_nao_e_sobrescrito_pelo_setor_bruto_do_provedor():
    assert categoria_fii("papel", ticker="MXRF11", segmento="Logística",
                         nome="Maxi Renda FII") == "Papel/CRI"
    assert categoria_fii("fof", ticker="TEST11", segmento="Residencial",
                         nome="FII sem indicação setorial") == "Fundo de Fundos"


def test_categorias_amplas_sao_fallback_quando_nao_ha_evidencia_especifica():
    assert categoria_fii("tijolo", segmento="1", nome="FII Exemplo") == "Tijolo"
    assert categoria_fii("papel", segmento="Outros", nome="FII Exemplo") == "Papel/CRI"
    assert categoria_fii("fof", segmento="Outros", nome="FII Exemplo") == "Fundo de Fundos"
    assert categoria_fii("hibrido", segmento="Outros", nome="FII Exemplo") == "Híbrido"


def test_ausencia_nao_recebe_classificacao_inventada():
    assert categoria_fii(None) == "Não classificado"
    assert categoria_fii("desconhecido", segmento="Outros") == "Não classificado"


def test_variantes_de_acentuacao_e_caixa_sao_normalizadas():
    assert categoria_fii("TIJOLO", segmento="SERVIÇOS MÉDICO - HOSPITALARES") == "Saúde"
    assert categoria_fii("tijolo", segmento="EDUCAÇÃO") == "Educacional"
