"""Resiliência medida e dependência regulatória — casos do universo real."""
from core.b3_cycle_evidence import (
    FRAGIL,
    INTERMEDIARIO,
    LIVRE,
    REGULADO_ESTATAL,
    REGULADO_TARIFA,
    RESILIENTE,
    SEM_EVIDENCIA,
    Resiliencia,
    classificar_resiliencia,
    classify_regulation,
    divergencias_de_ciclo,
    peso_regulado,
)

# Medidos em 30/07/2026 (margem de crise ÷ margem normal).
REAIS = {
    "ISAE4": 1.50, "VIVT3": 0.93, "SBSP3": 0.93, "SHUL4": 0.89,
    "WEGE3": 0.85, "EUCA4": 0.68, "LEVE3": 0.59, "PETR4": 0.27,
    "GGBR4": -0.53, "AZUL4": -1.07,
}


def _res(tk: str, anos: int = 3) -> Resiliencia:
    razao = REAIS[tk]
    return Resiliencia(tk, razao, 0.15, 0.15 * razao, anos,
                       classificar_resiliencia(razao, anos))


def test_faixas_separam_casos_conhecidos():
    assert _res("ISAE4").classe == RESILIENTE       # margem SUBIU na crise
    assert _res("SBSP3").classe == RESILIENTE
    assert _res("LEVE3").classe == INTERMEDIARIO    # nem defensiva, nem colapso
    assert _res("PETR4").classe == FRAGIL
    assert _res("AZUL4").classe == FRAGIL           # margem virou negativa


def test_leve3_nao_e_defensiva_apesar_da_reposicao():
    """Correção de uma leitura anterior baseada em narrativa, não em dado.

    A tese de que ~40% de receita de reposição tornaria a Mahle defensiva não
    aparece nos números: 0,59 é PIOR que a mediana do próprio setor Consumo
    Cíclico (0,78). O rótulo cíclico se sustenta.
    """
    assert _res("LEVE3").classe != RESILIENTE


def test_um_ano_de_crise_nao_basta():
    """Com um só ano, a leitura confunde ciclo com evento isolado da empresa."""
    assert classificar_resiliencia(0.2, anos_medidos=1) == SEM_EVIDENCIA
    assert not _res("PETR4", anos=1).confiavel


def test_sem_razao_nao_vira_fragil():
    """Ausência de medição nunca vira veredito."""
    assert classificar_resiliencia(None, 3) == SEM_EVIDENCIA
    assert classificar_resiliencia(float("nan"), 3) == SEM_EVIDENCIA


def test_estatal_tem_precedencia_sobre_o_setor():
    """PETR4 atua em setor livre, mas o controlador é a União."""
    assert classify_regulation("Petróleo, Gás e Biocombustíveis", "PETR4") == REGULADO_ESTATAL
    assert classify_regulation("Utilidade Pública", "SBSP3") == REGULADO_TARIFA
    assert classify_regulation("Comunicações", "VIVT3") == REGULADO_TARIFA
    assert classify_regulation("Bens Industriais", "WEGE3") == LIVRE


def test_peso_regulado_mede_dinheiro_e_nao_nomes():
    """Mesmo erro já corrigido no card de cíclicos: contagem não é peso."""
    pesos = {"PETR4": 0.40, "WEGE3": 0.20, "LEVE3": 0.20, "EUCA4": 0.20}
    setores = {"PETR4": "Petróleo, Gás e Biocombustíveis",
               "WEGE3": "Bens Industriais", "LEVE3": "Consumo Cíclico",
               "EUCA4": "Materiais Básicos"}
    pct, mapa = peso_regulado(pesos, setores)
    assert pct == 0.40                       # 1 nome de 4, mas 40% do dinheiro
    assert mapa["PETR4"] == REGULADO_ESTATAL


def test_peso_regulado_sem_pesos_nao_divide_por_zero():
    pct, _ = peso_regulado({}, {})
    assert pct == 0.0


def test_divergencia_aponta_ciclica_que_segurou():
    """WEGE3 e SHUL4: rótulo cíclico, comportamento defensivo."""
    tax = {"WEGE3": "ciclico", "SHUL4": "ciclico", "PETR4": "ciclico",
           "SBSP3": "defensivo"}
    medidas = {t: _res(t) for t in ("WEGE3", "SHUL4", "PETR4", "SBSP3")}
    achados = divergencias_de_ciclo(tax, medidas)
    assert [t for t, _, _ in achados] == ["SHUL4", "WEGE3"]   # ordem total


def test_divergencia_aponta_defensiva_que_desabou():
    tax = {"XPTO3": "defensivo"}
    m = Resiliencia("XPTO3", 0.10, 0.20, 0.02, 3, FRAGIL)
    assert divergencias_de_ciclo(tax, {"XPTO3": m})[0][0] == "XPTO3"


def test_divergencia_ignora_medicao_fraca():
    tax = {"WEGE3": "ciclico"}
    assert divergencias_de_ciclo(tax, {"WEGE3": _res("WEGE3", anos=1)}) == []
