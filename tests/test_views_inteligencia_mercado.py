"""A tela de Inteligência de Mercado.

Streamlit não tem harness de execução aqui, então o padrão do repositório vale:
o que dá para exercitar de verdade (montagem do painel, frescor, filtros,
degradação quando a fonte cai) é exercitado; o que é puramente visual é
verificado por inspeção do fonte -- presença dos elementos obrigatórios e
ordem relativa entre eles.
"""
from __future__ import annotations

import datetime as dt
import inspect
from pathlib import Path

from core.inteligencia import llm as L
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz
from views import inteligencia_mercado as V

AGORA = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)
FONTE = Path(V.__file__).read_text(encoding="utf-8")


# ── Frescor: atualização recente x vencida ───────────────────────────────────
def _painel(frescor, **kw):
    return P.montar(frescor=frescor, agora=AGORA, **kw)


def test_atualizacao_recente_nao_e_destacada():
    pn = _painel([qz.Frescor("Notícias", atualizado_em=AGORA - dt.timedelta(hours=1),
                             validade_horas=6.0)])
    assert pn.desatualizados == ()
    assert pn.ultima_atualizacao == AGORA - dt.timedelta(hours=1)


def test_atualizacao_vencida_e_destacada_e_vira_limitacao():
    pn = _painel([qz.Frescor("Notícias", atualizado_em=AGORA - dt.timedelta(hours=30),
                             validade_horas=6.0)])
    assert [f.rotulo for f in pn.desatualizados] == ["Notícias"]
    assert any("Notícias" in lim for lim in pn.limitacoes)


def test_ultima_atualizacao_e_a_fonte_mais_antiga():
    """Publicar a mais recente faria metade velha parecer atual."""
    pn = _painel([
        qz.Frescor("Notícias", atualizado_em=AGORA - dt.timedelta(hours=1)),
        qz.Frescor("Carteira", atualizado_em=AGORA - dt.timedelta(hours=20)),
    ])
    assert pn.ultima_atualizacao == AGORA - dt.timedelta(hours=20)


# ── Falha de provedor ────────────────────────────────────────────────────────
def test_provedor_fora_do_ar_declara_que_o_silencio_pode_ser_falha():
    pn = P.montar(provedores=[qz.Provedor("marketaux", disponivel=False,
                                          detalhe="HTTP 503")], agora=AGORA)
    assert pn.provedores_fora
    texto = " ".join(pn.limitacoes)
    assert "falha de coleta" in texto and "calmaria" in texto


def test_todos_os_provedores_fora_ainda_renderiza_painel():
    pn = P.montar(provedores=[qz.Provedor(n, disponivel=False, detalhe="timeout")
                              for n in ("alphavantage", "marketaux", "rss")],
                  agora=AGORA)
    assert pn.crise is not None and pn.antifragilidade is not None
    assert len(pn.provedores_fora) == 3


def test_falha_de_coleta_nao_apaga_o_painel(monkeypatch):
    """API caiu: a tela continua montando, com o motivo declarado."""
    monkeypatch.setattr(V, "carregar_posicoes",
                        lambda: (__import__("pandas").DataFrame(), "sem carteira"))
    monkeypatch.setattr(V, "situacao_dos_provedores", lambda: ())
    monkeypatch.setattr(V.st, "session_state",
                        {V.CHAVE_COLETA: (None, "a coleta falhou: Timeout")},
                        raising=False)
    pn = V.montar_painel(agora=AGORA)
    assert any("Timeout" in lim for lim in pn.limitacoes)


def test_sem_coleta_nao_e_o_mesmo_que_sem_noticias():
    assert "não coletada" in V.__doc__ or "ainda não olhamos" in V.MSG_SEM_COLETA
    assert "não significa" in V.MSG_SEM_COLETA


# ── LLM: a tela nunca publica número que o painel não tem ────────────────────
def test_a_tela_usa_explicar_e_nao_o_provedor_cru():
    corpo = inspect.getsource(V.render_explicacao)
    assert "intel_llm.explicar(" in corpo
    assert "_chat_complete" not in corpo


def test_resposta_reprovada_aparece_como_descarte_e_nao_como_analise():
    corpo = inspect.getsource(V.render_explicacao)
    assert "validacao" in corpo and "descartada" in corpo


def test_area_tecnica_publica_o_contexto_exato():
    corpo = inspect.getsource(V.render_explicacao)
    assert "exp.contexto" in corpo and "expander" in corpo


def test_numero_inventado_nao_chega_a_tela():
    pn = P.montar(agora=AGORA)
    exp = L.explicar(pn, chamar=lambda _p: "O índice está em 0,9987 e vai subir.")
    assert "0,9987" not in exp.texto
    assert exp.origem == "backend"


# ── Ordem e obrigações visuais ───────────────────────────────────────────────
def test_frescor_aparece_antes_do_conteudo():
    corpo = inspect.getsource(V.render)
    assert corpo.index("render_atualizacao") < corpo.index("abas_secao")


def test_existe_botao_de_atualizacao_manual():
    corpo = inspect.getsource(V.render_atualizacao)
    assert "st.button" in corpo and "Atualizar notícias agora" in corpo


def test_toda_secao_de_estimativa_traz_aviso_sem_garantia():
    for fn in (V.render_antifragilidade, V.render_fundamentos_cenario,
               V.render_explicacao):
        assert "aviso_sem_garantia" in inspect.getsource(fn), fn.__name__


def test_crise_declara_que_nada_e_executado_automaticamente():
    corpo = inspect.getsource(V.render_crise)
    assert "executada automaticamente" in corpo


def test_memoria_declara_que_passado_nao_garante_futuro():
    assert "não garante o futuro" in inspect.getsource(V.render_memoria)


def test_noticias_agrupam_eventos_iguais():
    corpo = inspect.getsource(V.render_noticias)
    assert "grupos" in corpo and "tipo_evento" in corpo


def test_filtros_obrigatorios_estao_na_tela():
    corpo = inspect.getsource(V.render_noticias)
    for campo in ("ticker", "setor", "pais", "confirmadas"):
        assert campo in corpo, campo


def test_alertas_exigem_autorizacao_explicita_para_canal_externo():
    corpo = inspect.getsource(V.render_configuracao_de_alertas)
    assert "autorizou_externo" in corpo
    assert "Nunca o símbolo de um ativo" in corpo


def test_a_tela_nao_promete_retorno():
    """O texto fixo da própria view tem de passar no gate de linguagem."""
    assert not L._frases_proibidas(FONTE)


# ── Filtros ──────────────────────────────────────────────────────────────────
def _item(**extra):
    base = dict(id="n1", titulo="t", fonte="f", url="", publicado_em=AGORA,
                tickers=("PETR4",), empresas=("Petrobras",),
                setores=("energia",), paises=("BR",), classes=("acao_br",),
                tipo_evento="resultado",
                estado_verificacao="confirmada_independente")
    base.update(extra)
    return P.ItemNoticia(**base)


def test_filtro_ignora_caixa_e_acento():
    it = _item()
    assert P.filtrar([it], empresa="petrobras") == (it,)
    assert P.filtrar([it], ticker="petr4") == (it,)
    assert P.filtrar([it], setor="ENERGIA") == (it,)


def test_filtro_de_verificacao_separa_confirmada_de_rumor():
    conf, rumor = _item(), _item(id="n2", estado_verificacao="nao_verificada",
                                tipo_evento="rumor")
    assert P.filtrar([conf, rumor], confirmadas=True) == (conf,)
    assert P.filtrar([conf, rumor], confirmadas=False) == (rumor,)


# ── Registro no app ──────────────────────────────────────────────────────────
def test_rota_registrada_no_app():
    app = Path(V.__file__).parent.parent / "app.py"
    texto = app.read_text(encoding="utf-8")
    assert '"inteligencia_mercado"' in texto
    assert texto.count("🧭 Inteligência de Mercado") >= 2


def test_view_expoe_render():
    assert callable(V.render)
    assert inspect.signature(V.render).parameters == {}
