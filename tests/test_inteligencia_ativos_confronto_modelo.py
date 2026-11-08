"""Inteligência dos Ativos: o confronto da carteira com a carteira
recomendada do Portfólio Global -- qualidade pela mesma régua nos dois lados,
pesos por classe contra o modelo e a estratégia, divergências por ativo com a
conclusão (sem contradizer a decisão da Inteligência) e inclusões."""
import pathlib
from types import SimpleNamespace

import pytest

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import confronto_modelo as cf
from core.inteligencia_ativos import referencia_modelo as rm
from core.inteligencia_ativos import veredito as vd
from views import inteligencia_ativos as tela


def _snap(nome: str, peso: float, setor: str = "x") -> dict:
    return {"identity": {"name": nome, "sector": setor},
            "metrics": {"weight": peso}}


SNAPSHOTS = {
    "b3": {"PETR4": _snap("Petrobras", 0.5), "VALE3": _snap("Vale", 0.3),
           "WEGE3": _snap("WEG", 0.2)},
    "fii": {"HGLG11": _snap("CSHG Logística", 1.0)},
    "us": {"AAPL": _snap("Apple", 1.0)},
}
ALVOS = {"b3": 0.5, "fii": 0.3, "us": 0.2}


def _pos(tk, classe, valor, moeda="BRL", pais="BR"):
    return {"ticker": tk, "nome": tk, "classe": classe, "moeda": moeda,
            "pais": pais, "valor_mercado": valor}


POSICOES = [
    _pos("PETR4", "Ações BR", 300.0),     # 30% real × 15% modelo
    _pos("ITSA4", "Ações BR", 100.0),     # 10% real, fora do modelo
    _pos("HGLG11", "FII", 50.0),          # 5% real × 18% modelo
    _pos("TESOURO IPCA", "Tesouro Direto", 550.0),
]


def _ref(**kw):
    return rm.montar(kw.get("snapshots", SNAPSHOTS),
                     {"targets": ALVOS, "renda_fixa": kw.get("rf", 0.4)},
                     kw.get("posicoes", POSICOES))


def _av(qualidade=av_.ADEQUADA, preco=av_.JUSTO, mercado=av_.NEUTRO,
        criticos=()):
    alertas = tuple(av_.Alerta(t, critico=True) for t in criticos)
    return av_.Avaliacao(
        perfil=av_.GERAL,
        dimensoes=(av_.Dimensao(av_.QUALIDADE, qualidade,
                                (av_.Criterio("roe", 1),)),
                   av_.Dimensao(av_.VALUATION, preco),
                   av_.Dimensao(av_.MERCADO, mercado)),
        alertas=alertas)


def _vered(tk, codigo, av=None, *, proporcao=None):
    from core.inteligencia_ativos import resumida as rs
    av = av or _av()
    d = SimpleNamespace(codigo=codigo, rotulo=rs.ROTULO_DECISAO[codigo],
                        detalhe=f"detalhe {codigo}", proporcao=proporcao,
                        substituto=None, avaliacao=av)
    limite = vd._LIMITE_DA_DECISAO[codigo]
    return vd.Veredito(tk, limite, f"motivo {codigo}", d.rotulo, d.detalhe,
                       av, d)


ESTRATEGIA = {"renda_fixa": 50.0, "acoes_br": 30.0, "fiis": 15.0,
              "exterior": 5.0}


def _conf(vereditos=None, avals=None, **kw):
    return cf.montar(kw.pop("ref", None) or _ref(), vereditos or {
        "PETR4": _vered("PETR4", "manter", _av(av_.FORTE)),
        "ITSA4": _vered("ITSA4", "manter", _av(av_.ADEQUADA)),
        "HGLG11": _vered("HGLG11", "acompanhar", _av(av_.FRAGIL)),
    }, avals if avals is not None else {
        "VALE3": _av(av_.FORTE), "WEGE3": _av(av_.FRAGIL),
        "AAPL": _av(av_.FORTE),
    }, alocacao_estrategia=kw.pop("estrategia", ESTRATEGIA), **kw)


def test_qualidade_mesma_regua_e_ponderada_pelo_peso():
    c = _conf()
    # carteira (risco): PETR4 30 forte, ITSA4 10 adequada, HGLG11 5 frágil
    assert c.carteira.nota == pytest.approx((30 * 3 + 10 * 2 + 5 * 1) / 45)
    assert c.carteira.peso_fragil == pytest.approx(5 / 45 * 100)
    # modelo: PETR4 15 forte, VALE3 9 forte, WEGE3 6 frágil, HGLG11 18
    # frágil (a avaliação do HGLG11 é a mesma da carteira), AAPL 12 forte
    assert c.modelo.nota == pytest.approx(
        (15 * 3 + 9 * 3 + 6 * 1 + 18 * 1 + 12 * 3) / 60)
    assert c.modelo.cobertura == pytest.approx(100.0)
    assert {q.classe for q in c.por_classe} == {"b3", "fii", "us"}


def test_sem_avaliacao_reduz_cobertura_sem_virar_nota_zero():
    c = _conf(avals={"VALE3": None})
    assert c.modelo.cobertura < 100.0
    assert c.modelo.nota is not None and c.modelo.nota >= 1.0


def test_aderencia_soma_o_minimo_dos_dois_lados():
    c = _conf()
    assert c.aderencia == pytest.approx(15.0 + 5.0)   # PETR4 e HGLG11


def test_acima_do_modelo_sem_venda_da_inteligencia_mantem_a_escolha():
    d = _conf().da_carteira("PETR4")
    assert d.diferenca == pytest.approx(15.0)
    assert d.conclusao == cf.PERMANECER
    assert any("Qualidade forte" in m for m in d.motivos)
    assert any("custo e imposto" in m for m in d.motivos)


def test_fora_do_modelo_nao_e_argumento_contra():
    d = _conf().da_carteira("ITSA4")
    assert d.situacao == rm.SO_NA_CARTEIRA and d.conclusao == cf.PERMANECER
    assert any("não é defeito do ativo" in m for m in d.motivos)


def test_abaixo_do_modelo_nao_manda_aportar_quando_a_inteligencia_barra():
    d = _conf().da_carteira("HGLG11")
    assert d.diferenca == pytest.approx(-13.0)
    assert d.conclusao == cf.PERMANECER
    assert "não libera aporte" in d.texto


def test_abaixo_do_modelo_com_compra_liberada_vira_aporte():
    v = {"PETR4": _vered("PETR4", "manter"),
         "HGLG11": _vered("HGLG11", "comprar")}
    d = _conf(v).da_carteira("HGLG11")
    assert d.conclusao == cf.MUDAR and "libera compra" in d.texto


def test_vender_da_inteligencia_vence_e_ganha_candidato_da_classe():
    v = {"PETR4": _vered("PETR4", "vender", _av(criticos=("dívida",))),
         "ITSA4": _vered("ITSA4", "manter", _av(av_.ADEQUADA))}
    d = _conf(v).da_carteira("PETR4")
    assert d.conclusao == cf.MUDAR and "não por causa do modelo" in d.texto
    assert any("VALE3" in m for m in d.motivos)


def test_reduzir_parcial_acima_do_modelo_diz_a_proporcao():
    v = {"PETR4": _vered("PETR4", "reduzir_parcial", proporcao=0.25)}
    d = _conf(v).da_carteira("PETR4")
    assert d.conclusao == cf.MUDAR and "25%" in d.texto


def test_conclusao_nunca_contradiz_o_limite_da_inteligencia():
    for codigo in ("manter", "acompanhar", "rebalancear", "reduzir_parcial",
                   "vender", "substituir", "comprar"):
        c = _conf({tk: _vered(tk, codigo) for tk in ("PETR4", "ITSA4",
                                                     "HGLG11")})
        for d in c.divergencias:
            if d.diferenca < 0 and d.conclusao == cf.MUDAR:
                assert codigo in ("comprar", "vender", "substituir"), codigo
            if d.diferenca > 0 and d.conclusao == cf.MUDAR:
                assert codigo in ("reduzir_parcial", "vender",
                                  "substituir"), codigo


def test_candidatos_pela_avaliacao_do_proprio_ativo():
    c = _conf()
    por = {d.ticker: d for d in c.candidatos}
    # VALE3 forte, acima da média da classe na carteira (2,75): candidato
    assert por["VALE3"].conclusao == cf.INCLUIR
    assert any("acima da média" in m for m in por["VALE3"].motivos)
    # WEGE3 frágil: o limite da Inteligência barra
    assert por["WEGE3"].conclusao == cf.NAO_INCLUIR
    # AAPL: você não tem Internacional e a estratégia prevê 5%
    assert por["AAPL"].conclusao == cf.INCLUIR
    assert any("não tem posição" in m for m in por["AAPL"].motivos)
    # O modelo usa 12%, mas a estratégia só prevê 5% em Internacional.
    assert any(m.startswith("Peso de referência: 5,0%") and "o modelo usa"
               in m for m in por["AAPL"].motivos)
    # Empresas B3 já está acima da estratégia: incluir é trocar, não somar.
    assert any("trocar por outro ativo" in m for m in por["VALE3"].motivos)
    assert c.candidatos[0].conclusao == cf.INCLUIR


def test_candidato_respeita_classe_zerada_na_estrategia_e_teto_por_ativo():
    c = _conf(estrategia={**ESTRATEGIA, "exterior": 0.0},
              limite_por_ativo=5.0)
    por = {d.ticker: d for d in c.candidatos}
    assert por["AAPL"].conclusao == cf.NAO_INCLUIR
    assert any("limite por ativo" in m for m in por["VALE3"].motivos)


def test_candidato_pior_que_a_media_da_classe_nao_entra():
    v = {"PETR4": _vered("PETR4", "manter", _av(av_.FORTE)),
         "ITSA4": _vered("ITSA4", "manter", _av(av_.FORTE))}
    c = _conf(v, {"VALE3": _av(av_.ADEQUADA)})
    vale = next(d for d in c.candidatos if d.ticker == "VALE3")
    assert vale.conclusao == cf.NAO_INCLUIR and "abaixo da média" in vale.texto
    assert c.nao_avaliados == 2   # WEGE3 e AAPL não foram avaliados


def test_classe_perto_da_estrategia_permanece_e_longe_rebalanceia():
    c = _conf(estrategia={"renda_fixa": 55.0, "acoes_br": 40.0, "fiis": 5.0,
                          "exterior": 0.0})
    por = {k.classe: k for k in c.classes}
    # B3: 40% real, 30% modelo, 40% estratégia → manter
    assert por["b3"].acentuada and por["b3"].conclusao == cf.PERMANECER
    c2 = _conf()
    por2 = {k.classe: k for k in c2.classes}
    # Renda fixa: 55% real, 40% modelo, 50% estratégia → perto (tol 10)
    assert por2["renda_fixa"].conclusao == cf.PERMANECER
    # FII: 5% real, 18% modelo, 15% estratégia → rebalancear, mesma direção
    assert por2["fii"].conclusao == cf.MUDAR
    assert "mesma direção" in por2["fii"].texto


def test_estrategia_reescalada_sem_renda_fixa_no_modelo():
    assert cf._alvo_na_base(ESTRATEGIA, "b3", rm.BASE_SEM_RENDA_FIXA) == \
        pytest.approx(60.0)
    assert cf._alvo_na_base(ESTRATEGIA, "b3", rm.BASE_PATRIMONIO) == 30.0
    assert cf._alvo_na_base({}, "b3", rm.BASE_PATRIMONIO) is None


def test_renda_fixa_nao_vira_divergencia_de_ativo():
    assert _conf().da_carteira("TESOURO IPCA") is None


def test_indisponivel_quando_a_referencia_esta():
    c = cf.montar(rm.indisponivel("sem modelo"), {}, {})
    assert not c.disponivel and c.para_llm("PETR4")["estado"] == "indisponível"


def test_para_llm_traz_o_ativo_a_qualidade_e_os_candidatos():
    llm = _conf().para_llm("PETR4")
    assert llm["ativo"]["conclusao"] == "Manter a sua escolha"
    assert llm["qualidade_carteira"]["cobertura_pct"] == 100.0
    assert {d["ticker"] for d in llm["candidatos_a_inclusao"]} == {"VALE3",
                                                                    "AAPL"}
    sem = _conf().para_llm("WEGE3")
    assert "nenhuma divergência" in sem["ativo"]["divergencia"]


def test_a_avaliar_pega_os_maiores_do_modelo_fora_da_carteira():
    assert [x.ticker for x in cf.a_avaliar(_ref(), 2)] == ["AAPL", "VALE3"]
    assert _ref().linha("VALE3").setor == "x"
    assert _ref().linha("ITSA4").classe_real == "b3"


def test_decisao_nao_importa_o_confronto():
    raiz = pathlib.Path(__file__).resolve().parents[1] / "core" / "inteligencia_ativos"
    for nome in ("veredito.py", "analise.py", "adequacao.py", "calculos.py",
                 "contexto.py", "painel.py", "resumida.py", "avaliacao.py"):
        assert "confronto_modelo" not in (raiz / nome).read_text("utf-8"), nome


def test_cartoes_escapam_e_mostram_os_numeros():
    c = _conf()
    html = tela.cartao_confronto(c)
    assert "Qualidade média" in html and "Sua estratégia" in html
    assert "Aderência ao modelo" in html
    div = tela.cartao_divergencias(c)
    assert "PETR4" in div and "Manter a sua escolha" in div
    cand = tela.cartao_candidatos(c)
    assert "VALE3" in cand and "Candidato a inclusão" in cand
    fora = tela.cartao_confronto(cf.indisponivel("<b>x</b>"))
    assert "<b>x" not in fora


def test_avaliacoes_do_modelo_lembra_sucesso_e_nao_lembra_falha(monkeypatch):
    chamadas = []

    def avaliar(tk, nome=None, setor=None, *, mercado="b3"):
        chamadas.append(tk)
        if tk == "AAPL" and chamadas.count("AAPL") == 1:
            raise ConnectionError("sem rede")
        return _av()
    monkeypatch.setattr(tela.vd, "avaliar_ativo", avaliar)
    monkeypatch.setattr(tela.st, "session_state", {})
    ref = _ref()
    primeira = tela._avaliacoes_do_modelo(ref)
    assert primeira["AAPL"] is None and primeira["VALE3"] is not None
    segunda = tela._avaliacoes_do_modelo(ref)
    assert segunda["AAPL"] is not None
    assert chamadas.count("VALE3") == 1 and chamadas.count("AAPL") == 2
