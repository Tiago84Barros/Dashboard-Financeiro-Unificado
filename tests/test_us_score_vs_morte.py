"""O score protege contra perda permanente de capital? (A-158)

O backtest americano é 100% sobrevivente, e o retorno futuro das empresas
mortas não existe em nenhuma fonte nossa -- o yfinance não serve deslistada e
chega a devolver a série de OUTRO papel que herdou o ticker. O que sobra, e é o
que mais importa ao investidor, é um desfecho observável sem cotação: a empresa
sumiu ou não.

O teste central destes testes é o erro que a primeira versão do experimento
cometeu: chamar de "morte" toda saída, inclusive aquisição. Empresa boa é
comprada com prêmio; contar isso como morte inverte a conclusão -- e inverteu:
as "mortas" pontuavam 56,3 contra 46,8 das sobreviventes.
"""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.testar_score_prediz_morte_us import (
    ADQUIRIDA,
    INDEFINIDO,
    SOBREVIVEU,
    SUMIU,
    _auc,
    _bloco,
    _itens,
    classificar_saida,
)


def _linhas(pares) -> pd.DataFrame:
    """pares: [(score, desfecho), ...]"""
    return pd.DataFrame([{"score": s, "desfecho": d} for s, d in pares])


def test_proxy_de_fusao_marca_aquisicao() -> None:
    for forma in ("DEFM14A", "PREM14A", "DEFM14C", "SC 13E3"):
        assert classificar_saida({"formas": ["10-K", forma, "8-K"]}) == ADQUIRIDA


def test_parar_de_arquivar_sem_marca_nao_e_morte() -> None:
    """Desregistro silencioso não é falência, e chamá-lo assim inverteu a medição.

    Numa sondagem de 60 saídas da coorte de 2012, só 3 tinham 8-K de falência
    contra 34 com item 2.01. Empurrar o resto para o grupo ruim não é erro
    conservador: é o grupo majoritário afogando o que se quer medir.
    """
    assert classificar_saida({"formas": ["10-K", "8-K", "15-12B", "25"]}) == INDEFINIDO


def test_s4_sozinho_nao_prova_que_a_empresa_foi_comprada() -> None:
    """Quem emite S-4 costuma ser o COMPRADOR; aceitá-lo esvaziaria o grupo ruim."""
    assert classificar_saida({"formas": ["10-K", "S-4", "S-4/A"]}) == INDEFINIDO


def test_sem_submissions_nao_ha_desfecho() -> None:
    assert classificar_saida({}) == INDEFINIDO
    assert classificar_saida({"formas": []}) == INDEFINIDO


def test_falencia_vem_do_item_do_8k_e_nao_do_tipo() -> None:
    """`8-K` cobre de troca de auditor a falência; quem discrimina é o item."""
    assert classificar_saida({"formas": ["10-K", "8-K"],
                              "itens_todos": ["1.03", "2.01"]}) == SUMIU


def test_venda_de_ativo_dentro_da_falencia_continua_falencia() -> None:
    """Recuperação judicial também arquiva 2.01; a ordem de checagem importa.

    Se a aquisição vencesse, o único desfecho que o investidor precisa ver
    sairia rotulado como bom desfecho.
    """
    assert classificar_saida({"formas": ["SC 13E3"],
                              "itens_todos": ["1.03"],
                              "itens_finais": ["2.01"]}) == SUMIU


def test_aquisicao_concluida_no_fim_da_historia_e_aquisicao() -> None:
    assert classificar_saida({"formas": ["10-K", "8-K"],
                              "itens_todos": ["2.01"],
                              "itens_finais": ["2.01"]}) == ADQUIRIDA


def test_compra_no_curso_normal_da_vida_nao_e_a_propria_empresa() -> None:
    """2.01 anos antes do fim é a empresa comprando algo, não sendo comprada."""
    assert classificar_saida({"formas": ["10-K", "8-K"],
                              "itens_todos": ["2.01"],
                              "itens_finais": ["5.02"]}) == INDEFINIDO


def test_oferta_hostil_conta_pelo_lado_de_quem_responde() -> None:
    assert classificar_saida({"formas": ["10-K", "SC 14D9"]}) == ADQUIRIDA


def test_itens_separam_o_fim_da_historia_do_resto() -> None:
    recentes = {"filingDate": ["2011-05-02", "2013-02-01", "2013-06-30"],
                "form": ["8-K", "8-K", "8-K"],
                "items": ["2.01", "5.02,8.01", "2.01,3.01"]}
    out = _itens(recentes)
    assert out["ultimo_arquivamento"] == "2013-06-30"
    assert set(out["itens_todos"]) == {"2.01", "5.02", "8.01", "3.01"}
    assert set(out["itens_finais"]) == {"5.02", "8.01", "2.01", "3.01"}
    assert "2011-05-02" not in out["itens_finais"]


def test_itens_mais_curto_que_datas_nao_desalinha() -> None:
    """A SEC devolve arrays paralelos; zipar sem preencher deslocaria as datas."""
    out = _itens({"filingDate": ["2012-01-01", "2013-01-01"],
                  "form": ["10-K", "8-K"], "items": ["1.03"]})
    assert out["itens_todos"] == ["1.03"]


def test_auc_meio_quando_o_score_nao_separa() -> None:
    d = _linhas([(50, SOBREVIVEU), (50, SUMIU), (50, ADQUIRIDA), (50, SUMIU)])
    d["nao_sumiu"] = d["desfecho"] != SUMIU
    assert _auc(d, "nao_sumiu") == 0.5


def test_auc_um_quando_separa_perfeitamente() -> None:
    d = _linhas([(90, SOBREVIVEU), (80, ADQUIRIDA), (20, SUMIU), (10, SUMIU)])
    d["nao_sumiu"] = d["desfecho"] != SUMIU
    assert _auc(d, "nao_sumiu") == 1.0


def test_auc_abaixo_de_meio_denuncia_sinal_invertido() -> None:
    """Score alto para quem sumiu é pior que score inútil, e tem de aparecer."""
    d = _linhas([(10, SOBREVIVEU), (20, ADQUIRIDA), (80, SUMIU), (90, SUMIU)])
    d["nao_sumiu"] = d["desfecho"] != SUMIU
    assert _auc(d, "nao_sumiu") == 0.0


def test_auc_sem_um_dos_grupos_nao_inventa_numero() -> None:
    d = _linhas([(90, SOBREVIVEU), (80, SOBREVIVEU)])
    d["nao_sumiu"] = d["desfecho"] != SUMIU
    assert _auc(d, "nao_sumiu") is None


def test_aquisicao_nao_conta_como_desaparecimento() -> None:
    """O erro que este experimento existe para não repetir.

    Se adquirida entrasse no grupo ruim, este cross-section -- em que as
    adquiridas são justamente as de score mais alto -- devolveria AUC baixa e a
    conclusão sairia invertida.
    """
    # Adquiridas no topo e em maioria: e a assinatura real da coorte de 2012,
    # onde metade das saidas tinha proxy de fusao.
    pares = ([(200 - i, ADQUIRIDA) for i in range(45)]
             + [(120 - i, SOBREVIVEU) for i in range(30)]
             + [(60 - i, SUMIU) for i in range(30)])
    b = _bloco(_linhas(pares))
    assert b["adquirida"] == 45 and b["sumiu"] == 30
    assert b["auc_nao_sumiu"] == 1.0

    errado = _linhas([(s, SUMIU if d == ADQUIRIDA else d) for s, d in pares])
    errado["nao_sumiu"] = errado["desfecho"] != SUMIU
    assert _auc(errado, "nao_sumiu") < 0.5


def test_bloco_recusa_amostra_pequena_em_vez_de_estimar() -> None:
    b = _bloco(_linhas([(50, SUMIU), (60, SOBREVIVEU)]))
    assert b["insuficiente"] is True and "auc_nao_sumiu" not in b


def test_bloco_recusa_quando_ha_poucas_mortes_mesmo_com_amostra_grande() -> None:
    """200 empresas e 5 falências não medem nada: o raro domina o erro.

    Sem este piso a AUC sairia com aparência de resultado sólido -- amostra
    grande no numerador da frase, evento raro no denominador da estimativa.
    """
    pares = ([(float(i), SOBREVIVEU) for i in range(200)]
             + [(float(i), SUMIU) for i in range(5)])
    assert _bloco(_linhas(pares))["insuficiente"] is True


def test_indefinido_sai_da_comparacao_mas_e_contado() -> None:
    """Excluir sem dizer quanto foi excluído esconde o tamanho da ignorância."""
    pares = ([(float(i), SOBREVIVEU) for i in range(60)]
             + [(float(i), SUMIU) for i in range(40)]
             + [(float(i), INDEFINIDO) for i in range(300)])
    b = _bloco(_linhas(pares))
    assert b["empresas"] == 100 and b["indefinido"] == 300
    assert b["sobreviveu"] + b["adquirida"] + b["sumiu"] == b["empresas"]


def test_decis_somam_a_amostra_inteira() -> None:
    pares = [(float(i), SUMIU if i % 3 == 0 else SOBREVIVEU) for i in range(120)]
    b = _bloco(_linhas(pares))
    assert sum(d["empresas"] for d in b["taxa_sumiu_por_decil"]) == b["empresas"]
    assert b["sobreviveu"] + b["adquirida"] + b["sumiu"] == b["empresas"]


def test_desfecho_e_colado_por_simbolo_e_nao_por_posicao() -> None:
    """O defeito que inverteu a medição inteira, e que não dava erro nenhum.

    `score_cross_section` devolve o quadro ordenado por nota. Colar o desfecho
    por posição não falha, não deixa NaN e não muda o tamanho -- só troca as
    etiquetas entre empresas. Aqui a entrada está em ordem inversa à nota, de
    modo que a versão posicional daria exatamente o resultado invertido.
    """
    import pandas as pd

    from core.us_score import score_cross_section
    from scripts.testar_score_prediz_morte_us import juntar_desfecho

    marcado = pd.DataFrame([
        {"symbol": "RUIM", "industry": "35", "sector": "x", "net_margin": -0.4,
         "roe": -0.5, "roa": -0.3, "desfecho": SUMIU},
        {"symbol": "MEIO", "industry": "35", "sector": "x", "net_margin": 0.05,
         "roe": 0.08, "roa": 0.03, "desfecho": ADQUIRIDA},
        {"symbol": "BOA", "industry": "35", "sector": "x", "net_margin": 0.25,
         "roe": 0.30, "roa": 0.12, "desfecho": SOBREVIVEU},
    ])
    scored = score_cross_section(marcado)
    assert list(scored["symbol"]) == ["BOA", "MEIO", "RUIM"], "motor ordena por nota"

    juntado = juntar_desfecho(scored, marcado)
    por_simbolo = dict(zip(juntado["symbol"], juntado["desfecho"]))
    assert por_simbolo == {"BOA": SOBREVIVEU, "MEIO": ADQUIRIDA, "RUIM": SUMIU}
    # A versao posicional teria dado o oposto -- e e o que estava rodando.
    assert list(marcado["desfecho"]) != list(juntado["desfecho"])


def test_juncao_recusa_simbolo_ausente_em_vez_de_deixar_buraco() -> None:
    import pandas as pd

    from scripts.testar_score_prediz_morte_us import juntar_desfecho

    scored = pd.DataFrame({"symbol": ["A", "B"], "score": [10.0, 5.0]})
    marcado = pd.DataFrame({"symbol": ["A"], "desfecho": [SUMIU]})
    with pytest.raises(ValueError):
        juntar_desfecho(scored, marcado)


def test_resultado_gravado_e_legivel_quando_existe() -> None:
    """A conclusão vira texto na tela; texto sem medição não pode ser afirmado."""
    import json
    from pathlib import Path

    alvo = Path(__file__).resolve().parents[1] / "data" / "us_score_vs_morte.json"
    if not alvo.exists():
        pytest.skip("experimento ainda nao rodado neste checkout")
    res = json.loads(alvo.read_text(encoding="utf-8"))
    for bloco in ("coorte_inteira", "apenas_exibiveis"):
        b = res[bloco]
        if b.get("insuficiente"):
            continue
        assert b["sobreviveu"] + b["adquirida"] + b["sumiu"] == b["empresas"]
        assert 0.0 <= b["auc_nao_sumiu"] <= 1.0


def test_frase_confessa_quando_o_score_nao_separa() -> None:
    """Frase que só sabe elogiar não é medição.

    AUC perto de 0,50 significa que o ranking exibido não protege contra perda
    permanente de capital, e isso tem de sair com a mesma clareza de um
    resultado bom -- é a informação que muda a decisão do usuário.
    """
    from core.us_survivorship import frase_score_vs_morte

    base = {"ano_coorte": 2012, "ano_desfecho": 2025}
    fraco = frase_score_vs_morte(
        {**base, "apenas_exibiveis": {"auc_nao_sumiu": 0.51, "empresas": 300,
                                      "sumiu": 40}})
    forte = frase_score_vs_morte(
        {**base, "apenas_exibiveis": {"auc_nao_sumiu": 0.72, "empresas": 300,
                                      "sumiu": 40}})
    assert "não protege" in fraco and "sinal real" in forte
    assert "50%" in fraco and "50%" in forte


def test_frase_nao_afirma_nada_sem_medicao() -> None:
    from core.us_survivorship import frase_score_vs_morte

    assert frase_score_vs_morte({}) is None
    assert frase_score_vs_morte({"apenas_exibiveis": {"insuficiente": True}}) is None
    assert frase_score_vs_morte({"apenas_exibiveis": {"empresas": 90}}) is None


def test_frase_usa_o_recorte_exibivel_e_nao_a_coorte_inteira() -> None:
    """A coorte inteira é dominada por arquivador minúsculo que a app não mostra.

    Medir nela responde uma pergunta que o usuário não faz; se a frase lesse o
    bloco errado, o número exibido seria de outra população.
    """
    from core.us_survivorship import frase_score_vs_morte

    f = frase_score_vs_morte({
        "ano_coorte": 2012, "ano_desfecho": 2025,
        "coorte_inteira": {"auc_nao_sumiu": 0.99, "empresas": 9999, "sumiu": 1},
        "apenas_exibiveis": {"auc_nao_sumiu": 0.60, "empresas": 250, "sumiu": 33}})
    assert "250 empresas" in f and "9999" not in f


def test_frase_publica_quantas_saidas_ficaram_de_fora() -> None:
    """Excluir o indefinido é correto; omitir o tamanho dele não é.

    Sem esse número o leitor supõe que as 250 empresas cobrem a safra inteira,
    quando a maior parte das saídas não pôde ser classificada.
    """
    from core.us_survivorship import frase_score_vs_morte

    f = frase_score_vs_morte({
        "ano_coorte": 2012, "ano_desfecho": 2025,
        "apenas_exibiveis": {"auc_nao_sumiu": 0.73, "empresas": 250,
                             "sumiu": 33, "indefinido": 412}})
    assert "412" in f and "fora da conta" in f
