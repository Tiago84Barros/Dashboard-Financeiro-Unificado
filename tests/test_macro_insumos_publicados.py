"""Insumos macro publicados: o arquivo responde o que o Docker responderia.

O que se prende:

* o mesmo insumo dá o mesmo snapshot pelas duas fontes -- impacto E
  ``snapshot_id``, que a carteira salva guarda para se reconhecer;
* o arquivo recusa o que não sabe responder (data anterior à publicação,
  modo ``reconstructed``) em vez de vazar o futuro;
* vencido, ilegível ou ausente vira ``None``, que as telas já tratam;
* ``get_macro_source`` prefere o Docker que responde e cai no arquivo;
* o publicador não renova a data sobre coleta parada.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.macro_data import database as macro_db
from core.macro_data import insumos_publicados as ip
from core.macro_data.portfolio_context import load_portfolio_macro_snapshot

# O conftest troca o carregador por um que devolve None; este é o original.
_carregar = ip.carregar_insumos_publicados

_PUBLICADO = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)


def _observacoes():
    linhas = []
    for i in range(24):
        ano, mes = divmod(2024 * 12 + 9 + i - 1, 12)
        periodo = date(ano, mes + 1, 1)
        linhas.append({
            "provider": "fred", "provider_code": "DCOILWTICO", "country_code": None,
            "category": "commodities", "frequency": "monthly", "unit": "USD",
            "source_url": "https://exemplo", "reference_period": periodo,
            "value": Decimal("60") + Decimal(i) * Decimal("1.5"),
            "retrieved_at": datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
            "released_at": None, "is_preliminary": False, "is_forecast": False,
            "vintage_date": None, "version_position": 1, "position": 24 - i,
        })
    return linhas


_EXPOSICOES = [
    {"asset_class": "b3", "sector": "Petróleo", "factor": "commodities",
     "sensitivity": Decimal("0.8"), "confidence": Decimal("0.6"), "channel": "receita"},
    {"asset_class": "us", "sector": "Energy", "factor": "commodities",
     "sensitivity": Decimal("0.5"), "confidence": Decimal("0.5"), "channel": "receita"},
]


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return self

    def all(self):
        return [dict(x) for x in self._linhas]


class _Conexao:
    def __init__(self, respostas):
        self._respostas = list(respostas)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return _Resultado(self._respostas.pop(0))


class _EngineFalso:
    """Responde as duas consultas do cálculo, na ordem: exposições, observações."""

    def __init__(self, asset_class):
        self._exp = [{k: v for k, v in e.items() if k != "asset_class"}
                     for e in _EXPOSICOES if e["asset_class"] == asset_class]

    def connect(self):
        return _Conexao([self._exp, _observacoes()])


def _arquivo():
    return ip.desserializar(ip.serializar(_PUBLICADO, _EXPOSICOES, _observacoes()))


@pytest.mark.parametrize("classe,setor", [("b3", "Petróleo"), ("us", "Energy")])
def test_arquivo_e_banco_dao_o_mesmo_snapshot(classe, setor):
    as_of = _PUBLICADO + timedelta(hours=5)
    ativos = {"AAA": setor, "BBB": "Setor sem exposição"}
    do_banco = load_portfolio_macro_snapshot(_EngineFalso(classe), asset_class=classe,
                                             assets=ativos, as_of=as_of)
    do_arquivo = load_portfolio_macro_snapshot(_arquivo(), asset_class=classe,
                                               assets=ativos, as_of=as_of)
    assert do_banco.impacts, "caso sintético não gerou impacto: o teste não prova nada"
    assert do_arquivo.impacts == do_banco.impacts
    # Decimal virando float mudava o id com os mesmos números.
    assert do_arquivo.snapshot_id == do_banco.snapshot_id


def test_serializacao_e_deterministica_e_preserva_tipos():
    a = ip.serializar(_PUBLICADO, _EXPOSICOES, _observacoes())
    assert a == ip.serializar(_PUBLICADO, _EXPOSICOES, _observacoes())
    ins = ip.desserializar(a)
    obs = ins.observacoes[0]
    assert isinstance(obs["value"], Decimal)
    assert isinstance(obs["reference_period"], date)
    assert obs["retrieved_at"].tzinfo is not None
    assert isinstance(ins.exposicoes[0]["sensitivity"], Decimal)


def test_arquivo_recusa_o_que_nao_sabe_responder():
    ins = _arquivo()
    with pytest.raises(ValueError, match="strict"):
        ins.ler(asset_class="b3", sectors=["Petróleo"], as_of=_PUBLICADO,
                knowledge_mode="reconstructed")
    with pytest.raises(ValueError, match="anterior"):
        ins.ler(asset_class="b3", sectors=["Petróleo"],
                as_of=_PUBLICADO - timedelta(days=1), knowledge_mode="strict")


def test_arquivo_filtra_classe_e_setor():
    exp, obs = _arquivo().ler(asset_class="us", sectors=["Energy"], as_of=_PUBLICADO,
                              knowledge_mode="strict")
    assert [e["sector"] for e in exp] == ["Energy"]
    assert len(obs) == 24


def test_carregar_vence_le_e_falha_calado(tmp_path):
    caminho = tmp_path / "m.json.gz"
    assert _carregar(caminho) is None  # ausente
    caminho.write_bytes(ip.serializar(_PUBLICADO, _EXPOSICOES, _observacoes()))
    assert _carregar(caminho, agora=_PUBLICADO + timedelta(days=2))
    vencido = _PUBLICADO + timedelta(days=ip.IDADE_MAXIMA_DIAS + 1)
    assert _carregar(caminho, agora=vencido) is None

    ruim = tmp_path / "r.json.gz"
    ruim.write_bytes(gzip.compress(json.dumps({"schema": "outro"}).encode()))
    assert _carregar(ruim) is None
    lixo = tmp_path / "l.json.gz"
    lixo.write_bytes(b"nao e gzip")
    assert _carregar(lixo) is None


class _EngineQueCai:
    disposed = False

    def connect(self):
        raise OSError("connection refused")

    def dispose(self):
        self.disposed = True


class _EngineQueResponde:
    def connect(self):
        return _Conexao([[]])


def test_fonte_prefere_docker_que_responde(monkeypatch):
    engine = _EngineQueResponde()
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: engine)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda *a, **k: "arquivo")
    assert macro_db.get_macro_source() is engine
    assert macro_db.descrever_fonte_macro(engine) == "Macro Docker local"


def test_fonte_cai_no_arquivo_quando_docker_nao_responde(monkeypatch):
    engine = _EngineQueCai()
    arquivo = _arquivo()
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: engine)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda *a, **k: arquivo)
    assert macro_db.get_macro_source() is arquivo
    assert engine.disposed
    assert macro_db.descrever_fonte_macro(arquivo) == "Macro publicado em 26/09/2026"


def test_fonte_sem_docker_nem_arquivo_e_none(monkeypatch):
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: None)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda *a, **k: None)
    assert macro_db.get_macro_source() is None


def test_publicador_recusa_coleta_parada(monkeypatch, tmp_path, capsys):
    from scripts import publish_macro_insumos as pub

    velhas = _observacoes()
    for o in velhas:
        o["retrieved_at"] = datetime.now(timezone.utc) - timedelta(days=pub.COLETA_MAXIMA_DIAS + 2)
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: object())
    monkeypatch.setattr(pub, "coletar", lambda engine, agora: (list(_EXPOSICOES), velhas))
    saida = tmp_path / "m.json.gz"
    assert pub.main(["--saida", str(saida)]) == 1
    assert not saida.exists()
    assert "coleta macro parada" in capsys.readouterr().out


def test_publicador_grava_coleta_fresca(monkeypatch, tmp_path):
    from scripts import publish_macro_insumos as pub

    frescas = _observacoes()
    for o in frescas:
        o["retrieved_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: object())
    monkeypatch.setattr(pub, "coletar", lambda engine, agora: (list(_EXPOSICOES), frescas))
    saida = tmp_path / "m.json.gz"
    assert pub.main(["--saida", str(saida)]) == 0
    ins = ip.desserializar(saida.read_bytes())
    assert {e["asset_class"] for e in ins.exposicoes} == {"b3", "us"}


def test_agenda_coleta_antes_de_publicar_e_commita_o_arquivo():
    from core.publicacao_agenda import POR_CHAVE

    alvo = POR_CHAVE["macro_insumos"]
    assert alvo.passos[-1] == ("scripts/publish_macro_insumos.py",)
    assert ("run_macro_updates.py",) in alvo.passos[:-1]
    assert alvo.artefatos == ("data/public/macro_insumos.json.gz",)
    assert ip.CAMINHO_PADRAO.as_posix().endswith(alvo.artefatos[0])
