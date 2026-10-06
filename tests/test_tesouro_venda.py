"""
tests/test_tesouro_venda.py

Guardas da sub-aba "Tesouro Direto": a projeção de carrego, a taxa de
indiferença, a série histórica da marcação, a leitura por título e a
apresentação. Sem banco e sem rede — tudo entra por parâmetro.

O que estes testes protegem é a **honestidade** da tela: ela diz se dá para
vender e de quanto seria a vantagem sem concluir "subiu, então venda". Vender e
recomprar o mesmo papel é estritamente pior (o IR antecipado é perda seca), e a
taxa de indiferença é a única resposta em número que a pergunta comporta.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

import pytest

from core.tesouro_historico import extremos_da_serie, serie_mtm_posicao
from core.tesouro_mtm import (
    LoteTesouro,
    TaxaContratada,
    aliquota_ir,
    avaliar_lote,
    dias_uteis,
    projetar_carrego,
    taxa_de_indiferenca,
    trajetoria_carrego,
)
from core.tesouro_venda import (
    AGIO,
    DESAGIO,
    NEUTRA,
    SEM_PRECO,
    ler_venda,
    resumo_da_carteira,
    rotulo_taxa,
)

HOJE = date(2026, 10, 2)
VENCIMENTO = date(2029, 1, 1)


def _lote(*, taxa: float, indexador: str = "PRE", quantidade: float = 100.0,
          aplicacao: date = date(2024, 5, 10),
          investido: float = 60_000.0) -> LoteTesouro:
    return LoteTesouro(
        data_aplicacao=aplicacao,
        quantidade=quantidade,
        preco_aplicacao=investido / quantidade,
        valor_investido=investido,
        taxa_contratada=TaxaContratada(indexador, taxa, f"{taxa:.2%}"),
        valor_bruto_extrato=investido * 1.2,
    )


def _avaliacao(lote: LoteTesouro, *, taxa_mercado: float, pu: float,
               nome: str = "Tesouro Prefixado 2029"):
    return avaliar_lote(lote, nome_titulo=nome, vencimento=VENCIMENTO,
                        data_avaliacao=HOJE, taxa_mercado_resgate=taxa_mercado,
                        pu_mercado=pu)


# ─────────────────────────────────────────────────────────────────────────────
# Projeção de carrego
# ─────────────────────────────────────────────────────────────────────────────

def test_carrego_capitaliza_o_bruto_de_hoje_ate_o_vencimento():
    lote = _lote(taxa=0.1180)
    aval = _avaliacao(lote, taxa_mercado=0.1360, pu=750.0)
    p = projetar_carrego([aval], vencimento=VENCIMENTO, data_avaliacao=HOJE,
                         taxa_mercado_resgate=0.1360)

    du = dias_uteis(HOJE, VENCIMENTO)
    assert p.du_restante == du
    assert p.valor_bruto_vencimento == pytest.approx(
        p.valor_bruto_hoje * (1.1360 ** (du / 252)))
    # Carregar só faz sentido se entrega mais que vender: o bruto cresce e o IR
    # do fim não come o acréscimo inteiro.
    assert p.valor_liquido_vencimento > p.valor_liquido_hoje


def test_carrego_sem_taxa_de_mercado_nao_inventa_vencimento():
    """Sem preço, o valor de hoje ainda existe; o do fim, não.

    Projetar com taxa nenhuma devolveria um número que ninguém mediu — e ele
    iria direto para a barra do gráfico como se fosse observação.
    """
    aval = _avaliacao(_lote(taxa=0.1180), taxa_mercado=None, pu=None)
    p = projetar_carrego([aval], vencimento=VENCIMENTO, data_avaliacao=HOJE,
                         taxa_mercado_resgate=None)
    assert p.valor_bruto_hoje is not None
    assert p.valor_liquido_vencimento is None
    assert p.imposto_antecipado is not None


def test_carrego_ignora_lote_sem_valor():
    aval = _avaliacao(_lote(taxa=0.1180), taxa_mercado=0.1360, pu=750.0)
    cego = replace(aval, valor_bruto=None, valor_liquido=None)
    p = projetar_carrego([cego], vencimento=VENCIMENTO, data_avaliacao=HOJE,
                         taxa_mercado_resgate=0.1360)
    assert p.valor_bruto_hoje is None
    assert p.valor_liquido_vencimento is None


# ─────────────────────────────────────────────────────────────────────────────
# Taxa de indiferença — o número que responde "de quanto é a vantagem?"
# ─────────────────────────────────────────────────────────────────────────────

def test_indiferenca_empata_com_carregar_por_construcao():
    """Reinvestir o líquido de hoje exatamente na taxa de indiferença empata.

    É o round-trip da função: se o número devolvido não reconstrói o valor de
    carregar, ele não é a fronteira da decisão e a tela estaria comparando o
    cardápio com uma régua errada.
    """
    lote = _lote(taxa=0.1180)
    aval = _avaliacao(lote, taxa_mercado=0.1360, pu=790.0)
    kwargs = dict(vencimento=VENCIMENTO, data_avaliacao=HOJE,
                  taxa_mercado_resgate=0.1360)
    indiferenca = taxa_de_indiferenca([aval], **kwargs)
    p = projetar_carrego([aval], **kwargs)
    assert indiferenca is not None

    du = p.du_restante
    bruto = p.valor_liquido_hoje * ((1.0 + indiferenca) ** (du / 252))
    aliq = aliquota_ir((VENCIMENTO - HOJE).days)
    liquido = bruto - max(bruto - p.valor_liquido_hoje, 0.0) * aliq
    assert liquido == pytest.approx(p.valor_liquido_vencimento, rel=1e-9)


def test_indiferenca_fica_acima_da_taxa_de_mercado():
    """O IR antecipado é o pedágio da troca, e ele aparece como taxa.

    Vender e recomprar o MESMO papel pela mesma taxa termina abaixo de
    carregar. Se a indiferença viesse igual (ou abaixo) da taxa de mercado, a
    tela diria que a troca é neutra quando ela é perda certa.
    """
    aval = _avaliacao(_lote(taxa=0.1180), taxa_mercado=0.1360, pu=790.0)
    indiferenca = taxa_de_indiferenca([aval], vencimento=VENCIMENTO,
                                      data_avaliacao=HOJE,
                                      taxa_mercado_resgate=0.1360)
    assert indiferenca > 0.1360


def test_indiferenca_sem_preco_e_none():
    aval = _avaliacao(_lote(taxa=0.1180), taxa_mercado=None, pu=None)
    assert taxa_de_indiferenca([aval], vencimento=VENCIMENTO,
                               data_avaliacao=HOJE,
                               taxa_mercado_resgate=None) is None


def test_indiferenca_no_vencimento_e_none():
    """Sem prazo restante não há taxa anual que signifique alguma coisa."""
    lote = _lote(taxa=0.1180, aplicacao=date(2024, 5, 10))
    aval = avaliar_lote(lote, nome_titulo="Tesouro Prefixado 2026",
                        vencimento=HOJE, data_avaliacao=HOJE,
                        taxa_mercado_resgate=0.1360, pu_mercado=1000.0)
    assert taxa_de_indiferenca([aval], vencimento=HOJE, data_avaliacao=HOJE,
                               taxa_mercado_resgate=0.1360) is None


# ─────────────────────────────────────────────────────────────────────────────
# Série histórica da marcação
# ─────────────────────────────────────────────────────────────────────────────

def _cotacao(dia: date, taxa: float, pu: float) -> dict:
    return {"base_date": dia, "sell_rate_dec": taxa, "sell_pu": pu}


def test_serie_nao_conta_lote_antes_de_ele_existir():
    """Lote comprado em junho não pode compor a posição de maio.

    Sem o recorte, a linha da oscilação mostraria uma posição que o usuário
    ainda não tinha — e o mínimo histórico sairia de um dia que nunca houve.
    """
    antigo = _lote(taxa=0.1180, aplicacao=date(2025, 1, 10))
    novo = _lote(taxa=0.1400, aplicacao=date(2025, 6, 10))
    serie = serie_mtm_posicao(
        [antigo, novo], vencimento=VENCIMENTO,
        cotacoes=[_cotacao(date(2025, 5, 2), 0.1300, 700.0),
                  _cotacao(date(2025, 7, 2), 0.1300, 710.0)])
    assert [p["lotes"] for p in serie] == [1, 2]


def test_serie_descarta_dia_sem_preco_em_vez_de_zerar():
    """Ausência de cotação não é marcação zero — é dia que não entra."""
    serie = serie_mtm_posicao(
        [_lote(taxa=0.1180, aplicacao=date(2025, 1, 10))],
        vencimento=VENCIMENTO,
        cotacoes=[_cotacao(date(2025, 5, 2), 0.1300, 700.0),
                  {"base_date": date(2025, 5, 5), "sell_rate_dec": None,
                   "sell_pu": None},
                  {"base_date": date(2025, 5, 6), "sell_rate_dec": 0.13,
                   "sell_pu": 0.0}])
    assert [p["data"] for p in serie] == [date(2025, 5, 2)]


def test_serie_sai_em_ordem_cronologica():
    lote = _lote(taxa=0.1180, aplicacao=date(2025, 1, 10))
    serie = serie_mtm_posicao(
        [lote], vencimento=VENCIMENTO,
        cotacoes=[_cotacao(date(2025, 7, 2), 0.1300, 710.0),
                  _cotacao(date(2025, 5, 2), 0.1300, 700.0)])
    assert [p["data"] for p in serie] == [date(2025, 5, 2), date(2025, 7, 2)]


def test_serie_ignora_data_no_vencimento_ou_depois():
    lote = _lote(taxa=0.1180, aplicacao=date(2025, 1, 10))
    assert serie_mtm_posicao(
        [lote], vencimento=date(2025, 5, 2),
        cotacoes=[_cotacao(date(2025, 5, 2), 0.1300, 1000.0)]) == []


def test_marcacao_positiva_exatamente_quando_contratada_passa_a_de_mercado():
    lote = _lote(taxa=0.1400, aplicacao=date(2025, 1, 10))
    (ponto,) = serie_mtm_posicao([lote], vencimento=VENCIMENTO,
                                 cotacoes=[_cotacao(date(2025, 5, 2), 0.1200, 700.0)])
    assert ponto["taxa_contratada"] > ponto["taxa_mercado"]
    assert ponto["mtm_pct"] > 0


def test_extremos_de_serie_vazia_e_dicionario_vazio():
    """Nada medido não vira zero medido."""
    assert extremos_da_serie([]) == {}


def test_extremos_apontam_minimo_maximo_e_ultimo():
    serie = [{"data": date(2025, 1, 2), "mtm_pct": 0.01},
             {"data": date(2025, 2, 3), "mtm_pct": -0.04},
             {"data": date(2025, 3, 4), "mtm_pct": 0.07},
             {"data": date(2025, 4, 5), "mtm_pct": 0.02}]
    e = extremos_da_serie(serie)
    assert (e["minimo"], e["data_minimo"]) == (-0.04, date(2025, 2, 3))
    assert (e["maximo"], e["data_maximo"]) == (0.07, date(2025, 3, 4))
    assert e["atual"] == 0.02 and e["pontos"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# Leitura por título
# ─────────────────────────────────────────────────────────────────────────────

class _Titulo:
    """Dublê de `core.tesouro_posicao.TituloAnalitico` com o necessário."""

    def __init__(self, *, taxa_contratada=0.1180, taxa_mercado=0.1360,
                 pu=790.0, nome="Tesouro Prefixado 2029", indexador="PRE",
                 key="PRE2029", fonte="curva", taxa_indice=None):
        self.security_key = key
        self.titulo = nome
        self.vencimento = VENCIMENTO
        self.indexador = indexador
        self.taxa_mercado_venda = taxa_mercado
        self.taxa_indice = taxa_indice
        self.aproximado = False
        lote = _lote(taxa=taxa_contratada, indexador=indexador)
        self.lotes = [lote]
        self.avaliacoes = [_avaliacao(lote, taxa_mercado=taxa_mercado, pu=pu,
                                      nome=nome)]
        aval = self.avaliacoes[0]
        self.valor_investido = lote.valor_investido
        self.ganho_mtm_reais = aval.ganho_mtm_reais
        self.mtm_pct = aval.mtm
        self._fonte = fonte

    @property
    def marcado_a_mercado(self) -> bool:
        return self._fonte == "curva" and self.mtm_pct is not None


def _oferta(key, nome, venc, taxa):
    return {"security_key": key, "title_name": nome, "maturity_date": venc,
            "buy_rate_dec": taxa}


def test_titulo_sem_curva_fica_em_sem_preco_e_nao_vira_neutro():
    """Lacuna e empate são desfechos opostos e não podem usar o mesmo rótulo."""
    titulo = _Titulo(taxa_mercado=None, pu=None, fonte="extrato")
    leitura = ler_venda(titulo, data_avaliacao=HOJE)
    assert leitura.situacao == SEM_PRECO
    assert leitura.pode_marcar is False
    assert leitura.taxa_indiferenca is None


def test_marcacao_minuscula_e_neutra_nao_agio():
    leitura = ler_venda(_Titulo(taxa_contratada=0.13601, taxa_mercado=0.1360,
                                pu=790.0), data_avaliacao=HOJE)
    assert leitura.situacao == NEUTRA


def test_contratada_acima_da_de_mercado_e_agio():
    leitura = ler_venda(_Titulo(taxa_contratada=0.1600, taxa_mercado=0.1200),
                        data_avaliacao=HOJE)
    assert leitura.situacao == AGIO and leitura.mtm_pct > 0


def test_contratada_abaixo_da_de_mercado_e_desagio():
    leitura = ler_venda(_Titulo(taxa_contratada=0.1100, taxa_mercado=0.1500),
                        data_avaliacao=HOJE)
    assert leitura.situacao == DESAGIO and leitura.mtm_pct < 0


def test_ganho_liquido_desconta_o_ir_do_ganho_bruto():
    """O ágio bruto não entra no bolso: o prêmio exibido é o depois do imposto."""
    leitura = ler_venda(_Titulo(taxa_contratada=0.1600, taxa_mercado=0.1200),
                        data_avaliacao=HOJE)
    assert 0 < leitura.ganho_mtm_liquido < leitura.ganho_mtm_reais


def test_alternativa_so_vem_do_mesmo_indexador():
    """Ágio de Selic e taxa cheia de prefixado não são a mesma grandeza."""
    cardapio = [_oferta("IPCA2035", "Tesouro IPCA+ 2035", date(2035, 5, 15), 0.0750),
                _oferta("PRE2031", "Tesouro Prefixado 2031", date(2031, 1, 1), 0.1390)]
    leitura = ler_venda(_Titulo(), data_avaliacao=HOJE, cardapio=cardapio)
    assert leitura.alternativa["security_key"] == "PRE2031"


def test_alternativa_nao_encurta_o_prazo():
    """Duas pernas que terminam em datas diferentes não se comparam."""
    cardapio = [_oferta("PRE2027", "Tesouro Prefixado 2027", date(2027, 1, 1), 0.9)]
    assert ler_venda(_Titulo(), data_avaliacao=HOJE,
                     cardapio=cardapio).alternativa is None


def test_alternativa_nao_e_o_proprio_titulo():
    """Recomprar o mesmo papel é a troca que nunca ganha."""
    cardapio = [_oferta("PRE2029", "Tesouro Prefixado 2029", VENCIMENTO, 0.1360)]
    assert ler_venda(_Titulo(key="PRE2029"), data_avaliacao=HOJE,
                     cardapio=cardapio).alternativa is None


def test_veredito_e_a_comparacao_com_a_indiferenca():
    cardapio = [_oferta("PRE2031", "Tesouro Prefixado 2031", date(2031, 1, 1), 0.1390)]
    leitura = ler_venda(_Titulo(), data_avaliacao=HOJE, cardapio=cardapio)
    assert leitura.alternativa_supera == (0.1390 > leitura.taxa_indiferenca)


def test_frase_sem_curva_nao_promete_preco_de_hoje():
    leitura = ler_venda(_Titulo(taxa_mercado=None, pu=None, fonte="extrato"),
                        data_avaliacao=HOJE)
    assert "extrato" in leitura.frase.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Resumo da carteira
# ─────────────────────────────────────────────────────────────────────────────

def test_resumo_soma_reais_e_nao_tira_media_de_percentuais():
    """Média de % trataria um lote de R$ 1.600 e um de R$ 51.000 como iguais."""
    grande = _Titulo(taxa_contratada=0.1600, taxa_mercado=0.1200, key="A")
    pequeno = _Titulo(taxa_contratada=0.1000, taxa_mercado=0.1500, key="B")
    pequeno.lotes = [_lote(taxa=0.1000, quantidade=2.0, investido=1_200.0)]
    pequeno.avaliacoes = [_avaliacao(pequeno.lotes[0], taxa_mercado=0.1500,
                                     pu=600.0)]
    pequeno.ganho_mtm_reais = pequeno.avaliacoes[0].ganho_mtm_reais
    pequeno.mtm_pct = pequeno.avaliacoes[0].mtm
    pequeno.valor_investido = 1_200.0

    leituras = [ler_venda(t, data_avaliacao=HOJE) for t in (grande, pequeno)]
    resumo = resumo_da_carteira(leituras)
    soma = sum(lv.ganho_mtm_reais for lv in leituras)
    assert resumo["ganho_mtm"] == pytest.approx(soma)
    assert resumo["com_agio"] == 1 and resumo["com_desagio"] == 1
    # E o percentual sai da base marcada, não da média das duas marcações.
    media_das_pontas = sum(lv.mtm_pct for lv in leituras) / 2
    assert resumo["mtm_pct"] != pytest.approx(media_das_pontas)


def test_resumo_de_carteira_vazia_nao_quebra():
    resumo = resumo_da_carteira([])
    assert resumo["com_agio"] == 0 and resumo["marcados"] == 0


def test_resumo_ignora_titulo_sem_preco_na_base_percentual():
    """Quem não tem preço não entra na conta — nem como zero."""
    cego = _Titulo(taxa_mercado=None, pu=None, fonte="extrato", key="C")
    resumo = resumo_da_carteira([ler_venda(cego, data_avaliacao=HOJE)])
    assert resumo["marcados"] == 0 and resumo["sem_preco"] == 1


@pytest.mark.parametrize("indexador,taxa,esperado", [
    ("PRE", 0.1360, "13,60% a.a."),
    ("IPCA", 0.0750, "IPCA + 7,50% a.a."),
    ("SELIC", 0.0012, "Selic + 0,12% a.a."),
    ("PRE", None, "—"),
])
def test_rotulo_de_taxa_diz_o_indexador(indexador, taxa, esperado):
    """Indexado publica *spread*: "7,50% a.a." sozinho seria outro número."""
    assert rotulo_taxa(indexador, taxa) == esperado


# ─────────────────────────────────────────────────────────────────────────────
# Apresentação
# ─────────────────────────────────────────────────────────────────────────────

def test_css_do_painel_nao_tem_cor_fora_de_token():
    """Cor crua não enxerga o tema claro: toda tinta sai de `var(--app-*)`."""
    from design import tesouro_painel

    cruas = re.findall(r"(?<!, )#[0-9A-Fa-f]{6}", tesouro_painel.TD_CSS)
    assert cruas == []


def test_painel_nao_usa_st_metric():
    """KPI da casa é card CSS; `st.metric` ignora o tema."""
    from pathlib import Path

    fonte = Path("design/tesouro_painel.py").read_text(encoding="utf-8")
    assert "st.metric" not in fonte


def test_paleta_das_figuras_serve_aos_dois_temas():
    """Cor de série não passa pelo adaptador de tema — medido.

    `design.tema_canvas.clarear_figura` converte fundo, grade, eixo e fonte,
    mas devolve `line.color` e `marker.color` intactos. Logo a paleta precisa
    ter contraste nos dois fundos, senão a linha some no tema claro.
    """
    from design.tesouro_painel import (
        _FIG_CURVA,
        _FIG_INVESTIDO,
        _FIG_VENCIMENTO,
        _FIG_VENDER,
        _GRAFICO_LINHA_ZERO,
    )

    def luminancia(hexa: str) -> float:
        canais = [int(hexa.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        canais = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                  for c in canais]
        return 0.2126 * canais[0] + 0.7152 * canais[1] + 0.0722 * canais[2]

    def contraste(cor: str, fundo: str) -> float:
        a, b = luminancia(cor), luminancia(fundo)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)

    cores = (_FIG_CURVA, _FIG_INVESTIDO, _FIG_VENDER, _FIG_VENCIMENTO,
             _GRAFICO_LINHA_ZERO)
    for cor in cores:
        assert contraste(cor, "#0E1117") >= 3.0, f"{cor} some no tema escuro"
        assert contraste(cor, "#FFFFFF") >= 3.0, f"{cor} some no tema claro"


def test_cards_fecham_o_artigo_de_cada_titulo():
    """Grade mal fechada derruba o card seguinte para dentro do anterior."""
    from design.tesouro_painel import cards_html

    titulo = _Titulo()
    leitura = ler_venda(titulo, data_avaliacao=HOJE)
    html = cards_html([(leitura, extremos_da_serie([]))])
    assert html.count("<article") == html.count("</article>") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Trajetória até o vencimento — a linha que o gráfico por título desenha
# ─────────────────────────────────────────────────────────────────────────────

def _projecao_padrao():
    aval = _avaliacao(_lote(taxa=0.1180), taxa_mercado=0.1360, pu=750.0)
    return projetar_carrego([aval], vencimento=VENCIMENTO, data_avaliacao=HOJE,
                            taxa_mercado_resgate=0.1360)


def test_trajetoria_termina_no_mesmo_numero_que_o_card_mostra():
    """A ponta da curva é o valor do vencimento, não uma recontagem dele.

    Se a figura recalculasse o fim por fora, uma divergência de arredondamento
    poria o gráfico e o texto contando valores diferentes para a mesma coisa —
    e o usuário não teria como saber qual dos dois acreditar.
    """
    p = _projecao_padrao()
    caminho = trajetoria_carrego(p, data_avaliacao=HOJE, vencimento=VENCIMENTO)

    assert caminho[-1]["data"] == VENCIMENTO
    assert caminho[-1]["valor_bruto"] == pytest.approx(
        p.valor_bruto_vencimento, rel=1e-12)
    assert caminho[0]["data"] == HOJE
    assert caminho[0]["valor_bruto"] == pytest.approx(p.valor_bruto_hoje)


def test_trajetoria_sobe_sempre_e_anda_para_frente():
    caminho = trajetoria_carrego(_projecao_padrao(), data_avaliacao=HOJE,
                                 vencimento=VENCIMENTO)
    datas = [p["data"] for p in caminho]
    valores = [p["valor_bruto"] for p in caminho]

    assert len(caminho) >= 8
    assert datas == sorted(datas)
    assert len(set(datas)) == len(datas)
    assert all(b > a for a, b in zip(valores, valores[1:]))


def test_trajetoria_sem_preco_nao_desenha_nada():
    aval = _avaliacao(_lote(taxa=0.1180), taxa_mercado=None, pu=None)
    p = projetar_carrego([aval], vencimento=VENCIMENTO, data_avaliacao=HOJE,
                         taxa_mercado_resgate=None)
    assert trajetoria_carrego(p, data_avaliacao=HOJE,
                              vencimento=VENCIMENTO) == []


def test_trajetoria_recusa_janela_que_nao_foi_a_projetada():
    """Projeção de uma janela desenhada noutra inventaria valor intermediário.

    O fator capitaliza por um prazo fixo; aplicá-lo a um eixo mais curto ou
    mais longo desenharia a mesma curva em cima de datas erradas.
    """
    p = _projecao_padrao()
    assert trajetoria_carrego(p, data_avaliacao=HOJE,
                              vencimento=date(2031, 1, 1)) == []


def test_serie_expoe_a_curva_do_lote_e_ela_explica_a_marcacao():
    lote = _lote(taxa=0.1180, aplicacao=date(2024, 5, 10))
    serie = serie_mtm_posicao(
        [lote], vencimento=VENCIMENTO,
        cotacoes=[{"base_date": date(2026, 9, 30), "sell_rate_dec": 0.1360,
                   "sell_pu": 750.0}])

    ponto = serie[0]
    assert ponto["valor_curva"] > 0
    # A marcação não é um terceiro número: é a distância entre as duas linhas.
    assert ponto["valor_bruto"] / ponto["valor_curva"] - 1.0 == pytest.approx(
        ponto["mtm_pct"])


# ─────────────────────────────────────────────────────────────────────────────
# O gráfico por título
# ─────────────────────────────────────────────────────────────────────────────

def _figura_padrao(*, com_serie: bool = True):
    from design.tesouro_painel import fig_titulo

    titulo = _Titulo()
    leitura = ler_venda(titulo, data_avaliacao=HOJE)
    serie = serie_mtm_posicao(
        titulo.lotes, vencimento=VENCIMENTO,
        cotacoes=[{"base_date": date(2026, 9, d), "sell_rate_dec": 0.1360,
                   "sell_pu": 780.0 + d} for d in (1, 10, 20, 30)],
    ) if com_serie else []
    trajetoria = trajetoria_carrego(leitura.projecao, data_avaliacao=HOJE,
                                    vencimento=VENCIMENTO)
    return fig_titulo(leitura, serie, trajetoria), leitura


def test_grafico_do_titulo_poe_passado_e_futuro_no_mesmo_quadro():
    """O pedido é exatamente este: a marcação e o caminho até o fim, juntos."""
    fig, leitura = _figura_padrao()
    nomes = [t.name for t in fig.data]

    assert "Valor de mercado" in nomes
    assert "Levando até o vencimento" in nomes
    assert "Líquido no vencimento" in nomes
    assert "Marcação" in nomes  # o painel de baixo, em percentual

    # A ponta da linha do futuro é o vencimento, e o losango marca o que sobra
    # depois do imposto — abaixo do bruto, nunca acima.
    futuro = next(t for t in fig.data if t.name == "Levando até o vencimento")
    desfecho = next(t for t in fig.data if t.name == "Líquido no vencimento")
    assert futuro.x[-1] == leitura.vencimento
    assert desfecho.y[0] == pytest.approx(leitura.liquido_vencimento)
    assert desfecho.y[0] < futuro.y[-1]


def test_grafico_do_titulo_desenha_a_curva_contratada_ao_lado_do_mercado():
    """Sem a linha de referência, a oscilação em reais não tem contra o quê."""
    fig, _ = _figura_padrao()
    mercado = next(t for t in fig.data if t.name == "Valor de mercado")
    curva = next(t for t in fig.data
                 if t.name == "Pela taxa que você contratou")
    assert len(curva.x) == len(mercado.x)
    assert all(v > 0 for v in curva.y)


def test_grafico_sem_historico_nao_inventa_painel_de_oscilacao():
    """Um ponto só não é oscilação; o painel de baixo some em vez de mentir."""
    fig, _ = _figura_padrao(com_serie=False)
    assert [t.name for t in fig.data if t.name == "Marcação"] == []
    assert "Levando até o vencimento" in [t.name for t in fig.data]
    assert fig.layout.height < 300


def test_etiqueta_do_grafico_escapa_o_nome_do_titulo():
    from design.tesouro_painel import nome_grafico_html

    leitura = ler_venda(_Titulo(nome="Tesouro <b>IPCA+</b> 2029"),
                        data_avaliacao=HOJE)
    html = nome_grafico_html(leitura)
    assert "<b>" not in html.replace("</div>", "")
    assert "&lt;b&gt;" in html
    assert VENCIMENTO.strftime("%d/%m/%Y") in html


def test_caixa_de_escolha_usa_o_nome_limpo_quando_ele_basta():
    """Carregar o vencimento em todos para desempatar nenhum é ruído."""
    from design.tesouro_painel import rotulos_escolha

    leituras = [ler_venda(_Titulo(nome=n, key=k), data_avaliacao=HOJE)
                for n, k in (("Tesouro Prefixado 2029", "PRE29"),
                             ("Tesouro Selic 2031", "SELIC31"))]
    assert rotulos_escolha(leituras) == ["Tesouro Prefixado 2029",
                                         "Tesouro Selic 2031"]


def test_caixa_de_escolha_desempata_nome_repetido_e_so_ele():
    """Dois papéis com o mesmo nome deixariam quem escolhe no escuro."""
    from design.tesouro_painel import rotulos_escolha

    leituras = [ler_venda(_Titulo(nome=n, key=k), data_avaliacao=HOJE)
                for n, k in (("Tesouro Prefixado 2029", "A"),
                             ("Tesouro Prefixado 2029", "B"),
                             ("Tesouro Selic 2031", "C"))]
    rotulos = rotulos_escolha(leituras)

    data = VENCIMENTO.strftime("%d/%m/%Y")
    assert rotulos[0] == rotulos[1] == f"Tesouro Prefixado 2029 · vence em {data}"
    assert rotulos[2] == "Tesouro Selic 2031"  # o não-repetido fica limpo
    assert len(rotulos) == 3
