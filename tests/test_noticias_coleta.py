"""Orquestração da coleta: falha parcial, frescor e procedência.

Cobre o cenário exigido "falha parcial de provedores" e as regras de frescor
que o usuário escreveu: indicação clara de fonte indisponível, registro da
última atualização bem-sucedida e -- a mais importante -- **nunca apresentar
notícia antiga como se fosse atual**.

Três distinções são afirmadas repetidamente porque as três já produziram bug
neste repositório se colapsadas:

* "consultei e não havia nada" != "não consegui consultar";
* "respondeu do cache vencido" != "respondeu";
* falha atualiza a última *tentativa*, nunca o último *sucesso*.

Nenhum teste toca rede: os provedores são objetos escritos aqui dentro, o que
de quebra prova que o motor não conhece nenhuma API concreta.
"""
from __future__ import annotations

import json

import pytest

from core.noticias import coleta, frescor_noticias, portoes, taxonomia
from core.noticias.provedores.base import (
    ORIGEM_CACHE_VENCIDO,
    Consulta,
    ProvedorIndisponivel,
    RespostaInvalida,
)
from core.noticias.rate_limit import LimiteExcedido
from core.noticias.transporte import ErroTransporte
from tests.apoio_noticias import AGORA, ProvedorFalso, item, quando

CONSULTA = Consulta(tickers=("ALFA3",), limite=10)

FATO = item("Fato relevante: Alfa comunica acordo de fusao com a Beta",
            "https://www.cvm.gov.br/doc/9", tickers=("ALFA3",),
            publicado_em=quando(1))
BALANCO = item("Alfa divulga balanco do terceiro trimestre",
               "https://www.reuters.com/e/1", tickers=("ALFA3",),
               publicado_em=quando(3))
ANTIGA = item("Alfa comentou o cenario do setor em evento no ano passado",
              "https://algumblog.blogspot.com/velha/1", tickers=("ALFA3",),
              publicado_em=quando(24 * 300))


def _registro():
    """Registro em memória: nenhum teste escreve no disco do usuário."""
    return frescor_noticias.RegistroColeta(persistir=False, agora=lambda: AGORA)


def _coletar(*provedores, **kw):
    kw.setdefault("agora", AGORA)
    return coleta.coletar(CONSULTA, list(provedores), **kw)


# ── falha parcial de provedores ─────────────────────────────────────────────

def test_um_provedor_fora_do_ar_nao_derruba_a_coleta():
    bom = ProvedorFalso("bom", [FATO])
    ruim = ProvedorFalso("ruim",
                         erro=ProvedorIndisponivel("ruim", "401 sem acesso"))

    r = _coletar(bom, ruim)

    assert len(r.avaliadas) == 1
    assert r.provedores_ok == ("bom",)
    assert r.provedores_consultados == ("bom", "ruim")
    assert r.degradado is True
    assert r.sem_fonte is False

    (falha,) = r.falhas
    assert falha.provedor == "ruim"
    assert falha.tipo == coleta.FALHA_INDISPONIVEL
    assert falha.texto() == "ruim: fonte indisponivel"


def test_todos_fora_do_ar_e_dito_com_todas_as_letras():
    r = _coletar(ProvedorFalso("a", erro=ProvedorIndisponivel("a", "sem chave")),
                 ProvedorFalso("b", erro=ErroTransporte("timeout")))

    assert r.sem_fonte is True
    assert r.avaliadas == ()
    assert len(r.falhas) == 2
    assert any("nao reflete o momento atual" in lim for lim in r.limitacoes)


def test_provedor_que_responde_vazio_nao_e_provedor_indisponivel():
    """Zero notícias é um resultado; zero fontes é uma falha. Não se confundem."""
    r = _coletar(ProvedorFalso("vazio", []))

    assert r.avaliadas == ()
    assert r.provedores_ok == ("vazio",)
    assert r.sem_fonte is False
    assert r.falhas == ()


@pytest.mark.parametrize(("erro", "tipo", "rotulo"), [
    (LimiteExcedido("provedor_x"),
     coleta.FALHA_LIMITE, "limite de requisicoes atingido"),
    (ProvedorIndisponivel("p", "401"), coleta.FALHA_INDISPONIVEL,
     "fonte indisponivel"),
    (RespostaInvalida("p", "json quebrado"), coleta.FALHA_INVALIDA,
     "resposta invalida da fonte"),
    (ErroTransporte("conexao recusada"), coleta.FALHA_REDE,
     "falha de comunicacao com a fonte"),
])
def test_cada_modo_de_falha_recebe_um_rotulo_proprio(erro, tipo, rotulo):
    """A tela precisa dizer coisas diferentes; um "erro" genérico não serve."""
    r = _coletar(ProvedorFalso("p", erro=erro))
    (falha,) = r.falhas
    assert falha.tipo == tipo
    assert falha.rotulo == rotulo


def test_falha_de_um_provedor_nao_vaza_a_mensagem_para_o_log(caplog):
    segredo = "CHAVE-DE-TESTE-NAO-E-CREDENCIAL-REAL"
    erro = ProvedorIndisponivel(
        "p", f"401 em https://api.exemplo/?token={segredo}")

    with caplog.at_level("WARNING", logger="core.noticias.coleta"):
        _coletar(ProvedorFalso("p", erro=erro))

    assert segredo not in caplog.text
    assert "p falhou" in caplog.text


# ── cache vencido: resultado degradado, e rotulado como tal ────────────────

def test_cache_vencido_socorre_a_tela_mas_nao_se_disfarca_de_atual():
    p = ProvedorFalso("p", erro=ProvedorIndisponivel("p", "fora do ar"),
                      cache_vencido=[FATO])
    r = _coletar(p)

    assert len(r.avaliadas) == 1
    assert r.origens["p"] == ORIGEM_CACHE_VENCIDO
    assert any("cache vencido" in lim for lim in r.limitacoes)

    (falha,) = r.falhas
    assert falha.usou_cache_vencido is True
    assert "fora do prazo" in falha.texto()

    # E, sobretudo: o provedor não entra na lista dos que responderam.
    assert r.provedores_ok == ()
    assert r.sem_fonte is True


def test_cache_vencido_pode_ser_recusado():
    p = ProvedorFalso("p", erro=ProvedorIndisponivel("p", "fora do ar"),
                      cache_vencido=[FATO])
    r = _coletar(p, permitir_cache_vencido=False)

    assert r.avaliadas == ()
    assert r.falhas[0].usou_cache_vencido is False


# ── registro da última atualização bem-sucedida ────────────────────────────

def test_sucesso_carimba_a_ultima_coleta_bem_sucedida():
    reg = _registro()
    _coletar(ProvedorFalso("bom", [FATO]), registro=reg)

    assert reg.ultimo_sucesso("bom") == AGORA
    estado = reg.estado("bom", cadencia_minutos=60)
    assert estado.fresco is True
    assert estado.itens_no_ultimo_sucesso == 1


def test_falha_atualiza_a_tentativa_e_nunca_o_sucesso():
    """A linha que impede o painel de dizer "atualizado agora" após um erro."""
    reg = _registro()
    _coletar(ProvedorFalso("ruim",
                           erro=ProvedorIndisponivel("ruim", "fora do ar")),
             registro=reg)

    assert reg.ultimo_sucesso("ruim") is None
    assert reg.ultima_tentativa("ruim") == AGORA

    estado = reg.estado("ruim", cadencia_minutos=60)
    assert estado.fresco is False
    assert estado.vencido is False, "sem sucesso nenhum, não é 'vencido'"
    assert estado.estado == frescor_noticias.ESTADO_DESCONHECIDO
    assert estado.ultimo_erro == coleta.FALHA_INDISPONIVEL


def test_cache_vencido_tambem_nao_carimba_sucesso():
    reg = _registro()
    _coletar(ProvedorFalso("p", erro=ProvedorIndisponivel("p", "fora do ar"),
                           cache_vencido=[FATO]), registro=reg)
    assert reg.ultimo_sucesso("p") is None


def test_sem_coleta_nenhuma_o_estado_diz_isso():
    estado = _registro().estado("nunca_rodou", cadencia_minutos=60)
    assert estado.estado == frescor_noticias.ESTADO_SEM_COLETA
    assert estado.texto() == "Nenhuma coleta bem-sucedida registrada ate agora."


# ── notícia antiga não vira notícia de agora ao passar pela coleta ─────────

def test_a_hora_da_coleta_nao_e_carimbada_como_hora_da_publicacao():
    sem_data = item("Alfa apresenta numeros do trimestre a analistas",
                    "https://www.reuters.com/s/1", tickers=("ALFA3",))
    r = _coletar(ProvedorFalso("p", [sem_data]))

    n = r.avaliadas[0].noticia
    assert n.publicado_em is None
    assert n.coletado_em == AGORA
    assert n.idade_em_minutos(AGORA) is None


def test_materia_de_um_ano_atras_chega_rotulada_como_antiga():
    r = _coletar(ProvedorFalso("p", [ANTIGA]))
    n = r.avaliadas[0].noticia

    texto, recente = frescor_noticias.rotular_idade(n, agora=AGORA)
    assert recente is False
    assert "ANTIGA" in texto
    assert r.avaliadas[0].faixa == taxonomia.FAIXA_INFORMATIVA


# ── deduplicação entre provedores e determinismo ──────────────────────────

def test_a_mesma_materia_em_dois_provedores_conta_uma_vez():
    r = _coletar(ProvedorFalso("a", [FATO, BALANCO]),
                 ProvedorFalso("b", [FATO]))

    assert r.itens_brutos == 3
    assert len(r.avaliadas) == 2
    assert r.duplicatas_removidas == 1


def test_a_ordem_dos_provedores_nao_muda_a_tela():
    """Desempate por identificador, não por qual API respondeu primeiro."""
    a = ProvedorFalso("a", [FATO, BALANCO])
    b = ProvedorFalso("b", [BALANCO, FATO])

    ida = [x.noticia.id_dedup for x in _coletar(a, b).avaliadas]
    volta = [x.noticia.id_dedup for x in _coletar(b, a).avaliadas]
    assert ida == volta


def test_notas_saem_em_ordem_decrescente():
    r = _coletar(ProvedorFalso("p", [FATO, BALANCO, ANTIGA]))
    notas = [x.nota for x in r.avaliadas]
    assert notas == sorted(notas, reverse=True)


# ── ausência de carteira e destaques ──────────────────────────────────────

def test_sem_carteira_a_coleta_declara_o_que_ficou_de_fora():
    r = _coletar(ProvedorFalso("p", [FATO]))
    assert any("sem carteira cadastrada" in lim for lim in r.limitacoes)
    assert r.avaliadas[0].relevancia.componentes["exposicao"] is None


def test_com_carteira_a_exposicao_entra_na_nota():
    perfil = portoes.Perfil(horizonte_meses=60,
                            exposicao_por_ativo={"ALFA3": 0.30},
                            tickers=("ALFA3",))
    sem = _coletar(ProvedorFalso("p", [FATO]))
    com = _coletar(ProvedorFalso("p", [FATO]), perfil=perfil)

    assert com.avaliadas[0].relevancia.componentes["exposicao"] is not None
    assert not any("sem carteira cadastrada" in lim for lim in com.limitacoes)
    assert com.avaliadas[0].nota != sem.avaliadas[0].nota


def test_destaques_selecionam_sem_apagar_o_resto():
    r = _coletar(ProvedorFalso("p", [FATO, ANTIGA]))
    altos = coleta.destaques(r, minimo=60.0)

    assert len(altos) < len(r.avaliadas)
    assert all(a.nota >= 60.0 for a in altos)
    assert len(r.por_faixa(taxonomia.FAIXA_INFORMATIVA)) >= 1


# ── a camada abstrata: provedor escrito no teste funciona no motor ────────

def test_o_motor_aceita_um_provedor_que_ele_nunca_viu():
    """Se um provedor de 40 linhas escrito aqui roda, não há acoplamento."""

    class ProvedorInventado:
        nome = "inventado"

        def disponivel(self):
            return True

        def buscar(self, consulta):
            from core.noticias.provedores.base import (
                ORIGEM_REDE,
                RespostaProvedor,
            )
            assert consulta.tickers == ("ALFA3",)
            return RespostaProvedor(
                provedor=self.nome, itens=(BALANCO,), origem=ORIGEM_REDE,
                consultado_em=AGORA, dados_de=AGORA,
                limitacoes=("nao informa autor",))

        def do_cache_vencido(self, consulta):
            return None

    r = _coletar(ProvedorInventado())
    assert r.provedores_ok == ("inventado",)
    assert len(r.avaliadas) == 1
    assert "inventado: nao informa autor" in r.limitacoes


# ── o job do pipeline respeita a cadência sem gastar requisição ───────────

def test_dentro_da_cadencia_o_job_nao_consulta_ninguem(monkeypatch):
    """O freio de cadência lê o estado compartilhado, e não o JSON local.

    A versão anterior deste teste escrevia o carimbo em
    ``frescor_noticias.CAMINHO_PADRAO`` e o job frearia por ele. Em produção
    isso nunca funcionou: runner do Actions, container do Streamlit e máquina do
    desenvolvedor não compartilham disco, cada processo encontrava o arquivo
    vazio e se via como a primeira execução do dia. O carimbo passou para
    ``estado_coleta`` -- que é o que os três alcançam --, e é ele que este teste
    exercita.
    """
    from core.noticias import cadencia as cad
    from core.noticias import estado_coleta as ec
    from data_pipeline.jobs import update_noticias

    agora = frescor_noticias.agora_utc()
    monkeypatch.setattr(ec, "ler", lambda **kw: ec.EstadoGlobal(
        modo=cad.MODO_NORMAL, ultima_tentativa=agora, ultimo_sucesso=agora,
        disponivel=True))

    def _explode(*a, **kw):  # pragma: no cover - só roda se o job furar a cadência
        raise AssertionError("o job nao podia montar provedor nenhum")

    monkeypatch.setattr(
        "core.noticias.provedores.registro.construir", _explode)

    resultado = update_noticias.run(agora=agora)

    assert resultado["status"] == "skipped"
    assert "desde a última tentativa" in resultado["error_message"]
    assert resultado["records_inserted"] == 0


def test_o_job_devolve_o_contrato_que_o_orquestrador_aceita(tmp_path,
                                                            monkeypatch):
    """Status fora do conjunto vira "failed" e o motivo real se perde."""
    from data_pipeline.jobs import update_noticias

    caminho = tmp_path / "coleta.json"
    caminho.write_text(json.dumps({
        update_noticias.JOB_NAME: {
            "ultimo_sucesso": frescor_noticias.agora_utc().isoformat(),
        }
    }), encoding="utf-8")
    monkeypatch.setattr(frescor_noticias, "CAMINHO_PADRAO", caminho)

    resultado = update_noticias.run()

    assert set(resultado) == {
        "status", "table_name", "source_name", "job_name",
        "records_inserted", "records_updated", "records_failed",
        "error_message",
    }
    assert resultado["status"] in {"success", "partial_success", "skipped",
                                   "failed"}
    assert resultado["job_name"] == update_noticias.JOB_NAME
