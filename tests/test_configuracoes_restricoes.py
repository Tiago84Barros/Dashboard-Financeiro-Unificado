"""Aba Configuracoes -> Restricoes: o painel desenha, filtra e oferece a decisao."""
import re

from streamlit.testing.v1 import AppTest


def _app():
    from core.lacunas import painel
    from views.configuracoes_restricoes import render_painel

    def item(imp, natureza, status, tela, msg, codigo):
        return {"impressao": imp, "natureza": natureza, "status": status, "tela": tela,
                "ultima_mensagem": msg, "codigo": codigo, "entidade": "",
                "modulo": "views/fiis.py:render", "ocorrencias": 3,
                "ocorrencias_14d": 2, "primeira_vez": "2026-10-01T10:00:00+00:00",
                "ultima_vez": "2026-10-05T10:00:00+00:00", "contexto": {"n": 4},
                "reincidente": imp == "r1", "prioridade": 5}

    render_painel(painel.Painel(
        itens=[item("r1", "Restrição", "aberta", "Seleção de FIIs",
                    "Sem VPA para 12 FIIs", "tela.fii.sem_vpa"),
               item("e1", "Erro", "aberta", "Configurações",
                    "KeyError: <b>chave</b>", "KeyError"),
               item("d1", "Detalhe técnico", "legitima", "Seleção de FIIs",
                    "Metodologia 2.1.0", "detalhe.fii.versao")],
        fontes={"local": "ok (3 registros)", "cloud": "indisponivel: OperationalError"},
        gerado_em="2026-10-05T12:00:00+00:00"))


def _blocos(at) -> list[str]:
    """Os ``st.markdown`` da tela, menos a folha de estilo.

    Sem descontar o ``<style>``, toda busca por seletor casa primeiro com a
    propria regra CSS e o teste passa a medir o estilo, nao o que foi desenhado.
    """
    return [m.value for m in at.markdown
            if not m.value.lstrip().startswith("<style>")]


def _html(at) -> str:
    return "\n".join(_blocos(at))


def _contagens(at) -> dict[str, str]:
    """Le os cards de contagem do HTML -- eles nao sao ``st.metric``."""
    bloco = next(b for b in _blocos(at) if "restr-kpis" in b)
    pares = re.findall(
        r'restr-kpi-label">([^<]+)</div>'
        r'<div class="restr-kpi-value">([^<]+)<', bloco)
    return {rotulo: valor for rotulo, valor in pares}


def test_painel_mostra_contagens_fontes_e_so_pendentes_por_padrao():
    at = AppTest.from_function(_app).run(timeout=60)
    assert not at.exception
    contagens = _contagens(at)
    assert contagens["Restrições pendentes"] == "1"
    assert contagens["Erros pendentes"] == "1"
    assert contagens["Detalhes técnicos"] == "1"
    assert contagens["Reincidentes"] == "1"
    html = _html(at)
    assert "cloud" in html and "indisponivel: OperationalError" in html
    tabela = at.dataframe[0].value
    assert set(tabela["Natureza"]) == {"Restrição", "Erro"}   # detalhe fora do padrao
    assert len(at.radio) == 1 and len(at.button) >= 1


def test_filtro_de_natureza_mostra_detalhe_tecnico():
    at = AppTest.from_function(_app).run(timeout=60)
    at.multiselect(key="cfg_restr_natureza").set_value(["Detalhe técnico"])
    at.multiselect(key="cfg_restr_status").set_value([])
    at.run(timeout=60)
    tabela = at.dataframe[0].value
    assert list(tabela["Mensagem"]) == ["Metodologia 2.1.0"]


def test_a_fonte_que_falhou_vem_com_o_tom_de_falha():
    """Fonte fora do ar nunca some da tela, e nao pode parecer fonte ok."""
    at = AppTest.from_function(_app).run(timeout=60)
    estado = next(b for b in _blocos(at) if "restr-estado" in b)
    chips = re.findall(r'restr-tom:var\((--app-[a-z]+)[^)]*\)">'
                       r'<span class="restr-chip-nome">([^<]+)</span>', estado)
    tons = dict((nome, token) for token, nome in chips)
    assert tons["cloud"] == "--app-danger"
    assert tons["local"] == "--app-primary"


def test_mensagem_do_log_nao_vira_html():
    """Mensagem de excecao e dado de terceiro: entra escapada na ficha."""
    at = AppTest.from_function(_app).run(timeout=60)
    at.selectbox(key="cfg_restr_escolha").set_value("e1").run(timeout=60)
    ficha = next(b for b in _blocos(at) if "restr-item-msg" in b)
    assert "&lt;b&gt;chave&lt;/b&gt;" in ficha
    assert "<b>chave</b>" not in ficha


def test_as_contagens_sao_cards_css_num_markdown_so():
    """Regra da casa: KPI e card CSS, e card CSS sai num ``st.markdown`` so."""
    at = AppTest.from_function(_app).run(timeout=60)
    assert not at.metric
    blocos = [b for b in _blocos(at) if "restr-kpi-value" in b]
    assert len(blocos) == 1
    assert blocos[0].count("restr-kpi-value") == 4


def test_cor_da_tela_sai_de_token_de_tema():
    """Literal hex sem ``var(--app-*)`` nao acompanha o tema claro."""
    from views import configuracoes_restricoes as restr

    assert all(valor.startswith("var(--app-") for valor in restr._TOM.values())
    sem_fallback = re.findall(r"(?<!, )#[0-9A-Fa-f]{6}", restr._RESTR_CSS)
    assert not sem_fallback, sem_fallback
