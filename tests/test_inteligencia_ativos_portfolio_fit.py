"""Inteligência dos Ativos: Portfolio Fit (contexto, regras, prompt,
validação e tela), com a LLM sempre simulada."""
import dataclasses
import json
import re

from core.inteligencia_ativos import fundamentos as fund
from core.inteligencia_ativos import leitura_llm
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import portfolio_fit as pf
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx
from views import inteligencia_ativos_fit as tela

VEREDITO = re.compile(r"\b(barat[oa]s?|car[oa]s?)\b")
MERCADO = "=== CONTEXTO DE MERCADO ===\nSelic 15,00% a.a. (Bacen)."


def _com_fundamentos(a: m.AnaliseAtivo) -> m.AnaliseAtivo:
    f = fund.Fundamentos("acao", "BRL", (
        fund.Indicador("roe", "ROE", "pct", 21.4, "CVM/DFP", "2025", None),))
    s = m.Secao("fundamentos", "Fundamentos", m.DISPONIVEL, "ok",
                f.como_dict(), "CVM")
    return dataclasses.replace(a, fundamentos=s)


def _resposta(**extra) -> dict:
    base = {
        "asset_role": ["income", "dividend"],
        "thesis_status": "mantida",
        "fundamental_analysis": "ROE de 21,4% no exercício de 2025.",
        "valuation_analysis": pf.NAO_DISPONIVEL,
        "peer_analysis": pf.NAO_DISPONIVEL,
        "scenario_impact": "Selic de 15,00% eleva o custo de oportunidade.",
        "portfolio_impact": "A classe já está acima do alvo.",
        "risks": ["Concentração em Ações Brasil."],
        "opportunities": [],
        "events_to_watch": [],
        "action_to_consider": m.REAVALIAR_APORTES,
        "reasoning_summary": "Fit médio: papel alinhado, classe acima do alvo.",
        "data_gaps": [],
        "dimensions": {
            pf.QUALIDADE: {"level": "forte", "fact": "ROE de 21,4%.",
                           "interpretation": "Rentabilidade alta."},
            pf.VALUATION: {"level": "insuficiente", "fact": pf.NAO_DISPONIVEL,
                           "interpretation": pf.NAO_CONCLUSIVO},
            pf.FIT: {"level": "medio", "fact": "Classe 10 pp acima do alvo.",
                     "interpretation": "Papel alinhado, excesso na classe."},
        },
        "conclusions": [{
            "fact": "Ações Brasil em 30% contra alvo de 20%.",
            "interpretation": "Excesso de 10 pp na classe.",
            "portfolio_impact": "Novo aporte afasta a carteira do alvo.",
            "action_to_consider": "Reavaliar novos aportes."}],
    }
    base.update(extra)
    return base


def _validar(a, ctx, dado, mercado=MERCADO):
    c = pf.contexto(a, ctx, cenario_mercado=mercado)
    return pf.validar(dado, c, pf.texto_ancora(c, mercado))


# -- ordem e contexto -------------------------------------------------------------

def test_ordem_obrigatoria_tem_os_16_passos_e_comeca_no_investidor():
    rotulos = [r for _, r in pf.ORDEM_ANALISE]
    assert len(rotulos) == 16
    assert rotulos[0] == "Objetivo do investidor"
    assert rotulos[-1] == "Alternativas"
    assert rotulos.index("Alocação atual") < rotulos.index("Fundamentos")


def test_contexto_tem_as_chaves_do_contrato_e_nao_o_banco():
    ctx = _ctx()
    c = pf.contexto(_analise("TAEE11", ctx), ctx)
    for k in ("portfolio", "policy", "allocation", "concentration", "scenario",
              "asset", "fundamentals", "valuation", "peers", "news", "reports",
              "events"):
        assert k in c
    assert isinstance(c["peers"], list) and isinstance(c["news"], list)
    assert c["policy"]["objetivo"] == "Renda passiva"
    assert c["asset"]["ticker"] == "TAEE11"
    # Sem política por ativo, o contexto diz isso em vez de inventar alvo.
    assert "não definido" in c["allocation"]["faixa_do_ativo"]["alvo_por_ativo"]
    # Seções sem dado viram lacunas ditas pelo código.
    assert any("Fundamentos" in g for g in c["data_gaps"])
    assert c["scenario"]["contexto_mercado"] == pf.NAO_DISPONIVEL
    json.dumps(c, ensure_ascii=False)


def test_alternativas_sao_da_mesma_classe_da_politica():
    ctx = _ctx()
    c = pf.contexto(_analise("HGLG11", ctx), ctx)
    assert [x["ticker"] for x in
            c["alternatives"]["mesma_classe_na_carteira"]] == ["XPML11"]


# -- fit por regras -----------------------------------------------------------------

def test_fit_por_regras_classe_acima_do_alvo_e_medio():
    ctx = _ctx()
    fit = pf.fit_por_regras(_analise("TAEE11", ctx), ctx)
    assert fit.nivel == "medio"
    assert any("acima do alvo" in x for x in fit.contra)
    assert not fit.bloqueios


def test_fit_por_regras_limite_por_ativo_violado_e_baixo():
    ctx = _ctx(single_asset_limit_pct=20)
    fit = pf.fit_por_regras(_analise("TAEE11", ctx), ctx)
    assert fit.nivel == "baixo"
    assert any("Concentração" in b for b in fit.bloqueios)


def test_fit_por_regras_classe_abaixo_do_alvo_e_papel_do_foco_e_alto():
    ctx = _ctx()
    fit = pf.fit_por_regras(_analise("HGLG11", ctx), ctx)
    assert fit.nivel == "alto"
    assert any("abaixo do alvo" in x for x in fit.a_favor)


# -- prompt ----------------------------------------------------------------------

def test_prompt_leva_ordem_dimensoes_alucinacao_e_regra_de_mercado():
    from core.contexto_mercado import REGRA_CONTEXTO_MERCADO
    s = pf.sistema()
    assert "ORDEM OBRIGATÓRIA" in s and "16. Alternativas" in s
    assert "NUNCA as combine" in s
    assert pf.NAO_DISPONIVEL in s and pf.NAO_CONCLUSIVO in s
    assert "FATO" in s and "IMPACTO NA CARTEIRA" in s
    assert REGRA_CONTEXTO_MERCADO in s
    assert not VEREDITO.search(s.lower())


def test_mensagens_sem_mercado_nao_viram_conjuntura_neutra():
    ctx = _ctx()
    msgs = pf.mensagens(pf.contexto(_analise("TAEE11", ctx), ctx), None)
    assert msgs[0]["role"] == "system"
    assert "CONTEXTO ESTRUTURADO" in msgs[1]["content"]
    assert "conjuntura neutra" in msgs[1]["content"]


# -- leitura e validação ------------------------------------------------------------

def test_ler_json_tolera_cerca_e_ruido():
    assert pf.ler_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert pf.ler_json('Segue: {"a": 1} fim') == {"a": 1}
    assert pf.ler_json("sem json") is None
    assert pf.ler_json("[1, 2]") is None


def test_resposta_valida_e_aprovada_e_dimensoes_ficam_separadas():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    le = _validar(a, ctx, _resposta())
    assert le.status == pf.APROVADA, (le.problemas, le.correcoes,
                                      le.numeros_sem_ancora)
    assert [d.chave for d in le.dimensoes] == [pf.QUALIDADE, pf.VALUATION,
                                                pf.FIT]
    assert le.dimensao(pf.QUALIDADE).nivel == "forte"
    assert le.dimensao(pf.FIT).nivel == "medio"
    assert le.conclusoes[0].impacto.startswith("Novo aporte")


def test_exemplo_b_fundamentos_fortes_e_fit_baixo_convivem():
    ctx = _ctx(single_asset_limit_pct=20)
    a = _com_fundamentos(_analise("TAEE11", ctx))
    dado = _resposta()
    dado["dimensions"][pf.FIT]["level"] = "baixo"
    le = _validar(a, ctx, dado)
    assert le.dimensao(pf.QUALIDADE).nivel == "forte"
    assert le.dimensao(pf.FIT).nivel == "baixo"


def test_nota_geral_e_ranking_sao_removidos():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    le = _validar(a, ctx, _resposta(overall_score=8.5, ranking=1))
    assert le.status == pf.COM_RESSALVAS
    assert any("ranking" in c for c in le.correcoes)
    assert not hasattr(le, "overall_score")


def test_dimensao_sem_dado_vira_insuficiente():
    ctx = _ctx()
    a = _analise("TAEE11", ctx)          # sem fundamentos (conftest offline)
    le = _validar(a, ctx, _resposta())
    q = le.dimensao(pf.QUALIDADE)
    assert q.nivel == pf.INSUFICIENTE
    assert q.interpretacao == pf.NAO_CONCLUSIVO
    assert any("sem dado" in c for c in le.correcoes)


def test_fit_alto_com_limite_violado_e_corrigido_para_as_regras():
    ctx = _ctx(single_asset_limit_pct=20)
    a = _com_fundamentos(_analise("TAEE11", ctx))
    dado = _resposta()
    dado["dimensions"][pf.FIT]["level"] = "alto"
    le = _validar(a, ctx, dado)
    assert le.dimensao(pf.FIT).nivel == "baixo"
    assert any("violado" in c for c in le.correcoes)


def test_numero_inventado_e_sinalizado():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    dado = _resposta(valuation_analysis="P/L de 7,3x e dividend yield de 11,8%.")
    le = _validar(a, ctx, dado)
    assert le.status == pf.COM_RESSALVAS
    assert le.numeros_sem_ancora


def test_campos_ausentes_viram_dado_nao_disponivel_e_valores_invalidos_saem():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    dado = _resposta(asset_role=["income", "moonshot"], thesis_status="otima",
                     action_to_consider="COMPRAR")
    del dado["peer_analysis"]
    le = _validar(a, ctx, dado)
    assert le.textos["peer_analysis"] == pf.NAO_DISPONIVEL
    assert le.papeis == ("income",)
    assert le.tese == pf.INSUFICIENTE
    assert le.acao == a.acao.estado
    assert le.status == pf.COM_RESSALVAS


def test_lacunas_do_codigo_entram_mesmo_sem_a_llm_repetir():
    ctx = _ctx()
    a = _analise("TAEE11", ctx)
    le = _validar(a, ctx, _resposta(data_gaps=[]))
    assert any("Fundamentos" in g for g in le.listas["data_gaps"])


def test_resposta_que_nao_e_objeto_e_rejeitada():
    ctx = _ctx()
    c = pf.contexto(_analise("TAEE11", ctx), ctx)
    le = pf.validar(None, c, "")
    assert le.status == pf.REJEITADA and le.fit_regras is not None


# -- I/O com LLM simulada ------------------------------------------------------------

def test_gerar_chama_uma_vez_com_json_e_valida():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    chamadas = []

    def falsa(msgs):
        chamadas.append(msgs)
        return "```json\n" + json.dumps(_resposta(), ensure_ascii=False) + "\n```"

    le = leitura_llm.gerar(a, ctx, chamar=falsa, mercado=MERCADO)
    assert len(chamadas) == 1
    assert "Selic 15,00%" in chamadas[0][1]["content"]
    assert le.status == pf.APROVADA


def test_gerar_com_provedor_fora_e_resposta_invalida_rejeita():
    ctx = _ctx()
    a = _analise("TAEE11", ctx)

    def quebra(_msgs):
        raise RuntimeError("sem provedor")

    le = leitura_llm.gerar(a, ctx, chamar=quebra, mercado=MERCADO)
    assert le.status == pf.REJEITADA and "sem provedor" in le.problemas[0]
    le = leitura_llm.gerar(a, ctx, chamar=lambda _m: "não sei",
                           mercado=MERCADO)
    assert le.status == pf.REJEITADA


# -- tela (cartões puros) -------------------------------------------------------------

def test_cartoes_usam_tokens_e_nao_somam_dimensoes():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    le = _validar(a, ctx, _resposta())
    html = (tela.cartao_fit_regras(pf.fit_por_regras(a, ctx))
            + tela.cartao_dimensoes(le) + tela.cartao_conclusoes(le)
            + tela.cartao_detalhes(le) + tela.cartao_validacao(le))
    assert "não existe nota geral nem ranking" in html.lower()
    for cab in ("Fato", "Interpretação", "Impacto na carteira",
                "Ação a considerar"):
        assert cab in html
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", html)
    assert not VEREDITO.search(html.lower())


def test_chave_de_sessao_muda_com_o_contexto():
    ctx = _ctx()
    a = _analise("TAEE11", ctx)
    c1 = pf.contexto(a, ctx)
    c2 = pf.contexto(a, _ctx(single_asset_limit_pct=20))
    assert tela.chave_sessao(a, c1) != tela.chave_sessao(a, c2)
