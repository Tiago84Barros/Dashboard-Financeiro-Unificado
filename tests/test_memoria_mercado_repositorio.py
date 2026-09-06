"""Persistência: armazém local sim, Supabase não -- e isso é código, não doc.

A instrução desta entrega é literal: *"salve-as no banco de dados local e nunca
no Supabase, ele já está quase no limite."* O Supabase estava em 425 MB de 500
MB. Uma regra que existe só na documentação é uma regra que alguém quebra numa
sexta-feira à tarde, então ela mora em `exigir_local` e é exercitada aqui pelos
dois lados: destino remoto levanta, destino local grava.

Nenhum teste aqui abre conexão. A engine é falsa e registra o SQL executado --
`tests/conftest.py` recusa socket para fora do loopback, e um teste que só
passasse com o Docker de pé mediria o Docker.
"""
from __future__ import annotations

import inspect
import json

import pytest
from sqlalchemy.engine import make_url

from core.memoria_mercado import MEMORIA_MERCADO_VERSAO
from core.memoria_mercado import repositorio as repo
from scripts import construir_memoria_mercado as construtor
from tests.apoio_memoria import RUIDO, dias_uteis, evento, indice_plano

URL_LOCAL = f"postgresql://postgres:{'x' * 16}@localhost:5433/postgres"
URL_SUPABASE = (f"postgresql://postgres.abcdefgh:{'x' * 16}"
                "@aws-0-sa-east-1.pooler.supabase.com:6543/postgres")


class FalsoResultado:
    def __init__(self, linhas=(), rowcount: int = 0):
        self._linhas = list(linhas)
        self.rowcount = rowcount

    def mappings(self):
        return iter(self._linhas)


class FalsaConexao:
    """Registra o que foi executado. Não fala com banco nenhum."""

    def __init__(self, engine):
        self.engine = engine

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.engine.executados.append((sql, params))
        return self.engine.responder(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, *_excecao):
        return False


class FalsaEngine:
    def __init__(self, url: str = URL_LOCAL, *, linhas=(), rowcount: int = 0):
        self.url = make_url(url)
        self.executados: list[tuple] = []
        self._linhas = list(linhas)
        self._rowcount = rowcount

    def responder(self, sql: str, params):
        if sql.upper().startswith("SELECT"):
            return FalsoResultado(self._linhas)
        return FalsoResultado(rowcount=self._rowcount)

    def begin(self):
        return FalsaConexao(self)

    def sql_executado(self) -> str:
        return "\n".join(s for s, _ in self.executados)


@pytest.fixture(autouse=True)
def schema_nao_memoizado(monkeypatch):
    """`garantir_schema` guarda um flag de módulo; sem zerar, o segundo teste
    do arquivo não veria o DDL que o primeiro já rodou."""
    monkeypatch.setattr(repo, "_schema_pronto", False)


def um_evento():
    dias = dias_uteis(500)
    return evento("ATV", reacao=-0.06, dias=dias, offset=200,
                  indice=indice_plano(dias), setor="Energia")


# ── o portão de destino ───────────────────────────────────────────────────────

def test_destino_local_e_aceito():
    assert repo.e_local(FalsaEngine(URL_LOCAL))
    repo.exigir_local(FalsaEngine(URL_LOCAL))       # não levanta
    for host in ("127.0.0.1", "host.docker.internal", "dfu_warehouse"):
        assert repo.e_local(FalsaEngine(f"postgresql://u:p@{host}:5433/postgres"))


def test_destino_supabase_e_recusado_antes_de_qualquer_insert():
    remota = FalsaEngine(URL_SUPABASE)
    with pytest.raises(repo.DestinoRemotoRecusado):
        repo.exigir_local(remota)
    with pytest.raises(repo.DestinoRemotoRecusado):
        repo.gravar([um_evento()], remota)
    # A recusa veio ANTES da conexão: nada foi executado.
    assert remota.executados == []


def test_leitura_e_limpeza_tambem_recusam_destino_remoto():
    remota = FalsaEngine(URL_SUPABASE)
    for chamada in (lambda: repo.carregar_eventos(remota),
                    lambda: repo.limpar_tipo(remota, "resultado")):
        with pytest.raises(repo.DestinoRemotoRecusado):
            chamada()
    assert remota.executados == []


def test_outros_hosts_gerenciados_tambem_sao_recusados():
    for url in ("postgresql://u:p@db.projeto.supabase.co:5432/postgres",
                "postgresql://u:p@ep-x.neon.tech/db",
                "postgresql://u:p@rds.amazonaws.com:5432/db"):
        assert not repo.e_local(FalsaEngine(url))


def test_engine_ausente_e_recusa_e_nao_uma_gravacao_silenciosa():
    """`memoria: fallback-nunca-contradiz`: `None` aqui não pode virar "usa o
    default", porque o default do repositório é o Supabase."""
    with pytest.raises(repo.DestinoRemotoRecusado):
        repo.exigir_local(None)


def test_a_senha_nunca_aparece_no_texto_da_url():
    e = FalsaEngine(URL_LOCAL)
    assert "senha_secreta" not in repo.url_da_engine(e)
    assert "localhost:5433" in repo.url_da_engine(e)
    assert "senha_secreta" not in json.dumps(repo.gravar([um_evento()], e),
                                             default=str)


# ── a linha gravada ───────────────────────────────────────────────────────────

def test_linha_do_evento_e_pura_e_traz_o_que_a_tabela_declara():
    ev = um_evento()
    linha = repo.linha_evento(ev)

    colunas = {c.strip() for c in
               repo._UPSERT.split("(", 1)[1].split(")", 1)[0].split(",")}
    colunas.discard("atualizado_em")
    assert colunas <= set(linha)

    assert linha["versao_metodologia"] == MEMORIA_MERCADO_VERSAO
    assert linha["chave"] == ev.chave and linha["simbolo"] == "ATV"
    assert linha["setor"] == "Energia"
    assert isinstance(linha["janelas"], str)      # JSON, não dict
    janelas = json.loads(linha["janelas"])
    assert set(janelas) == {str(h) for h in ev.janelas}
    assert janelas["1"]["retorno_ativo"] == ev.janelas[1].retorno_ativo


def test_modelo_anormal_e_por_janela_e_a_coluna_junta_o_conjunto_ordenado():
    """Regressão: a coluna lia um `evento.modelo_anormal` que não existe. O
    modelo é por horizonte, e `medir_evento` pode degradar em um deles."""
    ev = um_evento()
    assert not hasattr(ev, "modelo_anormal")
    modelos = {j.modelo_anormal for j in ev.janelas.values() if j.modelo_anormal}
    assert repo.linha_evento(ev)["modelo_anormal"] == ",".join(sorted(modelos))


def test_evento_sem_benchmark_grava_modelo_nulo_e_nao_string_vazia():
    ev = evento("ATV", reacao=-0.06, indice=None)
    linha = repo.linha_evento(ev)
    assert linha["modelo_anormal"] is None
    assert linha["benchmark"] is None
    assert linha["benchmark_sintetico"] is False
    assert json.loads(linha["limitacoes"])         # a limitação viaja junto


def test_versao_entra_na_chave_e_safras_diferentes_coexistem():
    """`memoria: versao-de-metodologia-sem-safra`: subir a versão sem
    reconstruir a safra esvazia o painel em silêncio, então a versão é parte
    da chave e não um filtro solto."""
    ev = um_evento()
    assert repo.linha_evento(ev, versao="v9")["versao_metodologia"] == "v9"
    assert "PRIMARY KEY (versao_metodologia, chave)" in "\n".join(repo.DDL_SQL)


# ── gravação ──────────────────────────────────────────────────────────────────

def test_gravar_cria_o_schema_e_faz_upsert_no_destino_local():
    e = FalsaEngine()
    resumo = repo.gravar([um_evento()], e)

    assert resumo["gravado"] and resumo["linhas"] == 1
    assert resumo["versao"] == MEMORIA_MERCADO_VERSAO
    sql = e.sql_executado()
    assert f"CREATE SCHEMA IF NOT EXISTS {repo.ESQUEMA}" in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql
    # O alvo do conflito e a identidade do FATO, e nao a `chave` de texto: ate
    # 06/09/2026 o UNIQUE recaia sobre a chave composta pelo chamador, e trocar
    # o formato dela re-admitia o acervo inteiro (8.923 linhas para 4.463
    # eventos). Ver a migracao `ux_mm_eventos_fato` -- `memoria:
    # chave-de-texto-nao-e-identidade-do-fato`.
    assert ("ON CONFLICT (versao_metodologia, tipo_evento, simbolo, "
            "data_evento) DO UPDATE") in sql


def test_gravar_sem_eventos_nao_abre_conexao_nem_finge_que_gravou():
    e = FalsaEngine()
    resumo = repo.gravar([], e)
    assert resumo == {"gravado": False, "motivo": "nenhum evento medido",
                      "linhas": 0}
    assert e.executados == []


def test_cenarios_vao_para_a_tabela_propria_em_ordem_deterministica():
    e = FalsaEngine()
    resumo = repo.gravar([um_evento()], e,
                         cenarios={"z": {"juros_br": 2.0}, "a": {"juros_br": 13.0}})
    assert resumo["cenarios"] == 2
    params = [p for sql, p in e.executados
              if sql.startswith("INSERT") and f"{repo.ESQUEMA}.cenarios" in sql]
    assert [linha["chave"] for linha in params[0]] == ["a", "z"]


def test_limpeza_apaga_todas_as_versoes_do_tipo():
    """`memoria: remocao-escopada-pelo-filtro-da-leitura`: um DELETE filtrado
    pela versão corrente deixa a safra antiga fora de alcance para sempre."""
    e = FalsaEngine(rowcount=17)
    assert repo.limpar_tipo(e, "resultado") == 17
    delete = next(sql for sql, _ in e.executados if sql.startswith("DELETE"))
    assert "WHERE tipo_evento = :tipo" in delete
    assert "versao" not in delete


# ── leitura ───────────────────────────────────────────────────────────────────

def test_leitura_devolve_horizonte_como_inteiro_e_limitacoes_como_tupla():
    linhas = [{"chave": "k", "simbolo": "ATV", "tipo_evento": "resultado",
               "janelas": json.dumps({"1": {"retorno_ativo": -0.06},
                                      "20": {"retorno_ativo": -0.04}}),
               "limitacoes": json.dumps(["sem indice de referencia"])}]
    lidas = repo.carregar_eventos(FalsaEngine(linhas=linhas))
    assert set(lidas[0]["janelas"]) == {1, 20}       # int, não "1"
    assert lidas[0]["limitacoes"] == ("sem indice de referencia",)


def test_filtros_da_leitura_entram_como_parametro_e_nao_por_concatenacao():
    e = FalsaEngine()
    repo.carregar_eventos(e, tipo_evento="resultado", simbolo="ATV")
    sql, params = next((s, p) for s, p in e.executados if s.startswith("SELECT"))
    assert "tipo_evento = :tipo_evento" in sql and "simbolo = :simbolo" in sql
    assert params == {"versao": MEMORIA_MERCADO_VERSAO,
                      "tipo_evento": "resultado", "simbolo": "ATV"}
    assert "ORDER BY data_evento, chave" in sql     # leitura determinística


# ── o script construtor ───────────────────────────────────────────────────────

def precos_falsos(simbolos, dias):
    """Linhas no formato que `carregar_series` espera do armazém."""
    linhas = []
    for k, s in enumerate(simbolos):
        preco = 100.0
        for i, d in enumerate(dias):
            if i:
                preco *= 1.0 + RUIDO * ((i * 7 + k * 3) % 11 - 5) / 5.0
            linhas.append({"simbolo": s, "data": d, "fechamento": preco,
                           "volume": 1_000_000.0})
    return linhas


def test_construir_mede_os_eventos_e_relata_a_procedencia_dos_precos():
    dias = dias_uteis(600)
    simbolos = [f"ATV{i:02d}" for i in range(25)]
    e = FalsaEngine(linhas=precos_falsos(simbolos, dias))
    eventos = [{"chave": f"k{i}", "simbolo": s, "tipo_evento": "resultado",
                "data": dias[300].isoformat(), "cenario": {"juros_br": 13.0}}
               for i, s in enumerate(simbolos)]

    saida = construtor.construir(e, mercado="us", eventos=eventos)
    rel = saida["relatorio"]

    assert rel["eventos_medidos"] == 25
    assert rel["sem_serie_de_precos"] == 0
    assert rel["fonte_precos"] == "market_us.prices_daily"
    assert rel["serie_diaria"] is True
    assert rel["indice_sintetico"] is True
    assert rel["cenarios"] == 25
    assert all(ev.benchmark_sintetico for ev in saida["medidos"])


def test_evento_sem_serie_de_precos_e_contado_e_nao_estimado():
    """`memoria: quadro-sem-coluna-passa-por-empty`: falta de dado tem de sair
    contada no relatório, não virar um evento medido a partir de nada."""
    dias = dias_uteis(600)
    e = FalsaEngine(linhas=precos_falsos(["ATV00"], dias))
    eventos = [{"chave": "k0", "simbolo": "ATV00", "tipo_evento": "resultado",
                "data": dias[300].isoformat()},
               {"chave": "k1", "simbolo": "NAOEXISTE", "tipo_evento": "resultado",
                "data": dias[300].isoformat()}]

    saida = construtor.construir(e, mercado="us", eventos=eventos)
    assert saida["relatorio"]["eventos_medidos"] == 1
    assert saida["relatorio"]["sem_serie_de_precos"] == 1


def test_painel_estreito_nao_produz_indice_e_o_aviso_sai_no_relatorio():
    dias = dias_uteis(600)
    simbolos = ["ATV00", "ATV01"]
    e = FalsaEngine(linhas=precos_falsos(simbolos, dias))
    eventos = [{"chave": f"k{i}", "simbolo": s, "tipo_evento": "resultado",
                "data": dias[300].isoformat()} for i, s in enumerate(simbolos)]

    saida = construtor.construir(e, mercado="us", eventos=eventos)
    assert saida["relatorio"]["indice_sintetico"] is False
    assert "sem retorno anormal" in saida["relatorio"]["aviso_indice"]
    assert all(not ev.tem_retorno_anormal for ev in saida["medidos"])


def test_as_tres_fontes_sao_diarias_e_a_b3_nao_carrega_mais_o_aviso():
    """A assimetria acabou em 02/09/2026, e o relatório tem de acompanhar.

    Este teste afirmava o contrário: que ações da B3 tinham 1.542 datas em 26
    anos e que o relatório precisava avisar. A ingestão do COTAHIST
    (`data_pipeline/market/b3_precos.py`) trocou a fonte por
    `market.b3_security_history` -- 1.627.752 linhas em 4.134 pregões, 2010 a
    2026. Manter a asserção velha guardaria uma limitação que já não existe, e
    o aviso apareceria na tela dizendo que o horizonte de 1 dia sai não medido
    quando ele passa a ser medido.
    """
    e = FalsaEngine(linhas=[])
    saida = construtor.construir(e, mercado="b3", eventos=[
        {"chave": "k", "simbolo": "PETR4", "tipo_evento": "resultado",
         "data": "2020-01-02"}])
    assert saida["relatorio"]["serie_diaria"] is True
    assert "aviso_densidade" not in saida["relatorio"]
    assert all(fonte["diaria"] for fonte in construtor.FONTES.values())


def test_fonte_nao_diaria_ainda_carrega_o_aviso(monkeypatch):
    """O mecanismo continua necessário, mesmo sem nenhuma fonte usando-o hoje.

    Sem este teste, o aviso viraria código morto no dia em que a B3 passou a
    ser diária -- e a próxima fonte esparsa entraria calada, com os horizontes
    curtos saindo `None` sem que a tela dissesse por quê.
    """
    monkeypatch.setitem(construtor.FONTES["b3"], "diaria", False)
    e = FalsaEngine(linhas=[])
    saida = construtor.construir(e, mercado="b3", eventos=[
        {"chave": "k", "simbolo": "PETR4", "tipo_evento": "resultado",
         "data": "2020-01-02"}])
    assert saida["relatorio"]["serie_diaria"] is False
    assert "horizontes curtos" in saida["relatorio"]["aviso_densidade"]


def test_o_script_le_a_senha_do_container_e_nunca_de_uma_constante():
    """A senha do armazem sai do `docker inspect`, nao do codigo -- regra
    permanente do usuario: chave em variavel de ambiente, nunca no fonte."""
    fonte = inspect.getsource(construtor.warehouse_url)
    assert "docker" in fonte and "POSTGRES_PASSWORD=" in fonte
    assert "localhost:5433" in fonte
    assert "quote_plus(senha)" in fonte      # senha com caractere especial
    # E o modulo inteiro nao carrega nenhuma senha literal.
    modulo = inspect.getsource(construtor)
    assert "postgres:postgres@" not in modulo
