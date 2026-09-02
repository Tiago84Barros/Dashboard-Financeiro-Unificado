"""A LLM explica e não inventa.

Os dois testes exigidos pelo requisito -- LLM recebendo dados incompletos e LLM
tentando apresentar número que o backend não forneceu -- são
``test_dados_incompletos_*`` e ``test_numero_inventado_reprova_a_resposta``.
Nenhum destes testes chama provedor: a função ``chamar`` é injetada.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core.eventos_extremos import antifragilidade as af
from core.inteligencia import llm as L
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz
from core.memoria_mercado import estimativa as mm
from core.memoria_mercado import scores as sc

AGORA = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def carteira(n: int = 16) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": [f"AT{i:02d}" for i in range(n)],
        "weight_global": [1.0 / n] * n,
        "sector": ["Financeiro", "Energia", "Saúde", "Varejo"] * (n // 4),
        "country": ["Brasil"] * (n // 2) + ["EUA"] * (n - n // 2),
        "currency": ["BRL"] * (n // 2) + ["USD"] * (n - n // 2),
        "asset_class": ["acao"] * n,
    })


def indice_saudavel():
    return af.calcular(carteira(), liquidez=0.25, correlacao_estresse=0.42,
                       qualidade_credito=0.8, perda_simulada=-0.18)


def decisao(**kw):
    base = dict(simbolo="PETR4", acoes=(sc.MANTER,), fator_prioridade=1.0,
                bloqueia_aporte=False, motivo="cenário estável",
                score_estrutural=None, score_conjuntural=None,
                confianca="media", limitacoes=())
    base.update(kw)
    return sc.Decisao(**base)


def estimativa_publicavel(n: int = 12):
    return mm.Estimativa(
        tipo_evento="alta_de_juros", simbolo="PETR4", faixa=(-0.12, -0.03),
        valor_central=-0.07, horizonte=(5, 21), horizonte_base="pregoes",
        direcao="baixa", n_amostra=n, similaridade=61.0, confianca="media",
        experimental=False, publicavel=True, intervalo_historico=(-0.22, 0.04),
        mediana_historica=-0.06)


def item(**extra):
    base = dict(id="n1", titulo="Banco central eleva a taxa básica",
                fonte="bcb.gov.br", publicado_em=AGORA - dt.timedelta(hours=2),
                estado_verificacao="confirmada_fonte_primaria",
                qualidade_conteudo=qz.FATO, n_fontes=3, tickers=("PETR4",))
    base.update(extra)
    return P.ItemNoticia(**base)


def painel_completo():
    return P.montar(indice=indice_saudavel(), est=estimativa_publicavel(),
                    decisoes=[decisao()], noticias=[item()],
                    frescor=[qz.Frescor("Notícias", atualizado_em=AGORA,
                                        validade_horas=6)],
                    agora=AGORA)


# ── Contexto: a LLM não vê nada além do painel ───────────────────────────────
def test_contexto_sai_inteiro_do_painel():
    pn = painel_completo()
    ctx = L.contexto(pn)
    assert "Antifragilidade da carteira" in ctx
    assert "Memória de mercado" in ctx
    assert "Banco central eleva a taxa básica" in ctx
    assert "bcb.gov.br" in ctx


def test_contexto_carrega_a_marca_de_qualidade_de_cada_numero():
    """Fato, hipótese e estimativa têm de chegar rotulados ao modelo."""
    ctx = L.contexto(painel_completo())
    assert "[Fato]" in ctx and "[Estimativa]" in ctx


def test_contexto_publica_o_que_nao_foi_medido():
    """Silenciar o ausente convidaria o modelo a preencher a lacuna."""
    pn = P.montar(decisoes=[decisao()], agora=AGORA)
    assert "não medido" in L.contexto(pn)


def test_contexto_de_um_simbolo_nao_vaza_os_outros():
    pn = P.montar(decisoes=[decisao(simbolo="PETR4"), decisao(simbolo="VALE3")],
                  agora=AGORA)
    ctx = L.contexto(pn, simbolo="PETR4")
    assert "PETR4" in ctx and "VALE3" not in ctx


def test_contexto_e_o_mesmo_texto_da_verificacao():
    """Prompt e âncora divergentes reprovariam citação correta."""
    pn = painel_completo()
    ctx = L.contexto(pn)
    assert ctx in L.montar_prompt(pn)


# ── Declarações obrigatórias: derivadas, não confiadas ao modelo ─────────────
def test_amostra_pequena_exige_declaracao():
    pn = P.montar(est=estimativa_publicavel(n=3), agora=AGORA)
    assert L.D_AMOSTRA_PEQUENA in L.declaracoes_obrigatorias(pn)


def test_amostra_grande_nao_exige_declaracao_de_amostra():
    pn = P.montar(est=estimativa_publicavel(n=40), agora=AGORA)
    assert L.D_AMOSTRA_PEQUENA not in L.declaracoes_obrigatorias(pn)


def test_sem_memoria_a_amostra_e_declarada_insuficiente():
    """Ausência de amostra não pode passar como amostra suficiente."""
    pn = P.montar(indice=indice_saudavel(), agora=AGORA)
    assert L.D_AMOSTRA_PEQUENA in L.declaracoes_obrigatorias(pn)


def test_dado_vencido_exige_declaracao():
    pn = P.montar(indice=indice_saudavel(), agora=AGORA, frescor=[
        qz.Frescor("Carteira", atualizado_em=AGORA - dt.timedelta(days=4),
                   validade_horas=24)])
    assert L.D_DESATUALIZADO in L.declaracoes_obrigatorias(pn)


def test_provedor_fora_do_ar_exige_declaracao():
    pn = P.montar(agora=AGORA, provedores=[
        qz.Provedor("newsapi", disponivel=False, detalhe="HTTP 503")])
    assert L.D_DESATUALIZADO in L.declaracoes_obrigatorias(pn)


def test_evento_nao_confirmado_exige_declaracao():
    pn = P.montar(noticias=[item(estado_verificacao="nao_verificada",
                                 qualidade_conteudo=qz.HIPOTESE)], agora=AGORA)
    assert L.D_NAO_CONFIRMADO in L.declaracoes_obrigatorias(pn)


def test_evento_confirmado_nao_exige_declaracao_de_confirmacao():
    pn = P.montar(noticias=[item()], agora=AGORA)
    assert L.D_NAO_CONFIRMADO not in L.declaracoes_obrigatorias(pn)


def test_fontes_divergentes_exigem_declaracao():
    pn = P.montar(noticias=[item(estado_verificacao="contestada")], agora=AGORA)
    assert L.D_FONTES_DIVERGEM in L.declaracoes_obrigatorias(pn)


def test_impacto_nao_estimavel_exige_declaracao():
    est = mm.Estimativa(
        tipo_evento="x", simbolo="PETR4", faixa=None, valor_central=None,
        horizonte=None, horizonte_base="pregoes", direcao="indefinida",
        n_amostra=9, similaridade=30.0, confianca="baixa", experimental=True,
        publicavel=False)
    pn = P.montar(est=est, agora=AGORA)
    assert L.D_SEM_IMPACTO in L.declaracoes_obrigatorias(pn)


def test_declaracoes_nao_se_repetem():
    pn = P.montar(est=estimativa_publicavel(n=2), agora=AGORA, provedores=[
        qz.Provedor("a", disponivel=False), qz.Provedor("b", disponivel=False)])
    exigidas = L.declaracoes_obrigatorias(pn)
    assert len(exigidas) == len(set(exigidas))


def test_prompt_lista_as_nove_perguntas_e_as_declaracoes():
    pn = P.montar(est=estimativa_publicavel(n=2), agora=AGORA)
    prompt = L.montar_prompt(pn)
    for pergunta in L.PERGUNTAS:
        assert pergunta in prompt
    assert L.TEXTO_DECLARACAO[L.D_AMOSTRA_PEQUENA] in prompt


# ── Validação: o portão que faz a regra valer ────────────────────────────────
def test_resposta_que_cita_o_painel_e_aprovada():
    pn = painel_completo()
    ind = pn.antifragilidade.valor_de("Índice de antifragilidade")
    resposta = (f"O índice de antifragilidade está em {ind.texto}. "
                "As fontes divergem? Não confirmado? Não se aplica.")
    v = L.validar(resposta, pn)
    assert v.aprovada and v.razao_ancorada == 1.0


def test_numero_inventado_reprova_a_resposta():
    """Teste exigido: a LLM tenta apresentar número que o backend não deu."""
    pn = painel_completo()
    v = L.validar("A carteira deve perder 41,3% e o índice está em 0,9987.", pn)
    assert not v.aprovada
    assert v.numeros_inventados
    assert "não publicou" in v.motivo


def test_score_alterado_cai_no_mesmo_portao():
    """Score trocado é, por construção, número fora do painel."""
    pn = P.montar(decisoes=[decisao(fator_prioridade=1.0)], agora=AGORA)
    v = L.validar("A prioridade de aporte deste ativo é 2,75.", pn)
    assert not v.aprovada and v.numeros_inventados


def test_promessa_de_retorno_reprova_mesmo_com_numero_certo():
    pn = painel_completo()
    ind = pn.antifragilidade.valor_de("Índice de antifragilidade")
    v = L.validar(f"Com índice de {ind.texto}, o retorno garantido é alto.", pn)
    assert not v.aprovada and "promessa de retorno" in v.frases_proibidas


def test_previsao_apresentada_como_fato_e_recusada():
    pn = painel_completo()
    v = L.validar("A ação vai subir nas próximas semanas.", pn)
    assert not v.aprovada


def test_ordem_de_operacao_e_recusada():
    pn = painel_completo()
    v = L.validar("Recomendo: compre agora enquanto está barato.", pn)
    assert not v.aprovada and "ordem de operação" in v.frases_proibidas


def test_declaracao_omitida_e_registrada_e_nao_reprova():
    """Omissão o backend conserta; invenção ele não conserta."""
    pn = P.montar(est=estimativa_publicavel(n=2), agora=AGORA)
    v = L.validar("Tudo tranquilo por aqui.", pn)
    assert v.aprovada
    assert L.D_AMOSTRA_PEQUENA in v.declaracoes_faltando


# ── explicar(): o caminho completo, sem provedor ─────────────────────────────
def test_resposta_boa_e_publicada_com_as_declaracoes_anexadas():
    pn = P.montar(est=estimativa_publicavel(n=2), agora=AGORA)
    exp = L.explicar(pn, chamar=lambda _p: "O evento foi noticiado hoje.")
    assert exp.gerada_por_llm
    assert L.TEXTO_DECLARACAO[L.D_AMOSTRA_PEQUENA] in exp.texto


def test_resposta_com_numero_inventado_nao_e_publicada():
    pn = painel_completo()
    exp = L.explicar(pn, chamar=lambda _p: "O índice está em 0,9987 e cairá 41,3%.")
    assert exp.origem == "backend"
    assert "0,9987" not in exp.texto
    assert exp.validacao is not None and not exp.validacao.aprovada


def test_falha_do_provedor_nao_deixa_a_tela_muda():
    def explode(_p):
        raise RuntimeError("HTTP 503")

    exp = L.explicar(painel_completo(), chamar=explode)
    assert exp.origem == "backend" and exp.texto.strip()


def test_resposta_vazia_cai_no_backend():
    exp = L.explicar(painel_completo(), chamar=lambda _p: "   ")
    assert exp.origem == "backend" and exp.texto.strip()


# ── Explicação determinística ────────────────────────────────────────────────
def test_deterministica_responde_as_nove_perguntas():
    exp = L.explicacao_deterministica(painel_completo(), simbolo="PETR4")
    for pergunta in L.PERGUNTAS:
        assert pergunta in exp.texto


def test_dados_incompletos_nao_viram_afirmacao_confiante():
    """Teste exigido: a LLM recebe dados incompletos.

    Sem antifragilidade calculada, a explicação tem de dizer que não sabe -- e
    dizer explicitamente que não saber não é estar seguro.
    """
    pn = P.montar(decisoes=[decisao()], agora=AGORA)
    exp = L.explicacao_deterministica(pn, simbolo="PETR4")
    assert "não pôde ser calculada" in exp.texto
    assert "não significa que ela seja resistente" in exp.texto


def test_dados_incompletos_chegam_declarados_ao_prompt():
    pn = P.montar(decisoes=[decisao()], agora=AGORA, provedores=[
        qz.Provedor("newsapi", disponivel=False, detalhe="HTTP 503")])
    prompt = L.montar_prompt(pn)
    assert L.TEXTO_DECLARACAO[L.D_DESATUALIZADO] in prompt
    assert L.TEXTO_DECLARACAO[L.D_AMOSTRA_PEQUENA] in prompt


def test_sem_noticia_a_ausencia_nao_vira_calmaria():
    pn = P.montar(indice=indice_saudavel(), agora=AGORA)
    exp = L.explicacao_deterministica(pn)
    assert "falha de coleta" in exp.texto


def test_deterministica_nao_inventa_numero():
    """A explicação do backend precisa passar no próprio portão."""
    pn = painel_completo()
    exp = L.explicacao_deterministica(pn, simbolo="PETR4")
    v = L.validar(exp.texto, pn, simbolo="PETR4")
    assert v.aprovada, v.numeros_inventados


def test_deterministica_nao_promete_retorno():
    exp = L.explicacao_deterministica(painel_completo(), simbolo="PETR4")
    assert L._frases_proibidas(exp.texto) == ()


def test_aviso_de_que_nao_e_recomendacao_nao_e_confundido_com_ordem():
    """"venda" como substantivo aparece no proprio aviso legal da tela.

    O padrao antigo casava a palavra solta e reprovava o disclaimer -- punia
    exatamente o texto que existe para proteger o usuario.
    """
    assert not L._frases_proibidas(
        "Nada aqui e recomendacao de compra ou venda.")
    assert L._frases_proibidas("Venda tudo agora.")
    assert L._frases_proibidas("Compre agora antes que suba.")
