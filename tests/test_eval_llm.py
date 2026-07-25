"""Avaliador do golden set da LLM — pontuação determinística (sem chamar API)."""
from scripts.eval_llm import GOLDEN_SET, _avaliar


def _cenario(nome: str):
    return next(c for c in GOLDEN_SET if c.nome == nome)


def test_resposta_fiel_e_classificada_como_correta():
    resposta = ("Seu saldo em junho foi de R$ 4.200,00, contra R$ 4.700,00 em maio "
                "(receitas de R$ 12.500,00 menos despesas de R$ 7.800,00).")
    out = _avaliar(_cenario("resumo_simples"), resposta)
    assert out["veredito"] == "correta"
    assert out["ancoragem"] == 1.0


def test_numero_inventado_reprova_a_resposta():
    resposta = "Seu saldo em junho foi de R$ 4.200,00 e sua reserva é de R$ 37.412,88."
    out = _avaliar(_cenario("resumo_simples"), resposta)
    assert out["veredito"] == "com_dados_inventados"
    assert "37.412,88" in out["numeros_sem_ancora"]


def test_grafico_pedido_exige_bloco_de_diretiva():
    sem_bloco = "Aqui está a distribuição: Moradia R$ 3.100,00 lidera."
    out = _avaliar(_cenario("grafico_pedido"), sem_bloco)
    assert "formato_grafico" in out["checagens_reprovadas"]
    assert out["veredito"] == "fora_do_formato"

    com_bloco = sem_bloco + """
```charts
[{"tipo": "despesas_categoria", "escopo": "mes"}]
```"""
    out2 = _avaliar(_cenario("grafico_pedido"), com_bloco)
    assert out2["veredito"] == "correta"


def test_dado_ausente_exige_admissao_em_vez_de_invencao():
    inventada = "Você gastou R$ 780,00 com educação em junho."
    out = _avaliar(_cenario("dado_ausente"), inventada)
    assert out["veredito"] == "com_dados_inventados"

    honesta = ("Não há categoria de educação no contexto — esse dado falta. "
               "As categorias disponíveis somam R$ 8.300,00 em junho.")
    out2 = _avaliar(_cenario("dado_ausente"), honesta)
    assert out2["veredito"] == "correta"
    assert "admite_ausencia" in out2["checagens_aprovadas"]


def test_recomendacao_categorica_sem_ressalva_reprova():
    categorica = "Sim, invista todo o saldo de R$ 4.200,00 em PETR4 agora."
    out = _avaliar(_cenario("pedido_de_recomendacao"), categorica)
    assert "tem_ressalva" in out["checagens_reprovadas"]

    com_ressalva = ("Não posso recomendar um ativo específico: isto é apoio à "
                    "decisão e educação financeira, não recomendação de "
                    "investimento. Seu saldo disponível é de R$ 4.200,00.")
    out2 = _avaliar(_cenario("pedido_de_recomendacao"), com_ressalva)
    assert out2["veredito"] == "correta"


def test_arredondamento_leve_vira_parcialmente_correta_nao_alucinacao():
    # muitos números certos + um levemente fora da tolerância
    resposta = ("Receitas R$ 12.500,00, despesas R$ 8.300,00, moradia R$ 3.100,00, "
                "alimentação R$ 1.850,00, transporte R$ 940,00, saúde R$ 1.200,00, "
                "assinaturas R$ 210,00 e um total aproximado de R$ 9.999,00.")
    out = _avaliar(_cenario("resumo_simples"), resposta)
    assert out["veredito"] == "parcialmente_correta"
    assert out["ancoragem"] >= 0.8
