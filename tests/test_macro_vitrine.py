"""A camada macro existe fisicamente e não atravessava para o app publicado.

As telas respondiam a isso com *"Camada macro do Docker local indisponível"* --
correto sobre o sintoma, mudo sobre a causa. Estes testes travam a ponte: o
impacto calculado no armazém é publicado por setor e relido em produção.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.macro_data import acesso
from core.macro_data.vitrine import linhas_do_snapshot
from core.macro_data.portfolio_context import PortfolioMacroSnapshot

AGORA = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _snapshot():
    return PortfolioMacroSnapshot(
        impacts={"BANCOS": 18.0, "SANEAMENTO": -7.5},
        details=(
            {"symbol": "BANCOS", "sector": "Bancos", "factor": "juros",
             "provider": "bcb", "provider_code": "SELIC", "direction": "positive",
             "intensity": 40.0, "confidence": 70.0, "channel": "margem"},
            {"symbol": "BANCOS", "sector": "Bancos", "factor": "cambio",
             "provider": "app4_domestic", "provider_code": "USDBRL",
             "direction": "negative", "intensity": 10.0, "confidence": 50.0,
             "channel": "funding"},
            {"symbol": "SANEAMENTO", "sector": "Saneamento", "factor": "juros",
             "provider": "bcb", "provider_code": "SELIC", "direction": "negative",
             "intensity": 30.0, "confidence": 60.0, "channel": "alavancagem"},
        ),
        as_of=AGORA, asset_count=2, covered_assets=2, source_count=9,
        limitations=("sensibilidades iniciais",), knowledge_mode="strict",
    )


def _linhas_publicadas(setores):
    return linhas_do_snapshot(_snapshot(), asset_class="b3",
                              setores={s: s for s in setores})


def _meta(gerada_em=AGORA, granularidade="setor"):
    return {"as_of": AGORA, "gerada_em": gerada_em, "knowledge_mode": "strict",
            "fontes": 9, "limitacoes": ["sensibilidades iniciais"],
            "granularidade": granularidade}


def test_publicacao_nao_trunca_os_fatores():
    """`views/empresas_b3.py` reagrega os fatores para separar macro externa de
    doméstica; truncar devolveria um número plausível e errado, sem sinal."""
    import json

    linha = next(l for l in _linhas_publicadas(["Bancos", "Saneamento"])
                 if l["simbolo"] == "BANCOS")
    fatores = json.loads(linha["fatores"])
    assert len(fatores) == 2
    assert {f["provider"] for f in fatores} == {"bcb", "app4_domestic"}


def test_setor_publicado_vira_impacto_do_ativo_da_carteira():
    snap = acesso.snapshot_da_vitrine(
        _linhas_publicadas(["Bancos", "Saneamento"]), _meta(),
        assets={"ITUB4": "Bancos", "BBAS3": "Bancos", "SAPR11": "Saneamento"})
    # O impacto de um símbolo É o do setor dele: a expansão é exata, não rateio.
    assert snap.impacts == {"ITUB4": 18.0, "BBAS3": 18.0, "SAPR11": -7.5}
    assert snap.coverage == 1.0


def test_setor_sem_cobertura_nao_vira_zero():
    snap = acesso.snapshot_da_vitrine(
        _linhas_publicadas(["Bancos"]), _meta(),
        assets={"ITUB4": "Bancos", "VALE3": "Mineração"})
    assert "VALE3" not in snap.impacts
    assert snap.asset_count == 2 and snap.covered_assets == 1


def test_leitura_declara_que_veio_da_vitrine():
    snap = acesso.snapshot_da_vitrine(
        _linhas_publicadas(["Bancos"]), _meta(), assets={"ITUB4": "Bancos"})
    assert any("vitrine" in lim for lim in snap.limitations)


class _EngineFalsa:
    pass


def _resolver(monkeypatch, *, linhas, meta, as_of=None):
    monkeypatch.setattr(acesso, "_idade_em_dias", lambda g: 0.0 if g else None)
    monkeypatch.setattr("core.macro_data.database.get_local_macro_engine",
                        lambda: None)
    monkeypatch.setattr("core.database.get_engine", lambda: _EngineFalsa())
    monkeypatch.setattr("core.macro_data.vitrine.ler",
                        lambda *a, **k: (linhas, meta))
    return acesso.resolver_macro(asset_class="b3",
                                 assets={"ITUB4": "Bancos"}, as_of=as_of)


def test_sem_armazem_local_a_vitrine_responde(monkeypatch):
    r = _resolver(monkeypatch, linhas=_linhas_publicadas(["Bancos"]), meta=_meta())
    assert r.disponivel and r.origem == acesso.ORIGEM_VITRINE
    assert r.snapshot.impacts == {"ITUB4": 18.0}


def test_nunca_publicada_difere_de_publicada_vazia(monkeypatch):
    nunca = _resolver(monkeypatch, linhas=(), meta=None)
    vazia = _resolver(monkeypatch, linhas=(), meta=_meta())
    assert "nunca publicada" in nunca.motivo
    assert "nenhum ativo com impacto" in vazia.motivo


def test_data_historica_nao_e_servida_pela_vitrine(monkeypatch):
    """A vitrine guarda UM retrato. Devolvê-lo para outra data seria olhar o
    passado com o que só se soube depois -- o look-ahead que o modo estrito
    existe para impedir."""
    r = _resolver(monkeypatch, linhas=_linhas_publicadas(["Bancos"]),
                  meta=_meta(), as_of=AGORA - timedelta(days=90))
    assert not r.disponivel and "histórica" in r.motivo


def test_vitrine_velha_continua_util_mas_se_declara():
    r = acesso.MacroResolvido(_snapshot(), acesso.ORIGEM_VITRINE,
                              gerada_em=AGORA, idade_dias=31.0)
    assert r.disponivel and r.envelhecida
    assert "31 dia" in r.rotulo() and "republicar" in r.rotulo()


def test_falha_de_leitura_nao_derruba_a_tela(monkeypatch):
    monkeypatch.setattr("core.macro_data.database.get_local_macro_engine",
                        lambda: None)
    monkeypatch.setattr("core.database.get_engine", lambda: _EngineFalsa())

    def explode(*a, **k):
        from core.macro_data.vitrine import VitrineMacroIlegivel
        raise VitrineMacroIlegivel("conexão recusada")

    monkeypatch.setattr("core.macro_data.vitrine.ler", explode)
    r = acesso.resolver_macro(asset_class="b3", assets={"ITUB4": "Bancos"})
    assert not r.disponivel and "ilegível" in r.motivo


def test_classe_invalida_e_rejeitada():
    from core.macro_data.vitrine import publicar
    with pytest.raises(ValueError):
        publicar(None, _snapshot(), asset_class="cripto")
