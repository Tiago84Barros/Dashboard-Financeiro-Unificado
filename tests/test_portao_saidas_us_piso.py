"""Uma saída no painel não corrige viés de sobrevivência.

O portão "Universo de deslistadas" aprovava com `if no_painel:` — qualquer
número acima de zero. O critério ficou invisível enquanto `market_us.delistings`
só existia no armazém local: em produção a consulta falhava e o portão devolvia
"não apurado". Em 31/08/2026 a tabela foi publicada na vitrine e o portão passou
a APROVAR com 7 saídas de 11.793 (0,06%), declarando corrigido um viés que a
medição não corrigiu.

A assimetria que produz esse número não é acidente de publicação: 1.882 das
1.889 saídas com símbolo resolvido não têm linha em `score_vintages`, porque as
safras são construídas a partir do universo vivo. Registrar a saída é barato;
o que corrige o viés é o painel consumi-la.
"""
from __future__ import annotations

import core.validacao_motor as vm


def _portao(total: int, no_painel: int, monkeypatch):
    monkeypatch.setattr(vm, "_registro_de_saidas_us",
                        lambda engine=None: (total, no_painel))
    return vm._deslistadas_us()


def test_uma_saida_em_onze_mil_nao_aprova(monkeypatch):
    """O caso real do dia da publicação."""
    p = _portao(11793, 7, monkeypatch)
    assert p.ok is False
    assert "0.1%" in p.detalhe or "0,1%" in p.detalhe
    assert "11793" in p.detalhe


def test_reprovar_por_fracao_nao_esconde_o_numerador(monkeypatch):
    """Quem lê precisa saber que ALGUMA saída chega — 7 é diferente de 0."""
    p = _portao(11793, 7, monkeypatch)
    assert "7" in p.detalhe
    assert "nenhuma" not in p.detalhe


def test_nenhuma_saida_continua_com_a_mensagem_propria(monkeypatch):
    p = _portao(11793, 0, monkeypatch)
    assert p.ok is False
    assert "nenhuma" in p.detalhe


def test_painel_que_consome_o_registro_aprova(monkeypatch):
    p = _portao(1000, 300, monkeypatch)
    assert p.ok is True
    assert "30%" in p.detalhe


def test_registro_vazio_nao_vira_reprovacao_por_fracao(monkeypatch):
    """Sem registro nenhum a pergunta é outra — e divisão por zero não é nota."""
    monkeypatch.setattr(vm, "_registro_de_saidas_us", lambda engine=None: (0, 0))
    monkeypatch.setattr(vm, "_deslistadas_us_pelo_painel",
                        lambda: vm.Portao("Universo de deslistadas", None,
                                          "nao apurado",
                                          dimensao=vm.DIM_SAIDAS))

    class _Morta:
        def connect(self):
            raise RuntimeError("sem banco")
    assert vm._deslistadas_us(_Morta()).ok is None
