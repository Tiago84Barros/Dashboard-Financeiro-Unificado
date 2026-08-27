# -*- coding: utf-8 -*-
"""A-137: o ticker do cancelamento CVM sai do FCA.

A lista de deslistadas ficou em 22 tickers curados nao porque a fonte fosse
paga, mas porque o mapa CNPJ->ticker exigido por `load_cvm_cancelamentos`
nunca foi ligado ao FCA -- que ja era baixado e parseado neste repositorio
para outra finalidade. Estes testes fixam a ponte.
"""
from __future__ import annotations

import core.survivorship_ingestion as si

_FCA = (
    "CNPJ_Companhia;Data_Referencia;Versao;Nome_Empresarial;Valor_Mobiliario;"
    "Sigla_Classe_Acao_Preferencial;Classe_Acao_Preferencial;Codigo_Negociacao;"
    "Composicao_BDR_Unit;Mercado;Sigla_Entidade_Administradora;"
    "Entidade_Administradora;Data_Inicio_Negociacao;Data_Fim_Negociacao;Segmento\n"
    "11.111.111/0001-11;2015-01-01;1;SAIU SA;Ações Ordinárias;;;SAIU3;;Bolsa;"
    "B3;B3;;;Tradicional\n"
    "11.111.111/0001-11;2015-01-01;1;SAIU SA;Ações Preferenciais;PN;;SAIU4;;"
    "Bolsa;B3;B3;;;Tradicional\n"
    "22.222.222/0001-22;2015-01-01;1;CURADA SA;Ações Ordinárias;;;CURA3;;Bolsa;"
    "B3;B3;;;Tradicional\n"
    "11.111.111/0001-11;2015-01-01;1;SAIU SA;Debêntures;;;SAIUDEB;;Balcão;"
    "B3;B3;;;\n"
).encode("latin-1")

_CAD_CANCELADAS = (
    "CNPJ_CIA;DENOM_SOCIAL;DENOM_COMERC;DT_REG;SIT;CD_CVM;SETOR_ATIV;"
    "DT_CANCEL;MOTIVO_CANCEL\n"
    "11.111.111/0001-11;SAIU S.A.;SAIU;2000-01-01;CANCELADA;1111;Comercio;"
    "2015-06-30;incorporacao\n"
    "22.222.222/0001-22;CURADA S.A.;CURADA;2000-01-01;CANCELADA;2222;Comercio;"
    "2016-03-15;opa\n"
    "33.333.333/0001-33;VIVA S.A.;VIVA;2000-01-01;ATIVO;3333;Comercio;;\n"
).encode("latin-1")


def _cache_fca(tmp_path, ano=2015):
    pasta = tmp_path / "fca"
    pasta.mkdir()
    (pasta / f"fca_valmob_{ano}.csv").write_bytes(_FCA)
    return pasta


def test_alias_do_fca_traz_todas_as_classes_e_ignora_debenture(tmp_path):
    pasta = _cache_fca(tmp_path)
    aliases = si.load_fca_aliases(anos=[2015], cache_dir=pasta,
                                  permitir_download=False)
    assert {a["ticker"] for a in aliases} == {"SAIU3", "SAIU4", "CURA3"}
    # o alias precisa sair no formato que _alias_matches_cvm_row consome
    saiu3 = next(a for a in aliases if a["ticker"] == "SAIU3")
    assert saiu3["cnpj_cia"] == "11111111000111"
    assert saiu3["fonte"] == "fca_cvm_2015"


def test_sem_cache_e_sem_rede_nao_explode(tmp_path):
    """Degradar em silencio: quem consome ja trata ausencia de alias."""
    assert si.load_fca_aliases(anos=[2015], cache_dir=tmp_path / "vazio",
                               permitir_download=False) == []


def test_cancelamento_ganha_ticker_pela_ponte_do_fca(tmp_path, monkeypatch):
    cad = tmp_path / "cad.csv"
    cad.write_bytes(_CAD_CANCELADAS)
    monkeypatch.setattr(si, "download_cvm_cadastro", lambda **kw: cad)

    delisted = si.load_cvm_cancelamentos(
        cache_path=cad, alias_path=tmp_path / "sem_alias.csv",
        fca_cache_dir=_cache_fca(tmp_path), permitir_download=False,
    )
    por_ticker = {d.ticker: d for d in delisted}
    # SAIU3/SAIU4 vieram do FCA; VIVA nao esta cancelada e nao entra
    assert {"SAIU3", "SAIU4", "CURA3"} == set(por_ticker)
    assert por_ticker["SAIU3"].data_delisting.isoformat() == "2015-06-30"
    assert por_ticker["SAIU3"].motivo == "incorporacao"


def test_alias_curado_tem_precedencia_sobre_o_fca(tmp_path, monkeypatch):
    """O curado carrega data/motivo revisados a mao; o FCA so resolve identidade.

    Sem a precedencia, o mesmo CNPJ entraria duas vezes e a versao automatica
    poderia sobrescrever a data conferida.
    """
    cad = tmp_path / "cad.csv"
    cad.write_bytes(_CAD_CANCELADAS)
    alias = tmp_path / "curado.csv"
    alias.write_text(
        "ticker,cnpj_cia,data_delisting,motivo\n"
        "CURA3,22.222.222/0001-22,2016-03-15,opa_conferida\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(si, "download_cvm_cadastro", lambda **kw: cad)

    delisted = si.load_cvm_cancelamentos(
        cache_path=cad, alias_path=alias,
        fca_cache_dir=_cache_fca(tmp_path), permitir_download=False,
    )
    cura = [d for d in delisted if d.ticker == "CURA3"]
    assert len(cura) == 1, "CNPJ curado nao pode reentrar pelo FCA"
    assert cura[0].motivo == "opa_conferida"


def test_resumo_reporta_o_que_a_ponte_produziu(tmp_path, monkeypatch):
    """O manifesto media uma fonte que nunca era consultada (incluir_cvm=False)."""
    cad = tmp_path / "cad.csv"
    cad.write_bytes(_CAD_CANCELADAS)
    monkeypatch.setattr(si, "download_cvm_cadastro", lambda **kw: cad)

    resumo = si.resumo_ingestao(
        dir_local=tmp_path / "sem_locais", incluir_cvm=True,
        cvm_cache_path=cad, cvm_alias_path=tmp_path / "sem_alias.csv",
        fca_cache_dir=_cache_fca(tmp_path), permitir_download=False,
    )
    assert resumo["fca_aliases"] == 3
    assert resumo["cvm_canceladas"] == 2
    assert resumo["cvm_mapeadas"] == 3  # SAIU3, SAIU4, CURA3
