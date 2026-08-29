"""O tamanho do viés de sobrevivência, medido fora do painel (A-157).

`medir_turnover` responde "o painel tem saídas?" e a resposta é zero. Isso
prova que a amostra é sobrevivente, mas não diz de quanto -- e sem o tamanho o
usuário não consegue descontar nada do retorno exibido. O tamanho não está no
painel por construção: quem morreu nunca entrou nele.

A fonte independente é o `full-index` da SEC, que é ponto-no-tempo de verdade:
lista quem arquivou relatório anual naquele trimestre, vivo ou não. Medido em
27/08/2026: das 9.686 empresas com relatório anual em 2010, 2.899 ainda
arquivavam em 2025 -- 70,1% desapareceram, contra zero mortes no nosso painel.

Estes testes rodam sem rede: exercitam o parser do índice com trechos reais e a
aritmética da coorte com conjuntos sintéticos.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.us_survivorship import (
    ciks_com_relatorio_anual,
    frase_mortalidade,
    medir_mortalidade,
)

# Trecho no formato real do form.idx (colunas de largura fixa, cabecalho junto).
IDX = """Form Type   Company Name                  CIK    Date Filed  File Name
---------------------------------------------------------------------------
10-K        ACME CORP                     320193 2010-03-01  edgar/data/320193/0000320193-10-000012.txt
10-K405     OLD FORM CO                    77476 2010-03-02  edgar/data/77476/0000077476-10-000003.txt
20-F        FOREIGN SA                   1018724 2010-03-03  edgar/data/1018724/0001018724-10-000005.txt
10-K/A      AMENDED INC                    12927 2010-03-04  edgar/data/12927/0000012927-10-000009.txt
8-K         NOISE CO                       19617 2010-03-05  edgar/data/19617/0000019617-10-000021.txt
"""


def test_indice_rejeita_cik_acima_do_limite_sec() -> None:
    idx_invalido = f"10-K  BIG CIK  1 2010-03-01  edgar/data/{10**400}/a.txt"
    assert ciks_com_relatorio_anual(idx_invalido) == set()


def test_indice_ignora_cik_com_milhares_de_digitos_sem_converter() -> None:
    idx_invalido = f"10-K  BIG CIK  1 2010-03-01  edgar/data/{'9' * 5000}/a.txt"
    assert ciks_com_relatorio_anual(idx_invalido) == set()


def test_indice_le_o_cik_do_caminho_e_nao_da_coluna() -> None:
    """Nome societario longo desalinha a coluna; o caminho nunca desalinha."""
    assert ciks_com_relatorio_anual(IDX) == {320193, 77476, 1018724}


def test_formularios_extintos_contam_como_relatorio_anual() -> None:
    """Quem so contasse 10-K leria troca de formulario como morte da empresa."""
    assert 77476 in ciks_com_relatorio_anual(IDX)


def test_aditamento_e_evento_nao_sao_relatorio_anual() -> None:
    ciks = ciks_com_relatorio_anual(IDX)
    assert 12927 not in ciks  # 10-K/A e aditamento do mesmo exercicio
    assert 19617 not in ciks  # 8-K e evento


def test_cabecalho_nao_vira_empresa() -> None:
    """A versao por posicao colhia um CIK 0 da linha de titulo."""
    assert 0 not in ciks_com_relatorio_anual(IDX)


def test_mortalidade_e_da_coorte_e_nao_do_tamanho_do_universo() -> None:
    """Universo que encolhe pouco pode esconder coorte que morreu muito.

    Aqui o universo vai de 4 para 4 empresas -- estavel -- mas metade da coorte
    de 2010 sumiu e foi substituida por gente nova. Contar so o total diria
    'nada aconteceu'.
    """
    m = medir_mortalidade({2010: {1, 2, 3, 4}, 2025: {3, 4, 5, 6}})
    assert m["universo_base"] == 4
    assert m["sobreviventes"] == 2
    assert m["mortalidade_pct"] == 50.0
    assert m["curva"]["2025"]["universo_do_ano"] == 4


def test_cobertura_e_mortes_comparam_o_painel_ao_universo_real() -> None:
    """O numero que importa: o painel pegou quem, e viu alguem morrer?"""
    m = medir_mortalidade({2010: {1, 2, 3, 4}, 2025: {3, 4, 5, 6}},
                          painel_por_ano={2010: {3, 4}, 2025: {3, 4}})
    assert m["painel_no_ano_base"] == 2
    assert m["cobertura_pct"] == 50.0
    assert m["mortes_no_painel"] == 0

    vies_menor = medir_mortalidade({2010: {1, 2, 3, 4}, 2025: {3, 4, 5, 6}},
                                   painel_por_ano={2010: {1, 3}, 2025: {3}})
    assert vies_menor["mortes_no_painel"] == 1


def test_um_ano_so_nao_produz_mortalidade() -> None:
    """Sem segundo ponto no tempo nao ha sobrevivencia a medir."""
    with pytest.raises(ValueError):
        medir_mortalidade({2010: {1, 2}})
    with pytest.raises(ValueError):
        medir_mortalidade({2010: {1, 2}, 2025: set()})


def _coorte_ampla_valida() -> dict:
    """Payload sintético com o mesmo contrato completo da medição publicada."""
    return {
        "medido_em": "2026-01-01",
        "ano_base": 2010,
        "ano_final": 2025,
        "universo_base": 9686,
        "sobreviventes": 2899,
        "mortalidade_pct": 70.07,
        "curva": {
            "2010": {"vivas": 9686, "universo_do_ano": 9686,
                     "sobrevivencia_pct": 100.0},
            "2025": {"vivas": 2899, "universo_do_ano": 7619,
                     "sobrevivencia_pct": 29.93},
        },
    }


def _coorte_operacional_valida() -> dict:
    return {
        "medido_em": "2026-01-01", "ano_base": 2010, "ano_final": 2025,
        "universo_base": 2, "sobreviventes": 1, "mortalidade_pct": 50.0,
        "populacao": "operacional", "cobertura_identidade_pct": 100.0,
        "sem_identidade_apurada": 0, "nao_classificados": 0,
        "curva": {
            "2010": {"vivas": 2, "universo_do_ano": 2,
                     "sobrevivencia_pct": 100.0},
            "2025": {"vivas": 1, "universo_do_ano": 1,
                     "sobrevivencia_pct": 50.0},
        },
    }


def _turnover_valido() -> dict:
    hoje = datetime.now(timezone.utc).date()
    return {
        "medido_em": hoje.isoformat(), "safras": 2,
        "primeira_safra": f"{hoje.year - 2}-06-30",
        "ultima_safra": f"{hoje.year - 1}-06-30",
        "empresas_primeira": 2, "empresas_ultima": 2,
        "entradas": 0, "saidas": 0,
    }


def test_sem_coorte_gravada_nao_inventa_numero() -> None:
    assert frase_mortalidade({}) is None
    assert frase_mortalidade({"saidas": 0}) is None
    assert "NÃO VERIFICADO" in frase_mortalidade({"coorte": {"ano_base": 2010}})


def test_frase_traz_o_tamanho_e_a_cobertura() -> None:
    coorte = _coorte_ampla_valida()
    coorte.update({"painel_no_ano_base": 106, "cobertura_pct": 1.09})
    f = frase_mortalidade({"coorte": coorte})
    assert "9.686" in f and "70%" in f and "1,1%" in f
    # a troca de separador nao pode comer a virgula da prosa
    assert "viés, medido" in f


def test_frase_nao_afirma_cobertura_que_nao_foi_medida() -> None:
    """Sem painel comparado, a frase para no tamanho do universo."""
    f = frase_mortalidade({"coorte": _coorte_ampla_valida()})
    assert "O painel cobre" not in f and "70%" in f


def test_medicao_gravada_no_repositorio_tem_a_coorte() -> None:
    """A tela publicada le este arquivo; o indice da SEC nao roda em producao."""
    from core.us_survivorship import carregar_medicao

    med = carregar_medicao()
    if med is None or "coorte" not in med:
        pytest.skip("coorte ainda nao medida neste checkout")
    c = med["coorte"]
    assert c["universo_base"] > c["sobreviventes"] > 0
    assert 0 < c["mortalidade_pct"] < 100
    assert c["cobertura_pct"] < 100  # painel jamais cobriu o universo real


def test_portao_de_deslistadas_cita_o_tamanho_do_vies(monkeypatch) -> None:
    """Reprovar sem o tamanho nao permite decidir; com o tamanho, permite."""
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: {
        **_turnover_valido(),
        "coorte": _coorte_ampla_valida()})
    p = portao()
    assert p.ok is False and "70%" in p.detalhe and "9686" in p.detalhe

    monkeypatch.setattr(us, "carregar_medicao",
                        lambda *a, **k: _turnover_valido())
    assert portao().ok is False  # sem coorte continua reprovando, sem numero


def test_portao_nao_publica_coorte_ampla_impossivel(monkeypatch) -> None:
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    coorte = _coorte_ampla_valida()
    coorte["mortalidade_pct"] = float("inf")
    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: {
        **_turnover_valido(), "coorte": coorte})
    p = portao()
    assert p.ok is None
    assert "NÃO VERIFICADO" in p.detalhe
    assert "inf" not in p.detalhe.lower()


def test_portao_prioriza_coorte_operacional_valida(monkeypatch) -> None:
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: {
        **_turnover_valido(), "coorte": _coorte_ampla_valida(),
        "coorte_operacional": _coorte_operacional_valida()})
    p = portao()
    assert p.ok is False
    assert "50% das 2" in p.detalhe
    assert "70%" not in p.detalhe


def test_portao_nao_faz_fallback_quando_operacional_eh_invalida(monkeypatch) -> None:
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: {
        **_turnover_valido(), "coorte": _coorte_ampla_valida(),
        "coorte_operacional": {}})
    p = portao()
    assert p.ok is None
    assert "NÃO VERIFICADO" in p.detalhe
    assert "70%" not in p.detalhe


@pytest.mark.parametrize("saidas,safras", [
    (True, 16), ("1", 16), (1.0, 16), (-1, 16), (1, True), (1, 1), (1, 0),
])
def test_portao_rejeita_turnover_sem_contrato_auditavel(monkeypatch, saidas, safras) -> None:
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: {
        "saidas": saidas, "safras": safras, "coorte": _coorte_ampla_valida()})
    p = portao()
    assert p.ok is None
    assert "NÃO VERIFICADO" in p.detalhe


def test_portao_rejeita_turnover_parcial(monkeypatch) -> None:
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: {"saidas": 1, "safras": 2})
    p = portao()
    assert p.ok is None
    assert "NÃO VERIFICADO" in p.detalhe


def test_writer_amplo_bloqueia_coorte_fora_do_contrato(monkeypatch, tmp_path) -> None:
    from scripts import medir_mortalidade_us as writer

    monkeypatch.setattr(writer, "ciks_do_ano", lambda ano, *_: {ano})
    monkeypatch.setattr(writer, "painel_por_ano", lambda *_: {})
    monkeypatch.setattr(writer, "medir_mortalidade", lambda *_: {
        "medido_em": "2025-01-01", "ano_base": 2010, "ano_final": 2025,
        "universo_base": 1, "sobreviventes": 1, "mortalidade_pct": 0.0,
        "curva": {"2010": {"vivas": 1, "universo_do_ano": 1,
                              "sobrevivencia_pct": 100.0},
                  "2025": {"vivas": 1, "universo_do_ano": 1,
                              "sobrevivencia_pct": 100.0}},
    })
    gravacoes: list[dict] = []
    monkeypatch.setattr(writer, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))
    monkeypatch.setattr(writer, "carregar_medicao", lambda: {"saidas": 0, "safras": 2})

    assert writer.main(["--anos", "2010", "2025", "--cache", str(tmp_path)]) == 2
    assert gravacoes == []
