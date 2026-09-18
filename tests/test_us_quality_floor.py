"""Piso absoluto de qualidade do módulo EUA — casos do universo real."""
import pandas as pd

from core.us_advanced_lab import (
    RISCO_ACCRUALS,
    RISCO_ALAVANCAGEM,
    RISCO_ALTMAN,
    RISCO_FCF,
    RISCO_LIQUIDEZ,
    RISCO_MARGEM,
    RISCO_PAYOUT,
    RISCO_PIOTROSKI,
)
from core.us_quality_floor import (
    APROVADO,
    REPROVADO,
    SEM_EVIDENCIA,
    FloorPolicy,
    apply_with_substitution,
    evaluate,
)


def _frame(linhas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(linhas)


UNIVERSO = _frame([
    {"symbol": "GOOD", "entry_status": "Observação", "risk_driver": "sem alerta crítico"},
    {"symbol": "ALSO", "entry_status": "Observação", "risk_driver": "sem alerta crítico"},
    {"symbol": "BAD1", "entry_status": "Excluída",
     "risk_driver": "margem líquida negativa; fluxo de caixa livre negativo"},
    {"symbol": "BAD2", "entry_status": "Excluída",
     "risk_driver": "dívida líquida/EBITDA elevada"},
    {"symbol": "MUTE", "entry_status": None, "risk_driver": None},
])


def test_excluida_do_laboratorio_e_reprovada():
    """824 das 2.831 empresas (29%) estavam nessa situação e lideravam mesmo assim."""
    v = evaluate(UNIVERSO)
    assert v["BAD1"].situacao == REPROVADO
    assert "margem líquida negativa" in "; ".join(v["BAD1"].motivos)
    assert v["GOOD"].situacao == APROVADO


def test_observacao_nao_reprova_por_padrao():
    """É a faixa NORMAL do universo americano: 2.007 de 2.831, e ZERO 'Aprovada'.

    Reprovar aqui esvaziaria a carteira inteira em vez de torná-la seletiva.
    """
    assert evaluate(UNIVERSO)["GOOD"].situacao == APROVADO
    apertado = evaluate(UNIVERSO, policy=FloorPolicy(reprovar_observacao=True))
    assert apertado["GOOD"].situacao == REPROVADO


def test_sem_veredito_nao_vira_reprovacao():
    """Ausência do laboratório é lacuna, não falha — condenar por dado que não
    chegou é o erro que a faixa de validação cometia no módulo B3."""
    assert evaluate(UNIVERSO)["MUTE"].situacao == SEM_EVIDENCIA


def test_substituto_do_mesmo_grupo_herda_o_peso():
    """A vaga do grupo é preservada: exigir qualidade não custa diversificação."""
    log: dict = {}
    pesos = {"BAD1": 0.12}
    finais = apply_with_substitution(
        ["BAD1"], [("BAD1", 90.0), ("GOOD", 80.0)], UNIVERSO, pesos,
        "Semicondutores", log)

    assert finais == ["GOOD"]
    assert pesos["GOOD"] == 0.12
    assert log["substituicoes"][0] == {"entra": "GOOD", "sai": "BAD1",
                                       "grupo": "Semicondutores"}


def test_grupo_sem_candidato_bom_fica_vazio_e_declarado():
    """Declarar é melhor que rebaixar em silêncio para o segundo pior."""
    log: dict = {}
    finais = apply_with_substitution(
        ["BAD1"], [("BAD1", 90.0), ("BAD2", 70.0)], UNIVERSO, {"BAD1": 0.1},
        "Biotecnologia", log)

    assert finais == []
    assert log["sem_substituto"][0]["symbol"] == "BAD1"
    assert not log.get("substituicoes")


def test_aprovada_passa_sem_tocar_na_lista():
    log: dict = {}
    finais = apply_with_substitution(
        ["GOOD", "ALSO"], [("GOOD", 90.0), ("ALSO", 80.0)], UNIVERSO,
        {"GOOD": 0.1, "ALSO": 0.1}, "Software", log)
    assert finais == ["GOOD", "ALSO"]
    assert not log.get("reprovados")


def test_frame_vazio_nao_quebra():
    assert evaluate(pd.DataFrame()) == {}
    assert evaluate(None) == {}


def test_o_piso_nao_define_limiar_proprio():
    """Trava de arquitetura: a régua mora em us_advanced_lab.

    Duplicar os cortes aqui criaria duas fontes de verdade divergindo com o
    tempo — o defeito que originou o piso. Se este teste falhar, alguém trouxe
    limiar numérico para dentro do piso.
    """
    import ast
    import re
    from pathlib import Path

    fonte = (Path(__file__).resolve().parents[1]
             / "core" / "us_quality_floor.py").read_text(encoding="utf-8")

    # Pela AST, não por texto: o que se proíbe é limiar no CÓDIGO. Docstring e
    # comentário precisam poder nomear os critérios — é assim que a guarda de
    # viabilidade explica o que cede e o que não cede. Filtrar por substring
    # confundia a explicação com a régua e transformava documentar em defeito.
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)) and ast.get_docstring(no) is not None:
            no.body = no.body[1:]
    corpo = ast.unparse(arvore)                # sem docstrings, sem comentários

    for termo in ("altman", "piotroski", "z_score", "sloan", "payout_ratio"):
        linhas = [linha for linha in corpo.splitlines() if termo in linha.lower()]
        assert not linhas, f"limiar de {termo} vazou para o piso: {linhas}"
    # Nenhuma comparação numérica de métrica financeira no corpo executável.
    assert not re.search(r"\b(risk_penalty|f_score)\b\s*[<>]=?", corpo)


# ── Guarda do piso de score de entrada ───────────────────────────────────────

def test_piso_inalcancavel_avisa_com_o_teto_real():
    """Subir o piso alguns pontos pode zerar a carteira sem explicação.

    O piso é comparado com a MÉDIA da indústria, e o score é recalculado sobre o
    universo já filtrado — não há como o usuário saber, olhando a tela, qual
    valor o mercado alcança naquele recorte.
    """
    import numpy as np

    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        build_portfolio_creation,
    )

    universo = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(8)],
        "name": [f"Empresa {i}" for i in range(8)],
        "sector": ["Software"] * 8, "industry": ["Software"] * 8,
        "score": np.linspace(60, 80, 8),
        "coverage": [90.0] * 8, "_market_cap": [5e9] * 8, "_years": [12] * 8,
        "roe": np.linspace(.10, .25, 8), "net_margin": np.linspace(.05, .2, 8),
        # Liquidez MEDIDA: sem ela o motor bloquearia por negociabilidade não
        # verificada (us-liquidity-2.0.0) e o teste deixaria de exercitar o
        # piso de score, que é o objeto sob teste.
        "giro_diario_usd": [5e6] * 8,
        "giro_diario_usd_at": [pd.Timestamp.now(tz="UTC")] * 8,
    })
    r = build_portfolio_creation(universo, USPortfolioCreationParams(min_entry_score=99.0))
    assert r["holdings"].empty
    aviso = " ".join(r["warnings"])
    assert "Nenhuma indústria aprovada" in aviso and "99" in aviso


def test_guarda_nao_dispara_quando_ha_carteira():
    """Aviso que aparece sempre vira ruído — só surge quando zera de fato.

    Duas indústrias de propósito: com uma só, o critério de vantagem sobre as
    demais não tem contra o que comparar e nada é aprovado — o aviso dispararia
    corretamente, mas por outro motivo, e o teste não provaria nada.
    """
    import numpy as np

    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        build_portfolio_creation,
    )

    n = 16
    universo = pd.DataFrame({
        "symbol": [f"S{i}" for i in range(n)],
        "name": [f"Empresa {i}" for i in range(n)],
        "sector": ["Tecnologia"] * n,
        "industry": ["Software"] * 8 + ["Semicondutores"] * 8,
        "score": np.r_[np.linspace(70, 85, 8), np.linspace(40, 55, 8)],
        "coverage": [90.0] * n, "_market_cap": [5e9] * n, "_years": [12] * n,
        "roe": np.r_[np.linspace(.15, .30, 8), np.linspace(.02, .08, 8)],
        "net_margin": np.r_[np.linspace(.12, .25, 8), np.linspace(.01, .05, 8)],
        # Ver o comentário do teste anterior: liquidez medida para que o objeto
        # sob teste continue sendo a guarda do piso de score.
        "giro_diario_usd": [5e6] * n,
        "giro_diario_usd_at": [pd.Timestamp.now(tz="UTC")] * n,
    })
    r = build_portfolio_creation(
        universo, USPortfolioCreationParams(min_entry_score=0.0, min_score_edge=0.0))
    assert r["review_portfolio"]["items"]
    assert r["review_portfolio"]["unallocated_weight"] > 0
    assert not any("Nenhuma indústria aprovada" in w for w in r["warnings"])


# -- Guarda de viabilidade e rede final ---------------------------------------
# "Nunca deixar zerada a criacao de qualquer portfolio" e regra do projeto. Os
# testes abaixo forcam o esvaziamento de verdade: sem eles, a guarda e a rede
# nunca chegam a ser executadas pela suite.

ACESSORIO = _frame([
    {"symbol": "SOMA", "entry_status": "Excluída",
     "risk_driver": f"{RISCO_ALTMAN}; {RISCO_PAYOUT}"},
    {"symbol": "SOM2", "entry_status": "Excluída",
     "risk_driver": f"{RISCO_ACCRUALS}; {RISCO_PIOTROSKI}"},
    {"symbol": "ESTR", "entry_status": "Excluída",
     "risk_driver": f"{RISCO_ALAVANCAGEM}; {RISCO_ALTMAN}"},
    {"symbol": "MUDO", "entry_status": "Excluída",
     "risk_driver": "sem alerta crítico"},
])


def test_industria_sem_ninguem_readmite_o_lider_quando_a_soma_e_acessoria():
    """Todos excluídos só pela SOMA de sinais que sozinhos não excluem.

    Deixar a indústria vazia aqui é perder representação por critério que o
    próprio motor declara insuficiente isolado. A reprovação segue no log.
    """
    log: dict = {}
    finais = apply_with_substitution(
        ["SOMA"], [("SOMA", 90.0), ("SOM2", 70.0)], ACESSORIO,
        {"SOMA": 0.1}, "Biotecnologia", log)

    assert finais == ["SOMA"]
    assert log["afrouxado_por_viabilidade"][0]["symbol"] == "SOMA"
    assert not log.get("sem_substituto")
    # A cessão não apaga a reprovação: ela continua registrada.
    assert log["reprovados"][0]["symbol"] == "SOMA"


def test_falha_estrutural_nunca_e_cedida():
    """Alavancagem no meio dos motivos e a vaga fica vazia — é o que o piso
    existe para barrar, e ceder aqui desligaria a proteção inteira."""
    log: dict = {}
    finais = apply_with_substitution(
        ["ESTR"], [("ESTR", 90.0)], ACESSORIO, {"ESTR": 0.1}, "Aço", log)

    assert finais == []
    assert log["sem_substituto"][0]["symbol"] == "ESTR"
    assert not log.get("afrouxado_por_viabilidade")


def test_exclusao_sem_motivo_acessorio_nao_e_cedida():
    """'Excluída' por score baixo traz 'sem alerta crítico' como motivo — não
    cabe em RISCOS_ACESSORIOS e portanto não passa pela guarda."""
    log: dict = {}
    finais = apply_with_substitution(
        ["MUDO"], [("MUDO", 90.0)], ACESSORIO, {"MUDO": 0.1}, "Varejo", log)
    assert finais == []
    assert log["sem_substituto"][0]["symbol"] == "MUDO"


def test_substituto_aprovado_tem_precedencia_sobre_a_cessao():
    """A cessão é o ÚLTIMO recurso: havendo nome aprovado na indústria, é ele
    que entra — a guarda não pode virar porta de entrada preferencial."""
    frame = pd.concat([ACESSORIO, UNIVERSO], ignore_index=True)
    log: dict = {}
    finais = apply_with_substitution(
        ["SOMA"], [("SOMA", 90.0), ("GOOD", 80.0)], frame, {"SOMA": 0.1},
        "Software", log)
    assert finais == ["GOOD"]
    assert not log.get("afrouxado_por_viabilidade")


def test_carteira_nunca_sai_vazia_por_causa_do_piso():
    """REDE FINAL. O piso reprova o universo inteiro e ainda assim sai carteira.

    A guarda de viabilidade age por indústria e não sabe que aquela era a
    última; só `select_industry_leaders` enxerga o conjunto. Carteira vazia é a
    única saída que a regra do projeto proíbe — quem recebe a tela em branco
    decide sem o motor, isto é, sem proteção nenhuma.
    """
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        select_industry_leaders,
    )

    eligible = pd.DataFrame([
        {"symbol": "ESTR", "industry_group": "Aço", "entry_score": 50.0,
         "fundamental_score": 50.0, "entry_status": "Excluída",
         "risk_driver": f"{RISCO_ALAVANCAGEM}; {RISCO_LIQUIDEZ}"},
        {"symbol": "EST2", "industry_group": "Aço", "entry_score": 40.0,
         "fundamental_score": 40.0, "entry_status": "Excluída",
         "risk_driver": f"{RISCO_MARGEM}; {RISCO_FCF}"},
    ])
    audit = pd.DataFrame([{"industry_group": "Aço", "status": "Aprovada"}])

    saida = select_industry_leaders(
        eligible, audit, USPortfolioCreationParams(leaders_per_industry=1))

    assert not saida.empty, "o piso zerou a carteira — regra do projeto violada"
    assert saida.attrs["quality_floor_log"]["carteira_preservada"]
    # A cessão é declarada na própria linha, não só no log.
    assert saida["selection_reason"].str.contains("Carteira preservada").all()
    # E a reprovação que a motivou continua legível.
    assert saida.attrs["quality_floor_log"]["reprovados"]


def test_rede_final_nao_dispara_quando_o_piso_deixa_alguem():
    """Rede que aparece sempre não é rede: com um nome aprovado, ela cala."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        select_industry_leaders,
    )

    eligible = pd.DataFrame([
        {"symbol": "GOOD", "industry_group": "Software", "entry_score": 80.0,
         "fundamental_score": 70.0, "entry_status": "Observação",
         "risk_driver": "sem alerta crítico"},
    ])
    audit = pd.DataFrame([{"industry_group": "Software", "status": "Aprovada"}])
    saida = select_industry_leaders(
        eligible, audit, USPortfolioCreationParams(leaders_per_industry=1))

    assert list(saida["symbol"]) == ["GOOD"]
    assert not saida.attrs["quality_floor_log"].get("carteira_preservada")
