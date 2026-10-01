"""Perfil financeiro que a entrevista da estratégia lê do Controle Financeiro."""
import datetime as dt
import json

from core import llm_estrategia as ent
from core.estrategia import perfil_financeiro as pf

# O conftest troca ``carregar`` por um que devolve None; este é o real.
CARREGAR = pf.carregar


def _mes(ano, mes, receitas, despesas, aportes=0.0, categorias=None):
    return {"ano": ano, "mes": mes, "receitas": receitas, "despesas": despesas,
            "aportes": aportes, "categorias": categorias or []}


def _ano_tipico():
    meses = []
    for i, (ano, mes) in enumerate(pf.janela(dt.date(2026, 9, 28))):
        cats = [{"nome": "Moradia", "gasto": 3000},
                {"nome": "Alimentação", "gasto": 1500},
                {"nome": "Pagamento de fatura", "gasto": 2000}]
        if i % 4 == 0:
            cats.append({"nome": "Viagem", "gasto": 2400})
        meses.append(_mes(ano, mes, 10_000 + (500 if i % 2 else 0),
                          sum(c["gasto"] for c in cats), 1500, cats))
    return meses


def test_janela_e_de_meses_fechados_sem_o_corrente():
    j = pf.janela(dt.date(2026, 1, 15))
    assert len(j) == 12
    assert j[0] == (2025, 1) and j[-1] == (2025, 12)
    assert (2026, 1) not in j


def test_renda_gasto_sobra_e_aporte_nao_e_despesa():
    p = pf.resumir(_ano_tipico())
    assert p.meses == 12 and p.periodo == "set/25 a ago/26"
    assert p.renda_media == 10_250.0
    assert p.renda_min == 10_000 and p.renda_max == 10_500
    assert p.renda_cv is not None and p.renda_cv < 0.10
    # despesa: 6500 fixos + viagem 2400 em 3 meses → 6500 + 600
    assert p.despesa_media == 7_100.0
    # sobra é receitas − despesas; o aporte não entra como saída
    assert p.sobra_media == 3_150.0
    assert p.aporte_medio == 1_500.0 and p.meses_com_aporte == 12
    assert p.meses_deficit == 0
    assert p.aporte_pct == round(1500 / 10250 * 100, 1)


def test_recorrentes_essenciais_e_fatura_fora_do_consumo():
    p = pf.resumir(_ano_tipico())
    nomes = {c.nome: c for c in p.categorias}
    assert "Pagamento de fatura" not in nomes          # é o cartão sendo pago
    assert p.pagamento_fatura_media == 2_000.0
    assert nomes["Moradia"].recorrente and nomes["Moradia"].essencialidade == "essencial"
    assert not nomes["Viagem"].recorrente and nomes["Viagem"].meses == 3
    assert nomes["Viagem"].essencialidade == "nao_essencial"
    assert p.despesa_recorrente_media == 4_500.0       # moradia + alimentação
    # essenciais 4500 de 5100 classificáveis
    assert p.essencial_pct == round(4500 / 5100 * 100, 1)


def test_mes_sem_lancamento_nao_derruba_a_media_e_sem_dado_e_none():
    meses = _ano_tipico()
    meses[0] = _mes(meses[0]["ano"], meses[0]["mes"], 0, 0)
    p = pf.resumir(meses)
    assert p.meses == 11 and p.periodo.startswith("out/25")
    assert pf.resumir([_mes(2026, 1, 0, 0)]) is None
    assert pf.resumir([]) is None


def test_cartao_fatura_recorrencias_e_parcelamentos():
    meses = _ano_tipico()
    faturas = [{"ano": m["ano"], "mes": m["mes"], "total": 1800.0} for m in meses]
    faturas.append({"ano": 2026, "mes": 9, "total": 9999.0})   # mês corrente
    compras = []
    for ano, mes in pf.janela(dt.date(2026, 9, 28))[-4:]:
        compras.append({"descricao": "NETFLIX.COM 1234", "valor": -55.9,
                        "data": dt.date(ano, mes, 10), "categoria": "Assinaturas",
                        "eh_despesa": True, "account_type": "credit_card",
                        "installment_total": 1})
        compras.append({"descricao": "LOJA X | Parcela 2/10", "valor": -300.0,
                        "data": dt.date(ano, mes, 10), "categoria": "Compras",
                        "eh_despesa": True, "account_type": "credit_card",
                        "installment_total": 10})
    compras.append({"descricao": "PAGAMENTO RECEBIDO", "valor": 1800.0,
                    "data": dt.date(2026, 8, 10), "categoria": "Pagamento",
                    "eh_despesa": False, "account_type": "credit_card"})
    dividas = [{"is_ativa": True, "total_compra": 3000.0, "total_parcelas": 10,
                "parcelas_restantes": 6},
               {"is_ativa": False, "total_compra": 900.0, "total_parcelas": 3,
                "parcelas_restantes": 0}]
    p = pf.resumir(meses, faturas=faturas, compras_cartao=compras, dividas=dividas)
    assert p.fatura_media == 1_800.0 and p.faturas == 12
    # parcela não é recorrência; a assinatura em 4 faturas é
    assert [g.nome for g in p.cartao_recorrentes] == ["NETFLIX.COM 1234"]
    assert "Cobranças que se repetem no cartão" in pf.texto(p)
    assert p.cartao_recorrentes[0].media_mensal == 55.9
    assert {c.nome for c in p.cartao_categorias} == {"Assinaturas", "Compras"}
    assert p.parcelamentos_ativos == 1
    assert p.parcelas_mensais == 300.0 and p.parcelamentos_saldo == 1_800.0


def test_texto_traz_os_numeros_e_as_regras_de_leitura():
    p = pf.resumir(_ano_tipico(), faturas=[{"ano": 2026, "mes": 8, "total": 1800}])
    t = pf.texto(p)
    assert "Renda média mensal: R$ 10.250,00" in t
    assert "estável" in t
    assert "Sobra média (receitas − despesas, antes de investir): R$ 3.150,00" in t
    assert "Aporte médio em investimentos: R$ 1.500,00" in t
    assert "6 meses de despesa = R$ 42.600,00" in t
    assert "Moradia R$ 3.000,00 R (essencial)" in t
    assert "Viagem R$ 600,00 (não essencial)" in t
    assert "não some com a fatura" in t
    assert "Fica fora das despesas do caixa" in t
    assert all(linha.startswith("- ") for linha in t.splitlines())


def test_sem_classificacao_fica_visivel_e_cv_sai_com_virgula():
    meses = [_mes(2026, m, 9000 + 300 * m, 4000, categorias=[
        {"nome": "Moradia", "gasto": 2000}, {"nome": "Outros", "gasto": 1000}])
        for m in range(1, 7)]
    p = pf.resumir(meses)
    assert p.essencial_pct == 100.0 and p.sem_classificacao_media == 1000.0
    t = pf.texto(p)
    assert "(R$ 1.000,00 por mês sem classificação ficam fora dessa conta)" in t
    assert "coeficiente de variação 0,0" in t


def test_renda_muito_variavel_e_deficit_aparecem():
    meses = [_mes(2026, m, r, 5000) for m, r in
             zip(range(1, 7), [3000, 12000, 4000, 15000, 2000, 9000])]
    p = pf.resumir(meses)
    assert p.renda_cv > 0.25 and p.meses_deficit == 3
    assert "muito variável" in pf.texto(p)
    assert "Meses com déficit: 3 de 6" in pf.texto(p)


# -- carregador ------------------------------------------------------------------

def _controle_falso(real=True, vazio_em=()):
    def _get(ano, mes):
        if (ano, mes) in vazio_em:
            return {"receitas": 0, "despesas": 0, "categorias": [],
                    "transacoes": [], "data_source": "real"}
        return {"receitas": 8000.0, "despesas": 5000.0,
                "categorias": [{"nome": "Moradia", "gasto": 2500.0}],
                "transacoes": [
                    {"valor": -1000.0, "eh_investimento": True,
                     "account_type": "checking"},
                    {"valor": -50.0, "eh_investimento": True,
                     "account_type": "credit_card"},
                    {"valor": -5000.0, "eh_despesa": True,
                     "account_type": "checking"}],
                "data_source": "real" if real else "mock_fallback"}
    return _get


def _repos(monkeypatch, get_controle, *, cartao=None, mock=False):
    from core import controle
    from core.config import settings
    monkeypatch.setattr(settings, "MOCK_MODE", mock)
    monkeypatch.setattr(controle, "get_controle", get_controle)
    monkeypatch.setattr(controle, "get_historico_cc_mensal",
                        lambda: [{"ano": 2026, "mes": 8, "total": 1200.0}])
    monkeypatch.setattr(controle, "get_transacoes_cartao_credito",
                        lambda: cartao or [])
    monkeypatch.setattr(controle, "get_dividas_cc", lambda: [])


def test_carregar_le_o_caixa_e_o_cartao_reais(monkeypatch):
    _repos(monkeypatch, _controle_falso())
    p = CARREGAR(dt.date(2026, 9, 28))
    assert p.meses == 12 and p.renda_media == 8000.0
    # aporte do caixa conta; o do cartão não
    assert p.aporte_medio == 1000.0
    assert p.fatura_media == 1200.0


def test_carregar_ignora_mock_e_fallback_e_nao_levanta(monkeypatch):
    _repos(monkeypatch, _controle_falso(), mock=True)
    assert CARREGAR(dt.date(2026, 9, 28)) is None
    _repos(monkeypatch, _controle_falso(real=False))
    assert CARREGAR(dt.date(2026, 9, 28)) is None

    def _cai(ano, mes):
        raise RuntimeError("Não foi possível carregar seus lançamentos.")
    _repos(monkeypatch, _cai)
    assert CARREGAR(dt.date(2026, 9, 28)) is None


def test_carregar_so_le_compras_da_fatura_csv(monkeypatch):
    compra = {"descricao": "SPOTIFY", "valor": -21.9, "categoria": "Assinaturas",
              "eh_despesa": True, "account_type": "credit_card",
              "installment_total": 1}
    cartao = ([{**compra, "source": "csv", "data": dt.date(2026, m, 5)}
               for m in (6, 7, 8)]
              + [{**compra, "source": "manual", "data": dt.date(2026, m, 5)}
                 for m in (3, 4, 5)])
    _repos(monkeypatch, _controle_falso(), cartao=cartao)
    p = CARREGAR(dt.date(2026, 9, 28))
    assert [(g.nome, g.faturas) for g in p.cartao_recorrentes] == [("SPOTIFY", 3)]


# -- prompt ----------------------------------------------------------------------

def test_prompt_manda_avaliar_pelo_perfil_sem_gravar_sozinho():
    assert "PERFIL FINANCEIRO" in ent._SISTEMA
    assert "sobra média" in ent._SISTEMA
    assert "Aporte não é despesa" in ent._SISTEMA
    assert "só o usuário confirma" in ent._SISTEMA
    capturado = {}

    def _chat(messages, **_):
        capturado["messages"] = messages
        return json.dumps({"updates": {"monthly_contribution": 3000},
                           "evidence": {},
                           "next_question": "Sua sobra média é R$ 3.150; "
                                            "aportar R$ 3.000 faz sentido?",
                           "finished": False})
    contexto = "PERFIL FINANCEIRO do usuário:\n" + pf.texto(pf.resumir(_ano_tipico()))
    etapa = ent.proxima_etapa({}, [], "quero aportar", contexto=contexto,
                              chat=_chat)
    sistema = "\n".join(m["content"] for m in capturado["messages"]
                        if m["role"] == "system")
    assert "Sobra média" in sistema and "R$ 10.250,00" in sistema
    # o perfil sugere, mas o valor sem trecho do usuário não é gravado
    assert "monthly_contribution" in etapa.rejeitados
    assert "sobra média" in etapa.pergunta
