"""A medição da carteira por perfil não pode ficar para trás do motor.

``data/oos_carteira_b3.json`` (auditoria B3-03) é o único lugar em que a aba
de Criação de Portfólio B3 diz quanto a carteira de cada perfil rendeu fora da
amostra, LÍQUIDA de custos, contra pesos iguais, Selic e Ibovespa. Na 2.30.0 o
perfil Equilibrado mediu +4,2 pp/safra com IC 95% de -0,7 a +10,8 pp em 9
safras -- cruza o zero. Se o score mudar e ninguém remedir, a tela segue
mostrando o número da versão anterior como se fosse desta; a tela avisa
"VENCIDA", mas aviso que ninguém lê não serve de portão. Este teste serve.

Mesmo desenho de ``tests/test_vantagem_oos_nao_envelhece.py``: a versão
corrente é lida da constante do motor, nunca copiada para cá.
"""
from __future__ import annotations

import json

from core.b3_methodology import SCORE_VERSION
from core.b3_oos_carteira import CAMINHO_MEDICAO, VARIANTES, carregar, vencida
from core.b3_portfolio_presets import AMPLO, CONSERVADOR, RECOMENDADO
from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

REMEDIR = ("py -3.12 scripts/medir_oos_carteira_b3.py  (armazém local ligado; "
           "lê fundamentos sem gravar nada)")


def test_medicao_gravada_e_da_versao_que_o_motor_roda():
    motivos = vencida(carregar(), SCORE_VERSION, PRESETS_VERSION)
    assert not motivos, (
        "oos da carteira por perfil desatualizado: " + "; ".join(motivos)
        + f". A aba mostraria o número de outra versão. Remedir: {REMEDIR}")


def test_os_tres_perfis_do_codigo_foram_medidos_com_as_variantes_do_portao():
    dados = json.loads(CAMINHO_MEDICAO.read_text(encoding="utf-8"))
    for nome in (RECOMENDADO, CONSERVADOR, AMPLO):
        assert nome in dados["perfis"], f"perfil {nome!r} sem medição. Remedir: {REMEDIR}"
        variantes = dados["perfis"][nome]["variantes"]
        assert set(variantes) == set(VARIANTES)
        principal = variantes["sem_portao"]["vs_equal_weight"]
        assert principal["n_safras"] >= 2, f"{nome}: amostra sem intervalo"


def test_portao_de_llm_declarado_fora_da_medicao():
    """O portão de LLM lê o dossiê de hoje: medir com ele seria olhar o futuro.
    O JSON tem que dizer isso, senão a tela sugere que a medição o cobre."""
    dados = json.loads(CAMINHO_MEDICAO.read_text(encoding="utf-8"))
    assert dados["portao_llm"]["dentro_da_medicao"] is False
    assert dados["portao_llm"]["por_que"]
    assert dados["fora_do_pit"]


def test_vencida_detecta_troca_de_versao_e_arquivo_ausente(tmp_path):
    assert vencida(carregar(tmp_path / "nao_existe.json"), SCORE_VERSION,
                   PRESETS_VERSION) == ["sem medição gravada"]
    velho = {"versao_metodologia": "0.0.1", "versao_presets": PRESETS_VERSION}
    assert vencida(velho, SCORE_VERSION, PRESETS_VERSION)


def test_banda_do_portao_tem_largura_e_contem_o_sem_portao():
    """Na medição 1.0.0 o veto era um por segmento e, com um nome por
    segmento, 'veta o melhor' e 'veta o pior' empatavam em todas as safras:
    banda de largura zero. Desde a 2.0.0 (um veto por safra na carteira, com
    "não vetar" como opção) a banda contém o sem-portão no bruto e só fica sem
    largura se nenhum veto mudar nada -- o que, numa carteira real, é defeito."""
    from core.b3_oos_carteira import PORTAO_VETA_O_MELHOR, PORTAO_VETA_O_PIOR, SEM_PORTAO

    dados = json.loads(CAMINHO_MEDICAO.read_text(encoding="utf-8"))
    for nome, medido in dados["perfis"].items():
        por_var = {v: {s["safra"]: s for s in medido["variantes"][v]["safras"]
                       if "bruto" in s}
                   for v in (SEM_PORTAO, PORTAO_VETA_O_MELHOR, PORTAO_VETA_O_PIOR)}
        larguras = []
        for safra, sem in por_var[SEM_PORTAO].items():
            adv = por_var[PORTAO_VETA_O_MELHOR][safra]["bruto"]
            fav = por_var[PORTAO_VETA_O_PIOR][safra]["bruto"]
            assert adv - 1e-9 <= sem["bruto"] <= fav + 1e-9, (nome, safra)
            larguras.append(fav - adv)
        assert larguras and max(larguras) > 1e-6, f"{nome}: banda do portão sem largura"
