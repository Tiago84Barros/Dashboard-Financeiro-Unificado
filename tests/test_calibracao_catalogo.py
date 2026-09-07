"""De onde vêm os eventos da validação -- e de onde, declaradamente, não vêm.

O teste central deste arquivo é o que falha quando alguém acrescenta um tipo à
taxonomia: :func:`cobertura` levanta erro em vez de deixar o tipo novo fora da
calibração em silêncio. É o remédio direto para
``memoria: verificador-e-escritor-listas-diferentes`` -- lá, a checagem lia uma
lista e o escritor lia outra, e a migration ficou registrada sem nunca rodar.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from core.calibracao import catalogo as cat
from core.noticias import taxonomia as tax


class EngineFalsa:
    """Devolve linhas fixas. O SQL real é exercitado pelo script, não aqui."""

    def __init__(self, linhas):
        self._linhas = linhas
        self.parametros: list[dict] = []

    @contextmanager
    def begin(self):
        engine = self

        class _Conn:
            def execute(self, _sql, params=None):
                engine.parametros.append(dict(params or {}))

                class _R:
                    def mappings(_self):
                        return list(engine._linhas)
                return _R()

        yield _Conn()


LINHAS = [
    {"simbolo": "petr4", "data": "2024-05-10", "subtipo": None},
    {"simbolo": "VALE3", "data": "2024-05-11 00:00:00", "subtipo": "jcp"},
    {"simbolo": "  ", "data": "2024-05-12", "subtipo": None},   # descartada
]


def test_cobertura_recusa_tipo_da_taxonomia_sem_declaracao(monkeypatch):
    """Subir a taxonomia sem declarar a fonte tem que doer na hora.

    A chave falsa precisa ser uma que nunca vá existir. Este teste usava
    ``pandemia``, e em 03/09/2026 ``pandemia`` virou tipo de verdade -- com
    declaracao em ``SEM_FONTE``. A partir dali o teste passaria a montar uma
    taxonomia com o tipo duplicado, ``cobertura()`` nao acharia buraco nenhum e
    o teste falharia sem que houvesse defeito. Fixture que colide com a
    realidade envelhece calada.
    """
    novo = tax.TipoEvento(chave="tipo_hipotetico_de_teste", rotulo="Hipotetico",
                          materialidade=0.9, persistencia=0.9,
                          horizonte="longo", escopo="macro")
    monkeypatch.setattr(tax, "TIPOS", tax.TIPOS + (novo,))
    monkeypatch.setattr(tax, "POR_CHAVE",
                        {**tax.POR_CHAVE, novo.chave: novo})

    with pytest.raises(RuntimeError, match="sem declaracao de fonte"):
        cat.cobertura()


def test_cobertura_recusa_declaracao_de_tipo_inexistente(monkeypatch):
    monkeypatch.setitem(cat.SEM_FONTE, "tipo_que_nao_existe", "motivo")
    with pytest.raises(RuntimeError, match="nao existe na taxonomia"):
        cat.cobertura()


def test_cobertura_real_e_publicada_sem_arredondar_para_cima():
    """O número honesto de hoje, não o número que se gostaria de ter."""
    cob = cat.cobertura()
    assert set(cob.com_fonte) | set(cob.sem_fonte) == set(tax.POR_CHAVE)
    assert 0 < cob.fracao < 0.5          # a cobertura é baixa, e o resumo o diz
    assert "de %d tipos" % cob.total_tipos in cob.resumo()


def test_carregar_monta_chave_ponto_no_tempo_e_descarta_linha_sem_simbolo():
    engine = EngineFalsa(LINHAS)
    fonte = cat.FONTES[0]
    eventos = cat.carregar(engine, fonte, ate=None)

    assert len(eventos) == 2                       # a de símbolo vazio saiu
    assert eventos[0]["chave"] == f"{fonte.tipo_evento}:PETR4:2024-05-10"
    assert eventos[0]["simbolo"] == "PETR4"        # normalizado
    assert eventos[1]["data"] == "2024-05-11"      # truncado no dia
    assert eventos[0]["ressalvas"] == list(fonte.ressalvas)


def test_corte_temporal_vai_como_parametro_e_nao_por_interpolacao():
    """O corte é o que impede look-ahead; ele precisa chegar ao SQL."""
    from datetime import date
    engine = EngineFalsa(LINHAS)
    cat.carregar(engine, cat.FONTES[0], ate=date(2020, 1, 31))
    assert engine.parametros[-1]["ate"] == date(2020, 1, 31)


def test_montar_declara_a_ressalva_de_cada_fonte_e_a_ausencia_das_outras():
    """As limitações são o que impede ler a tabela como se tudo valesse igual."""
    resultado = cat.montar(EngineFalsa(LINHAS))
    limitacoes = resultado["limitacoes"]

    assert resultado["eventos"], "as fontes declaradas precisam render eventos"
    assert any("sem fonte historica" in lim for lim in limitacoes)
    # 'indefinido' é resíduo da taxonomia e não vira limitação anunciada.
    assert not any(lim.startswith("indefinido:") for lim in limitacoes)
    # Toda fonte com ressalva aparece nomeada.
    for fonte in cat.FONTES:
        for ressalva in fonte.ressalvas:
            assert f"{fonte.rotulo}: {ressalva}" in limitacoes


def test_filtro_por_tipo_nao_esconde_a_cobertura_total():
    """Pedir um tipo só não pode fazer a cobertura parecer melhor do que é."""
    tipo = cat.FONTES[0].tipo_evento
    resultado = cat.montar(EngineFalsa(LINHAS), tipos=[tipo])
    assert set(resultado["por_tipo"]) == {tipo}
    assert resultado["cobertura"].total_tipos == len(tax.TIPOS)
