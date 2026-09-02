"""Infraestrutura de atualização contínua: cadência, estado, job e saúde.

Os treze cenários que o requisito nomeia, mais as verificações de saúde. Nada
aqui toca rede ou banco: o estado compartilhado é substituído por um duplo em
memória, e os provedores são os falsos de ``tests/apoio_noticias.py``.

Por que o duplo, e não um SQLite
--------------------------------
O DDL é Postgres (``JSONB``, ``BIGSERIAL``, ``pg_try_advisory_lock``). Um SQLite
aceitaria um schema parecido e provaria coisa diferente da que roda em produção.
O que estes testes verificam é a **decisão** do job -- qual modo, qual universo,
qual status, o que avança e o que não avança -- e essa decisão não é do banco.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.noticias import cadencia as cad
from core.noticias import estado_coleta as ec
from core.noticias import saude
from core.noticias import universo_coleta as uni
from data_pipeline.jobs import update_noticias as job
from tests.apoio_noticias import AGORA, ProvedorFalso, item

from core.noticias.provedores.base import ProvedorIndisponivel
from core.noticias.rate_limit import LimiteExcedido


class ConfigFalsa:
    noticias_freq_normal_min = 240.0
    noticias_freq_vigilancia_min = 60.0
    noticias_freq_crise_min = 20.0
    noticias_max_sem_atualizacao_min = 0.0
    noticias_max_retentativas = 1
    noticias_backoff_s = 0.0
    noticias_timeout_s = 5.0
    noticias_retencao_dias = 180
    noticias_cache_ttl_s = 60
    noticias_limite = 10


CONFIG = ConfigFalsa()


# ── Duplo do estado compartilhado ────────────────────────────────────────────
class EstadoFalso:
    """Substitui o banco: guarda o mesmo que ele guardaria, em memória."""

    def __init__(self, *, modo=cad.MODO_NORMAL, ultima_tentativa=None,
                 ultimo_sucesso=None, lock_livre=True):
        self.estado = ec.EstadoGlobal(
            modo=modo, ultima_tentativa=ultima_tentativa,
            ultimo_sucesso=ultimo_sucesso, disponivel=True)
        self.lock_livre = lock_livre
        self.ciclos: list[ec.Ciclo] = []
        self.sucessos: list[datetime | None] = []
        self.expurgos = 0

    def instalar(self, monkeypatch, *, novas=0):
        from contextlib import contextmanager

        @contextmanager
        def _travar(engine=None, **kw):
            yield self.lock_livre

        def _registrar(ciclo, *, engine=None, sucesso_em=None):
            self.ciclos.append(ciclo)
            self.sucessos.append(sucesso_em)
            return {"gravado": True, "ciclo_id": len(self.ciclos)}

        monkeypatch.setattr(ec, "ler", lambda **kw: self.estado)
        monkeypatch.setattr(ec, "travar", _travar)
        monkeypatch.setattr(ec, "registrar", _registrar)
        monkeypatch.setattr(ec, "contar_novas", lambda ids, **kw: novas)
        monkeypatch.setattr(
            ec, "expurgar",
            lambda **kw: (setattr(self, "expurgos", self.expurgos + 1)
                          or {"expurgado": True, "itens": 0, "ciclos": 0}))

    @property
    def ultimo(self) -> ec.Ciclo:
        assert self.ciclos, "nenhum ciclo foi registrado"
        return self.ciclos[-1]


class ConsumoFalso:
    """Armazém de cota em memória: nenhum teste toca disco nem banco."""

    def __init__(self):
        self.registros: dict = {}

    def disponivel(self) -> bool:
        return True

    def carregar(self):
        return dict(self.registros)

    def salvar(self, registros):
        self.registros = dict(registros)


def _preparar(monkeypatch, provedores, *, universo=("PETR4",)):
    """Isola o job de tudo que não é a decisão sob teste."""
    from core.noticias.provedores import registro as reg

    monkeypatch.setattr("core.config.settings", CONFIG, raising=False)
    monkeypatch.setattr(ec, "ConsumoBanco", lambda engine=None: ConsumoFalso())
    monkeypatch.setattr(reg, "construir", lambda **kw: list(provedores))
    monkeypatch.setattr(uni, "montar", lambda modo, **kw: (universo, ()))
    monkeypatch.setattr("core.noticias.armazenamento.gravar",
                        lambda resultado, **kw: {
                            "gravado": True, "itens": len(resultado.avaliadas),
                            "avaliacoes": len(resultado.avaliadas)})


def _provedor_ok(nome="bom"):
    return ProvedorFalso(nome, [item("Empresa X divulga resultado",
                                     f"https://exemplo.test/{nome}")])


# ── 1. Ciclo normal ──────────────────────────────────────────────────────────
def test_ciclo_normal_coleta_grava_e_agenda_o_proximo(monkeypatch):
    est = EstadoFalso()
    est.instalar(monkeypatch, novas=1)
    _preparar(monkeypatch, [_provedor_ok()])

    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "success"
    ciclo = est.ultimo
    assert ciclo.modo == cad.MODO_NORMAL
    assert ciclo.status == cad.STATUS_ATUALIZADO
    assert ciclo.provedores_ok == ("bom",)
    assert ciclo.proximo_ciclo_em is not None
    assert est.sucessos[-1] is not None, "sucesso real precisa avançar o carimbo"


def test_dentro_da_cadencia_nao_gasta_requisicao(monkeypatch):
    est = EstadoFalso(ultima_tentativa=AGORA - timedelta(minutes=5))
    est.instalar(monkeypatch)
    provedor = _provedor_ok()
    _preparar(monkeypatch, [provedor])

    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "skipped"
    assert provedor.chamadas == 0, "coleta pulada não pode consultar ninguém"
    assert not est.ciclos


def test_forcar_atravessa_a_cadencia(monkeypatch):
    est = EstadoFalso(ultima_tentativa=AGORA - timedelta(minutes=5))
    est.instalar(monkeypatch)
    provedor = _provedor_ok()
    _preparar(monkeypatch, [provedor])

    saida = job.run(engine=None, agora=AGORA, forcar=True)

    assert saida["status"] == "success"
    assert provedor.chamadas == 1
    assert est.ultimo.forcado is True


# ── 2 a 4. Vigilância, crise e rebaixamento ──────────────────────────────────
@pytest.mark.parametrize("nivel,modo,alvos", [
    (0, cad.MODO_NORMAL, 3),
    (1, cad.MODO_VIGILANCIA, 2),
    (2, cad.MODO_VIGILANCIA, 2),
    (3, cad.MODO_CRISE, 1),
    (4, cad.MODO_CRISE, 1),
])
def test_nivel_define_modo_e_universo(nivel, modo, alvos):
    assert cad.modo_para_nivel(nivel) == modo
    assert len(cad.PRIORIDADES[modo]) == alvos


def test_vigilancia_coleta_mais_vezes_que_normal_e_crise_mais_que_vigilancia():
    normal = cad.cadencia(cad.MODO_NORMAL, config=CONFIG)
    vigil = cad.cadencia(cad.MODO_VIGILANCIA, config=CONFIG)
    crise = cad.cadencia(cad.MODO_CRISE, config=CONFIG)
    assert crise.intervalo_min < vigil.intervalo_min < normal.intervalo_min
    assert crise.sla_min < vigil.sla_min < normal.sla_min


def test_ativacao_de_crise_muda_o_ciclo_registrado(monkeypatch):
    est = EstadoFalso(ultima_tentativa=AGORA - timedelta(minutes=30))
    est.instalar(monkeypatch)
    _preparar(monkeypatch, [_provedor_ok()])

    saida = job.run(engine=None, agora=AGORA, nivel=3)

    assert saida["status"] == "success"
    assert est.ultimo.modo == cad.MODO_CRISE
    # 30 min de intervalo no modo normal seriam pouco; em crise (20 min) já
    # passou. O nível é o que destrava esta execução.
    assert est.ultimo.iniciado_em == AGORA


def test_rebaixamento_volta_a_espacar_a_coleta(monkeypatch):
    est = EstadoFalso(modo=cad.MODO_CRISE,
                      ultima_tentativa=AGORA - timedelta(minutes=30))
    est.instalar(monkeypatch)
    provedor = _provedor_ok()
    _preparar(monkeypatch, [provedor])

    saida = job.run(engine=None, agora=AGORA, nivel=0)

    assert saida["status"] == "skipped", (
        "no modo normal, 30 min não completam o intervalo de 240 min")
    assert provedor.chamadas == 0


def test_encerramento_da_vigilancia_herda_a_histerese_do_motor():
    """O modo não tem critério próprio de subida ou descida.

    A tabela é total e determinística: dado o nível, o modo é função dele. Toda
    a proteção contra oscilação (12 h no nível, um degrau por avaliação) mora em
    ``eventos_extremos.transicao`` e não é reimplementada aqui -- dois juízes de
    crise poderiam discordar, e discordariam em silêncio.
    """
    assert set(cad.MODO_POR_NIVEL) == {0, 1, 2, 3, 4}
    assert cad.modo_para_nivel(None) == cad.MODO_NORMAL


# ── 5. Execução concorrente ──────────────────────────────────────────────────
def test_execucao_concorrente_e_recusada_e_nao_gasta_cota(monkeypatch):
    est = EstadoFalso(lock_livre=False)
    est.instalar(monkeypatch)
    provedor = _provedor_ok()
    _preparar(monkeypatch, [provedor])

    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "skipped"
    assert "ja esta em andamento" in saida["error_message"]
    assert provedor.chamadas == 0
    assert not est.ciclos, "recusa por lock não é incidente"


# ── 6 a 8. Provedores ────────────────────────────────────────────────────────
def test_um_provedor_fora_do_ar_degrada_sem_derrubar(monkeypatch):
    est = EstadoFalso()
    est.instalar(monkeypatch)
    ruim = ProvedorFalso("ruim", erro=ProvedorIndisponivel("x", "fora do ar"))
    _preparar(monkeypatch, [_provedor_ok(), ruim])

    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "partial_success"
    assert est.ultimo.status == cad.STATUS_DEGRADADO
    assert est.ultimo.provedores_ok == ("bom",)
    assert est.ultimo.provedores_falha == ("ruim",)
    assert est.sucessos[-1] is not None, "alguém respondeu: o carimbo avança"


def test_todos_indisponiveis_nao_avanca_o_carimbo_de_sucesso(monkeypatch):
    anterior = AGORA - timedelta(hours=3)
    est = EstadoFalso(ultimo_sucesso=anterior)
    est.instalar(monkeypatch)
    _preparar(monkeypatch, [
        ProvedorFalso("a", erro=ProvedorIndisponivel("x", "fora do ar")),
        ProvedorFalso("b", erro=ProvedorIndisponivel("x", "fora do ar"))])

    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "failed"
    assert est.ultimo.status == cad.STATUS_INDISPONIVEL
    assert est.sucessos[-1] is None, (
        "falha total não pode avançar o último sucesso: o painel diria "
        "'atualizado agora' sem nenhum dado novo")
    assert est.ultimo.erros


def test_falha_total_e_retentada_com_espera_crescente(monkeypatch):
    est = EstadoFalso()
    est.instalar(monkeypatch)
    ruim = ProvedorFalso("a", erro=ProvedorIndisponivel("x", "fora do ar"))
    _preparar(monkeypatch, [ruim])

    job.run(engine=None, agora=AGORA)

    # noticias_max_retentativas = 1 -> uma tentativa original e uma repetição.
    assert ruim.chamadas == 2


def test_limite_de_requisicoes_esgotado_nao_vira_sucesso(monkeypatch):
    est = EstadoFalso()
    est.instalar(monkeypatch)
    _preparar(monkeypatch, [
        ProvedorFalso("a", erro=LimiteExcedido("a"))])

    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "failed"
    assert est.sucessos[-1] is None
    assert any("limite" in e.lower() for e in est.ultimo.erros)


# ── 9 e 10. Dado vencido e atualização parcial ───────────────────────────────
def test_dado_vencido_bloqueia_recomendacao_de_emergencia():
    ritmo = cad.cadencia(cad.MODO_CRISE, config=CONFIG)
    velho = AGORA - timedelta(minutes=ritmo.sla_min + 10)

    situacao = cad.status(velho, ritmo, agora=AGORA, provedores_ok=1,
                          provedores_previstos=1)

    assert situacao == cad.STATUS_ATRASADO
    assert cad.permite_recomendacao_emergencial(situacao) is False


def test_atualizacao_parcial_nao_se_apresenta_como_completa():
    ritmo = cad.cadencia(cad.MODO_NORMAL, config=CONFIG)
    situacao = cad.status(AGORA, ritmo, agora=AGORA, provedores_ok=1,
                          provedores_previstos=2)

    assert situacao == cad.STATUS_DEGRADADO
    assert cad.permite_recomendacao_emergencial(situacao) is False


def test_cache_vencido_e_degradacao_declarada():
    ritmo = cad.cadencia(cad.MODO_NORMAL, config=CONFIG)
    situacao = cad.status(AGORA, ritmo, agora=AGORA, provedores_ok=1,
                          provedores_previstos=1, usou_cache_vencido=True)
    assert situacao == cad.STATUS_DEGRADADO


def test_idade_precede_parcialidade_na_classificacao():
    """Dado velho E parcial é atrasado: a idade é o defeito maior."""
    ritmo = cad.cadencia(cad.MODO_NORMAL, config=CONFIG)
    velho = AGORA - timedelta(minutes=ritmo.sla_min + 1)
    assert cad.status(velho, ritmo, agora=AGORA, provedores_ok=1,
                      provedores_previstos=2) == cad.STATUS_ATRASADO


def test_nunca_coletou_nao_e_atualizado():
    ritmo = cad.cadencia(cad.MODO_NORMAL, config=CONFIG)
    assert cad.status(None, ritmo, agora=AGORA) == cad.STATUS_INDISPONIVEL


# ── 11 e 12. Reinicialização e frontend fechado ──────────────────────────────
def test_estado_vem_do_banco_e_nao_da_memoria_do_processo():
    """Reinicializar o servidor não pode reabrir a torneira de requisições.

    O módulo não guarda carimbo em variável global: quem responde "quando foi a
    última tentativa" é ``ler()``, que consulta o banco. Um processo novo
    encontra o mesmo freio que o anterior encontrou.
    """
    import inspect

    fonte = inspect.getsource(ec)
    assert "def ler(" in fonte
    # Nenhum carimbo mora em estado de módulo: só o guarda de schema.
    globais = [nome for nome, valor in vars(ec).items()
               if isinstance(valor, datetime)]
    assert globais == []


def test_o_job_roda_sem_streamlit(monkeypatch):
    """Frontend fechado: o caminho da coleta não importa a interface."""
    import sys

    est = EstadoFalso()
    est.instalar(monkeypatch)
    _preparar(monkeypatch, [_provedor_ok()])

    monkeypatch.setitem(sys.modules, "streamlit", None)
    saida = job.run(engine=None, agora=AGORA)

    assert saida["status"] == "success"


def test_o_job_nao_depende_de_session_state():
    import inspect

    fonte = inspect.getsource(job)
    assert "session_state" not in fonte
    assert "streamlit" not in fonte


# ── 13. Fuso horário ─────────────────────────────────────────────────────────
def test_carimbos_sao_comparados_em_utc(monkeypatch):
    """Carimbo ingênuo é lido como UTC, nunca como hora local de quem gravou."""
    ingenuo = datetime(2026, 9, 1, 9, 0)          # sem tzinfo
    consciente = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    ritmo = cad.cadencia(cad.MODO_NORMAL, config=CONFIG)

    a = cad.proximo_ciclo(ingenuo, ritmo, agora=AGORA)
    b = cad.proximo_ciclo(consciente, ritmo, agora=AGORA)
    assert a == b


def test_timezone_de_apresentacao_nao_muda_a_decisao(monkeypatch):
    """``NOTICIAS_TIMEZONE`` é de exibição. Trocá-lo não move gatilho nenhum."""
    est = EstadoFalso(ultima_tentativa=AGORA - timedelta(minutes=300))
    est.instalar(monkeypatch)
    _preparar(monkeypatch, [_provedor_ok()])

    monkeypatch.setattr(CONFIG, "NOTICIAS_TIMEZONE", "Asia/Tokyo",
                        raising=False)
    saida = job.run(engine=None, agora=AGORA)
    assert saida["status"] == "success"
    assert est.ultimo.iniciado_em.tzinfo is not None


# ── Universo por modo ────────────────────────────────────────────────────────
def test_universo_encolhe_conforme_a_frequencia_sobe(monkeypatch):
    monkeypatch.setattr(uni, "da_carteira",
                        lambda **kw: (("AAAA3", "BBBB4"), ""))
    monkeypatch.setattr(uni, "dos_candidatos",
                        lambda **kw: (("CCCC11",), ""))

    normal, _ = uni.montar(cad.MODO_NORMAL)
    crise, _ = uni.montar(cad.MODO_CRISE)

    assert normal == ("AAAA3", "BBBB4", "CCCC11")
    assert crise == ("AAAA3", "BBBB4"), "crise cobre só a carteira"


def test_truncagem_do_universo_e_declarada(monkeypatch):
    muitos = tuple(f"AAA{i:02d}3" for i in range(30))
    monkeypatch.setattr(uni, "da_carteira", lambda **kw: (muitos, ""))
    monkeypatch.setattr(uni, "dos_candidatos", lambda **kw: ((), ""))

    tickers, limitacoes = uni.montar(cad.MODO_NORMAL, limite=5)

    assert len(tickers) == 5
    assert any("truncado" in linha for linha in limitacoes)


def test_carteira_ilegivel_nao_derruba_a_coleta(monkeypatch):
    def _explode(**kw):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(
        "core.portfolio.repository.load_active_snapshots", _explode)
    tickers, motivo = uni.da_carteira()
    assert tickers == ()
    assert "indisponível" in motivo


# ── Saúde ────────────────────────────────────────────────────────────────────
def test_saude_nao_confunde_desconhecido_com_falha(monkeypatch):
    monkeypatch.setattr(saude, "get_engine", lambda: None)
    v = saude.checar_banco(engine=None)
    assert v.ok is None
    assert v.rotulo == "não verificado"


def test_agendador_parado_e_detectado(monkeypatch):
    parado = AGORA - timedelta(hours=48)
    monkeypatch.setattr(ec, "ler", lambda **kw: ec.EstadoGlobal(
        modo=cad.MODO_NORMAL, ultima_tentativa=parado, disponivel=True))

    v = saude.checar_agendador(agora=AGORA, config=CONFIG)

    assert v.ok is False
    assert "prevê uma a cada" in v.detalhe


def test_agendador_no_ritmo_passa(monkeypatch):
    monkeypatch.setattr(ec, "ler", lambda **kw: ec.EstadoGlobal(
        modo=cad.MODO_NORMAL, ultima_tentativa=AGORA - timedelta(minutes=30),
        disponivel=True))
    assert saude.checar_agendador(agora=AGORA, config=CONFIG).ok is True


def test_ciclo_preso_acusa_worker_morto(monkeypatch):
    monkeypatch.setattr(ec, "ultimos_ciclos", lambda n=10, **kw: (
        {"iniciado_em": AGORA - timedelta(hours=2), "concluido_em": None,
         "status": cad.STATUS_INDISPONIVEL},))

    v = saude.checar_worker(agora=AGORA)

    assert v.ok is False
    assert "sem conclusão" in v.detalhe


def test_resumo_conta_os_tres_estados():
    verificacoes = (
        saude.Verificacao("a", True, ""),
        saude.Verificacao("b", False, ""),
        saude.Verificacao("c", None, ""),
    )
    r = saude.resumo(verificacoes)
    assert (r["ok"], r["falha"], r["desconhecido"]) == (1, 1, 1)
    assert r["falhando"] == ("b",)


# ── Cota compartilhada entre execuções ───────────────────────────────────────
def test_cota_contada_em_arquivo_local_e_limitacao_declarada(monkeypatch):
    """Sem banco, o teto diário não atravessa execuções -- e isso é dito.

    O runner do Actions nasce com disco limpo. Um orçamento em arquivo faria
    cada uma das 48 execuções diárias se ver com a cota inteira do provedor: o
    teto existiria no código e não existiria na prática. Quando o armazém
    compartilhado não está disponível, o ciclo carrega a limitação escrita.
    """
    class SemBanco(ConsumoFalso):
        def disponivel(self):
            return False

    est = EstadoFalso()
    est.instalar(monkeypatch)
    _preparar(monkeypatch, [_provedor_ok()])
    monkeypatch.setattr(ec, "ConsumoBanco", lambda engine=None: SemBanco())
    # Orçamento sem caminho: nenhum arquivo é criado pelo teste.
    from core.noticias import rate_limit as rl
    original = rl.Orcamento
    monkeypatch.setattr(
        rl, "Orcamento",
        lambda *a, **kw: original(*a, **{**kw, "caminho": None}))

    job.run(engine=None, agora=AGORA)

    assert any("cota de requisições" in lim for lim in est.ultimo.limitacoes)


def test_armazem_compartilhado_guarda_o_consumo(monkeypatch):
    """Com banco, a marca de chamada sobrevive ao fim do processo."""
    from core.noticias.rate_limit import Orcamento

    armazem = ConsumoFalso()
    primeiro = Orcamento(caminho=None, armazem=armazem)
    primeiro.registrar("alphavantage")

    # Processo novo, mesmo armazém: o contador não reinicia.
    segundo = Orcamento(caminho=None, armazem=armazem)
    assert segundo.restante("alphavantage")["dia"] == 24
