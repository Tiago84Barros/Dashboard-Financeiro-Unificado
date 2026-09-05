"""Os medidores de homologação, contra um Postgres de verdade.

Por que não com dublê
---------------------
O que estes medidores fazem é quase tudo SQL: ``COUNT(*) FILTER``,
``jsonb_array_length``, ``NULLIF(TRIM(...))``, aritmética de intervalo. Um dublê
de engine testaria o ``if`` do piso de amostra e deixaria passar exatamente a
metade que pode estar errada -- e neste projeto já houve o caso do gate que lia
uma tabela e o escritor que gravava em outra
(``memoria: verificador-e-escritor-listas-diferentes``).

Então o teste cria as tabelas num schema descartável do armazém local, escreve
linhas com defeito conhecido e cobra o número. Sem armazém no ar, ele **pula**
-- e pular é dizer que não foi medido, que é a mesma regra que o módulo sob
teste aplica a si mesmo.

O que cada caso protege
-----------------------
O grupo mais importante é o do piso de amostra. Um acervo vazio devolve zero
notícias sem fonte e zero ciclos com erro; os dois critérios são "menor
melhor", e os dois passariam por não terem sido testados. É o defeito de
``memoria: zero-censura-e-assinatura`` -- ausência lida como aprovação.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import create_engine, text

from core.auditoria import trilha
from core.homologacao import medicoes as M

SCHEMA = "app4_medicoes_teste"

_DDL = [
    f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",
    f"CREATE SCHEMA {SCHEMA}",
    f"""CREATE TABLE {SCHEMA}.noticias_itens (
            id_dedup     TEXT PRIMARY KEY,
            titulo       TEXT NOT NULL,
            dominio      TEXT,
            veiculo      TEXT,
            publicado_em TIMESTAMPTZ,
            coletado_em  TIMESTAMPTZ NOT NULL
        )""",
    f"""CREATE TABLE {SCHEMA}.noticias_coleta_ciclos (
            id          BIGSERIAL PRIMARY KEY,
            iniciado_em TIMESTAMPTZ NOT NULL,
            status      TEXT NOT NULL,
            erros       JSONB NOT NULL DEFAULT '[]'::jsonb
        )""",
    f"""CREATE TABLE {SCHEMA}.recomendacao_auditoria (
            id            TEXT PRIMARY KEY,
            momento       TIMESTAMPTZ NOT NULL,
            motivo        TEXT NOT NULL DEFAULT '',
            evidencias    JSONB NOT NULL DEFAULT '[]'::jsonb,
            motor         TEXT NOT NULL DEFAULT '',
            versao_modelo TEXT NOT NULL DEFAULT '',
            versao_dados  TEXT NOT NULL DEFAULT '',
            llm_aprovada  BOOLEAN
        )""",
]


@pytest.fixture(scope="module")
def engine():
    """Armazém local, com ``search_path`` apontado para o schema descartável.

    O ``search_path`` é o que faz as consultas do módulo -- que citam
    ``noticias_itens`` sem schema -- caírem nas tabelas do teste em vez de nas
    de produção. É também o que garante que um erro de nome de coluna apareça
    aqui como erro, e não como zero.
    """
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        url = _warehouse_url()
        motor = create_engine(
            url, connect_args={"options": f"-csearch_path={SCHEMA},public"})
        with motor.begin() as conn:
            for ddl in _DDL:
                conn.execute(text(ddl))
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


@pytest.fixture(autouse=True)
def _sem_engine_de_producao(monkeypatch, engine):
    """Nenhum medidor deste arquivo pode alcançar o Supabase."""
    monkeypatch.setattr(M, "_engine", lambda: engine)
    monkeypatch.setattr(trilha, "TABELA", f"{SCHEMA}.recomendacao_auditoria")


@pytest.fixture(autouse=True)
def _limpar(engine):
    with engine.begin() as conn:
        for tabela in ("noticias_itens", "noticias_coleta_ciclos",
                       "recomendacao_auditoria"):
            conn.execute(text(f"TRUNCATE {SCHEMA}.{tabela}"))
    yield


def _agora(horas: float = 0.0) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=horas)


def _noticias(engine, *, n: int, sem_data: int = 0, sem_fonte: int = 0,
              dias_atras: float = 1.0) -> None:
    with engine.begin() as conn:
        for i in range(n):
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.noticias_itens
                    (id_dedup, titulo, dominio, veiculo, publicado_em,
                     coletado_em)
                VALUES (:id, 'titulo', :dom, :vei, :pub, :col)
            """), {
                "id": str(uuid.uuid4()),
                "dom": None if i < sem_fonte else "exemplo.com",
                # Veículo em branco não é fonte: o critério pergunta se dá
                # para dizer *de onde* veio, e string vazia não diz.
                "vei": "   " if i < sem_fonte else "Exemplo",
                "pub": None if i < sem_data else _agora(24 * dias_atras),
                "col": _agora(24 * dias_atras),
            })


def _ciclos(engine, *, n: int, falhos: int = 0, por_erro: int = 0) -> None:
    with engine.begin() as conn:
        for i in range(n):
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.noticias_coleta_ciclos
                    (iniciado_em, status, erros)
                VALUES (:ini, :st, CAST(:err AS jsonb))
            """), {
                "ini": _agora(24),
                "st": "degradado" if i < falhos else "atualizado",
                "err": ('["provedor fora"]' if falhos <= i < falhos + por_erro
                        else "[]"),
            })


def _registros(engine, *, n: int, incompletos: int = 0,
               julgadas: int = 0, reprovadas: int = 0) -> None:
    with engine.begin() as conn:
        for i in range(n):
            julgada = i < julgadas
            conn.execute(text(f"""
                INSERT INTO {SCHEMA}.recomendacao_auditoria
                    (id, momento, motivo, evidencias, motor, versao_modelo,
                     versao_dados, llm_aprovada)
                VALUES (:id, :m, :motivo, CAST(:ev AS jsonb), :motor, :vm, :vd,
                        :llm)
            """), {
                "id": str(uuid.uuid4()), "m": _agora(24),
                "motivo": "" if i < incompletos else "queda de 12% em 3 dias",
                "ev": "[]" if i < incompletos else '["ibov -12%"]',
                "motor": "" if i < incompletos else "eventos_extremos",
                "vm": "" if i < incompletos else "v3",
                "vd": "" if i < incompletos else "2026-09-01",
                "llm": (i < reprovadas) is False if julgada else None,
            })


# ── O piso de amostra ────────────────────────────────────────────────────────

def test_acervo_vazio_nao_vira_zero_item_sem_fonte():
    """Zero de zero é ausência, não aprovação."""
    m = M.medir_detalhado()["itens_sem_fonte"]
    assert m.valor is None
    assert m.amostra == 0
    assert "amostra insuficiente" in m.motivo
    assert f"mínimo de {M.MINIMO_ITENS}" in m.motivo


def test_um_ciclo_so_nao_vira_taxa_de_erro_zero(engine):
    _ciclos(engine, n=1)
    m = M.medir_detalhado()["taxa_de_erro_da_coleta"]
    assert m.valor is None and m.amostra == 1
    assert "amostra insuficiente" in m.motivo


def test_o_criterio_com_amostra_pequena_nao_avanca_a_fase(engine):
    """Não medido não avança e não reprova -- nem quando o número seria bom."""
    from core.homologacao import criterios as C

    _noticias(engine, n=3)
    avaliacao = C.avaliar(1, M.medir())
    assert "itens_sem_fonte" in avaliacao.nao_medidos
    assert "itens_sem_fonte" not in avaliacao.reprovados
    assert not avaliacao.pode_avancar


# ── Os números, quando há amostra ────────────────────────────────────────────

def test_cobertura_de_frescor_conta_quem_tem_carimbo(engine):
    _noticias(engine, n=40, sem_data=10)
    m = M.medir_detalhado()["cobertura_de_frescor"]
    assert m.valor == pytest.approx(30 / 40)
    assert m.amostra == 40


def test_item_com_veiculo_em_branco_conta_como_sem_fonte(engine):
    """``TRIM`` no SQL: espaço não é procedência, e o teste cobra isso."""
    _noticias(engine, n=40, sem_fonte=4)
    m = M.medir_detalhado()["itens_sem_fonte"]
    assert m.valor == 4.0
    assert m.amostra == 40


def test_itens_sem_fonte_e_contagem_e_nao_taxa(engine):
    """Numa amostra grande, uma taxa arredondaria os poucos para zero."""
    _noticias(engine, n=400, sem_fonte=1)
    assert M.medir_detalhado()["itens_sem_fonte"].valor == 1.0


def test_ciclo_com_erro_registrado_conta_como_falho(engine):
    """Status utilizável e provedor perdido no caminho ainda é falha."""
    _ciclos(engine, n=20, falhos=2, por_erro=3)
    m = M.medir_detalhado()["taxa_de_erro_da_coleta"]
    assert m.valor == pytest.approx(5 / 20)


def test_cobertura_da_trilha_reprova_registro_sem_por_que(engine):
    _registros(engine, n=20, incompletos=5)
    m = M.medir_detalhado()["cobertura_da_trilha"]
    assert m.valor == pytest.approx(15 / 20)


def test_reprovacao_da_llm_tem_denominador_proprio(engine):
    """Divide pelas julgadas, não pelo total.

    Com 40 recomendações e 20 explicações validadas, dividir pelo total faria a
    taxa cair pela metade justamente nos períodos em que a LLM esteve
    desligada: o critério melhoraria por não ter sido exercido.
    """
    _registros(engine, n=40, julgadas=20, reprovadas=4)
    m = M.medir_detalhado()["respostas_llm_reprovadas"]
    assert m.valor == pytest.approx(4 / 20)
    assert m.amostra == 20


# ── A janela ─────────────────────────────────────────────────────────────────

def test_noticia_fora_da_janela_nao_entra_na_conta(engine):
    _noticias(engine, n=40, dias_atras=M.JANELA_DIAS + 5)
    m = M.medir_detalhado()["cobertura_de_frescor"]
    assert m.valor is None and m.amostra == 0


# ── A falha ──────────────────────────────────────────────────────────────────

def test_tabela_ausente_diz_que_falhou_e_nao_diz_zero(monkeypatch, engine):
    """Erro de leitura precisa parecer erro.

    ``memoria: quadro-sem-coluna-passa-por-empty`` -- uma falha de leitura que
    devolve resultado vazio vira uma afirmação sobre o mundo. Aqui ela tem de
    virar ``None`` com o motivo, e o motivo tem de citar a falha.
    """
    monkeypatch.setattr(M, "_SQL_ACERVO",
                        "SELECT 1 FROM tabela_que_nao_existe WHERE :dias > 0")
    detalhe = M.medir_detalhado()
    for nome in ("cobertura_de_frescor", "itens_sem_fonte"):
        assert detalhe[nome].valor is None
        assert "a medição falhou" in detalhe[nome].motivo
    # E o erro do acervo não pode carimbar os critérios da trilha.
    assert "a medição falhou" not in detalhe["cobertura_da_trilha"].motivo


def test_falha_de_um_lote_nao_derruba_os_outros_medidores(monkeypatch, engine):
    monkeypatch.setattr(M, "_SQL_ACERVO", "SELECT 1 FROM nada WHERE :dias > 0")
    assert M.medir_detalhado()["cenarios_historicos_reproduzidos"].valor == 11.0


# ── O que a tela escreve ─────────────────────────────────────────────────────

def test_situacao_distingue_amostra_pequena_de_medidor_ausente(engine):
    _ciclos(engine, n=2)
    detalhe = M.medir_detalhado()
    assert "amostra insuficiente" in M.situacao("taxa_de_erro_da_coleta",
                                                detalhe)
    assert "operação real observada" in M.situacao(
        "falsos_positivos_nivel_3_ou_4", detalhe)


def test_situacao_mostra_o_tamanho_da_amostra_junto_do_numero(engine):
    _noticias(engine, n=40, sem_data=10)
    texto = M.situacao("cobertura_de_frescor", M.medir_detalhado())
    assert texto.startswith("medido: 0.75")
    assert "amostra: 40" in texto


def test_todo_criterio_de_toda_fase_tem_medidor_ou_motivo():
    """Nenhum critério pode ficar sem medidor **e** sem explicação."""
    from core.homologacao import criterios as C

    for fase, criterios in C.EXIGIDO.items():
        for c in criterios:
            assert c.nome in M.COBERTOS or c.nome in M.SEM_MEDIDOR, (
                f"critério {c.nome} da fase {fase} não tem medidor nem motivo")


def test_nenhum_criterio_esta_nos_dois_lados():
    """Medidor e desculpa ao mesmo tempo esconderiam qual dos dois vale."""
    assert not (M.COBERTOS & set(M.SEM_MEDIDOR))
