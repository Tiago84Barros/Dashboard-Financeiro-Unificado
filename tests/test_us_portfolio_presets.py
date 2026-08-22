"""Perfis da Criação de Portfólio EUA.

A trava principal não é o conteúdo dos textos — é a correspondência entre o que
o perfil PROMETE nos rótulos e o que o motor realmente aceita. Um perfil que
grava uma chave inexistente, ou um valor fora das opções do controle, não falha:
ele é silenciosamente ignorado e o usuário roda a carteira achando que aplicou.
"""
from __future__ import annotations

import pytest

from core.us_portfolio_presets import (
    AMPLO,
    DEFENSIVO,
    PERSONALIZADO,
    PRESETS,
    RECOMENDADO,
    avaliar_configuracao,
    identificar_perfil,
)


def test_identifica_cada_perfil_pelos_proprios_valores():
    for nome, preset in PRESETS.items():
        assert identificar_perfil(dict(preset.valores)) == nome


def test_configuracao_fora_dos_perfis_e_personalizada():
    valores = dict(PRESETS[RECOMENDADO].valores)
    valores["us_create_topn"] = 37
    assert identificar_perfil(valores) == PERSONALIZADO


def test_recomendado_nao_dispara_nenhum_alerta():
    """Perfil recomendado que já nasce com aviso treinaria o usuário a ignorar
    todos os avisos."""
    assert avaliar_configuracao(dict(PRESETS[RECOMENDADO].valores)) == []


def test_amplo_declara_seus_proprios_custos():
    """O perfil de diagnóstico É a configuração cara — precisa dizer isso."""
    alertas = avaliar_configuracao(dict(PRESETS[AMPLO].valores))
    texto = " ".join(alertas)
    assert "melhor de dois" in texto          # amostra mínima 2
    assert "preferenc" in texto.lower()       # sem piso de volume
    assert "DESLIGADO" in texto               # piso de qualidade
    assert PRESETS[AMPLO].ressalva


def test_defensivo_avisa_do_custo_de_ter_menos_ativos():
    assert "risco por" in PRESETS[DEFENSIVO].ressalva


def test_piso_impossivel_por_ativo_e_apontado():
    """Teto abaixo de 1/N não é preferência, é impossibilidade aritmética."""
    valores = dict(PRESETS[RECOMENDADO].valores)
    valores["us_create_max_weight"] = 3        # 3% com 30 ativos exige >= 3,34%
    alertas = " ".join(avaliar_configuracao(valores))
    assert "matematicamente impossível" in alertas


def test_piso_de_score_inerte_esta_documentado_mas_nao_alerta():
    """Inércia é documentação; alerta é custo.

    O piso de score de entrada não muda nada de 30 a 55 e nem mesmo a 65 altera
    a carteira final. O usuário precisa saber disso — senão mexe no controle,
    nada acontece, e ele conclui que o mercado é que não tem nada melhor. Mas
    virar ALERTA faria o perfil recomendado nascer com um aviso, e aviso que
    aparece sempre treina o usuário a ignorar todos.
    """
    assert any("INERTE" in e for e in PRESETS[RECOMENDADO].evidencias)
    assert avaliar_configuracao(dict(PRESETS[RECOMENDADO].valores)) == []

    # O caso que DESTRÓI a carteira continua sendo alerta.
    valores = dict(PRESETS[RECOMENDADO].valores)
    valores["us_create_entry_score"] = 80
    assert any("NENHUMA" in a for a in avaliar_configuracao(valores))


# ── Correspondência com os controles reais da tela ───────────────────────────

def test_toda_chave_de_perfil_existe_como_controle_na_view():
    """Chave errada não falha: o perfil é aplicado e o controle ignora."""
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1]
             / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    for preset in PRESETS.values():
        for chave in preset.valores:
            assert f'key="{chave}"' in fonte, f"{chave} não é key de nenhum widget"


def test_valores_de_selectbox_batem_com_as_opcoes_da_tela():
    """Valor fora da lista de opções quebra o widget em runtime."""
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1]
             / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    for preset in PRESETS.values():
        for chave, valor in preset.valores.items():
            if isinstance(valor, str):
                assert f'"{valor}"' in fonte, f"{chave}={valor!r} não está nas opções"


def test_o_recomendado_cobre_todos_os_controles_dos_outros_perfis():
    """Aplicar um perfil e depois outro não pode deixar resíduo do anterior.

    Se DEFENSIVO grava uma chave que RECOMENDADO não grava, voltar ao
    recomendado mantém o valor defensivo — e a tela mostra "Equilibrado"
    exibindo uma configuração que não é a dele.
    """
    base = set(PRESETS[RECOMENDADO].valores)
    for nome, preset in PRESETS.items():
        se_sobram = set(preset.valores) - base
        # A exceção conhecida é o spread de resiliência, que só existe quando a
        # exigência está ligada — e o recomendado a mantém desligada.
        assert se_sobram <= {"us_create_resilience_spread"}, (nome, se_sobram)


@pytest.mark.parametrize("nome", list(PRESETS))
def test_todo_perfil_declara_evidencia(nome):
    """Perfil sem evidência é gosto disfarçado de medição."""
    assert PRESETS[nome].evidencias
    assert PRESETS[nome].resumo
