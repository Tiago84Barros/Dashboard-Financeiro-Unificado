"""O chat do Tesouro recebe a taxa contratada e a data de cada compra.

A LLM da sub-aba Tesouro dizia não ter a taxa contratada nem a data de compra
dos títulos, com os lotes do Extrato Analítico no banco: o contexto só levava
o agregado. E afirmava que o valor de mercado vinha do saldo da corretora, o
que deixou de valer quando a carteira passou a marcar pela curva (PR #621).

Dados sintéticos: nenhuma taxa, data ou quantidade aqui é da carteira real.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.llm_context_carteira import build_carteira_classe_context
from core.portfolio_valuations import aggregate_tesouro, texto_marcacao_tesouro
from core.tesouro_mtm import LoteTesouro, TaxaContratada
from core.tesouro_posicao import TituloAnalitico


def _lote(dia: date, qtd: float, taxa: TaxaContratada | None,
          investido: float = 1000.0) -> LoteTesouro:
    return LoteTesouro(data_aplicacao=dia, quantidade=qtd, preco_aplicacao=investido / qtd,
                       valor_investido=investido, taxa_contratada=taxa)


def _titulo(chave: str, nome: str, lotes, *, indexador: str = "PRE",
            venda: float | None = 0.101, compra: float | None = 0.102,
            mtm: float | None = 0.031, origem: str = "extrato",
            report: date | None = date(2030, 3, 4)) -> TituloAnalitico:
    return TituloAnalitico(
        security_key=chave, titulo=nome, vencimento=date(2033, 1, 1),
        custodiante=None, report_date=report, lotes=list(lotes), avaliacoes=[],
        taxa_mercado_venda=venda, taxa_mercado_compra=compra, pu_mercado=900.0,
        data_curva=date(2030, 5, 6) if venda is not None else None,
        quantidade=sum(lote.quantidade for lote in lotes),
        valor_investido=sum(lote.valor_investido for lote in lotes),
        valor_bruto=None, valor_liquido=None, ir=None, iof=None,
        ganho_mtm_reais=12.0 if mtm is not None else None, mtm_pct=mtm,
        fonte_preco="curva" if mtm is not None else "indisponivel",
        aproximado=False, indexador=indexador, taxa_indice=None,
        origem_lotes=origem)


PRE = _titulo("TPRE2033", "Tesouro Prefixado 2033", [
    _lote(date(2029, 2, 3), 1.5, TaxaContratada("PRE", 0.1111, "11,11%")),
    _lote(date(2029, 8, 9), 0.5, TaxaContratada("PRE", 0.1222, "12,22%")),
])
IPCA_CUPOM = _titulo("TIPCAJ2033", "Tesouro IPCA+ com Juros Semestrais 2033", [
    _lote(date(2028, 1, 2), 3.0, TaxaContratada("IPCA", 0.0555, "IPCA + 5,55%")),
], indexador="IPCA", venda=0.0612, compra=0.0624, mtm=-0.0211)


def _pos(ticker, qtd, nome=None, fonte="curva_tesouro", vm=1000.0):
    return {"ticker": ticker, "nome": nome or ticker, "quantidade": qtd,
            "valor_mercado": vm, "total_investido": 900.0, "cotacao_fonte": fonte}


def _contexto(lotes, posicoes=None, **kw):
    posicoes = posicoes if posicoes is not None else [
        _pos("TPRE2033", 2.0, "Tesouro Prefixado 2033"),
        _pos("TIPCA2033", 3.0, "Tesouro IPCA+ com Juros Semestrais 2033")]
    return build_carteira_classe_context(
        "tesouro", posicoes, tesouro=aggregate_tesouro(posicoes, 2030),
        lotes_tesouro=lotes, **kw)


# ── os lotes chegam à LLM ───────────────────────────────────────────────────


def test_cada_lote_leva_data_de_compra_e_taxa_contratada():
    texto = _contexto({"titulos": [PRE, IPCA_CUPOM], "diagnostico": {"origem": "extrato"}})
    assert "TAXA CONTRATADA" in texto
    assert "aplicado em 03/02/2029 a 11,11%" in texto
    assert "aplicado em 09/08/2029 a 12,22%" in texto
    assert "aplicado em 02/01/2028 a IPCA + 5,55%" in texto
    assert "Extrato Analítico de 04/03/2030" in texto


def test_taxa_de_hoje_e_marcacao_vem_com_a_data_da_curva():
    texto = _contexto({"titulos": [PRE, IPCA_CUPOM], "diagnostico": {"origem": "extrato"}})
    assert "curva de 06/05/2030" in texto
    assert "venda (recompra) 10,10%" in texto
    assert "venda (recompra) IPCA + 6,12%" in texto
    assert "+3,10%" in texto and "-2,11%" in texto


def test_peso_do_lote_sai_em_percentual_e_quantidade_so_a_pedido():
    lotes = {"titulos": [PRE], "diagnostico": {"origem": "extrato"}}
    sem = _contexto(lotes, [_pos("TPRE2033", 2.0, "Tesouro Prefixado 2033")])
    assert "75% da quantidade do título" in sem
    assert "quantidade 1,50" not in sem and "R$" not in sem
    com = _contexto(lotes, [_pos("TPRE2033", 2.0, "Tesouro Prefixado 2033")],
                    valores_reais=True)
    assert "quantidade 1,50" in com and "investido R$ 1.000,00" in com


def test_taxa_deduzida_da_movimentacao_nao_se_passa_por_impressa():
    titulo = _titulo("TPRE2033", "Tesouro Prefixado 2033", [
        _lote(date(2029, 2, 3), 2.0, TaxaContratada("PRE", 0.1111, "11,11%"))],
        origem="movimentacao", report=None)
    texto = _contexto({"titulos": [titulo], "diagnostico": {"origem": "movimentacao"}})
    assert "DEDUZIDA" in texto and "Movimentação" in texto
    assert "Extrato Analítico de" not in texto


def test_lote_sem_taxa_nao_vira_zero():
    titulo = _titulo("TPRE2033", "Tesouro Prefixado 2033", [
        _lote(date(2029, 2, 3), 2.0, None)], origem="movimentacao", report=None)
    texto = _contexto({"titulos": [titulo], "diagnostico": {"origem": "movimentacao"}})
    assert "taxa não lida" in texto
    assert "a 0,00%" not in texto


def test_titulo_fora_da_custodia_e_nomeado():
    texto = _contexto({"titulos": [PRE], "diagnostico": {
        "origem": "movimentacao", "divergentes": [("TSELIC2031", 2.0, 1.0)]}})
    assert "TSELIC2031" in texto and "custódia" in texto


# ── cobertura do extrato contra a posição de hoje ───────────────────────────


def test_cupom_casa_com_a_posicao_pela_chave_da_curva():
    # A foto chama o título de cupom de TIPCA2033; o extrato, de TIPCAJ2033.
    texto = _contexto({"titulos": [PRE, IPCA_CUPOM], "diagnostico": {"origem": "extrato"}})
    assert "não está na carteira atual" not in texto
    assert texto.count("cobrem a posição atual inteira") == 2


def test_compra_depois_do_extrato_deixa_parte_sem_taxa():
    texto = _contexto({"titulos": [PRE], "diagnostico": {"origem": "extrato"}},
                      [_pos("TPRE2033", 4.0, "Tesouro Prefixado 2033")])
    assert "50% da quantidade da posição atual" in texto
    assert "desconhecidas" in texto


def test_titulo_vendido_depois_do_extrato_e_marcado_como_passado():
    texto = _contexto({"titulos": [PRE, IPCA_CUPOM], "diagnostico": {"origem": "extrato"}},
                      [_pos("TPRE2033", 2.0, "Tesouro Prefixado 2033")])
    assert "TIPCAJ2033" in texto
    assert "não está na carteira atual" in texto


def test_posicao_sem_lote_algum_e_listada():
    texto = _contexto({"titulos": [PRE], "diagnostico": {"origem": "extrato"}},
                      [_pos("TPRE2033", 2.0, "Tesouro Prefixado 2033"),
                       _pos("TSELIC2031", 1.0, "Tesouro Selic 2031")])
    assert "sem nenhum lote lido: TSELIC2031" in texto


def test_posicao_sem_quantidade_nao_inventa_cobertura():
    texto = _contexto({"titulos": [PRE], "diagnostico": {"origem": "extrato"}},
                      [_pos("TPRE2033", 0.0, "Tesouro Prefixado 2033")])
    assert "cobertura não medida" in texto


# ── ausência e falha ditas, nunca caladas ───────────────────────────────────


def test_falha_de_leitura_nao_vira_inexistencia():
    texto = _contexto({"erro": "conexão recusada"})
    assert "conexão recusada" in texto
    assert "NÃO que não existe" in texto


def test_sem_lote_algum_diz_o_que_falta():
    texto = _contexto({"titulos": [], "diagnostico": {}})
    assert "nenhum lote lido" in texto
    assert "Extrato Analítico" in texto


def test_outras_classes_e_chamadas_antigas_nao_mudam():
    posicoes = [_pos("TPRE2033", 2.0)]
    antigo = build_carteira_classe_context(
        "tesouro", posicoes, tesouro=aggregate_tesouro(posicoes, 2030))
    assert "LOTES DO TESOURO" not in antigo
    acoes = build_carteira_classe_context(
        "acoes", [{"ticker": "AAAA3", "valor_mercado": 10.0}],
        lotes_tesouro={"titulos": [PRE]})
    assert "LOTES DO TESOURO" not in acoes


# ── a limitação velha saiu; a marcação diz de onde vem ──────────────────────


def test_contexto_nao_afirma_mais_saldo_da_corretora_quando_ha_curva():
    texto = _contexto(None)
    assert "não de marcação a mercado independente" not in texto
    assert "curva oficial do Tesouro" in texto


@pytest.mark.parametrize("fontes,esperado", [
    (["curva_tesouro", "curva_tesouro"], "de todos os 2 títulos vem da curva oficial"),
    (["curva_tesouro", "curva_tesouro_antiga"], "1 título pela última curva oficial, desatualizada"),
    (["snapshot"], "1 título pelo saldo da foto da corretora/B3"),
])
def test_marcacao_conta_titulos_por_fonte(fontes, esperado):
    posicoes = [_pos(f"TPRE20{30 + i}", 1.0, fonte=f) for i, f in enumerate(fontes)]
    resumo = aggregate_tesouro(posicoes, 2030)
    assert esperado in texto_marcacao_tesouro(resumo["marcacao"])


def test_marcacao_conta_o_ticker_uma_vez():
    posicoes = [_pos("TPRE2033", 1.0), _pos("TPRE2033", 1.0)]
    assert aggregate_tesouro(posicoes, 2030)["marcacao"] == {
        "curva": 1, "curva_antiga": 0, "foto": 0}


def test_carteira_vazia_nao_afirma_fonte():
    assert texto_marcacao_tesouro(aggregate_tesouro([], 2030)["marcacao"]) == ""


# ── o carregador nunca levanta ──────────────────────────────────────────────


def test_carregador_devolve_erro_declarado(monkeypatch):
    from core import tesouro_posicao

    def explode(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(tesouro_posicao, "carregar_titulos", explode)
    assert tesouro_posicao.lotes_para_contexto(object(), "u")["erro"].startswith("RuntimeError")
    assert "erro" in tesouro_posicao.lotes_para_contexto(None, "u")
    assert "erro" in tesouro_posicao.lotes_para_contexto(object(), "")


def test_carregador_repassa_titulos_e_diagnostico(monkeypatch):
    from core import tesouro_posicao

    def falso(engine, user_id, diagnostico=None):
        diagnostico["origem"] = "extrato"
        return [PRE]

    monkeypatch.setattr(tesouro_posicao, "carregar_titulos", falso)
    saida = tesouro_posicao.lotes_para_contexto(object(), "u")
    assert saida == {"titulos": [PRE], "diagnostico": {"origem": "extrato"}}


def test_falha_de_leitura_nao_fica_no_cache_do_chat(monkeypatch):
    from core import tesouro_posicao
    from design import portfolio_db_analysis as pdb

    respostas = iter([{"erro": "banco fora"},
                      {"titulos": [PRE], "diagnostico": {"origem": "extrato"}}])
    monkeypatch.setattr(tesouro_posicao, "lotes_para_contexto",
                        lambda engine, user_id: next(respostas))
    pdb._lotes_tesouro_cacheado.clear()
    try:
        assert pdb.carregar_lotes_tesouro() == {"erro": "banco fora"}
        assert pdb.carregar_lotes_tesouro()["titulos"] == [PRE]
    finally:
        pdb._lotes_tesouro_cacheado.clear()


# ── Grau de Confiança mede a taxa que o chat repete ─────────────────────────


class _ConnLinhas:
    def __init__(self, taxas):
        self.taxas = taxas

    def execute(self, *_a, **_k):
        return iter([(t,) for t in self.taxas])


def test_grau_de_confianca_conta_lotes_com_taxa_legivel():
    from core.confianca_secao import _taxa_contratada_do_tesouro

    comp = _taxa_contratada_do_tesouro(_ConnLinhas(["11,11%", "IPCA + 5,55%", None, "??"]))
    assert comp.nome == "Taxa contratada do Tesouro"
    assert comp.pct == pytest.approx(50.0)
    assert "2 de 4" in comp.evidencia


def test_grau_de_confianca_sem_extrato_nao_mede():
    from core.confianca_secao import _taxa_contratada_do_tesouro

    comp = _taxa_contratada_do_tesouro(_ConnLinhas([]))
    assert comp.pct is None and "Extrato Analitico" in comp.evidencia


def test_grau_de_confianca_falha_da_taxa_nao_derruba_a_curva():
    from core.confianca_secao import confianca_investimentos

    class _Quebrado:
        def connect(self):
            raise RuntimeError("sem banco")

    nomes = {c.nome: c.pct for c in confianca_investimentos(_Quebrado()).componentes}
    assert nomes["Taxa contratada do Tesouro"] is None
    assert "Curva do Tesouro" in nomes
