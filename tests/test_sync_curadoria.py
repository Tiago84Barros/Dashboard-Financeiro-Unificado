"""Bucketização de sinal na curadoria do sync (scripts/sync_docs_to_supabase)."""
from scripts.sync_docs_to_supabase import _bucket


def test_bucket_alto_sinal():
    assert _bucket("Fato Relevante", "") == "fato"
    assert _bucket("", "Dados Econômico-Financeiros") == "resultado"
    assert _bucket("Comunicado ao Mercado", "") == "comunicado"


def test_bucket_proventos_e_assembleia():
    assert _bucket("Aviso aos Acionistas", "") == "provento"
    assert _bucket("", "Relatório Proventos") == "provento"
    assert _bucket("Assembleia", "AGO") == "assembleia"


def test_bucket_capital_e_critico():
    assert _bucket("Escrituras e aditamentos de debêntures", "") == "capital"
    assert _bucket("", "OPA - Edital de Oferta Pública") == "capital"
    assert _bucket("Recuperação Judicial", "") == "critico"


def test_bucket_default():
    assert _bucket("Estatuto Social", "") == "outro"
