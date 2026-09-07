"""A justificativa da transição de nível sobrevive ao processo (Item 25).

Por que este arquivo existe
---------------------------
O Prompt 4 pede que toda decisão automática seja auditável. A decisão sobre
notícia já era (``noticias_avaliacoes.acao`` e ``.portoes``); a decisão sobre
**nível** não era. ``transicao.avaliar`` monta um veredito com nível bruto,
tetos aplicados, cobertura por classe de evidência e uma ``RegraAplicada`` por
regra que incidiu -- e desse veredito só o número do modo chegava ao banco, via
``estado_coleta.definir_modo``. O resto morria com o processo.

Não era erro: era a impossibilidade de responder "por que estamos no Nível 3?"
depois que o job termina.

O que se cobra aqui:

1. **A regra é gravada em campos, não em frase.** Texto formatado responde "o
   que apareceu na tela"; ``chave``/``efeito``/``de``/``para`` respondem "qual
   regra, com que efeito, movendo de quanto para quanto".
2. **Ausência é declarada, nunca vazio silencioso.** Trilha ilegível e trilha
   vazia são estados diferentes do mundo.
3. **O destino é o armazém local**, e a recusa de destino remoto é do módulo,
   não do chamador distraído.
4. **A fiação existe no job**, verificada por mutação -- e ela grava a trilha
   *antes* de a cadência ser decidida.
5. **O alvo do ``ON CONFLICT`` é o índice que o DDL cria.** Escritor e
   verificador lendo listas diferentes já produziu, neste projeto, migration
   certa e nunca executada.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from core.eventos_extremos import evidencias as ev
from core.eventos_extremos import niveis, transicao, trilha


def _veredito(*, n_fontes=3, materialidade=0.9, anterior=None):
    info = ev.informacional(
        fonte_oficial=True,
        n_fontes_independentes=n_fontes,
        confiabilidade_maxima=0.9,
        concordancia=1.0,
        horas_desde_publicacao=1.0,
        materialidade=materialidade,
        abrangencia=niveis.ABRANGENCIA_PAIS,
    )
    return transicao.avaliar(
        ev.Conjunto(informacional=info),
        abrangencia=niveis.ABRANGENCIA_PAIS,
        evento_id="evt-teste",
        anterior=anterior,
        agora=dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc))


# ─────────────────────────── o formato da linha ──────────────────────────────

def test_a_regra_e_gravada_em_campos_e_nao_so_em_frase():
    """``descrever()`` é para o log; a auditoria precisa dos campos.

    Guardar só a frase obrigaria a auditoria a fazer *parsing* de texto em
    português para responder "qual teto barrou o Nível 4" -- e a frase muda
    quando alguém melhora a redação, sem que nada avise.
    """
    v = _veredito()
    linha = trilha.linha(v)
    regras = json.loads(linha["regras"])

    assert regras, "nenhuma regra registrada: o veredito não se explica"
    for r in regras:
        assert set(r) == {"chave", "efeito", "motivo", "de", "para"}
    assert any(r["efeito"] == transicao.EFEITO_TETO for r in regras), (
        "sem evidência de mercado o teto de R6 tem de incidir e aparecer")


def test_o_nivel_bruto_e_o_teto_sao_gravados_junto_do_nivel_final():
    """"O 4 foi avaliado e barrado" é informação; "deu 3" não é.

    Gravar só ``nivel`` deixa a auditoria sem como distinguir um Nível 3 que
    nunca chegou perto do 4 de um Nível 3 que era 4 e foi barrado pela
    cobertura.
    """
    linha = trilha.linha(_veredito())

    assert linha["nivel"] == int(_veredito().nivel.codigo)
    assert linha["nivel_bruto"] >= linha["nivel"], (
        "nível final acima do bruto: os tetos deixariam de fazer sentido")
    assert linha["teto_aplicado"] is not None


def test_a_versao_da_metodologia_viaja_na_linha():
    """Um Nível 3 sob limiares antigos e outro sob os novos não são o mesmo
    fato. Sem a versão gravada, o histórico mistura os dois em silêncio."""
    linha = trilha.linha(_veredito())

    assert linha["versao_metodologia"] == transicao.EVENTOS_EXTREMOS_VERSAO


def test_o_nivel_anterior_ausente_e_none_e_nao_zero():
    """``0`` afirma "estava em Normal"; ``None`` diz "não havia estado".

    São coisas diferentes: a primeira avaliação de um evento não é uma subida
    a partir de Normal, e gravar zero inventaria uma transição que não houve.
    """
    assert trilha.linha(_veredito())["nivel_anterior"] is None

    anterior = transicao.Estado(nivel=niveis.NIVEL_ATENCAO,
                                desde=dt.datetime(2026, 9, 5, 6, 0,
                                                  tzinfo=dt.timezone.utc))
    com_anterior = trilha.linha(_veredito(anterior=anterior))
    assert com_anterior["nivel_anterior"] == niveis.NIVEL_ATENCAO


def test_a_cobertura_por_classe_de_evidencia_e_gravada():
    """A cobertura é o que sustenta o teto. Sem ela, a linha diz que houve teto
    e não diz por quê."""
    cobertura = json.loads(trilha.linha(_veredito())["cobertura"])

    assert cobertura, "cobertura vazia: o motivo do teto não fica registrado"
    assert all(isinstance(v, (int, float)) for v in cobertura.values())


# ──────────────────────── os caminhos de ausência ────────────────────────────

def test_sem_veredito_nao_ha_o_que_registrar():
    resultado = trilha.registrar(None)

    assert resultado["gravado"] is False
    assert resultado["motivo"]


def test_sem_armazem_a_ausencia_e_declarada(monkeypatch):
    """A trilha não pode derrubar a coleta -- mas também não pode calar."""
    monkeypatch.setattr(trilha, "engine_acervo", lambda: None)
    resultado = trilha.registrar(_veredito())

    assert resultado["gravado"] is False
    assert "nao foi persistida" in resultado["motivo"]


def test_leitura_sem_armazem_nao_vira_trilha_vazia(monkeypatch):
    """``((), ())`` diria "nenhuma transição aconteceu" -- uma afirmação sobre
    o mundo feita exatamente quando não se conseguiu olhar."""
    monkeypatch.setattr(trilha, "engine_acervo", lambda: None)
    linhas, limitacoes = trilha.ultimas()

    assert linhas == ()
    assert limitacoes, "trilha ilegível passou por trilha vazia"


def test_resolver_o_destino_tambem_esta_coberto_pela_promessa(monkeypatch):
    """O defeito que a suite achou na primeira versao deste modulo.

    ``engine_acervo()`` le ``settings``, e a resolucao ficava **fora** do
    ``try``: com configuracao incompleta, um ``AttributeError`` subia pelo job
    inteiro. Nove testes de infraestrutura quebraram de uma vez -- em producao
    seria o job de noticias caindo por causa da trilha que deveria apenas
    documenta-lo. "Nunca levanta" so vale se cobrir a linha que descobre para
    onde escrever.
    """
    def _explode():
        raise AttributeError("'ConfigFalsa' object has no attribute "
                             "'NOTICIAS_LOCAL_DB_URL'")

    monkeypatch.setattr(trilha, "engine_acervo", _explode)

    resultado = trilha.registrar(_veredito())
    assert resultado["gravado"] is False
    assert "NOTICIAS_LOCAL_DB_URL" in resultado["motivo"]

    linhas, limitacoes = trilha.ultimas()
    assert linhas == () and limitacoes


def test_gravacao_que_falha_nao_derruba_a_coleta(monkeypatch):
    """Exceção na gravação vira motivo, não vira traceback subindo pelo job."""
    class _Motor:
        def begin(self):
            raise RuntimeError("conexao recusada")

    monkeypatch.setattr(trilha, "exigir_local", lambda *a, **k: None)
    resultado = trilha.registrar(_veredito(), engine=_Motor())

    assert resultado["gravado"] is False
    assert "conexao recusada" in resultado["motivo"]


# ───────────────────────────── o destino local ───────────────────────────────

def test_o_destino_remoto_e_recusado_pelo_modulo():
    """A instrução do usuário é literal, e um ``engine=`` distraído não pode
    bastar para encher o banco de que a produção depende.

    A recusa vira motivo em vez de exceção -- a coleta não cai por causa da
    trilha --, mas ela **acontece**: nada é gravado.
    """
    import inspect

    fonte = inspect.getsource(trilha.registrar)
    assert "exigir_local(motor" in fonte, (
        "a guarda de destino local saiu de registrar(): a trilha voltaria a "
        "poder ser gravada no Supabase por um engine= distraído")


# ────────────────────────── escritor x verificador ───────────────────────────

def test_o_alvo_do_on_conflict_e_o_indice_que_o_ddl_cria():
    """Migration certa, registrada e nunca executada já aconteceu aqui.

    Se o ``ON CONFLICT`` citar colunas que o índice único não tem, o
    ``INSERT`` levanta em produção -- e o caminho de erro engole a exceção,
    então o sintoma seria "a trilha nunca grava", sem erro nenhum visível.
    """
    ddl = " ".join(trilha.DDL_SQL)
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ix_trilha_ciclo_versao" in ddl
    assert "(ciclo_em, versao_metodologia)" in ddl
    assert "WHERE ciclo_em IS NOT NULL" in ddl

    sql = str(trilha._UPSERT)
    assert "ON CONFLICT (ciclo_em, versao_metodologia)" in sql
    assert "WHERE ciclo_em IS NOT NULL" in sql


def test_a_chave_e_carimbo_de_ciclo_e_nao_numero_de_sequencia():
    """Sequência que reinicia já colidiu neste projeto e fez um portão declarar
    "coberto" lendo procedência de outro payload. ``id`` existe para ordenar,
    não para identificar o fato.
    """
    l1 = trilha.linha(_veredito(), ciclo_em=dt.datetime(2026, 9, 5, 12, 0,
                                                        tzinfo=dt.timezone.utc))
    assert l1["ciclo_em"] is not None
    assert "id" not in l1, "a linha carrega id próprio: a chave voltou a ser sequência"


# ──────────────────────────────── a fiação ───────────────────────────────────

def test_o_job_registra_a_trilha_antes_de_decidir_a_cadencia():
    """Sem esta chamada, o Item 25 reabre sem deixar rastro no log.

    A ordem também importa: a trilha documenta o veredito, e decidir a cadência
    primeiro não quebraria nada hoje -- mas deixaria o registro dependente de um
    caminho que pode retornar cedo amanhã.
    """
    import inspect

    from data_pipeline.jobs import update_noticias as job

    fonte = inspect.getsource(job._executar)
    assert "trilha.registrar(veredito" in fonte, (
        "o job deixou de persistir a justificativa da transição")
    assert "ciclo_em=ciclo.iniciado_em" in fonte, (
        "a trilha foi gravada sem carimbo de ciclo: perde a chave natural e "
        "cada reexecução vira um fato novo")
    assert (fonte.index("trilha.registrar(veredito")
            < fonte.index("nivel_cadencia = da_coleta.nivel_para_cadencia"))


def test_o_job_nao_manda_a_trilha_para_o_supabase():
    """``engine`` no job é o do Supabase. Passá-lo aqui mandaria a trilha para
    o banco que a instrução do usuário exclui -- e ``exigir_local`` a recusaria
    todo ciclo, gravando nada com cara de gravar."""
    import inspect

    from data_pipeline.jobs import update_noticias as job

    fonte = inspect.getsource(job._executar)
    chamada = fonte[fonte.index("trilha.registrar("):]
    chamada = chamada[:chamada.index(")") + 1]
    assert "engine=" not in chamada, chamada


def test_a_falha_da_trilha_chega_as_limitacoes_do_ciclo():
    """"Não persisti a auditoria" tem de aparecer no relatório da execução.
    Um log de warning num runner do Actions é onde as coisas vão morrer."""
    import inspect

    from data_pipeline.jobs import update_noticias as job

    fonte = inspect.getsource(job._executar)
    assert "trilha de auditoria da transicao nao persistida" in fonte


@pytest.mark.parametrize("campo", ["nivel", "nivel_bruto", "severidade",
                                    "confianca", "regras", "cobertura",
                                    "limitacoes", "abrangencia", "evento_id",
                                    "notificar", "avaliado_em"])
def test_toda_coluna_do_ddl_tem_valor_na_linha(campo):
    """Coluna que o DDL cria e o ``INSERT`` não preenche fica ``NULL`` para
    sempre, sem erro -- e a auditoria descobre isso no dia em que precisar."""
    linha = trilha.linha(_veredito())
    assert campo in linha
    assert f"{campo}," in str(trilha._UPSERT) or f"{campo})" in str(trilha._UPSERT)
