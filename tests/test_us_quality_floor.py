"""Piso absoluto de qualidade do módulo EUA — casos do universo real."""
import pandas as pd
import pytest

from core.us_quality_floor import (
    APROVADO, REPROVADO, SEM_EVIDENCIA, FloorPolicy, apply_with_substitution,
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
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1]
             / "core" / "us_quality_floor.py").read_text(encoding="utf-8")
    corpo = fonte.split('"""', 2)[-1]          # ignora o docstring do módulo
    import re
    for termo in ("altman", "piotroski", "z_score", "sloan", "payout_ratio"):
        linhas = [l for l in corpo.splitlines()
                  if termo in l.lower() and not l.strip().startswith("#")]
        assert not linhas, f"limiar de {termo} vazou para o piso: {linhas}"
    # Nenhuma comparação numérica de métrica financeira no corpo executável.
    assert not re.search(r"^\s*(?!#).*\b(risk_penalty|f_score)\b\s*[<>]=?",
                         corpo, re.M)
