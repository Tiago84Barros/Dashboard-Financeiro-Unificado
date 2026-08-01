"""Perfis pré-configurados da carteira B3 (puro).

Cada valor dos perfis vem da varredura automatizada de 29/07/2026 sobre o
universo real — não de preferência. Os testes travam essa correspondência.
"""
from __future__ import annotations

import pytest

from core.b3_portfolio_presets import (
    AMPLO, CONSERVADOR, PERSONALIZADO, PRESETS, RECOMENDADO,
    avaliar_configuracao, identificar_perfil,
)


# ── estrutura dos perfis ─────────────────────────────────────────────────────

def test_todo_perfil_declara_evidencia():
    """Perfil sem evidência é preferência disfarçada de recomendação."""
    for nome, preset in PRESETS.items():
        assert preset.evidencias, f"{nome} sem evidência"
        assert preset.resumo, f"{nome} sem resumo"


def test_recomendado_usa_criterio_economico():
    """Os modos estatísticos aprovam zero segmentos na B3."""
    assert PRESETS[RECOMENDADO].valores["pb3_criterio_aprov2"].startswith("Econômico")


def test_recomendado_tem_tetos_de_concentracao_ativos():
    valores = PRESETS[RECOMENDADO].valores
    assert valores["pb3_teto_setor"] < 100
    assert valores["pb3_teto_ciclico"] < 100


def test_recomendado_nao_liga_resiliencia():
    """A 5 p.p. o filtro corta de 10 para 6 ativos e penaliza utilities."""
    assert PRESETS[RECOMENDADO].valores["pb3_resiliencia"] is False


def test_perfis_de_risco_declaram_ressalva():
    assert PRESETS[CONSERVADOR].ressalva
    assert PRESETS[AMPLO].ressalva
    assert "não use para decidir" in PRESETS[AMPLO].resumo.lower() \
        or "diagnóstico" in PRESETS[AMPLO].ressalva.lower()


def test_amplo_nao_finge_ser_recomendacao():
    """Sem tetos, o perfil amplo não protege concentração — precisa dizer."""
    assert PRESETS[AMPLO].valores["pb3_teto_setor"] == 100
    assert "concentração" in PRESETS[AMPLO].ressalva.lower()


# ── identificação do perfil ativo ────────────────────────────────────────────

def test_identifica_perfil_exato():
    assert identificar_perfil(dict(PRESETS[RECOMENDADO].valores)) == RECOMENDADO
    assert identificar_perfil(dict(PRESETS[AMPLO].valores)) == AMPLO


def test_valor_alterado_vira_personalizado():
    valores = dict(PRESETS[RECOMENDADO].valores)
    valores["pb3_teto_setor"] = 42
    assert identificar_perfil(valores) == PERSONALIZADO


def test_configuracao_vazia_e_personalizada():
    assert identificar_perfil({}) == PERSONALIZADO


# ── alertas de configuração com custo medido ─────────────────────────────────

def test_recomendado_nao_dispara_alerta():
    assert avaliar_configuracao(dict(PRESETS[RECOMENDADO].valores)) == []


def test_criterio_estatistico_alerta_sobre_zero_aprovados():
    alertas = avaliar_configuracao({"pb3_criterio_aprov2": "Sinal fundamental (Rank-IC)",
                                    "pb3_teto_setor": 30, "pb3_teto_ciclico": 60})
    assert any("zero segmentos" in a for a in alertas)


def test_resiliencia_com_spread_alto_alerta_com_o_numero_medido():
    alertas = avaliar_configuracao({
        "pb3_criterio_aprov2": "Econômico (Brasil)",
        "pb3_resiliencia": True, "pb3_roic_spread": 5.0,
        "pb3_teto_setor": 30, "pb3_teto_ciclico": 60})
    assert any("10 para 6" in a for a in alertas)
    assert any("utilities" in a for a in alertas)


def test_resiliencia_em_zero_nao_alerta():
    """A 0 p.p. o custo é 1 ativo — não merece alerta."""
    alertas = avaliar_configuracao({
        "pb3_criterio_aprov2": "Econômico (Brasil)",
        "pb3_resiliencia": True, "pb3_roic_spread": 0.0,
        "pb3_teto_setor": 30, "pb3_teto_ciclico": 60})
    assert not any("p.p." in a for a in alertas)


def test_sem_tetos_alerta_sobre_concentracao():
    alertas = avaliar_configuracao({
        "pb3_criterio_aprov2": "Econômico (Brasil)",
        "pb3_teto_setor": 100, "pb3_teto_ciclico": 100})
    assert any("único fator" in a for a in alertas)


def test_margem_alta_alerta_sobre_encolhimento():
    alertas = avaliar_configuracao({
        "pb3_criterio_aprov2": "Econômico (Brasil)", "pb3_thr_selic_hist": 25.0,
        "pb3_teto_setor": 30, "pb3_teto_ciclico": 60})
    assert any("7 ativos" in a for a in alertas)


def test_grupo_minimo_baixo_alerta():
    alertas = avaliar_configuracao({
        "pb3_criterio_aprov2": "Econômico (Brasil)", "pb3_min_empresas": 1,
        "pb3_teto_setor": 30, "pb3_teto_ciclico": 60})
    assert any("1–2 empresas" in a for a in alertas)


def test_alertas_nunca_bloqueiam_apenas_informam():
    """avaliar_configuracao devolve texto; a decisão continua do usuário."""
    alertas = avaliar_configuracao(dict(PRESETS[AMPLO].valores))
    assert isinstance(alertas, list)
    assert all(isinstance(a, str) for a in alertas)


# ── integração com a interface ───────────────────────────────────────────────

def test_seletor_de_perfil_renderiza_com_evidencia_e_alerta():
    """O perfil precisa aparecer com a evidência à vista, e a configuração
    atual (padrão dos widgets, sem tetos) deve disparar o alerta medido."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import views.portfolio_b3 as view
view._render_perfil_configuracao()
""").run(timeout=60)

    assert not app.exception
    rotulos = [s.label for s in app.selectbox]
    assert "Perfil de configuração" in rotulos
    assert any("Aplicar perfil" in b.label for b in app.button)
    legendas = "\n".join(c.value for c in app.caption)
    assert "Equilibrado" in legendas
    # sem nada configurado, os tetos ficam ausentes → alerta de concentração
    avisos = "\n".join(w.value for w in app.warning)
    assert "único fator" in avisos


def test_aplicar_perfil_grava_todos_os_parametros():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import views.portfolio_b3 as view
view._render_perfil_configuracao()
""").run(timeout=60)
    app.button(key="pb3_aplicar_perfil").click().run(timeout=60)

    assert not app.exception
    for chave, esperado in PRESETS[RECOMENDADO].valores.items():
        assert app.session_state[chave] == esperado, chave
    # com o perfil aplicado, o alerta de concentração some
    avisos = "\n".join(w.value for w in app.warning)
    assert "único fator" not in avisos


def test_valores_de_preset_existem_nas_opcoes_do_widget():
    """Um preset que grave valor fora das opções quebra o app em runtime.

    `_aplicar` escreve direto em `st.session_state[chave]`, e o Streamlit
    rejeita valor ausente da lista de opções do selectbox. Ler as opções do
    fonte da view amarra as duas pontas: renomear um rótulo lá sem atualizar o
    preset aqui passa a falhar no CI, não na tela do usuário.
    """
    import re
    from pathlib import Path

    from core.b3_portfolio_presets import PRESETS

    fonte = (Path(__file__).resolve().parents[1]
             / "views" / "portfolio_b3.py").read_text(encoding="utf-8")

    for chave in ("pb3_min_mcap", "pb3_min_adtv"):
        # Captura a lista de opções literal que precede key="<chave>".
        bloco = re.search(
            r"\[([^\]]*?)\][^\[\]]*?key=\"" + chave + r"\"", fonte, re.S)
        assert bloco, f"não achei as opções de {chave} na view"
        opcoes = set(re.findall(r'"([^"]+)"', bloco.group(1)))
        assert opcoes, f"lista de opções de {chave} veio vazia"
        for preset in PRESETS.values():
            if chave in preset.valores:
                assert preset.valores[chave] in opcoes, (
                    f"{preset.nome}: {chave}={preset.valores[chave]!r} não está "
                    f"entre as opções {sorted(opcoes)}")


def test_piso_de_liquidez_alto_avisa_o_custo_medido():
    from core.b3_portfolio_presets import avaliar_configuracao

    alertas = avaliar_configuracao({"pb3_min_adtv": "≥ R$ 20 mi"})
    assert any("113 empresas" in a and "LEVE3" in a for a in alertas)

    # O recomendado não pode disparar alerta sobre si mesmo.
    from core.b3_portfolio_presets import PRESETS, RECOMENDADO
    assert not [a for a in avaliar_configuracao(PRESETS[RECOMENDADO].valores)
                if "liquidez" in a.lower() or "tamanho" in a.lower()]


def test_sem_piso_de_liquidez_tambem_avisa():
    from core.b3_portfolio_presets import avaliar_configuracao

    alertas = avaliar_configuracao({"pb3_min_adtv": "Sem filtro"})
    assert any("220 das 442" in a for a in alertas)
