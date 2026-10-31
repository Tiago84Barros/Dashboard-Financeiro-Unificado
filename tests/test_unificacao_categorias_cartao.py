"""A fatura do cartão usa os nomes das categorias de sistema (31/10/2026).

Antes, o importador criava ``Saúde & Bem-estar``, ``Transporte & Combustível``
etc. na conta de cada usuário, e o mesmo gasto caía em duas categorias conforme
fosse pago no débito (extrato, nomes de sistema) ou no cartão. Estes testes
prendem a unificação e o script que migra o que ficou gravado.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.card_categorization import (
    CATEGORY_MAP,
    CATEGORY_TYPE,
    DESCRICAO_CATEGORIA_JEV,
    MERCHANT_RULES,
    NOMES_LEGADOS,
    REVIEW_SENTINEL,
    classify,
    nome_unificado,
)
from core.categorias import SEED

RAIZ = Path(__file__).resolve().parent.parent

#: Categorias do cartão sem par entre as de sistema do seletor (``SEED``).
#: ``Alimentação`` é de sistema desde a migration 008, mas não está no SEED.
SO_DO_CARTAO = {
    "Alimentação", "Cuidados pessoais", "Casa & Construção", "Tarifas & Anuidade",
    "Créditos e Estornos", "Pagamento de Cartão", REVIEW_SENTINEL,
}


# ── a taxonomia ──────────────────────────────────────────────────────────────

def test_categoria_do_cartao_com_par_de_sistema_usa_o_nome_de_sistema():
    sistema = set(SEED["saida"])
    proprias = [c for c in CATEGORY_TYPE if c not in SO_DO_CARTAO and c not in sistema]
    assert proprias == [], f"nome próprio da fatura reapareceu: {proprias}"


def test_nenhum_nome_antigo_sobrou_nas_regras_do_cartao():
    usados = (set(CATEGORY_TYPE) | {cat for _, cat in MERCHANT_RULES}
              | set(CATEGORY_MAP.values()) | set(DESCRICAO_CATEGORIA_JEV))
    assert not usados & set(NOMES_LEGADOS)


def test_todo_destino_de_nome_antigo_existe_na_taxonomia():
    for antigo in NOMES_LEGADOS:
        for descricao in ("", "POSTO PANA VIP", "UBER TRIP"):
            assert nome_unificado(antigo, descricao) in CATEGORY_TYPE


@pytest.mark.parametrize(("antigo", "novo"), [
    ("Compras / Varejo", "Compras"),
    ("Assinaturas & Serviços digitais", "Assinaturas"),
    ("Saúde & Bem-estar", "Saúde"),
    ("Lazer & Entretenimento", "Lazer"),
    ("Educação & Profissional", "Educação"),
])
def test_nome_antigo_vira_o_de_sistema(antigo, novo):
    assert nome_unificado(antigo) == novo
    assert nome_unificado(f"  {antigo} ") == novo


@pytest.mark.parametrize(("descricao", "esperado"), [
    ("POSTO PANA VIP | Compra 01/10/2026", "Combustível"),
    ("SHELLBOX*ABASTECE", "Combustível"),
    ("AUTO NORTE COMBUSTIVEIS LTDA", "Combustível"),
    ("PBADMINISTRADORA", "Combustível"),
    ("UBER TRIP", "Transporte"),
    ("ESTAPAR ESTACIONAMENTO", "Transporte"),
    ("", "Transporte"),
])
def test_transporte_e_combustivel_se_divide_pela_descricao(descricao, esperado):
    assert nome_unificado("Transporte & Combustível", descricao) == esperado


def test_nome_que_nao_e_antigo_passa_intacto():
    for nome in ("Saúde", "Cuidados pessoais", REVIEW_SENTINEL, "Qualquer Coisa"):
        assert nome_unificado(nome) == nome


# ── o importador ─────────────────────────────────────────────────────────────

def test_posto_vai_para_combustivel_e_aplicativo_para_transporte():
    assert classify("Relacionados a Automotivo", "POSTO SAO JORGE", 100.0)[0] == "Combustível"
    assert classify("Transporte", "UBER *TRIP", 30.0)[0] == "Transporte"
    assert classify("Recreativo", "CINEMARK", 60.0)[0] == "Lazer"
    assert classify("Serviços de telecomunicações", "TIM", 50.0)[0] == "Assinaturas"


def test_regra_aprendida_com_nome_antigo_continua_valendo():
    """``card_category_rules`` guarda o nome que a pessoa escolheu na época."""
    regras = [("SHELL", "Transporte & Combustível"), ("ESTAPAR", "Transporte & Combustível"),
              ("DROGASIL", "Saúde & Bem-estar")]
    assert classify("", "SHELL BOX", 80.0, user_rules=regras) == ("Combustível", "usuario:SHELL")
    assert classify("", "ESTAPAR CENTRO", 15.0, user_rules=regras) == ("Transporte", "usuario:ESTAPAR")
    assert classify("", "DROGASIL 123", 40.0, user_rules=regras) == ("Saúde", "usuario:DROGASIL")


def test_lancamento_com_nome_antigo_e_reclassificado_para_o_novo():
    """O ``recategorizar_cartao.py`` reprocessa a categoria gravada."""
    assert classify("Saúde & Bem-estar", "CLINICA X", 200.0) == ("Saúde", "nome-legado")
    assert classify("Transporte & Combustível", "OFICINA DO ZE", 300.0) == ("Transporte", "nome-legado")
    assert classify("Saúde", "CLINICA X", 200.0) == ("Saúde", "ja-catalogada")


def test_essencialidade_do_chat_do_cartao_entende_nome_antigo_e_novo():
    from core.llm_context_financeiro import _card_essencialidade

    assert _card_essencialidade("Saúde") == "essencial"
    assert _card_essencialidade("Saúde & Bem-estar") == "essencial"
    assert _card_essencialidade("Combustível") == "essencial"
    assert _card_essencialidade("Transporte & Combustível") == "essencial"
    assert _card_essencialidade("Lazer & Entretenimento") == "nao_essencial"
    assert _card_essencialidade("Assinaturas") == "nao_essencial"


def test_nenhum_codigo_do_app_volta_a_escrever_nome_antigo():
    """Só a tradução (card_categorization) e a migração podem citá-los."""
    permitidos = {RAIZ / "core" / "card_categorization.py",
                  RAIZ / "scripts" / "unificar_categorias_cartao.py"}
    achados = []
    for pasta in ("core", "views", "design", "etl"):
        for arq in (RAIZ / pasta).rglob("*.py"):
            if arq in permitidos:
                continue
            texto = arq.read_text(encoding="utf-8")
            achados += [f"{arq.relative_to(RAIZ)}: {nome}"
                        for nome in NOMES_LEGADOS if nome in texto]
    assert achados == []


# ── o script de migração ─────────────────────────────────────────────────────

def _colunas_que_apontam_para_categories() -> set[tuple[str, str]]:
    """(tabela, coluna) com ``REFERENCES categories`` nos DDLs do repositório."""
    fontes = list((RAIZ / "supabase_unificado" / "schema").glob("*.sql"))
    fontes.append(RAIZ / "core" / "bank_statement_import.py")
    achados: set[tuple[str, str]] = set()
    for arq in fontes:
        texto = arq.read_text(encoding="utf-8")
        for bloco in re.finditer(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(?:public\.)?(\w+)\s*\((.*?)\n\s*\)",
                                 texto, flags=re.S | re.I):
            tabela, corpo = bloco.group(1), bloco.group(2)
            for col in re.finditer(r"^\s*(\w+)\s+UUID[^\n]*REFERENCES\s+categories\b",
                                   corpo, flags=re.M | re.I):
                achados.add((tabela, col.group(1)))
    return achados


def test_migracao_cobre_toda_coluna_que_aponta_para_categoria():
    """Coluna nova apontando para ``categories`` precisa entrar no script —
    senão o DELETE da categoria antiga falha (e o script a mantém) ou, pior,
    um ``ON DELETE CASCADE`` leva a linha junto."""
    from scripts.unificar_categorias_cartao import REFERENCIAS

    tratadas = {(t, c) for t, c, _ in REFERENCIAS}
    tratadas |= {("budgets", "category_id"), ("categories", "parent_id")}
    achadas = _colunas_que_apontam_para_categories()
    assert ("transactions", "category_id") in achadas  # o leitor do DDL funciona
    assert achadas - tratadas == set()


def test_migracao_nao_roda_em_mock(monkeypatch, capsys):
    from core.config import settings
    from scripts import unificar_categorias_cartao as script

    monkeypatch.setattr(settings, "MOCK_MODE", True)
    assert script.main(["--aplicar"]) == 1
    assert "MOCK_MODE" in capsys.readouterr().out
