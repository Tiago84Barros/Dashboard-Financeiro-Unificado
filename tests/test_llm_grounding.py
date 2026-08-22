"""Verificador de ancoragem numérica das respostas da LLM (puro, offline)."""
import pytest

from core.llm_grounding import check_grounding, extract_numbers, parse_number

# ── parsing de números pt-BR / en-US ─────────────────────────────────────────

@pytest.mark.parametrize("token,esperado", [
    ("1.234,56", 1234.56),      # pt-BR
    ("1,234.56", 1234.56),      # en-US
    ("12,5", 12.5),
    ("1.234.567", 1234567.0),
    ("45", 45.0),
    ("-320,10", -320.10),
    ("3.14", 3.14),
])
def test_parse_number_formatos(token, esperado):
    assert parse_number(token) == pytest.approx(esperado)


def test_parse_number_ambiguo_vira_none():
    # "1.234" pode ser mil-e-duzentos ou 1,234 — não adivinhar
    assert parse_number("1.234") is None
    assert parse_number("abc") is None


def test_extract_numbers_aplica_escala_textual():
    valores = [v for v, _ in extract_numbers("lucro de 2,5 milhões no período")]
    assert 2.5 in valores and 2_500_000.0 in valores


# ── ancoragem ────────────────────────────────────────────────────────────────

CONTEXTO = """
Receitas do mês: R$ 12.500,00
Despesas do mês: R$ 8.300,00
Cartão de crédito: R$ 2.145,90
Categoria Alimentação: R$ 1.230,45
"""


def test_resposta_fiel_ao_contexto_e_totalmente_ancorada():
    resposta = ("Suas receitas somaram R$ 12.500,00 e as despesas R$ 8.300,00. "
                "A fatura do cartão foi de R$ 2.145,90.")
    rel = check_grounding(resposta, CONTEXTO)
    assert rel.ratio == 1.0
    assert not rel.ungrounded


def test_numero_inventado_e_sinalizado():
    resposta = "Suas despesas somaram R$ 8.300,00 e o aluguel foi de R$ 4.789,33."
    rel = check_grounding(resposta, CONTEXTO)
    assert rel.ratio < 1.0
    assert [c.value for c in rel.ungrounded] == [pytest.approx(4789.33)]


def test_conta_derivada_do_contexto_conta_como_ancorada():
    # saldo = 12.500,00 - 8.300,00 = 4.200,00 (o prompt PEDE que mostre a conta)
    resposta = "O saldo do mês foi de R$ 4.200,00."
    rel = check_grounding(resposta, CONTEXTO)
    assert rel.ratio == 1.0
    assert rel.claims[0].reason == "derivado do contexto"


def test_derivacao_pode_ser_desligada_para_checagem_estrita():
    rel = check_grounding("Saldo de R$ 4.200,00.", CONTEXTO, allow_derived=False)
    assert rel.ungrounded


def test_percentual_equivalente_a_fracao_do_contexto():
    rel = check_grounding("A margem foi de 26%.", "margem: 0.26")
    assert rel.ratio == 1.0


def test_anos_e_contagens_nao_sao_afirmacoes_factuais():
    rel = check_grounding("Em 2025 você teve 3 categorias acima da média.",
                          CONTEXTO)
    assert rel.checked == 0          # nada a verificar → não penaliza
    assert rel.ratio == 1.0


def test_arredondamento_dentro_da_tolerancia_e_aceito():
    rel = check_grounding("Cerca de R$ 2.146,00 no cartão.", CONTEXTO)
    assert rel.ratio == 1.0


def test_resposta_sem_numeros_nao_penaliza():
    rel = check_grounding("Recomendo revisar as assinaturas.", CONTEXTO)
    assert rel.checked == 0 and rel.ratio == 1.0


def test_contexto_vazio_deixa_tudo_sem_ancora():
    rel = check_grounding("Gastou R$ 999,99.", "")
    assert rel.ratio == 0.0
    assert rel.ungrounded[0].reason == "sem âncora no contexto"


def test_variacao_percentual_nao_ancora_valor_em_reais():
    """Defeito real encontrado ao construir o harness: (1850-210)/210 = 780,95%
    ancorava indevidamente 'R$ 780,00' — número inventado. Unidades separadas."""
    rel = check_grounding("Você gastou R$ 780,00 com educação.", CONTEXTO)
    assert rel.ungrounded, "valor em reais não pode casar com variação percentual"

    # o mesmo número COMO percentual continua ancorado (é derivação legítima)
    contexto = "Alimentação: R$ 1.230,45\nAssinaturas: R$ 210,00"
    variacao = (1230.45 - 210.0) / 210.0 * 100.0
    rel2 = check_grounding(f"Aumento de {variacao:.2f}% entre as categorias.", contexto)
    assert rel2.ratio == 1.0


def test_soma_de_pares_nao_ancora_qualquer_numero():
    """Tolerância apertada nas derivações: aritmética é exata, não arredondada."""
    rel = check_grounding("Total de R$ 9.999,00.", CONTEXTO)
    assert rel.ungrounded


def test_raw_preserva_o_token_sem_pontuacao_final():
    rel = check_grounding("Gastou R$ 4.789,33.", CONTEXTO)
    assert rel.ungrounded[0].raw == "4.789,33"


# ── integração com a interface do chat ───────────────────────────────────────

def test_aviso_da_ui_so_aparece_quando_ha_numero_sem_lastro():
    from views.controle_financeiro import _aviso_ancoragem

    assert _aviso_ancoragem("Suas despesas foram R$ 8.300,00.", CONTEXTO) == ""
    aviso = _aviso_ancoragem("Seu aluguel é R$ 4.789,33.", CONTEXTO)
    assert "4.789,33" in aviso and "não foi encontrado" in aviso


def test_aviso_da_ui_nunca_derruba_o_chat():
    from views.controle_financeiro import _aviso_ancoragem

    assert _aviso_ancoragem(None, None) == ""
    assert _aviso_ancoragem("", "") == ""


# ── Notação: pt-BR e en-US no MESMO verificador ──────────────────────────────
# A LLM responde ora numa notação, ora noutra. "12,500" vale 12.500 em inglês e
# 12,5 em português, e nada no token decide.

@pytest.mark.parametrize("token,esperado", [
    ("1.234,56", 1234.56), ("1,234.56", 1234.56),      # milhar + decimal
    ("24,8", 24.8), ("24.8", 24.8),                     # decimal, 1 casa
    ("3.100,00", 3100.0), ("3,100.00", 3100.0),         # decimal, 2 casas
    ("1.234.567,89", 1234567.89), ("1,234,567.89", 1234567.89),
    ("0,500", 0.5), ("0.500", 0.5),                     # zero à esquerda não é ambíguo
    ("8058", 8058.0), ("12,345,678", 12345678.0),
])
def test_parse_number_aceita_as_duas_notacoes(token, esperado):
    assert parse_number(token) == pytest.approx(esperado)


@pytest.mark.parametrize("token", ["3.100", "3,100", "12.500", "12,500"])
def test_separador_ambiguo_e_ignorado_nas_duas_notacoes(token):
    """Regressão: a guarda existia só para o ponto.

    A vírgula caía num `replace(',', '.')` cego e "12,500" virava 12,5 — erro de
    fator 1.000, silencioso. Valor real de R$ 12.500 não ancorava e a resposta
    correta era acusada de inventar dado. Ignorar não cria falso positivo nem
    falso negativo; chutar cria os dois.
    """
    assert parse_number(token) is None


def test_tolerancia_sai_da_precisao_declarada():
    """R$ 7.777,00 inventado passava por casar com 7.800 dentro de 1%.

    Quem escreve centavos afirma seis dígitos de precisão; quem escreve
    "7,8 mil" afirma dois e merece a folga.
    """
    contexto = "Despesas: R$ 7.800,00"
    exato = check_grounding("Gastou R$ 7.777,00.", contexto)
    assert not exato.claims[0].grounded

    aproximado = check_grounding("Gastou cerca de R$ 7,8 mil.", contexto)
    assert all(c.grounded for c in aproximado.claims)


def test_mesmo_token_ancora_sob_qualquer_leitura():
    """"8,3 mil" gera duas leituras (8,3 e 8.300) da MESMA afirmação.

    Julgá-las isoladas fazia o mesmo texto sair aprovado numa e reprovado na
    outra. Vale para "24,8%" (percentual e absoluta) pelo mesmo motivo.
    """
    contexto = "Despesas: R$ 8.300,00\nReceitas: R$ 12.500,00"
    rel = check_grounding("Gastou cerca de R$ 8,3 mil.", contexto)
    assert all(c.grounded for c in rel.claims)


def test_cadeia_aceita_conta_certa_e_recusa_errada():
    """A projeção encadeia: 20% de 1.210 = 242 → 8.300 − 242 = 8.058."""
    contexto = ("Despesas: R$ 8.300,00\nAssinaturas: R$ 210,00\n"
                "Outros: R$ 1.000,00")
    pergunta = "E se eu cortar 20% dos gastos não essenciais?"
    certa = check_grounding(
        "Não essenciais somam R$ 1.210,00; cortar 20% economiza R$ 242,00, "
        "deixando R$ 8.058,00.", contexto, pergunta=pergunta)
    assert all(c.grounded for c in certa.claims)

    errada = check_grounding(
        "Não essenciais somam R$ 1.210,00; cortar 20% economiza R$ 900,00, "
        "deixando R$ 7.400,00.", contexto, pergunta=pergunta)
    ruins = [c.raw for c in errada.claims if not c.grounded]
    assert "900,00" in ruins and "7.400,00" in ruins


def test_cem_da_formula_de_percentual_nao_e_dado():
    """"(3.100 / 12.500) × 100" — o 100 é a base da conversão, não um valor."""
    contexto = "Moradia: R$ 3.100,00\nReceitas: R$ 12.500,00"
    rel = check_grounding("(3.100 / 12.500) × 100 ≈ 24,8%", contexto)
    assert all(c.grounded for c in rel.claims)
