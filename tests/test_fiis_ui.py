"""Contratos de interface da seção Seleção de FIIs.

Além do que a tela deve mostrar, estes testes travam o que NÃO pode ter mudado:
seleção, filtros, cálculos e ranking. As alterações desta rodada são de
interface, e um teste que só olhasse a interface deixaria passar exatamente o
tipo de regressão que mais custa aqui.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import views.fiis as fiis

_RAIZ = Path(__file__).resolve().parents[1]
_BRUTO = (_RAIZ / "views" / "fiis.py").read_text(encoding="utf-8")


def _sem_comentarios(codigo: str) -> str:
    """Só o que roda. Comentários explicam por que algo FOI removido e citam a
    string removida — procurá-la no fonte cru acusaria a própria explicação."""
    return "\n".join(
        linha for linha in codigo.splitlines() if not linha.lstrip().startswith("#")
    )


# Emenda literais adjacentes: o texto que o usuário lê não tem as quebras de
# linha que o código-fonte tem.
_FONTE = re.sub(r'"\s*\n\s*"', "",
                re.sub(r"'\s*\n\s*'", "", _sem_comentarios(_BRUTO)))


# ── 1. Aviso redundante de universo em diligência ────────────────────────────

def test_banner_de_diligencia_saiu_de_todas_as_abas():
    """Repetia, em toda aba, o que o rótulo da própria aba já diz."""
    assert "Universo bruto em diligência" not in _FONTE
    corpo = inspect.getsource(fiis.render)
    assert "st.warning(" not in corpo


def test_estado_do_gate_continua_visivel():
    """O sinal não pode ter sumido junto com o banner."""
    corpo = inspect.getsource(fiis.render)
    # O painel de qualidade dos dados segue mostrando o estado do gate.
    assert "_render_data_health_summary(health_metrics, gate)" in corpo
    # A aprovação continua sendo anunciada — aí é notícia, não rótulo.
    assert "apta à publicação como Carteira Modelo" in corpo


def test_primeira_aba_e_estatica_e_nao_rotula_pelo_gate():
    """A primeira aba só apresenta o universo disponível — não é diligência nem
    seleção, então seu rótulo não pode mais alternar com o gate de publicação."""
    assert fiis._TABS[0] == "📋 FIIs Disponíveis"
    corpo = inspect.getsource(fiis.render)
    assert "_TABS[1:]" not in corpo
    assert 'status_copy["tab"]' not in corpo


def test_rodape_da_primeira_aba_ainda_reflete_o_gate():
    """O estado do gate não sumiu: só saiu do rótulo do botão da aba."""
    copy_ok = fiis._selection_status_copy(validation_applicable=True, can_publish=True)
    copy_pendente = fiis._selection_status_copy(validation_applicable=False, can_publish=False)
    assert "atende ao gate vigente" in copy_ok["footer"]
    assert "universo bruto de FIIs disponíveis" in copy_pendente["footer"]


# ── 2. Ranking sem cards e sem medianas ──────────────────────────────────────

def test_ranking_nao_tem_mais_cards_nem_medianas():
    corpo = inspect.getsource(fiis._tab_ranking)
    assert "DY 12m mediano" not in corpo
    assert "P/VP mediano" not in corpo
    assert "🏆 Top" not in corpo
    assert "_render_grupo" not in corpo
    # O universo continua sendo apresentado — em tabela.
    assert "st.dataframe(show" in corpo


def test_helpers_dos_cards_foram_removidos():
    """Código que só os cards usavam não pode ficar para trás."""
    for nome in ("_fii_card_html", "_render_grupo", "_score_cls"):
        assert not hasattr(fiis, nome), nome
        assert nome not in _FONTE, nome


def test_filtros_do_ranking_continuam_iguais():
    """Nenhum filtro pode ter mudado: a alteração era só de exibição."""
    corpo = inspect.getsource(fiis._tab_ranking)
    for controle in ('st.selectbox("Segmento"', 'st.selectbox("Tipo"',
                     'st.slider("DY 12m mín. (%)"', 'st.slider("P/VP máx."'):
        assert controle in corpo, controle
    assert 'view["DY_12m"].fillna(0) * 100 >= dy_min' in corpo
    assert 'view["P/VP"].fillna(99) <= pvp_max' in corpo


# ── 3 e 8. Scroll ao topo ────────────────────────────────────────────────────

def test_trocar_de_aba_rola_para_o_topo():
    corpo = inspect.getsource(fiis.render)
    assert 'st.session_state["_fii_rolar_topo"] = True' in corpo
    assert 'st.session_state.pop("_fii_rolar_topo", False)' in corpo
    assert "rolar_para_topo()" in corpo


def test_rolagem_usa_o_componente_compartilhado():
    """Mesmo mecanismo das vitrines B3/EUA e do Controle Financeiro."""
    from design.componentes import rolar_para_topo
    assert fiis.rolar_para_topo is rolar_para_topo


def test_rolagem_e_pontual_e_nao_persistente():
    """pop e não get: senão qualquer filtro da aba jogaria o usuário ao topo."""
    corpo = inspect.getsource(fiis.render)
    assert 'st.session_state.get("_fii_rolar_topo"' not in corpo


# ── 4. Carteira e elegibilidade recolhidos ───────────────────────────────────

def test_controles_de_preferencia_nascem_recolhidos():
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    assert 'st.expander("⚙️ Carteira e elegibilidade", expanded=False)' in corpo
    assert 'st.expander("🌐 Cenário macroeconômico e estresse", expanded=False)' in corpo
    # Sem cabeçalho solto fora do expander.
    assert 'st.markdown("**Carteira e elegibilidade**")' not in corpo


def test_nenhum_parametro_de_selecao_mudou():
    """Os defaults definem a carteira — mudá-los mudaria o resultado."""
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    for default in (
        '"Nº máximo de FIIs", 8, 20, 12',
        '"Máx. por FII (%)", 5, 25, 15, 1',
        '"Liquidez mín. (R$ mi/dia)", 0.0, 20.0, 1.0, .5',
        '"Histórico mín. (meses)", 0, 60, 24, 6',
        '"DY 12m mín. (%)", 0.0, 20.0, 8.0, .5',
        '"Drawdown máx. tolerado (%)", 10, 60, 35, 5',
        '"Penalização por correlação", 0.0, .30, .12, .02',
        '"Incerteza ponderada máxima da carteira (%)", 20, 50, 35, 1',
        '"Selic (%)", 0.0, 30.0, 15.0, .25',
        '"IPCA (%)", -2.0, 20.0, 4.5, .25',
        '"Choque de vacância (%)", 0.0, 20.0, 8.0, 1.0',
        '"Eventos de crédito (%)", 0.0, 10.0, 3.0, .5',
    ):
        assert default in corpo, default


def test_chaves_de_sessao_dos_controles_preservadas():
    """Trocar a key resetaria a configuração do usuário em silêncio."""
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    for key in ("fii_pref_integrated_assets", "fii_pref_integrated_max_asset",
                "fii_pref_integrated_liquidity", "fii_pref_integrated_history",
                "fii_pref_integrated_dy", "fii_pref_integrated_drawdown",
                "fii_pref_integrated_correlation", "fii_pref_integrated_pvp",
                "fii_pref_integrated_selic", "fii_pref_integrated_ipca",
                "fii_pref_integrated_delta", "fii_pref_integrated_vacancy",
                "fii_pref_integrated_credit", "fii_pref_integrated_uncertainty",
                "fii_pref_integrated_regions", "fii_pref_integrated_properties",
                "fii_pref_integrated_multicategory"):
        assert key in corpo, key


def test_contrato_de_retorno_das_preferencias_intacto():
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    for chave in ("scenario", "portfolio_policy", "eligibility_policy",
                  "correlation_penalty"):
        assert f'"{chave}"' in corpo, chave


# ── 5, 6 e 7. Seções removidas ───────────────────────────────────────────────

def test_subtitulo_de_metodologia_saiu_do_resultado():
    corpo = inspect.getsource(fiis._carteira_integrada)
    assert "Metodologia Integrada" not in corpo
    assert 'st.subheader("Resultado da seleção")' in corpo


def test_diagnostico_dos_filtros_removido():
    corpo = _sem_comentarios(inspect.getsource(fiis._carteira_integrada))
    assert "Diagnóstico dos filtros de elegibilidade" not in corpo
    assert "exclusion_counts" not in corpo
    # O total de elegíveis, que é o número que decide, continua.
    assert "Universo elegível" in corpo


def test_monitoramento_operacional_removido_com_o_calculo():
    assert "Monitoramento operacional" not in _FONTE
    assert not hasattr(fiis, "_render_portfolio_monitor")
    # O cálculo saiu junto: alimentava só aquele expander.
    assert "build_fii_portfolio_monitor" not in _FONTE
    assert "fii_portfolio_monitor" not in _FONTE


def test_motor_do_monitor_segue_no_projeto():
    """Removi a tela, não a capacidade — o módulo e seus testes ficam."""
    from core.fii_portfolio_monitor import build_fii_portfolio_monitor
    assert callable(build_fii_portfolio_monitor)


def test_gate_de_publicacao_da_carteira_intacto():
    """O freio de verdade não podia sair junto com o monitor."""
    corpo = inspect.getsource(fiis._carteira_integrada)
    assert "evaluate_publication_gate(" in corpo
    assert "Rascunho não publicável" in corpo
    assert "Carteira apta à publicação segundo os gates vigentes." in corpo


# ── Motor de seleção intocado ────────────────────────────────────────────────

def test_pipeline_de_selecao_preservado():
    corpo = inspect.getsource(fiis._carteira_integrada)
    for etapa in ("apply_integrated_eligibility(", "score_fiis_by_type(",
                  "evaluate_publication_gate("):
        assert etapa in corpo, etapa


def test_score_do_universo_continua_igual():
    corpo = inspect.getsource(fiis.render)
    assert "score_fiis_by_type(" in corpo
    assert 'df["Score"] = df["Ticker"].map' in corpo
    assert 'ranked = df[df["Score"].notna()].sort_values(' in corpo
