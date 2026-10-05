"""
tests/test_leitura_relatorios.py
Resumo por LLM dos trechos dos relatórios (etapa 10), com LLM simulada.
"""
from __future__ import annotations

import json
from dataclasses import replace

from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import leitura_relatorios as lr
from tests.test_inteligencia_ativos_informacoes import TRECHOS

MERCADO = "=== CONTEXTO DE MERCADO ===\nSelic 15,00% (BCB, 01/10/2026)."
REL = inf.Relatorios(documentos=(), trechos=TRECHOS)


def _analise(rel: inf.Relatorios | None = REL):
    import tests.test_inteligencia_ativos_adequacao as ad
    a = ad._analise("TAEE11", ad._ctx())
    return replace(a, relatorios=replace(
        a.relatorios, dados=rel.como_dict() if rel else None))


def _llm(resposta: dict | str, guarda: list | None = None):
    def chamar(msgs):
        if guarda is not None:
            guarda.append(msgs)
        return resposta if isinstance(resposta, str) else json.dumps(resposta)
    chamar.modelo = "simulado"
    return chamar


BOA = {
    "sintese": "O lucro subiu 8% no 2T26 e a empresa pagou JCP.",
    "pontos": [
        {"tema": "proventos", "texto": "JCP de R$ 0,50 por ação (11/08/2026)."},
        {"tema": "Resultado", "texto": "Lucro de R$ 3 bi (10/08/2026)."},
    ],
    "atencao": ["Se a alta do lucro se repete no 3T26."],
    "lacunas": ["Dado não disponível.", "Os trechos não falam da dívida."],
}


def test_prompt_leva_trechos_mercado_e_a_regra():
    guarda: list = []
    lr.gerar(_analise(), REL, chamar=_llm(BOA, guarda), mercado=MERCADO)
    sistema, usuario = guarda[0][0]["content"], guarda[0][1]["content"]
    assert lr.REGRA_CONTEXTO_MERCADO in sistema
    assert "Trechos · Resultado:" in usuario and "Selic 15,00%" in usuario
    assert "O lucro foi de R$ 3 bi" in usuario


def test_resposta_valida_vira_resumo_ordenado_por_tema():
    r = lr.gerar(_analise(), REL, chamar=_llm(BOA), mercado=MERCADO)
    assert r.status == lr.APROVADA, (r.problemas, r.numeros_sem_ancora)
    assert [p.tema for p in r.pontos] == ["resultado", "proventos"]
    assert r.pontos[1].rotulo == "Proventos e recompra"
    assert r.lacunas == ("Os trechos não falam da dívida.",)
    assert r.modelo == "simulado"


def test_numero_inventado_fica_com_ressalva():
    dado = dict(BOA, sintese="O lucro subiu 37% e a dívida caiu para R$ 9 bi.")
    r = lr.gerar(_analise(), REL, chamar=_llm(dado), mercado=MERCADO)
    assert r.status == lr.COM_RESSALVAS
    assert any("37" in n for n in r.numeros_sem_ancora)


def test_falhas_viram_resumo_rejeitado():
    assert lr.gerar(_analise(), REL, chamar=_llm("não é json"),
                    mercado=MERCADO).status == lr.REJEITADA
    assert lr.gerar(_analise(), REL, chamar=_llm({"pontos": []}),
                    mercado=MERCADO).status == lr.REJEITADA

    def quebra(msgs):
        raise TimeoutError("lento")
    r = lr.gerar(_analise(), REL, chamar=quebra, mercado=MERCADO)
    assert r.status == lr.REJEITADA and "TimeoutError" in r.problemas[0]
    # Sem trechos, a LLM nem é chamada.
    guarda: list = []
    vazio = inf.Relatorios()
    assert lr.gerar(_analise(vazio), vazio, chamar=_llm(BOA, guarda),
                    mercado=MERCADO).status == lr.REJEITADA
    assert guarda == []


def test_relatorios_da_analise():
    assert lr.relatorios_da_analise(_analise()).trechos == TRECHOS
    assert lr.relatorios_da_analise(_analise(None)) is None


def test_cartao_escapa_e_mostra_validacao():
    from views import inteligencia_ativos_relatorios as tela
    r = lr.validar(dict(BOA, sintese="<script>x</script> lucro de R$ 3 bi"),
                   lr.texto_entrada(REL, "TAEE11", MERCADO))
    h = tela.cartao(replace(r, modelo="m1"))
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert h.index("Resultado") < h.index("Proventos e recompra")
    # problemas do validador e o modelo vão ao log, não à tela
    assert "Acompanhar" in h and "m1" not in h
    assert "não falam da dívida" in h      # conteúdo da resposta da LLM
    rej = tela.cartao(lr.falha("A LLM não respondeu."))
    assert "Resumo não gerado" in rej and "não respondeu" not in rej


def test_chave_muda_quando_os_trechos_mudam():
    from views import inteligencia_ativos_relatorios as tela
    outro = inf.Relatorios(trechos=TRECHOS[:1])
    assert tela.chave_sessao("X", REL) == tela.chave_sessao("X", REL)
    assert tela.chave_sessao("X", REL) != tela.chave_sessao("X", outro)


def test_fluxo_corta_depois_da_etapa_10():
    from views import inteligencia_ativos as tela
    a = _analise()
    antes, depois = tela.fluxo_partes(a)
    assert "Relatórios" in antes and "Impacto na carteira" not in antes
    assert "Impacto na carteira" in depois
    assert antes + depois == tela.fluxo_html(a)
