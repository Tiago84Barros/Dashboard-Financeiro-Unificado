"""A medição de vantagem não pode ficar para trás da versão que o motor roda.

Em 06/09/2026 a tela de Grau de Confiança mostrava Empresas Americanas com 50%
de rigor contra 33% de Empresas B3 e Seleção de FIIs. Os EUA não tinham vencido
nada a mais: tinham **parado de responder uma pergunta**. A medição gravada em
``data/vantagem_oos.json`` era da metodologia 0.5.0 e o motor já rodava 0.8.0,
então o portão devolvia "não apurado" -- corretamente, porque resultado de outra
versão não atesta esta -- e "não apurado" sai do denominador da nota.

Remedida na 0.8.0 (``scripts/medir_vantagem_oos.py --motor us``), a vantagem foi
reprovada (IC 95% do excesso: -11,77% a +10,91% em 16 períodos) e os EUA caíram
para os mesmos 33%. O número honesto era o menor.

O portão não tem defeito. O que faltava era alguém perceber que a medição
envelheceu, e ninguém percebe uma nota que sobe
(``memoria: quem-pergunta-menos-tira-nota-maior``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.vantagem_oos import CAMINHO_MEDICAO, medicoes_vencidas, versao_corrente

MOTORES = ("b3", "fii", "us")


def test_nenhuma_medicao_gravada_ficou_para_tras():
    vencidas = medicoes_vencidas()
    assert not vencidas, (
        "medição de vantagem fora da amostra desatualizada: "
        + "; ".join(f"{motor} foi medido na metodologia {medida} mas o motor "
                    f"roda {atual}" for motor, (medida, atual) in sorted(vencidas.items()))
        + ". Enquanto durar, o portão sai do denominador e a nota de rigor "
          "SOBE por ter deixado de responder. Remedir: "
          "py -3.12 scripts/medir_vantagem_oos.py --motor <motor>")


@pytest.mark.parametrize("motor", MOTORES)
def test_versao_corrente_le_a_constante_que_o_motor_usa(motor):
    """Derivada do módulo do motor, não copiada para cá.

    Cópia da versão dentro do teste passaria a valer sozinha no dia em que a
    constante mudasse, e o teste diria "em dia" comparando a medição velha com
    uma expectativa igualmente velha.
    """
    versao = versao_corrente(motor)
    assert versao and versao[0].isdigit(), f"{motor}: versão implausível {versao!r}"


def test_motor_desconhecido_nao_vira_em_dia_silenciosamente():
    with pytest.raises(ValueError):
        versao_corrente("cripto")


def test_medicao_de_motor_desconhecido_no_arquivo_nao_derruba_a_checagem(tmp_path):
    """Arquivo com chave estranha é dado ruim, não motivo para parar de checar."""
    caminho = tmp_path / "vantagem.json"
    caminho.write_text(json.dumps({
        "cripto": {"versao_metodologia": "9.9.9"},
        "us": {"versao_metodologia": "0.0.1"},
    }), encoding="utf-8")
    vencidas = medicoes_vencidas(caminho)
    assert "cripto" not in vencidas
    assert vencidas["us"][0] == "0.0.1"


def test_arquivo_ausente_nao_e_medicao_vencida(tmp_path):
    """Nunca medido e medido-e-envelhecido são estados diferentes.

    Só o segundo é regressão. Tratar o primeiro como falha cobraria medição de
    quem ainda não tem armazém local para rodá-la.
    """
    assert medicoes_vencidas(tmp_path / "nao_existe.json") == {}


def test_o_arquivo_de_producao_existe_e_e_json_de_objeto():
    """Se o arquivo sumir ou virar lista, a checagem acima passa a não checar nada."""
    dados = json.loads(Path(CAMINHO_MEDICAO).read_text(encoding="utf-8"))
    assert isinstance(dados, dict) and dados, "data/vantagem_oos.json vazio ou não-objeto"
