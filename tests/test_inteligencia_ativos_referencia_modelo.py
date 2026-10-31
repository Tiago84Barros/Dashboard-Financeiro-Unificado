"""Inteligência dos Ativos: a carteira recomendada do Portfólio Global como
referência comparativa -- pesos, base, indisponibilidade, e a garantia de que
ela não entra na decisão (regras, ação a considerar, veredito)."""
import pathlib

import pytest

from core.inteligencia_ativos import portfolio_fit as pf
from core.inteligencia_ativos import referencia_modelo as rm
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx
from views import inteligencia_ativos as tela

pytestmark = pytest.mark.usefixtures("estrategia_falsa", "cenario_falso")


def _snap(nome: str, peso: float) -> dict:
    return {"identity": {"name": nome, "sector": "x"},
            "metrics": {"weight": peso}}


SNAPSHOTS = {
    "b3": {"PETR4": _snap("Petrobras", 0.6), "VALE3": _snap("Vale", 0.4)},
    "fii": {"HGLG11": _snap("CSHG Logística", 1.0)},
    "us": {"AAPL": _snap("Apple", 1.0)},
}
ALVOS = {"b3": 0.5, "fii": 0.3, "us": 0.2}
POSICOES = [
    {"ticker": "PETR4.SA", "nome": "Petrobras", "classe": "Ações BR",
     "moeda": "BRL", "pais": "BR", "valor_mercado": 300.0},
    {"ticker": "HGLG11", "nome": "CSHG Log", "classe": "FII",
     "moeda": "BRL", "pais": "BR", "valor_mercado": 200.0},
    {"ticker": "TESOURO IPCA 2035", "nome": "Tesouro", "classe":
     "Tesouro Direto", "moeda": "BRL", "pais": "BR", "valor_mercado": 400.0},
    {"ticker": "ITSA4", "nome": "Itaúsa", "classe": "Ações BR",
     "moeda": "BRL", "pais": "BR", "valor_mercado": 100.0},
]


def _ref(renda_fixa=0.4, snapshots=SNAPSHOTS, alvos=ALVOS, posicoes=POSICOES):
    return rm.montar(snapshots, {"targets": alvos, "renda_fixa": renda_fixa},
                     posicoes)


def test_pesos_no_patrimonio_com_renda_fixa_definida():
    ref = _ref()
    assert ref.disponivel and ref.base == rm.BASE_PATRIMONIO
    petr = ref.linha("PETR4")
    # 60% da renda variável × 50% de B3 × 60% do modelo
    assert petr.peso_modelo == pytest.approx(18.0)
    assert petr.peso_real == pytest.approx(30.0)
    assert petr.diferenca == pytest.approx(12.0)
    assert petr.situacao == rm.NOS_DOIS
    assert ref.linha("ITSA4").situacao == rm.SO_NA_CARTEIRA
    assert ref.linha("ITSA4").diferenca == pytest.approx(10.0)
    assert [x.ticker for x in ref.fora_da_carteira()] == ["AAPL", "VALE3"]
    classes = {c.classe: c for c in ref.classes}
    assert classes["b3"].alvo == pytest.approx(30.0)
    assert classes["b3"].real == pytest.approx(40.0)
    assert classes["us"].real == 0.0
    assert classes["renda_fixa"].alvo == pytest.approx(40.0)


def test_sem_fatia_de_renda_fixa_compara_so_a_parcela_de_risco():
    ref = _ref(renda_fixa=None)
    assert ref.base == rm.BASE_SEM_RENDA_FIXA
    petr = ref.linha("PETR4")
    assert petr.peso_modelo == pytest.approx(30.0)   # 50% × 60%
    assert petr.peso_real == pytest.approx(50.0)     # 300 / 600
    assert ref.linha("TESOURO IPCA 2035") is None
    assert "renda_fixa" not in {c.classe for c in ref.classes}


def test_ticker_com_sufixo_sa_casa_com_o_modelo():
    assert rm.normalizar(" petr4.sa ") == "PETR4"
    assert _ref().linha("PETR4.SA").situacao == rm.NOS_DOIS


def test_indisponivel_sem_modelo_sem_alvo_ou_sem_carteira():
    assert not _ref(snapshots={"b3": {}}).disponivel
    assert "alocação-alvo" in _ref(alvos={}).motivo
    assert not _ref(posicoes=[]).disponivel
    assert pf.NAO_DISPONIVEL not in _ref(alvos={}).para_llm("PETR4")["estado"]


def test_classe_com_alvo_sem_modelo_vira_aviso():
    ref = _ref(snapshots={k: v for k, v in SNAPSHOTS.items() if k != "us"})
    assert any("Internacional" in a and "20,0%" in a for a in ref.avisos)


def test_falha_de_leitura_e_nomeada(monkeypatch):
    def quebra(engine=None, owner_id=None):
        raise ConnectionError("sem rede")
    monkeypatch.setattr(rm, "_ler", quebra)
    ref = rm.carregar(POSICOES)
    assert not ref.disponivel and "ConnectionError" in ref.motivo


def test_referencia_fica_fora_das_regras_e_da_acao():
    ctx = _ctx()
    analise = _analise("TAEE11", ctx)
    ref = _ref().para_llm(analise.ativo.ticker)
    sem = pf.contexto(analise, ctx)
    com = pf.contexto(analise, ctx, referencia_modelo=ref)
    assert com["app_model_reference"] == ref
    assert sem["app_model_reference"] == {"estado": pf.NAO_DISPONIVEL}
    assert {k: v for k, v in com.items() if k != "app_model_reference"} == \
        {k: v for k, v in sem.items() if k != "app_model_reference"}
    assert "referência comparativa" in ref["natureza"]
    assert rm.REGRA_REFERENCIA in pf.sistema()


def test_modulos_de_decisao_nao_importam_a_referencia():
    raiz = pathlib.Path(__file__).resolve().parents[1] / "core" / "inteligencia_ativos"
    for nome in ("veredito.py", "analise.py", "adequacao.py", "calculos.py",
                 "contexto.py", "painel.py"):
        assert "referencia_modelo" not in (raiz / nome).read_text("utf-8"), nome


def test_cartoes_escapam_e_avisam_que_e_referencia():
    ref = _ref()
    html = tela.cartao_referencia_ativo(ref, "PETR4")
    assert "18,0%" in html and "30,0%" in html and "+12,0 pp" in html
    assert "não é evidência independente" not in html   # vai ao log
    geral = tela.cartao_referencia_carteira(ref)
    assert "Do modelo, fora da sua carteira" in geral and "AAPL" in geral
    fora = tela.cartao_referencia_ativo(rm.indisponivel("<b>x</b>"), "PETR4")
    assert "<b>x" not in fora and "&lt;b&gt;" not in fora   # motivo vai ao log


def test_tela_mostra_a_referencia_sem_quebrar():
    from tests.test_inteligencia_ativos_tela import CARTEIRA_COMPLETA, _liberada, _rodar
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    app.toggle(key="ia_detalhe").set_value(True).run(timeout=30)
    assert not app.exception
    html = "".join(md.value for md in app.markdown)
    assert "carteira recomendada do Portfólio Global" in html
    assert "não tem carteira-modelo ativa" not in html   # vai ao log


def test_memo_nao_lembra_falha_de_leitura(monkeypatch):
    chamadas = []

    def ler(engine=None, owner_id=None):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise ConnectionError("sem rede")
        return SNAPSHOTS, {"targets": ALVOS, "renda_fixa": 0.4}
    monkeypatch.setattr(rm, "_ler", ler)
    monkeypatch.setattr(tela.st, "session_state", {})
    carteira = {"posicoes": POSICOES}
    falhou = tela._referencia_memo(carteira)
    assert falhou.falha_de_leitura and not falhou.disponivel
    assert tela._referencia_memo(carteira).disponivel   # tentou de novo
    assert tela._referencia_memo(carteira).disponivel   # agora lembrou
    assert len(chamadas) == 2


def test_memo_lembra_indisponivel_sem_alocacao(monkeypatch):
    chamadas = []

    def ler(engine=None, owner_id=None):
        chamadas.append(1)
        return SNAPSHOTS, {"targets": {}, "renda_fixa": None}
    monkeypatch.setattr(rm, "_ler", ler)
    monkeypatch.setattr(tela.st, "session_state", {})
    carteira = {"posicoes": POSICOES}
    assert not tela._referencia_memo(carteira).falha_de_leitura
    tela._referencia_memo(carteira)
    assert len(chamadas) == 1
