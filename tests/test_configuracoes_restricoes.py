"""Aba Configuracoes -> Restricoes: o painel desenha, filtra e oferece a decisao."""
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
               item("e1", "Erro", "aberta", "Configurações", "KeyError", "KeyError"),
               item("d1", "Detalhe técnico", "legitima", "Seleção de FIIs",
                    "Metodologia 2.1.0", "detalhe.fii.versao")],
        fontes={"local": "ok (3 registros)", "cloud": "indisponivel: OperationalError"},
        gerado_em="2026-10-05T12:00:00+00:00"))


def test_painel_mostra_metricas_fontes_e_so_pendentes_por_padrao():
    at = AppTest.from_function(_app).run(timeout=60)
    assert not at.exception
    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Restrições pendentes"] == "1"
    assert metricas["Erros pendentes"] == "1"
    assert metricas["Detalhes técnicos"] == "1"
    assert metricas["Reincidentes"] == "1"
    assert any("cloud: indisponivel" in c.value for c in at.caption)
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
