"""Cenário de Investimentos: premissa do usuário que a LLM lê e nunca altera."""
import contextlib
import dataclasses
import datetime as dt
import json
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from core.cenario import divergencia, referencias
from core.cenario import modelo as mod
from core.cenario import repositorio as repo
from core.inteligencia_ativos import analise, leitura_llm, secoes
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import portfolio_fit as pf
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx
from tests.test_inteligencia_ativos_portfolio_fit import (
    MERCADO,
    _resposta,
    _validar,
)
from views import configuracoes_cenario as tela
from views import inteligencia_ativos_fit as tela_fit

HOJE = dt.date(2026, 9, 26)
AGORA = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.timezone.utc)


def _entrada(valor="15% a.a.", direcao="queda", confianca="media",
             fonte="Focus/BCB"):
    return {"current_value": valor, "expected_direction": direcao,
            "confidence": confianca, "source": fonte}


def _cenario(**entradas) -> mod.Cenario:
    entradas = entradas or {"interest_rate": _entrada()}
    c, alterados, erros = mod.revisar(
        mod.Cenario.de_dict(None), entradas, origem=mod.MANUAL,
        hoje=dt.date(2026, 9, 1), agora=AGORA)
    assert not erros and alterados
    return c


# -- modelo ---------------------------------------------------------------------

def test_doze_campos_com_os_cinco_atributos():
    assert len(mod.CAMPOS) == 12 and len(set(mod.CHAVES)) == 12
    assert set(mod.Item().como_dict()) == {
        "current_value", "expected_direction", "confidence", "source",
        "last_updated"}


def test_revisar_data_so_os_itens_alterados_e_sobe_a_versao():
    c = _cenario(interest_rate=_entrada(), fx=_entrada("R$ 5,50", "alta"))
    assert c.versao == 1 and c.item("fx").last_updated == "2026-09-01"
    novo, alterados, erros = mod.revisar(
        c, {"interest_rate": _entrada(), "fx": _entrada("R$ 5,80", "alta")},
        origem=mod.MANUAL, hoje=HOJE, agora=AGORA)
    assert not erros and alterados == ["fx"] and novo.versao == 2
    assert novo.item("fx").last_updated == "2026-09-26"
    assert novo.item("interest_rate").last_updated == "2026-09-01"
    assert novo.historico[0]["changed"] == ["fx"]
    assert len(novo.historico) == 2


def test_salvar_sem_mudanca_nao_cria_versao():
    c = _cenario()
    novo, alterados, erros = mod.revisar(
        c, {"interest_rate": _entrada()}, origem=mod.MANUAL, hoje=HOJE,
        agora=AGORA)
    assert novo is c and alterados == [] and erros == []


def test_premissa_sem_procedencia_e_recusada():
    _, alterados, erros = mod.revisar(
        mod.Cenario.de_dict(None),
        {"inflation": _entrada("4,5%", "", "", ""),
         "credit": _entrada("", "alta", "", "")},
        origem=mod.MANUAL, hoje=HOJE, agora=AGORA)
    assert alterados == []
    assert any("preencha direção esperada, confiança, fonte" in e for e in erros)
    assert any("sem valor atual" in e for e in erros)


def test_origem_llm_nao_existe():
    assert "llm" not in mod.ORIGENS
    with pytest.raises(ValueError):
        mod.revisar(mod.Cenario.de_dict(None), {}, origem="llm", hoje=HOJE,
                    agora=AGORA)


def test_gravar_em_recusa_versao_diferente_e_preserva_o_resto():
    c = _cenario()
    extra = {"theme": "light"}
    gravado = mod.gravar_em(extra, c, versao_esperada=0)
    assert gravado["theme"] == "light"
    assert mod.Cenario.de_dict(gravado[mod.CHAVE_PREFERENCIA]).versao == 1
    with pytest.raises(mod.ConflitoDeVersao):
        mod.gravar_em(gravado, c, versao_esperada=0)


def test_ida_e_volta_pelo_dict():
    c = _cenario(interest_rate=_entrada(), commodities=_entrada("Petróleo "
                 "a US$ 80", "estavel", "baixa", "minha leitura"))
    volta = mod.Cenario.de_dict(json.loads(json.dumps(c.como_dict())))
    assert volta == c


def test_envelhecido_depois_de_90_dias():
    c = _cenario()
    assert c.envelhecidos(dt.date(2026, 11, 29)) == []
    assert c.envelhecidos(dt.date(2026, 12, 1)) == ["interest_rate"]


# -- sinais de revisão ------------------------------------------------------------

def _ref(valor, data=dt.date(2026, 9, 20)):
    return divergencia.Referencia(valor, data, "Selic (teste)", "% a.a.")


def test_referencia_mais_nova_e_distante_gera_sinal_com_a_frase():
    s = divergencia.sinais(_cenario(), {"interest_rate": _ref(14.25)})
    assert len(s) == 1 and s[0].chave == "interest_rate"
    assert s[0].texto.endswith(mod.FRASE_REVISAO)


def test_referencia_antiga_ou_dentro_da_tolerancia_nao_gera_sinal():
    c = _cenario()
    assert divergencia.sinais(c, {"interest_rate": _ref(
        10.0, dt.date(2025, 12, 31))}) == ()
    assert divergencia.sinais(c, {"interest_rate": _ref(15.2)}) == ()
    assert divergencia.sinais(None, {"interest_rate": _ref(1.0)}) == ()


def test_cambio_usa_tolerancia_relativa():
    c = _cenario(fx=_entrada("R$ 5,50 por US$", "alta"))
    ref = divergencia.Referencia(5.70, dt.date(2026, 9, 20), "câmbio", "R$")
    assert divergencia.sinais(c, {"fx": ref}) == ()
    ref = dataclasses.replace(ref, valor=5.90)
    assert [s.chave for s in divergencia.sinais(c, {"fx": ref})] == ["fx"]


# -- referências (puras) --------------------------------------------------------------

OBS = (
    {"country_code": "BRA", "provider_code": "selic",
     "reference_period": dt.date(2024, 12, 31), "value": Decimal("12.25"),
     "is_forecast": False},
    {"country_code": "BRA", "provider_code": "selic",
     "reference_period": dt.date(2025, 12, 31), "value": Decimal("15.0"),
     "is_forecast": False},
    {"country_code": "BRA", "provider_code": "selic",
     "reference_period": dt.date(2026, 12, 31), "value": Decimal("13.0"),
     "is_forecast": True},
    {"country_code": "US", "provider_code": "FEDFUNDS",
     "reference_period": dt.date(2026, 8, 1), "value": Decimal("3.63"),
     "is_forecast": False},
)


def test_referencia_usa_o_ultimo_observado_e_ignora_projecao():
    refs = referencias.de_observacoes(OBS)
    assert set(refs) == {"interest_rate"}
    assert refs["interest_rate"].valor == 15.0
    assert refs["interest_rate"].data == dt.date(2025, 12, 31)


def test_sugestao_traz_a_data_e_deixa_julgamento_em_branco():
    sug = referencias.sugestoes_de_observacoes(OBS)
    assert sug["interest_rate"]["current_value"] == "15,00 % a.a."
    assert "31/12/2025" in sug["interest_rate"]["source"]
    assert "série anual" in sug["interest_rate"]["source"]
    assert "Fed Funds 3,63%" in sug["global_economy"]["current_value"]
    assert all(set(v) == {"current_value", "source"} for v in sug.values())


# -- repositório (engine falso, sem banco) -------------------------------------------------

class _Engine:
    @contextlib.contextmanager
    def begin(self):
        yield object()


def test_salvar_confere_versao_e_so_grava_com_mudanca(monkeypatch):
    from core import user_accounts
    gravado = {}
    estado = {"extra": {"theme": "dark"}}
    monkeypatch.setattr(user_accounts, "locked_preferences",
                        lambda conn, uid: dict(estado["extra"]))

    def escrever(conn, uid, extra):
        gravado["n"] = gravado.get("n", 0) + 1
        estado["extra"] = extra
    monkeypatch.setattr(user_accounts, "write_preferences", escrever)
    kw = dict(engine=_Engine(), owner_id="u1", agora=AGORA)

    c, alt, err = repo.salvar({"interest_rate": _entrada()},
                              versao_esperada=0, origem=mod.MANUAL, **kw)
    assert c.versao == 1 and alt == ["interest_rate"] and gravado["n"] == 1
    assert estado["extra"]["theme"] == "dark"
    repo.salvar({"interest_rate": _entrada()}, versao_esperada=1,
                origem=mod.MANUAL, **kw)
    assert gravado["n"] == 1                    # sem mudança, sem escrita
    with pytest.raises(mod.ConflitoDeVersao):
        repo.salvar({"fx": _entrada("5,5")}, versao_esperada=0,
                    origem=mod.MANUAL, **kw)
    with pytest.raises(ValueError):
        repo.salvar({}, versao_esperada=1, origem="llm", **kw)
    assert gravado["n"] == 1


# -- análise do ativo -------------------------------------------------------------------

def _com_cenario(ticker="HGLG11", sinais=()):
    ctx = dataclasses.replace(_ctx(), cenario=_cenario(
        interest_rate=_entrada(), inflation=_entrada("4,5%", "estavel")),
        sinais_cenario=sinais)
    return _analise(ticker, ctx), ctx


def test_secao_cenario_sem_cadastro_aponta_configuracoes():
    a = _analise("HGLG11", _ctx())
    assert a.cenario.estado == m.SEM_DADOS
    assert '"Meu cenário", no fim desta aba' in a.cenario.resumo


def test_secao_cenario_disponivel_com_itens_relevantes_da_classe():
    a, _ = _com_cenario()
    assert a.cenario.estado == m.DISPONIVEL
    assert a.cenario.dados["versao"] == 1
    assert "Taxa de juros" in a.cenario.dados["mais_relevantes_para_a_classe"]
    assert mod.FRASE_REVISAO not in a.cenario.resumo


def test_sinal_entra_na_secao_e_no_texto_da_llm():
    s = divergencia.sinais(_cenario(), {"interest_rate": _ref(14.0)})
    a, ctx = _com_cenario(sinais=s)
    assert mod.FRASE_REVISAO in a.cenario.resumo
    texto = analise.texto_para_llm(a, ctx)
    assert "CENÁRIO DE INVESTIMENTOS" in texto and "Não altere" in texto
    assert "Sinal de revisão" in texto


def test_texto_sem_cenario_diz_para_nao_presumir():
    ctx = _ctx()
    texto = analise.texto_para_llm(_analise("HGLG11", ctx), ctx)
    assert "não cadastrou cenário" in texto


def test_provedor_nao_le_banco(monkeypatch):
    monkeypatch.setattr(repo, "carregar", lambda **k: pytest.fail("leu banco"))
    a, ctx = _com_cenario()
    assert secoes.provedor_cenario(a.ativo, ctx).estado == m.DISPONIVEL


# -- Portfolio Fit ------------------------------------------------------------------------

def test_prompt_leva_a_regra_e_a_frase_de_revisao():
    s = pf.sistema()
    assert mod.REGRA_CENARIO in s and mod.FRASE_REVISAO in s
    assert "scenario.cenario_do_investidor" in s


def test_contexto_do_fit_leva_o_cenario_so_quando_existe():
    a, ctx = _com_cenario()
    c = pf.contexto(a, ctx, cenario_mercado=MERCADO)
    assert c["scenario"]["cenario_do_investidor"]["versao"] == 1
    ctx0 = _ctx()
    c0 = pf.contexto(_analise("HGLG11", ctx0), ctx0, cenario_mercado=MERCADO)
    assert c0["scenario"]["cenario_do_investidor"] == pf.NAO_DISPONIVEL


def test_validador_descarta_reescrita_e_marca_revisao():
    ctx = _ctx()
    a = _analise("TAEE11", ctx)
    dado = _resposta(
        updated_scenario={"interest_rate": {"current_value": "10%"}},
        scenario_impact="Selic de 15,00% eleva o custo de oportunidade. "
                        + mod.FRASE_REVISAO)
    le = _validar(a, ctx, dado)
    assert le.revisao_cenario is True
    assert any("reescrever o Cenário" in c for c in le.correcoes)
    assert le.status == pf.COM_RESSALVAS
    le = _validar(a, ctx, _resposta())
    assert le.revisao_cenario is False


def test_gerar_nunca_grava_o_cenario(monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("a LLM tentou gravar o cenário")
    monkeypatch.setattr(repo, "salvar", proibido)
    a, ctx = _com_cenario("TAEE11")
    resposta = _resposta(new_scenario={"fx": "R$ 7"},
                         scenario_impact=mod.FRASE_REVISAO)
    le = leitura_llm.gerar(a, ctx, chamar=lambda _m: json.dumps(
        resposta, ensure_ascii=False), mercado=MERCADO)
    assert le.revisao_cenario and ctx.cenario.versao == 1


def test_aviso_da_tela_do_fit():
    ctx = _ctx()
    assert tela_fit.aviso_cenario(ctx) is None
    s = divergencia.sinais(_cenario(), {"interest_rate": _ref(14.0)})
    html = tela_fit.aviso_cenario(dataclasses.replace(ctx, sinais_cenario=s))
    assert mod.FRASE_REVISAO in html and "não foi alterado" in html
    assert "#" not in html.split("style=")[1][:200]


# -- editor ------------------------------------------------------------------------------

def test_cartao_de_situacao_usa_tokens():
    html = tela.cartao_situacao(mod.Cenario.de_dict(None), (), HOJE)
    assert "Nenhum cenário cadastrado" in html
    s = divergencia.sinais(_cenario(), {"interest_rate": _ref(14.0)})
    html = tela.cartao_situacao(_cenario(), s, HOJE)
    assert "Versão 1" in html and mod.FRASE_REVISAO in html
    assert "var(--app-warning)" in html and "#" not in html


def _app_editor():
    from views.configuracoes_cenario import render
    render()


@pytest.fixture
def editor(monkeypatch):
    estado = {"c": mod.Cenario.de_dict(None), "salvos": []}

    def salvar(entradas, *, versao_esperada, origem, **k):
        estado["salvos"].append((entradas, versao_esperada, origem))
        novo, alt, err = mod.revisar(estado["c"], entradas, origem=origem,
                                     hoje=HOJE, agora=AGORA)
        if not err and alt:
            estado["c"] = novo
        return novo, alt, err

    monkeypatch.setattr(repo, "carregar", lambda **k: estado["c"])
    monkeypatch.setattr(repo, "salvar", salvar)
    monkeypatch.setattr(referencias, "referencias", lambda **k: {})
    monkeypatch.setattr(referencias, "sugestoes",
                        lambda **k: referencias.sugestoes_de_observacoes(OBS))
    return estado


def test_editor_salva_manual(editor):
    at = AppTest.from_function(_app_editor).run()
    assert not at.exception
    at.text_input(key=tela.chave_widget(0, "interest_rate", "valor")).input(
        "15% a.a.")
    at.selectbox(key=tela.chave_widget(0, "interest_rate", "direcao")).select(
        "queda")
    at.selectbox(key=tela.chave_widget(0, "interest_rate", "confianca")
                 ).select("alta")
    at.text_input(key=tela.chave_widget(0, "interest_rate", "fonte")).input(
        "Focus")
    at.button[1].click().run()
    assert not at.exception
    assert editor["salvos"][0][1:] == (0, mod.MANUAL)
    assert editor["c"].versao == 1
    assert "Cenário salvo (versão 1)" in at.success[0].value


def test_sugestao_so_preenche_e_nao_grava(editor):
    at = AppTest.from_function(_app_editor).run()
    at.button(key="cfg_cenario_sugerir_v0").click().run()
    assert not at.exception and editor["salvos"] == []
    assert at.text_input(key=tela.chave_widget(0, "interest_rate", "valor")
                         ).value == "15,00 % a.a."
    assert "Nada foi salvo" in at.info[0].value
    # direção e confiança são do usuário: salvar sem elas é recusado
    at.button[1].click().run()
    assert editor["c"].versao == 0
    assert editor["salvos"][0][2] == mod.ATUALIZACAO_SOLICITADA
    assert "não foi salvo" in at.error[0].value
