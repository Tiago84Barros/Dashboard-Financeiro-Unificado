"""Teste do desconto de liderança da B3 ligado e desligado (sem armazém)."""
from __future__ import annotations

import types
from pathlib import Path

import scripts.medir_decaimento_lideranca_b3 as md
import scripts.medir_nota_fatores_b3 as nfs


def test_sem_desconto_e_identidade_e_nao_muta():
    scores = {"A3": 1.0, "B3": 0.5}
    out = nfs._sem_desconto(scores, {"A3": 4})
    assert out == scores and out is not scores


def test_desconto_ligado_de_fato_rebaixa_o_lider_antigo():
    # O controle: a função que a aba usa rebaixa quem lidera há anos.
    from views.empresas_b3 import _apply_decay_penalty
    out = _apply_decay_penalty({"A3": 1.0, "B3": 1.0}, {"A3": 3})
    assert out["A3"] < out["B3"]


def test_cache_sem_desconto_e_separado():
    base = nfs.caminho_do_cache(Path("c"), "Equilibrado")
    sem = nfs.caminho_do_cache(Path("c"), "Equilibrado", sem_decaimento=True)
    assert base != sem and sem.name.endswith("_sem_decaimento.pkl")
    assert nfs.caminho_do_cache(None, "Equilibrado") is None


def test_rodada_sem_desconto_troca_so_durante_a_rodada(tmp_path):
    original = lambda s, a, **k: {t: v * 0.5 for t, v in s.items()}  # noqa: E731
    view = types.SimpleNamespace(_apply_decay_penalty=original)
    vista = []

    def rodar(nome, valores):
        vista.append(view._apply_decay_penalty({"A3": 1.0}, {"A3": 5}))
        return ("r", "p", "par", "d")

    M = types.SimpleNamespace(_rodar_perfil=rodar)
    rodada = nfs.rodada_da_aba(M, view, "Equilibrado", {}, tmp_path, sem_decaimento=True)
    assert rodada == ("r", "p", "par", "d")
    assert vista == [{"A3": 1.0}]                  # desligado durante a rodada
    assert view._apply_decay_penalty is original   # restaurado depois
    # Segunda chamada lê o pickle próprio, sem rodar a aba de novo.
    nfs.rodada_da_aba(M, view, "Equilibrado", {}, tmp_path, sem_decaimento=True)
    assert len(vista) == 1
    # O controle não lê o pickle sem desconto.
    nfs.rodada_da_aba(M, view, "Equilibrado", {}, tmp_path)
    assert vista[-1] == {"A3": 0.5}


def test_rodada_restaura_o_desconto_mesmo_com_erro(tmp_path):
    original = object()
    view = types.SimpleNamespace(_apply_decay_penalty=original)

    def quebra(nome, valores):
        raise RuntimeError("aba caiu")

    M = types.SimpleNamespace(_rodar_perfil=quebra)
    try:
        nfs.rodada_da_aba(M, view, "Equilibrado", {}, None, sem_decaimento=True)
    except RuntimeError:
        pass
    assert view._apply_decay_penalty is original


def test_decidir_exige_media_e_ic_acima_de_zero():
    assert md.decidir({"media": 0.02, "ic95": [0.001, 0.04]})
    assert not md.decidir({"media": 0.02, "ic95": [-0.01, 0.04]})
    assert not md.decidir({"media": -0.01, "ic95": [0.001, 0.04]})
    assert not md.decidir({"media": None, "ic95": [None, None]})


def test_lado_a_lado_junta_as_safras_das_duas():
    out = md.lado_a_lado([{"safra": 2018, "giro": 0.6, "liquido": 0.1}],
                         [{"safra": 2018, "giro": 0.2}, {"safra": 2019, "giro": 0.1}])
    assert out["2018"]["com_desconto"]["giro"] == 0.6
    assert out["2018"]["sem_desconto"]["giro"] == 0.2
    assert out["2019"]["com_desconto"]["giro"] is None


def test_a_aba_chama_o_desconto_pelo_nome_que_a_medicao_troca():
    # Se a aba passar a chamar o desconto por outro caminho (ex.:
    # `_pps.apply_decay_penalty`), a rodada "sem desconto" sairia igual ao
    # controle sem aviso. As duas chamadas (laço anual e carteira atual) têm de
    # ler o nome global do módulo.
    import inspect

    import views.portfolio_b3 as view
    fonte = inspect.getsource(view._processar_segmento)
    assert fonte.count("_apply_decay_penalty(") == 2
    assert "apply_decay_penalty(" not in fonte.replace("_apply_decay_penalty(", "")
    assert hasattr(view, "_apply_decay_penalty")
