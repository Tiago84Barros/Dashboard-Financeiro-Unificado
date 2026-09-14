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
from core.fii_carteira_protegida import STATUS_READMITIDO

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


# ── 2. Ranking sem medianas nem Top-N por medalha; universo em cards CSS ─────

def test_ranking_nao_tem_medianas_nem_topn_por_medalha():
    corpo = inspect.getsource(fiis._tab_ranking)
    assert "DY 12m mediano" not in corpo
    assert "P/VP mediano" not in corpo
    assert "🏆 Top" not in corpo
    assert "_render_grupo" not in corpo
    # O universo é apresentado em cards CSS (padrão de Empresas B3/EUA), não
    # em tabela — mas sem reintroduzir a antiga mediana/Top-N por medalha.
    assert "_cards_de_fiis_disponiveis(view)" in corpo
    assert "st.dataframe(show" not in corpo


def test_helpers_dos_cards_antigos_foram_removidos():
    """Código que só os cards antigos (com mediana/medalha) usavam não pode ficar para trás."""
    for nome in ("_fii_card_html", "_render_grupo", "_score_cls"):
        assert not hasattr(fiis, nome), nome
        assert nome not in _FONTE, nome


def test_filtros_do_ranking_mantem_tipo_e_metricas():
    """A categoria canônica substitui o segmento bruto sem mudar os demais filtros."""
    corpo = inspect.getsource(fiis._tab_ranking)
    for controle in ('st.selectbox("Categoria"', 'st.selectbox("Tipo"',
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
        '"Nº máximo de FIIs", 8, 20, 14',
        '"Máx. por FII (%)", 5, 25, 10, 1',
        '"Liquidez mín. (R$ mi/dia)", 0.0, 20.0, 1.0, .5',
        '"Histórico mín. (meses)", 0, 60, 24, 6',
        '"DY recorrente 12m mín. (%)", 0.0, 20.0, 8.0, .5',
        '"Drawdown máx. tolerado (%)", 10, 60, 35, 5',
        '"Penalização por correlação", 0.0, .30, .12, .02',
        '"Incerteza ponderada máxima da carteira (%)", 20, 50, 35, 1',
        '"Choque de vacância (%)", 0.0, 20.0, 8.0, 1.0',
        '"Eventos de crédito (%)", 0.0, 10.0, 3.0, .5',
    ):
        assert default in corpo, default


def test_carteira_exibe_o_yield_recorrente_que_decide_ao_lado_do_divulgado():
    """A mesma renda usada no gate precisa ser visível para quem avalia a carteira."""
    corpo = inspect.getsource(fiis._carteira_integrada)
    tabela = inspect.getsource(fiis._render_portfolio_table)
    assert '"DY recorrente": dy_recorrente(item)' in corpo
    assert '"DY divulgado": item.get("dy_12m")' in corpo
    assert '"DY recorrente"' in tabela
    assert '"DY divulgado"' in tabela


def test_nota_de_viabilidade_da_opacidade_e_exibida():
    """Comportamento, não texto-fonte: a nota tem de sair no aviso.

    A versão anterior afirmava sobre o código-fonte do render, e isso continua
    passando mesmo se o aviso deixar de aparecer na tela.
    """
    avisos = fiis._avisos_de_cessao_de_protecao({
        "viability_notes": ["custo da opacidade afrouxado em 40% do intervalo"],
    })

    assert avisos == [
        "Proteção ajustada para preservar a viabilidade da carteira: "
        "custo da opacidade afrouxado em 40% do intervalo"
    ]
    assert fiis._avisos_de_cessao_de_protecao({}) == []


def test_readmitido_aparece_ticker_a_ticker_com_o_portao_reprovado():
    avisos = fiis._avisos_de_cessao_de_protecao({
        "protecao_cedida_na_elegibilidade": [
            {"ticker": "REND11", "motivos": ["renda recorrente abaixo do mínimo"],
             "na_carteira": True},
            {"ticker": "LOCA11", "motivos": ["concentração de locatário acima do teto"],
             "na_carteira": False},
        ],
    })

    assert len(avisos) == 1
    assert "REND11 — renda recorrente abaixo do mínimo" in avisos[0]
    assert ("LOCA11 — concentração de locatário acima do teto "
            "(fora da carteira final)") in avisos[0]


def test_readmitido_nao_recebe_posicao_inventada_no_card():
    """O card publica ausência de posição, nunca a última posição."""
    sem_posicao = fiis._posicao_no_ranking(
        {"rank": None, "peer_count": 7, "top_percent": None})
    com_posicao = fiis._posicao_no_ranking(
        {"rank": 2, "peer_count": 7, "top_percent": 29})

    assert sem_posicao == "sem posição no ranking do tipo"
    assert com_posicao == "#2 de 7 · top 29%"


def test_o_piso_da_tela_e_o_da_renda_recorrente():
    """A coluna que decide e a que aparece têm de ser a mesma."""
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    assert '"DY recorrente 12m mín. (%)"' in corpo
    assert "min_recurrent_dy_12m=min_dy" in corpo
    assert "min_dy_12m=" not in corpo


def test_slider_de_dy_recorrente_explica_o_que_incide():
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    assert "Incide sobre dy_12m × income_recurrence" in corpo


def test_protecao_excedida_e_exibida_ticker_a_ticker():
    """O campo que a Task 5 passou a devolver não pode ficar enterrado.

    ``opacidade_excedente`` nomeia fundo, teto do spec, peso obtido e motivo;
    a tela precisa ler exatamente esses campos, não uma nota agregada.
    """
    corpo = inspect.getsource(fiis._carteira_integrada)
    assert 'result.get("protecao_excedida")' in corpo
    assert 'result["protecao_excedida"]' in corpo
    for campo in ("ticker", "peso_final", "teto_spec"):
        assert f"item['{campo}']" in corpo or f'item["{campo}"]' in corpo


def test_relatorio_de_exclusao_da_tela_e_generico_quanto_ao_motivo():
    """A tela não pode filtrar por uma lista fixa de razões conhecidas.

    As quatro razões da Task 2 (renda recorrente ausente/abaixo do mínimo,
    concentração de locatário e vencimentos em 24m acima do teto) só chegam à
    tela se ``_diagnostico_de_exclusao`` renderizar qualquer chave presente em
    ``exclusion_counts`` — o mecanismo já é genérico, e este teste prova que
    continua sendo.
    """
    corpo = inspect.getsource(fiis._diagnostico_de_exclusao)
    assert 'eligibility.get("exclusion_counts")' in corpo
    assert "for motivo, qtd in contagem.items()" in corpo


def test_as_quatro_razoes_novas_chegam_com_contagem_ao_relatorio_da_tela():
    """Prova de ponta a ponta: o que o motor conta é o que a tela recebe.

    ``_diagnostico_de_exclusao`` lê ``eligibility["exclusion_counts"]`` sem
    filtrar por nome (teste acima); falta provar que o motor de fato preenche
    essa chave com as quatro razões novas, cada uma com sua contagem.
    """
    from core.fii_integrated_model import (
        IntegratedEligibilityPolicy,
        apply_integrated_eligibility,
    )

    base = {
        "tipo": "tijolo", "liquidez_diaria": 5e6, "pvp": .95,
        "history_months": 60, "max_drawdown": -.20,
        "dy_12m": .12, "income_recurrence": .90,
        "tenant_concentration": .10, "lease_expiry_concentration_24m": .10,
    }
    linhas = [
        {**base, "ticker": "A11", "income_recurrence": None},
        {**base, "ticker": "B11", "income_recurrence": .20},
        {**base, "ticker": "C11", "tenant_concentration": .90},
        {**base, "ticker": "D11", "lease_expiry_concentration_24m": .90},
    ]
    _, eligibility = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
    contagem = eligibility["exclusion_counts"]
    assert contagem["renda recorrente ausente"] == 1
    assert contagem["renda recorrente abaixo do mínimo"] == 1
    assert contagem["concentração de locatário acima do teto"] == 1
    assert contagem["vencimentos em 24m acima do teto"] == 1


def test_selic_e_ipca_partem_da_observacao_e_nao_de_literal():
    """O literal 15,0% sobreviveu ao ciclo de corte e virou premissa falsa."""
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    assert '"Selic (%)", 0.0, 30.0, observado.padrao_selic, .25' in corpo
    assert 'observado.ipca if observado.ipca is not None' in corpo
    assert 'observado.selic_change_12m or 0.0' in corpo
    assert '30.0, 15.0' not in corpo and '20.0, 4.5' not in corpo


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


# ── Cessão de proteção registrada na safra PIT ───────────────────────────────

def test_protocolo_pit_declara_a_protecao_cedida_na_safra():
    """O certificado grava a cessão e ninguém lia.

    Na safra real, 42 dos 70 períodos só tiveram carteira porque a proteção
    foi cedida, e o card dizia "Aprovado" em verde sem uma palavra sobre
    isso — veredito de protocolo lido como ausência de risco.
    """
    card = fiis._card_do_protocolo_pit("passed", {"backtest": {
        "periods": 70, "concession_periods": 42,
        "concession_period_fraction": .6,
    }})

    assert "42 de 70" in card
    assert "60%" in card
    assert "não é ausência de risco" in card
    # Verde é o vocabulário de "sem ressalva" nesta tela.
    assert "#00C896" not in card


def test_protocolo_pit_sem_cessao_continua_aprovado_sem_ressalva():
    card = fiis._card_do_protocolo_pit("passed", {"backtest": {
        "periods": 70, "concession_periods": 0,
        "concession_period_fraction": 0.0,
    }})

    assert "#00C896" in card
    assert "cedida" not in card


def test_card_do_protocolo_pit_sai_num_bloco_unico():
    """Div aberta num bloco e fechada em outro vira moldura vazia."""
    card = fiis._card_do_protocolo_pit("passed", {})
    assert card.count("<div") == card.count("</div>")
    assert card.startswith("<div") and card.endswith("</div>")


# ── Prontidão medida sobre quem a tela exibe ─────────────────────────────────

def test_universo_exibido_inclui_o_readmitido_quando_o_estrito_esta_vazio():
    """Com estrito vazio e candidatos, a tela publicava "0/0" de prontidão ao
    lado de uma carteira cheia: os KPIs mediam o universo estrito e a carteira
    exibia readmitidos."""
    candidatos = [{"ticker": "REND11", "tipo": "tijolo"},
                  {"ticker": "LOCA11", "tipo": "tijolo"}]
    result = {"protecao_cedida_na_elegibilidade": [
        {"ticker": "REND11", "motivos": ["renda recorrente abaixo do mínimo"],
         "na_carteira": True},
    ]}

    universo = fiis._universo_exibido([], candidatos, result)

    assert [row["ticker"] for row in universo] == ["REND11"]
    # A distinção não some: o readmitido continua marcado como readmitido.
    assert universo[0]["eligibility_status"] == STATUS_READMITIDO


def test_universo_exibido_preserva_o_estrito_e_nao_duplica():
    estritos = [{"ticker": "OK0011", "tipo": "tijolo"}]
    candidatos = [{"ticker": "REND11", "tipo": "tijolo"}]
    sem_cessao = fiis._universo_exibido(estritos, candidatos, {})

    assert [row["ticker"] for row in sem_cessao] == ["OK0011"]
    assert "eligibility_status" not in sem_cessao[0]

    com_cessao = fiis._universo_exibido(estritos, candidatos, {
        "protecao_cedida_na_elegibilidade": [{"ticker": "REND11"}]})
    assert [row["ticker"] for row in com_cessao] == ["OK0011", "REND11"]


def test_kpis_de_prontidao_medem_o_universo_exibido():
    """O gate e os dois KPIs não podem sair de `scored`, que é só o estrito."""
    corpo = inspect.getsource(fiis._carteira_integrada)
    assert "_universo_exibido(" in corpo
    gate = corpo.index("investable_gate = evaluate_publication_gate(")
    montagem = corpo.index("result = montar_carteira_com_concessao(")
    assert gate > montagem, "o gate precisa conhecer a carteira que a tela exibe"


def test_card_de_renda_recorrente_declara_cobertura_parcial():
    """Ponderado sobre metade da carteira precisa dizer que é metade."""
    card = fiis._card_de_renda_recorrente({
        "recurrent_yield_12m": .108, "recurrent_yield_coverage": .5,
        "trailing_yield_12m": .12,
    })
    assert "10.8%" in card
    assert "50%" in card
    assert card.count("<div") == card.count("</div>")


def test_card_de_renda_recorrente_sem_dado_nao_mostra_zero():
    card = fiis._card_de_renda_recorrente({
        "recurrent_yield_12m": None, "recurrent_yield_coverage": 0.0,
        "trailing_yield_12m": .12,
    })
    assert "0.0%" not in card
    assert "—" in card

