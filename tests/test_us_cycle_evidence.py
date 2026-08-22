"""Resiliência em recessão do módulo EUA — exige a crise de 2008."""
from core.us_cycle_evidence import (
    FRAGIL,
    INTERMEDIARIO,
    MIN_ANOS_CRISE,
    RESILIENTE,
    SEM_EVIDENCIA,
    classificar,
    cobertura,
    montar,
)


def test_faixas_separam_os_casos():
    assert classificar(1.10, anos_2008=3) == RESILIENTE
    assert classificar(0.60, anos_2008=3) == INTERMEDIARIO
    assert classificar(0.20, anos_2008=3) == FRAGIL


def test_so_covid_nao_recebe_veredito():
    """Decisão central deste módulo.

    Só 548 das 3.048 empresas (18%) alcançam 2008; 2.244 têm apenas COVID. A
    COVID foi choque de oferta, curto e seguido de estímulo fiscal — empresa de
    software atravessou 2020 com margem intacta e isso não diz nada sobre
    recessão de demanda. No módulo B3 esse mesmo formato fez commodity medir
    como mais defensiva que saneamento.
    """
    m = montar({"SOCOVID": {"razao": 1.05, "margem_normal": 0.2,
                            "margem_crise": 0.21, "anos_2008": 0, "anos_covid": 3}})
    assert m["SOCOVID"].classe == SEM_EVIDENCIA
    assert not m["SOCOVID"].confiavel
    assert m["SOCOVID"].crises_medidas == "2020"


def test_um_ano_de_crise_nao_basta():
    assert classificar(0.3, anos_2008=1) == SEM_EVIDENCIA
    assert MIN_ANOS_CRISE == 2


def test_com_2008_recebe_veredito_e_declara_as_crises():
    m = montar({"VETERANA": {"razao": 0.85, "margem_normal": 0.14,
                             "margem_crise": 0.12, "anos_2008": 4, "anos_covid": 3}})
    v = m["VETERANA"]
    assert v.confiavel and v.classe == RESILIENTE
    assert v.crises_medidas == "2008-09 e 2020"


def test_sem_razao_nao_vira_fragil():
    m = montar({"X": {"razao": None, "anos_2008": 4, "anos_covid": 3}})
    assert m["X"].classe == SEM_EVIDENCIA


def test_cobertura_declara_a_base():
    """Sem isto o usuário veria tabela curta e suporia que as ausentes são normais."""
    c = cobertura(montar({
        "A": {"razao": 0.9, "anos_2008": 4, "anos_covid": 3},
        "B": {"razao": 1.0, "anos_2008": 0, "anos_covid": 3},
        "C": {"razao": None, "anos_2008": 0, "anos_covid": 0},
    }))
    assert c == {"total": 3, "com_veredito": 1, "so_covid": 1, "sem_crise": 1}


def test_dimensao_de_governo_nao_foi_trazida():
    """Escolha do usuário: os EUA quase não têm estatal listada, e o
    equivalente (tarifa, reembolso público, defesa) é outro objeto."""
    import core.us_cycle_evidence as mod
    proibidos = [n for n in dir(mod)
                 if any(t in n.lower() for t in ("regula", "estatal", "governo"))]
    assert not proibidos, f"dimensão de governo vazou: {proibidos}"


def test_razao_absurda_e_artefato_nao_resiliencia():
    """ESS aparecia com razão 77,90 e AZTA com 59,74 na varredura real.

    Empresa não triplica margem operacional numa recessão. Sem o teto, esses
    casos entrariam como as MAIS resilientes do universo — o pior falso
    positivo possível, porque premia o artefato contábil.
    """
    from core.us_cycle_evidence import RAZAO_MAXIMA_PLAUSIVEL

    assert classificar(77.90, anos_2008=4) == SEM_EVIDENCIA
    assert classificar(RAZAO_MAXIMA_PLAUSIVEL + 0.01, anos_2008=4) == SEM_EVIDENCIA
    assert classificar(RAZAO_MAXIMA_PLAUSIVEL, anos_2008=4) == RESILIENTE


def test_colapso_profundo_segue_sendo_fragil():
    """A cauda negativa NÃO tem teto: margem que vira −42× a normal é colapso,
    e truncar ali perderia informação verdadeira."""
    assert classificar(-42.43, anos_2008=4) == FRAGIL
