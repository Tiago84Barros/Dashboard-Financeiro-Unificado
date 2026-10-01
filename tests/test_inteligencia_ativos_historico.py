"""Histórico das análises: quando salvar, comparação e auditoria."""
import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

from core import llm_b3
from core.inteligencia_ativos import analise, leitura_llm, painel
from core.inteligencia_ativos import historico as hist
from core.inteligencia_ativos import historico_repo as hrepo
from core.inteligencia_ativos import modelos as m
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx
from tests.test_inteligencia_ativos_portfolio_fit import MERCADO, _resposta

# o conftest troca o repositório por um dicionário; o real fica guardado aqui
_REGISTRAR_REAL = hrepo.registrar
_CARREGAR_REAL = hrepo.carregar

AGORA = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.timezone.utc)


def _foto(ticker="HGLG11", *, dias=0, versao=2, cenario=None, modelo=None,
          **campos):
    quando = AGORA + dt.timedelta(days=dias)
    base = dict(peso=8.0, tese=painel.TESE_VALIDA, acao=m.EXPOSICAO_ADEQUADA,
                riscos=())
    base.update(campos)
    return hist.Snapshot(
        ticker=ticker,
        auditoria=hist.Auditoria(
            analysis_timestamp=quando.isoformat(timespec="seconds"),
            investment_policy_version=versao, scenario_version=cenario,
            model_used=modelo),
        **base)


# -- quando salvar ------------------------------------------------------------------

def test_primeira_foto_sempre_entra_e_repeticao_nao():
    a = _foto()
    assert hist.motivo_para_salvar(a, None) == hist.PRIMEIRA
    assert hist.motivo_para_salvar(_foto(dias=3), a) is None


def test_mudanca_material_justifica_a_foto():
    a = _foto()
    assert hist.motivo_para_salvar(_foto(versao=3), a) == "a estratégia mudou"
    assert hist.motivo_para_salvar(_foto(cenario=4), a) == "o cenário mudou"
    assert hist.motivo_para_salvar(
        _foto(tese=painel.TESE_EM_RISCO), a) == "o status da tese mudou"
    assert hist.motivo_para_salvar(
        _foto(acao=m.REAVALIAR_TESE), a) == "a ação a considerar mudou"
    assert hist.motivo_para_salvar(
        _foto(riscos=("x",)), a) == "os sinais de risco mudaram"
    assert "peso" in hist.motivo_para_salvar(_foto(peso=9.0), a)
    # abaixo de 1 pp não é mudança material
    assert hist.motivo_para_salvar(_foto(peso=8.9), a) is None
    assert hist.motivo_para_salvar(_foto(dias=31), a) == hist.ANTIGA
    assert hist.motivo_para_salvar(_foto(dias=30), a) is None
    assert hist.motivo_para_salvar(_foto(modelo="openai/x"), a) == hist.LEITURA_LLM


def test_anexar_limita_por_chave_e_forcar_grava_sem_mudanca():
    historico = {}
    for i in range(hist.MAX_POR_CHAVE + 3):
        historico, gravadas = hist.anexar(historico, [_foto(peso=float(i + 1))])
        assert gravadas == ["HGLG11"]
    lista = historico["HGLG11"]
    assert len(lista) == hist.MAX_POR_CHAVE
    assert lista[-1].peso == hist.MAX_POR_CHAVE + 3.0
    historico, gravadas = hist.anexar(historico, [_foto(peso=lista[-1].peso)])
    assert gravadas == []
    historico, gravadas = hist.anexar(historico, [_foto(peso=lista[-1].peso)],
                                      forcar=hist.MANUAL)
    assert gravadas == ["HGLG11"]
    assert historico["HGLG11"][-1].motivo == hist.MANUAL


def test_ler_e_gravar_preservam_o_resto_das_preferencias():
    extra = {"tema": "escuro"}
    historico, _ = hist.anexar({}, [_foto(valuation={"pvp": hist.Metrica(
        "P/VP", 0.93, "x")}, cenario=5, modelo="gemini/y")])
    novo = hist.gravar_em(extra, historico)
    assert novo["tema"] == "escuro" and extra == {"tema": "escuro"}
    # o que vai para o banco é JSON simples
    lido = hist.ler(json.loads(json.dumps(novo)))
    s = lido["HGLG11"][0]
    assert s.valuation["pvp"] == hist.Metrica("P/VP", 0.93, "x")
    assert s.auditoria.scenario_version == 5
    assert s.auditoria.model_used == "gemini/y"
    assert hist.ler({hist.CHAVE_PREFERENCIA: {"schema": "outro"}}) == {}
    assert hist.ler(None) == {}


# -- comparação ------------------------------------------------------------------------

def test_comparar_escreve_as_frases_do_historico():
    antes = _foto(peso=8.0, fundamentos={"div": hist.Metrica(
        "Dívida líquida/EBITDA", 1.5, "x")})
    agora = _foto(dias=40, peso=12.0, riscos=("a", "b"), fundamentos={
        "div": hist.Metrica("Dívida líquida/EBITDA", 2.4, "x")})
    frases = hist.comparar(antes, agora)
    assert "Na análise anterior (26/09/2026), o peso era 8,0%; agora é 12,0%." in frases
    assert "Desde a última análise, a tese permaneceu válida." in frases
    assert any(f.startswith("O risco aumentou") for f in frases)
    assert any(f.startswith("Dívida líquida/EBITDA aumentou de") for f in frases)


def test_comparar_ignora_ruido_e_registra_mudanca_de_tese():
    antes = _foto(valuation={"pl": hist.Metrica("P/L", 10.0, "x")})
    agora = _foto(tese=painel.TESE_EM_RISCO, cenario=2,
                  valuation={"pl": hist.Metrica("P/L", 10.1, "x")})
    frases = hist.comparar(antes, agora)
    assert "A tese passou de válida para com sinal contra." in frases
    assert not any("P/L" in f for f in frases)       # 1% é ruído
    assert any("O cenário mudou" in f for f in frases)
    assert hist.comparar(None, agora) == []


def test_foto_da_carteira_compara_patrimonio_e_alocacao():
    ctx = _ctx()
    r = painel.resumo(ctx, analise.analisar_carteira(ctx), hoje=AGORA.date())
    atual = hist.capturar_carteira(r, ctx, agora=AGORA)
    assert atual.ticker == hist.CARTEIRA and atual.valor == 11000.0
    antes = replace(atual, valor=10000.0,
                    alocacao={**atual.alocacao, "Renda fixa": 40.0})
    frases = hist.comparar(antes, atual)
    assert any("o patrimônio era R$ 10.000,00" in f for f in frases)
    assert "Renda fixa passou de 40,0% para 45,0% da carteira." in frases
    assert hist.motivo_para_salvar(atual, antes) == "a alocação por classe mudou"


# -- auditoria -------------------------------------------------------------------------

def test_captura_registra_os_campos_de_auditoria():
    ctx = _ctx()
    a = _analise("HGLG11", ctx)
    s = hist.capturar(a, ctx, agora=AGORA, modelo="openai/gpt")
    au = s.auditoria.como_dict()
    assert set(au) == {"analysis_timestamp", "model_used", "data_timestamp",
                       "scenario_version", "investment_policy_version",
                       "sources_used"}
    assert au["analysis_timestamp"] == "2026-09-26T12:00:00+00:00"
    assert au["investment_policy_version"] == 2
    assert au["model_used"] == "openai/gpt"
    assert s.peso == 12.0 and s.tese in painel.ROTULO_TESE
    # fonte composta não duplica a fonte simples
    fontes = au["sources_used"]
    assert len(fontes) == len(set(fontes))
    assert not any(", " in f for f in fontes)


def test_data_dos_dados_nunca_fica_no_futuro():
    ctx = _ctx()
    a = _analise("HGLG11", ctx)
    sec = m.Secao("eventos", "Próximos eventos", m.DISPONIVEL, "ok",
                  dados={"itens": [{"data": "2027-01-01",
                                    "referencia": "2027-02-01"},
                                   {"retrieved_at": "2026-09-20T10:00:00"},
                                   {"fim": "2025"}]})
    a2 = replace(a, eventos=sec)
    assert hist.data_dos_dados(a2, AGORA.date()) == "2026-09-20"


# -- modelo que respondeu -----------------------------------------------------------------

def test_leitura_llm_guarda_o_modelo_que_respondeu():
    ctx = _ctx()
    a = _analise("TAEE11", ctx)

    def falsa(_msgs):
        return json.dumps(_resposta())
    falsa.modelo = "teste/modelo-1"
    le = leitura_llm.gerar(a, ctx, chamar=falsa, mercado=MERCADO)
    assert le.modelo == "teste/modelo-1"


def test_llm_b3_registra_o_provedor_de_fallback(monkeypatch):
    def cliente(resposta=None, erro=None):
        def create(**_):
            if erro:
                raise erro
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=resposta))])
        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_b3, "_provider_chain", lambda _p=None: [
        ("openai", cliente(erro=RuntimeError("cota")), "gpt-x"),
        ("gemini", cliente(resposta="{}"), "gemini-y")])
    assert llm_b3._chat_complete([{"role": "user", "content": "oi"}]) == "{}"
    assert llm_b3.ultimo_modelo() == "gemini/gemini-y"
    monkeypatch.setattr(llm_b3, "_provider_chain", lambda _p=None: [
        ("openai", cliente(erro=RuntimeError("cota")), "gpt-x")])
    try:
        llm_b3._chat_complete([{"role": "user", "content": "oi"}])
    except RuntimeError:
        pass
    # falha total não herda o modelo da chamada anterior
    assert llm_b3.ultimo_modelo() is None


# -- repositório (engine falso, sem banco) --------------------------------------------------

class _Conn:
    def __init__(self, banco):
        self.banco = banco

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self):
        self.extra = {"outra": 1}

    def begin(self):
        return _Conn(self)


def test_repositorio_trava_decide_e_so_escreve_quando_ha_foto(monkeypatch):
    import core.user_accounts as ua
    escritas = []
    monkeypatch.setattr(ua, "locked_preferences",
                        lambda conn, uid: dict(conn.banco.extra))
    monkeypatch.setattr(ua, "write_preferences",
                        lambda conn, uid, extra: (escritas.append(uid),
                                                  setattr(conn.banco, "extra",
                                                          extra)))
    eng = _Engine()
    historico, gravadas = _REGISTRAR_REAL([_foto()], engine=eng, owner_id="u1")
    assert gravadas == ["HGLG11"] and escritas == ["u1"]
    assert eng.extra["outra"] == 1 and hist.CHAVE_PREFERENCIA in eng.extra
    _, gravadas = _REGISTRAR_REAL([_foto(dias=1)], engine=eng, owner_id="u1")
    assert gravadas == [] and escritas == ["u1"]     # nada material: não escreve
