"""Tradução CVM SETOR_ATIV → taxonomia B3 (scripts/enrich_setores_cvm)."""
from scripts.enrich_setores_cvm import _map_cvm_setor, _norm


def test_map_setores_diretos():
    assert _map_cvm_setor("Energia Elétrica")[0] == "Utilidade Pública"
    assert _map_cvm_setor("Bancos") == ("Financeiro", "Intermediários Financeiros", "Bancos")
    assert _map_cvm_setor("Telecomunicações")[0] == "Comunicações"
    assert _map_cvm_setor("Petróleo e Gás")[0] == "Petróleo, Gás e Biocombustíveis"


def test_map_ignora_acentos_e_caixa():
    assert _map_cvm_setor("energia eletrica")[0] == "Utilidade Pública"
    assert _map_cvm_setor("METALURGIA E SIDERURGIA")[0] == "Materiais Básicos"


def test_holding_herda_do_sufixo():
    # "Emp. Adm. Part. - X" herda o setor de X
    assert _map_cvm_setor("Emp. Adm. Part. - Saneamento, Serv. Água e Gás")[0] == "Utilidade Pública"
    assert _map_cvm_setor("Emp. Adm. Part. - Intermediação Financeira")[0] == "Financeiro"


def test_holding_sem_sufixo_conhecido_vira_holding():
    assert _map_cvm_setor("Emp. Adm. Part. - Sem Setor Principal")[0] == "Financeiro"


def test_setor_desconhecido_retorna_none():
    assert _map_cvm_setor("Categoria Inexistente XYZ") is None


def test_norm_remove_sufixos_societarios():
    assert _norm("Alupar Investimento S.A.") == "ALUPARINVESTIMENTO"
    assert _norm("JSL S.A.") == "JSL"
