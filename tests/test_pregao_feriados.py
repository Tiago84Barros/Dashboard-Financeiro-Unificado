"""O feriado entrou na contagem de pregão -- e a limitação passou a ser medida.

Estes testes defendem **propriedades**, e não o conteúdo do artefato: o
calendário é gerado do armazém local e muda a cada republicação. Um teste que
afirmasse "2026-09-07 é feriado" quebraria no dia em que o artefato fosse
regerado com outra janela, e quebraria dizendo a coisa errada.

O que eles defendem:

* o feriado observado sai da contagem, e apenas dentro da janela observada;
* fora da janela a contagem **recua** para dia útil puro, em vez de assumir que
  não há feriado -- a diferença entre "medido e não há" e "não medido";
* ausência ou corrupção do artefato nunca vira exceção;
* a frase da limitação é derivada da medição, nunca escrita à mão.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from core import pregao


@pytest.fixture(autouse=True)
def _sem_cache():
    """O calendário é cacheado no módulo; teste que o troca precisa limpar."""
    pregao._calendario.cache_clear()
    yield
    pregao._calendario.cache_clear()


def _artefato(tmp_path, feriados, inicio="2026-01-01", fim="2026-12-31",
              praca="B3"):
    caminho = tmp_path / "calendario_pregao.json"
    caminho.write_text(json.dumps({
        "gerado_em": "2026-09-06",
        "pracas": {praca: {"inicio": inicio, "fim": fim, "fonte": "teste",
                           "pregoes_observados": 250, "feriados": feriados,
                           "por_ano": {}}},
    }), encoding="utf-8")
    return caminho


# -- o feriado sai da conta -------------------------------------------------
def test_feriado_observado_nao_conta_como_pregao(monkeypatch, tmp_path):
    """Sem isto, a notícia envelhece num dia em que ninguém pôde negociar."""
    # 2026-09-07 (Independência) cai numa segunda-feira.
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO",
                        _artefato(tmp_path, ["2026-09-07"]))

    sexta = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)   # após o fechamento
    terca = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
    assert pregao.pregoes_encerrados_entre(sexta, terca) == 1  # só a terça

    pregao._calendario.cache_clear()
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO", tmp_path / "ausente.json")
    assert pregao.pregoes_encerrados_entre(sexta, terca) == 2  # segunda entrava


def test_feriado_de_outra_praca_nao_vaza(monkeypatch, tmp_path):
    """Thanksgiving não fecha a B3, e 07/09 não fecha Nova York."""
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO",
                        _artefato(tmp_path, ["2026-09-07"], praca="NYSE"))
    sexta = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
    terca = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)

    assert pregao.pregoes_encerrados_entre(sexta, terca, pregao.NYSE) == 1
    assert pregao.pregoes_encerrados_entre(sexta, terca, pregao.B3) == 2


def test_esta_aberto_respeita_feriado(monkeypatch, tmp_path):
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO",
                        _artefato(tmp_path, ["2026-09-07"]))
    meio_dia = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)  # 12h em SP
    assert not pregao.esta_aberto(meio_dia)


def test_proximo_fechamento_pula_o_feriado(monkeypatch, tmp_path):
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO",
                        _artefato(tmp_path, ["2026-09-07"]))
    segunda = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    assert pregao.proximo_fechamento(segunda).date() == date(2026, 9, 8)


# -- fora da janela é DESCONHECIDO, não "não há" ----------------------------
def test_fora_da_janela_observada_a_contagem_recua(monkeypatch, tmp_path):
    """"Não medido" e "medido e não há" não podem produzir a mesma resposta.

    Um artefato que cobre até 2026-06-30 não sabe nada sobre setembro. Tratar o
    silêncio dele como "não há feriado em setembro" seria transformar ausência
    de medição em afirmação -- o modo de falha de
    ``memoria: fallback-nunca-contradiz``.
    """
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO",
                        _artefato(tmp_path, ["2026-09-07"],
                                  inicio="2026-01-01", fim="2026-06-30"))
    sexta = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
    terca = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
    # A data consta como feriado, mas está fora da janela: não vale.
    assert pregao.pregoes_encerrados_entre(sexta, terca) == 2


def test_o_recuo_so_pode_superestimar_pregoes(monkeypatch, tmp_path):
    """A direção do erro é a garantia que sobra fora da janela.

    Ela precisa ser verificada, e não apenas afirmada no docstring: com
    feriados a contagem tem de ser sempre **menor ou igual** à contagem sem
    eles, nunca maior. Se algum dia inverter, a notícia velha passa a parecer
    fresca -- e é o único desfecho que este módulo promete não produzir.
    """
    feriados = ["2026-09-07", "2026-10-12", "2026-11-02", "2026-11-20"]
    inicio = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO", tmp_path / "ausente.json")
    sem = [pregao.pregoes_encerrados_entre(inicio, inicio + timedelta(days=d))
           for d in range(1, 100)]
    pregao._calendario.cache_clear()
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO", _artefato(tmp_path, feriados))
    com = [pregao.pregoes_encerrados_entre(inicio, inicio + timedelta(days=d))
           for d in range(1, 100)]

    assert all(c <= s for c, s in zip(com, sem)), "feriado ADICIONOU pregao"
    assert com[-1] == sem[-1] - len(feriados)


# -- ausência do artefato nunca derruba a coleta ----------------------------
@pytest.mark.parametrize("conteudo", [
    None,                                  # arquivo inexistente
    "{ isto nao e json",                   # ilegível
    '{"pracas": {"B3": {"feriados": ["nao-e-data"]}}}',   # malformado
    '{"pracas": {"B3": {"inicio": "2026-01-01"}}}',       # sem "fim"
    '{"outra_coisa": 1}',                  # esquema estranho
])
def test_artefato_ruim_e_ausencia_nunca_excecao(monkeypatch, tmp_path, conteudo):
    """Precisão de meia dúzia de dias por ano não pode derrubar o ciclo.

    ``core/noticias/relevancia.py`` chama isto por notícia avaliada. Uma
    exceção aqui viraria falha de coleta inteira -- e é exatamente a troca que
    ``rotulo_do_destino`` fez errado em 06/09/2026, quando o texto de uma
    limitação derrubou 17 caminhos de teste.
    """
    caminho = tmp_path / "calendario_pregao.json"
    if conteudo is not None:
        caminho.write_text(conteudo, encoding="utf-8")
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO", caminho)

    sexta = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
    terca = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
    assert pregao.pregoes_encerrados_entre(sexta, terca) == 2
    assert pregao.cobertura()["observado"] is False


# -- a limitação é derivada, não escrita à mão ------------------------------
def test_a_limitacao_publicada_sai_da_medicao(monkeypatch, tmp_path):
    """Texto fixo envelhece invertido: ``memoria: aviso-que-envelhece-invertido``.

    Enquanto o módulo não modelava feriado, "feriado não é modelado" era rigor.
    Depois de 06/09/2026 a mesma frase seria falsa com a mesma cara. Por isso a
    frase tem de citar a janela realmente observada.
    """
    monkeypatch.setattr(pregao, "ARTEFATO_CALENDARIO",
                        _artefato(tmp_path, ["2026-09-07", "2026-10-12"],
                                  inicio="2026-01-01", fim="2026-08-31"))
    cob = pregao.cobertura()

    assert cob["observado"] is True
    assert cob["feriados"] == 2
    assert cob["inicio"] == date(2026, 1, 1) and cob["fim"] == date(2026, 8, 31)
    assert "2026-08-31" in cob["limitacao"], "a frase nao cita a janela medida"
    assert "superestimar" in cob["limitacao"], "a direcao do erro sumiu do texto"


def test_nenhum_texto_de_limitacao_afirma_que_feriado_nao_e_modelado():
    """A frase antiga não pode ter sobrado em nenhum lugar do caminho vivo.

    A busca é por substring de propósito -- ela pega inclusive o texto narrando
    a história do módulo. Contar o passado em pretérito ("até 06/09/2026 a
    contagem era dia útil puro") custa uma frase; deixar a afirmação no
    presente custou a este projeto um aviso que envelheceu invertido e seguiu
    soando como rigor por semanas.
    """
    import inspect

    from core.noticias import relevancia

    for modulo in (pregao, relevancia):
        fonte = inspect.getsource(modulo)
        assert "feriado nenhum" not in fonte.lower(), (
            f"{modulo.__name__} ainda afirma que nao modela feriado")


# -- o artefato publicado é o real, e é coerente ----------------------------
def test_o_artefato_versionado_cobre_as_duas_pracas():
    """Sanidade do que está no repositório -- sem afirmar datas específicas."""
    if not pregao.ARTEFATO_CALENDARIO.exists():
        pytest.skip("calendario ainda nao publicado neste checkout")

    for praca in (pregao.B3, pregao.NYSE):
        cob = pregao.cobertura(praca)
        assert cob["observado"], f"{praca.nome} fora do artefato publicado"
        anos = (cob["fim"] - cob["inicio"]).days / 365.25
        por_ano = cob["feriados"] / anos
        # Bolsa nenhuma tem menos de 5 nem mais de 20 feriados por ano. Fora
        # dessa faixa o artefato virou outra coisa -- ingestao truncada lida
        # como feriado, por exemplo -- e ninguem percebeu.
        assert 5 <= por_ano <= 20, f"{praca.nome}: {por_ano:.1f} feriados/ano"
