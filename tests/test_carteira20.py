"""Registro imutável da Carteira 20 B3 (core/carteira20.py).

Duas famílias de teste:

- a cadeia de hashes, em registros sintéticos: o que a verificação aceita e
  o que ela pega;
- o arquivo publicado: ``HASHES_REGISTRADOS`` fixa, em ordem, o hash de cada
  registro já commitado. Mudar um registro antigo derruba este teste mesmo que
  a cadeia inteira seja recalculada. Registro novo entra acrescentando o hash
  dele no fim da lista, no mesmo PR que o grava.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta

import pytest

from core import carteira20 as c20

#: Um hash por registro de ``data/public/carteira20_registro.jsonl``, na ordem.
#: Só se acrescenta no fim.
HASHES_REGISTRADOS: list[str] = [
    "6230d78028503324f9c2402a48540452ae9a76f29efb82fdc0d1958b0bfc6080",  # 0 protocolo
]

QUANDO = datetime(2026, 10, 25, 12, 0, tzinfo=c20.FUSO_B3)


def _carteira(n=c20.N_ACOES, peso=None):
    peso = peso if peso is not None else round(1 / n, 6)
    return [{"ticker": f"TCK{i:02d}3", "peso_alvo": peso} for i in range(n)]


def _revisao(**muda):
    corpo = {"data_formacao": "2026-09-30", "pregao_execucao": "2026-10-26",
             "carteira": _carteira()}
    corpo.update(muda)
    return corpo


def _cadeia(tmp_path, *extras):
    caminho = tmp_path / "registro.jsonl"
    c20.anexar("protocolo", {"nome": "teste"}, caminho=caminho, registrado_em=QUANDO)
    for i, (tipo, corpo) in enumerate(extras, start=1):
        c20.anexar(tipo, corpo, caminho=caminho, registrado_em=QUANDO + timedelta(minutes=i))
    return caminho


def _regravar(caminho, registros):
    caminho.write_text("".join(c20.canonico(r) + "\n" for r in registros),
                       encoding="utf-8", newline="\n")


# -- Cadeia ---------------------------------------------------------------------

def test_cadeia_valida(tmp_path):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()))
    status = c20.verificar_cadeia(c20.ler(caminho))
    assert status.integra, status.erros
    assert status.n == 2
    regs = c20.ler(caminho)
    assert regs[1]["hash_anterior"] == regs[0]["hash"]
    assert status.ultimo_hash == regs[1]["hash"]


def test_hash_e_estavel_e_nao_depende_da_ordem_das_chaves():
    a = {"b": 1, "a": [1, 2], "hash": "x"}
    b = {"a": [1, 2], "b": 1}
    assert c20.calcular_hash(a) == c20.calcular_hash(b)


def test_nan_e_recusado():
    with pytest.raises(ValueError):
        c20.canonico({"x": float("nan")})


def test_adulterar_um_byte_quebra_o_hash(tmp_path):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()))
    regs = c20.ler(caminho)
    regs[1]["corpo"]["carteira"][0]["ticker"] = "PETR4"
    _regravar(caminho, regs)
    status = c20.verificar_cadeia(c20.ler(caminho))
    assert not status.integra
    assert any("hash não confere" in e for e in status.erros)


def test_recalcular_o_hash_adulterado_quebra_o_elo(tmp_path):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()), ("revisao", _revisao(
        data_formacao="2027-04-30", pregao_execucao="2027-05-03")))
    regs = c20.ler(caminho)
    regs[1]["corpo"]["carteira"][0]["ticker"] = "PETR4"
    regs[1]["hash"] = c20.calcular_hash(regs[1])
    status = c20.verificar_cadeia(regs)
    assert any("seq 2: hash_anterior" in e for e in status.erros)


def test_remover_registro_do_meio_e_detectado(tmp_path):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()), ("revisao", _revisao(
        data_formacao="2027-04-30", pregao_execucao="2027-05-03")))
    regs = c20.ler(caminho)
    status = c20.verificar_cadeia([regs[0], regs[2]])
    assert not status.integra


def test_primeiro_registro_precisa_ser_protocolo(tmp_path):
    caminho = tmp_path / "r.jsonl"
    with pytest.raises(ValueError, match="protocolo"):
        c20.anexar("revisao", _revisao(), caminho=caminho, registrado_em=QUANDO)
    assert not caminho.exists()


def test_segundo_protocolo_e_recusado(tmp_path):
    caminho = _cadeia(tmp_path)
    with pytest.raises(ValueError, match="protocolo novo"):
        c20.anexar("protocolo", {}, caminho=caminho, registrado_em=QUANDO)


def test_anexar_recusa_cadeia_quebrada(tmp_path):
    caminho = _cadeia(tmp_path)
    regs = c20.ler(caminho)
    regs[0]["corpo"]["nome"] = "outro"
    _regravar(caminho, regs)
    antes = caminho.read_bytes()
    with pytest.raises(ValueError, match="cadeia quebrada"):
        c20.anexar("revisao", _revisao(), caminho=caminho, registrado_em=QUANDO)
    assert caminho.read_bytes() == antes


def test_revisao_depois_da_abertura_nao_e_previsao(tmp_path):
    caminho = _cadeia(tmp_path)
    tarde = datetime(2026, 10, 26, 10, 0, tzinfo=c20.FUSO_B3)
    with pytest.raises(ValueError, match="não é previsão"):
        c20.anexar("revisao", _revisao(), caminho=caminho, registrado_em=tarde)
    c20.anexar("revisao", _revisao(), caminho=caminho,
               registrado_em=tarde - timedelta(seconds=1))


def test_horario_em_outro_fuso_e_comparado_no_instante(tmp_path):
    from zoneinfo import ZoneInfo
    caminho = _cadeia(tmp_path)
    # 12:30 UTC = 09:30 em São Paulo: antes da abertura.
    utc = datetime(2026, 10, 26, 12, 30, tzinfo=ZoneInfo("UTC"))
    c20.anexar("revisao", _revisao(), caminho=caminho, registrado_em=utc)


@pytest.mark.parametrize("corpo, trecho", [
    (_revisao(carteira=_carteira(19, 1 / 19)), "20 tickers"),
    (_revisao(carteira=_carteira()[:19] + [_carteira()[0]]), "20 tickers"),
    (_revisao(carteira=_carteira(peso=0.06)), "somam"),
    (_revisao(pregao_execucao="2026-09-30"), "posterior"),
    ({"carteira": _carteira()}, "faltam"),
])
def test_revisao_invalida(tmp_path, corpo, trecho):
    caminho = _cadeia(tmp_path)
    with pytest.raises(ValueError, match=trecho):
        c20.anexar("revisao", corpo, caminho=caminho, registrado_em=QUANDO)


def test_registro_fora_de_ordem_no_tempo(tmp_path):
    caminho = _cadeia(tmp_path)
    with pytest.raises(ValueError, match="antes do registro anterior"):
        c20.anexar("revisao", _revisao(), caminho=caminho,
                   registrado_em=QUANDO - timedelta(hours=1))


def test_registrado_em_sem_fuso_e_recusado():
    with pytest.raises(ValueError, match="fuso"):
        c20.montar_registro("protocolo", {}, [], registrado_em=datetime(2026, 10, 25))


# -- Correções ------------------------------------------------------------------

def test_correcao_substitui_o_corpo_vigente_e_preserva_o_original(tmp_path):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()))
    rev = c20.ler(caminho)[1]
    nova = _revisao(carteira=[{"ticker": f"NOV{i:02d}3", "peso_alvo": 0.05}
                              for i in range(20)])
    c20.anexar("correcao", {"referencia": {"seq": 1, "hash": rev["hash"]},
                            "motivo": "ticker trocado", "substitui": nova},
               caminho=caminho, registrado_em=QUANDO + timedelta(days=5))
    regs = c20.ler(caminho)
    assert c20.verificar_cadeia(regs).integra
    [item] = c20.revisoes(regs)
    assert item["registro"]["hash"] == rev["hash"]
    assert item["corpo"]["carteira"][0]["ticker"] == "NOV003"
    assert len(item["correcoes"]) == 1
    assert c20.vigente(regs) == item


def test_correcao_sem_substituir_so_anota(tmp_path):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()))
    rev = c20.ler(caminho)[1]
    c20.anexar("correcao", {"referencia": {"seq": 1, "hash": rev["hash"]},
                            "motivo": "nota"}, caminho=caminho,
               registrado_em=QUANDO + timedelta(days=1))
    [item] = c20.revisoes(c20.ler(caminho))
    assert item["corpo"] == rev["corpo"]
    assert len(item["correcoes"]) == 1


@pytest.mark.parametrize("corpo, trecho", [
    ({"referencia": {"seq": 9, "hash": "x"}, "motivo": "m"}, "citar um registro"),
    ({"referencia": {"seq": 1, "hash": "errado"}, "motivo": "m"}, "hash diferente"),
    ({"referencia": {"seq": 1}, "motivo": ""}, "sem motivo"),
])
def test_correcao_invalida(tmp_path, corpo, trecho):
    caminho = _cadeia(tmp_path, ("revisao", _revisao()))
    with pytest.raises(ValueError, match=trecho):
        c20.anexar("correcao", corpo, caminho=caminho,
                   registrado_em=QUANDO + timedelta(days=1))


def test_so_revisao_pode_ser_substituida(tmp_path):
    caminho = _cadeia(tmp_path)
    proto = c20.ler(caminho)[0]
    with pytest.raises(ValueError, match="só uma revisão"):
        c20.anexar("correcao", {"referencia": {"seq": 0, "hash": proto["hash"]},
                                "motivo": "m", "substitui": _revisao()},
                   caminho=caminho, registrado_em=QUANDO + timedelta(days=1))


# -- Prefixo --------------------------------------------------------------------

def test_prefixo():
    assert c20.verificar_prefixo("a\nb\n", "a\nb\nc\n") == []
    assert c20.verificar_prefixo("", "a\n") == []
    assert "linha 2" in c20.verificar_prefixo("a\nb\n", "a\nX\nc\n")[0]
    assert "linha 2" in c20.verificar_prefixo("a\nb\n", "a\n")[0]


# -- Calendário -----------------------------------------------------------------

def test_calendario_planejado():
    assert c20.calendario_planejado(2027, 2028) == [
        {"data_formacao": "2027-04-30", "pregao_execucao": "2027-05-03"},
        {"data_formacao": "2027-10-29", "pregao_execucao": "2027-11-01"},
        {"data_formacao": "2028-04-28", "pregao_execucao": "2028-05-02"},
        {"data_formacao": "2028-10-31", "pregao_execucao": "2028-11-01"},
    ]


def test_pregao():
    assert c20.e_pregao(date(2026, 10, 26))
    assert not c20.e_pregao(date(2026, 10, 25))      # domingo
    assert not c20.e_pregao(date(2027, 5, 1))        # Dia do Trabalho
    assert c20.proximo_pregao(date(2026, 10, 23)) == date(2026, 10, 26)


# -- O arquivo publicado --------------------------------------------------------

def test_registro_publicado_integro():
    regs = c20.ler()
    status = c20.verificar_cadeia(regs)
    assert status.integra, status.erros


def test_registro_publicado_bate_com_os_hashes_fixados():
    """Cada registro tem o hash fixado, na ordem; nada antigo mudou ou sumiu."""
    hashes = [r["hash"] for r in c20.ler()]
    assert hashes[:len(HASHES_REGISTRADOS)] == HASHES_REGISTRADOS, \
        "um registro já publicado mudou ou sumiu"
    assert hashes == HASHES_REGISTRADOS, \
        "registro novo sem hash fixado: acrescente-o ao fim de HASHES_REGISTRADOS"


def test_registro_publicado_e_linha_canonica():
    """Cada linha é a forma canônica do registro, para o hash do arquivo ser
    reprodutível por quem o reler."""
    for linha in c20.ler_texto().splitlines():
        assert linha == c20.canonico(json.loads(linha))


def test_main_e_prefixo_do_registro_atual():
    """O que está na main só cresce. Sem git ou sem a main local, pula."""
    caminho = c20.REGISTRO.relative_to(c20.RAIZ).as_posix()
    try:
        r = subprocess.run(["git", "show", f"origin/main:{caminho}"], cwd=c20.RAIZ,
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git indisponível")
    if r.returncode != 0:
        pytest.skip("registro ainda não está na origin/main (ou ela não foi buscada)")
    publicado = r.stdout.decode("utf-8").replace("\r\n", "\n")
    assert c20.verificar_prefixo(publicado, c20.ler_texto()) == []
