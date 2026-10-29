"""A busca da Seleção de FIIs mostra o relatório do analista e o leva ao chat.

Dados sintéticos; os leitores do banco e a própria seção são substituídos,
para o teste não sair para o banco (a seção tem teste próprio)."""
import pandas as pd

from views import fiis as view


def test_busca_mostra_o_relatorio_e_o_leva_ao_chat(monkeypatch):
    chamadas = {}
    fii = pd.Series({"Ticker": "AAAA11", "Nome": "Fundo Sintético",
                     "Tipo": "Papel"})
    monkeypatch.setattr(view._mr, "load_fii_one", lambda tk: fii)
    monkeypatch.setattr(view._mr, "load_fii_metrics_mensal",
                        lambda tk: pd.DataFrame())
    monkeypatch.setattr(view._mr, "load_fii_series", lambda tks: {})

    def secao(tk, nome, setor, *, mercado):
        chamadas["secao"] = (tk, nome, setor, mercado)
        return "RELATORIO SINTETICO"

    monkeypatch.setattr(view, "secao_relatorio", secao)
    monkeypatch.setattr(view, "render_chat_ativo",
                        lambda **kw: chamadas.setdefault("chat", kw))
    monkeypatch.setattr(view, "build_fii_ativo_context",
                        lambda tk, **kw: kw)

    view._tab_busca(pd.DataFrame({"Ticker": ["AAAA11"],
                                  "Nome": ["Fundo Sintético"],
                                  "Score": [1.0]}))

    # Sem setor, como o portão da Inteligência dos Ativos lê o FII.
    assert chamadas["secao"] == ("AAAA11", "Fundo Sintético", None, "fii")
    contexto = chamadas["chat"]["build_context"]("pergunta")
    assert contexto["relatorio"] == "RELATORIO SINTETICO"
