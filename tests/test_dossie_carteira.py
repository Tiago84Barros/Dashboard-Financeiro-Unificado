"""O dossiê por classe: seções, contexto, pares, documentos e a regra 7.

O que estes testes prendem não é o texto do prompt — é o que faria o dossiê
mentir sem quebrar: uma classe perdendo seção, o patrimônio total vazando no
contexto, um par vindo de outro setor, uma falha de leitura passando por
"nada a relatar" e a proibição de recomendar voltando por descuido.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from core.carteira_documentos import documentos_da_classe  # noqa: E402
from core.llm_carteira import escopo_da_classe, regras_da_analise  # noqa: E402
from core.llm_context_carteira import build_carteira_classe_context  # noqa: E402
from core.llm_dossie_carteira import (  # noqa: E402
    prompt_do_dossie,
    secoes_da_classe,
)
from core.portfolio_db_analysis import _pares_do_universo  # noqa: E402

_CLASSES = ("acoes", "fiis", "tesouro", "exterior")


def _posicoes():
    return [
        {"ticker": "BBAS3", "classe": "Ações BR", "valor_mercado": 34090.95,
         "setor": "Financeiro"},
        {"ticker": "TAEE11", "classe": "Ações BR", "valor_mercado": 23000.00,
         "setor": "Energia"},
    ]


# ── seções ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("classe", _CLASSES)
def test_cada_classe_declara_as_doze_secoes(classe):
    secoes = secoes_da_classe(classe)
    assert len(secoes) == 12
    assert all(titulo and instrucao for titulo, instrucao in secoes)


@pytest.mark.parametrize("classe", _CLASSES)
def test_titulos_nao_se_repetem_dentro_da_classe(classe):
    titulos = [t for t, _ in secoes_da_classe(classe)]
    assert len(set(titulos)) == 12, f"{classe}: título repetido em {titulos}"


def test_tesouro_difere_de_acoes_onde_deve_diferir():
    # 1 concentração, 5 substituição, 7 rebalanceamento: as três seções cuja
    # lógica muda com a classe. Se alguma voltar a ser igual, o dossiê de
    # Tesouro passa a pedir "pares do mesmo setor" de um título público.
    acoes = secoes_da_classe("acoes")
    tesouro = secoes_da_classe("tesouro")
    for indice in (0, 4, 6):
        assert acoes[indice] != tesouro[indice], f"seção {indice + 1} não diferiu"


def test_classe_desconhecida_cai_em_acoes():
    assert secoes_da_classe("criptoarte") == secoes_da_classe("acoes")


@pytest.mark.parametrize("classe", _CLASSES)
def test_prompt_carrega_as_secoes_o_escopo_e_as_regras(classe):
    prompt = prompt_do_dossie("CONTEXTO QUALQUER", classe=classe)
    for titulo, _ in secoes_da_classe(classe):
        assert titulo in prompt
    assert escopo_da_classe(classe) in prompt
    assert regras_da_analise() in prompt
    assert "CONTEXTO QUALQUER" in prompt


def test_tributacao_nao_vem_com_numero_congelado():
    # Alíquota e limite de isenção são conhecimento externo que envelhece; o
    # prompt exige que o modelo NOMEIE o regime, não que repita um número nosso.
    for classe in _CLASSES:
        prompt = prompt_do_dossie("", classe=classe)
        assert "20.000" not in prompt
        assert "R$ 20" not in prompt


# ── contexto: reais são opt-in ───────────────────────────────────────────────

def test_sem_toggle_nenhum_valor_absoluto_aparece():
    texto = build_carteira_classe_context("acoes", _posicoes())
    assert "34.090" not in texto
    assert "23.000" not in texto


def test_com_toggle_o_valor_aparece_por_posicao():
    texto = build_carteira_classe_context("acoes", _posicoes(), valores_reais=True)
    assert "34.090,95" in texto
    assert "BBAS3" in texto


def test_o_contexto_diz_que_o_patrimonio_total_nao_esta_nele():
    texto = build_carteira_classe_context("acoes", _posicoes(), valores_reais=True)
    baixo = texto.lower()
    assert "patrimônio" in baixo
    # A soma desta classe é derivável e isso é aceito; o consolidado não entra.
    assert "57.090" not in texto


# ── pares do universo ────────────────────────────────────────────────────────

def _candidatos():
    return [
        {"ticker": "BBAS3", "score": 90.0, "grupo": "Financeiro"},
        {"ticker": "ITUB4", "score": 88.0, "grupo": "Financeiro"},
        {"ticker": "BBDC4", "score": 70.0, "grupo": "Financeiro"},
        {"ticker": "SANB11", "score": 60.0, "grupo": "Financeiro"},
        {"ticker": "PETR4", "score": 99.0, "grupo": "Petróleo"},
        {"ticker": "SEMNOTA", "score": None, "grupo": "Financeiro"},
    ]


def test_pares_excluem_o_que_ja_esta_na_carteira():
    pares = _pares_do_universo(_candidatos(), carregados=["BBAS3"],
                               grupos=["Financeiro"])
    assert "BBAS3" not in [p["ticker"] for p in pares]


def test_pares_ficam_no_grupo_dos_ativos_do_usuario():
    pares = _pares_do_universo(_candidatos(), carregados=["BBAS3"],
                               grupos=["Financeiro"])
    assert {p["grupo"] for p in pares} == {"Financeiro"}


def test_par_sem_nota_nao_entra():
    # Ativo não apurado não é ativo mediano — recomendar o que não foi medido
    # é exatamente o que a regra 5 do prompt proíbe.
    pares = _pares_do_universo(_candidatos(), carregados=[], grupos=["Financeiro"])
    assert "SEMNOTA" not in [p["ticker"] for p in pares]


def test_teto_por_grupo_impede_um_setor_de_tomar_o_contexto():
    pares = _pares_do_universo(_candidatos(), carregados=[],
                               grupos=["Financeiro", "Petróleo"], por_grupo=2)
    financeiros = [p for p in pares if p["grupo"] == "Financeiro"]
    assert len(financeiros) == 2
    assert "PETR4" in [p["ticker"] for p in pares]


def test_pares_sao_deterministicos_no_empate():
    empatados = [{"ticker": t, "score": 50.0, "grupo": "Financeiro"}
                 for t in ("CCCC4", "AAAA3", "BBBB3")]
    saida = [p["ticker"] for p in _pares_do_universo(
        empatados, carregados=[], grupos=["Financeiro"], por_grupo=3)]
    assert saida == ["AAAA3", "BBBB3", "CCCC4"]


def test_sem_grupo_conhecido_nao_inventa_par():
    pares = _pares_do_universo(_candidatos(), carregados=[], grupos=[None, ""])
    assert pares == []


# ── documentos: falha é erro declarado, nunca lista vazia ────────────────────

def test_falha_de_leitura_vira_erro_declarado(monkeypatch):
    import core.noticias.vitrine as vitrine

    def _explode(engine, simbolos):
        raise vitrine.VitrineIlegivel("conexão recusada")

    monkeypatch.setattr(vitrine, "ler", _explode)
    saida = documentos_da_classe("fiis", ["HGLG11"], engine=object())
    assert saida["erro"]
    assert saida["itens"] == []


def test_banco_ausente_e_erro_nao_silencio():
    saida = documentos_da_classe("fiis", ["HGLG11"], engine=None)
    assert saida["erro"]


def test_tesouro_declara_a_ausencia_de_corpus_em_vez_de_sumir():
    saida = documentos_da_classe("tesouro", ["Tesouro Selic 2029"])
    assert saida["erro"] is None
    assert saida["nota"]


def test_contexto_distingue_erro_de_janela_vazia():
    com_erro = build_carteira_classe_context(
        "fiis", _posicoes(),
        documentos={"erro": "A vitrine não pôde ser lida.", "itens": []})
    vazio = build_carteira_classe_context(
        "fiis", _posicoes(), documentos={"fonte": "vitrine", "itens": []})
    assert "não pôde ser lida" in com_erro
    assert "fora da janela de coleta" in vazio


def test_contexto_usa_a_nota_da_classe_sem_corpus():
    texto = build_carteira_classe_context(
        "tesouro", _posicoes(),
        documentos=documentos_da_classe("tesouro", ["Tesouro IPCA+ 2035"]))
    assert "não têm documentos por emissor" in texto
    assert "fora da janela de coleta" not in texto


# ── regra 7: a proibição não pode voltar em silêncio ────────────────────────

def test_recomendacao_e_permitida_e_presa_ao_lastro():
    regras = regras_da_analise()
    assert "permitida" in regras
    assert "lastro" in regras
    # A redação antiga proibia; se ela voltar, este teste cai.
    assert "Não faça recomendação personalizada" not in regras
    assert "não recomende compra" not in regras.lower()


def test_a_regra_vale_para_o_chat_e_para_o_dossie():
    # Uma fonte só. Guarda duplicada diverge, e divergir aqui é o chat
    # permitir o que o dossiê proíbe na mesma aba, sem nada quebrar.
    fonte = (_RAIZ / "core" / "llm_carteira.py").read_text(encoding="utf-8")
    assert fonte.count("REGRAS OBRIGATÓRIAS") == 1
    for modulo in ("core/llm_dossie_carteira.py", "core/llm_carteira.py"):
        texto = (_RAIZ / modulo).read_text(encoding="utf-8")
        assert "regras_da_analise()" in texto
