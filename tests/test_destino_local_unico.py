"""Uma guarda só, e o teste que impede uma quarta cópia de aparecer.

A guarda "isto não pode ser gravado na nuvem" existia **três** vezes, e as
cópias divergiram nas duas direções. Medido em 05/09/2026, antes da unificação:

    destino                        b3_precos    as outras duas
    dfu_warehouse (Docker)         RECUSA       aceita
    Supabase com host na query     aceita       RECUSA

A segunda linha é a cara: ~1 GB de preço diário apontados para uma instância
com 23 MB de folga, porque aquela cópia só olhava ``url.host`` e desistia
quando ele vinha vazio.

Metade destes testes defende o comportamento; a outra metade defende a
**unicidade**, porque foi a duplicação que produziu o defeito, não o algoritmo.
Um teste que só verificasse o comportamento passaria feliz no dia em que
alguém colar uma quarta cópia.
"""
import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from core import destino_local
from core.memoria_mercado import repositorio
from data_pipeline.market import b3_precos

RAIZ = Path(__file__).resolve().parents[1]

GUARDAS = (
    ("b3_precos", b3_precos.exigir_local),
    ("memoria_mercado", repositorio.exigir_local),
)


def _eng(url):
    return create_engine(url)


# ── comportamento ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("nome,guarda", GUARDAS)
def test_o_armazem_de_dentro_do_docker_e_aceito(nome, guarda):
    """``dfu_warehouse`` é o host do contêiner, e era recusado no b3_precos."""
    guarda(_eng("postgresql+psycopg2://u:s@dfu_warehouse:5432/dfu"))


@pytest.mark.parametrize("nome,guarda", GUARDAS)
def test_destino_de_nuvem_com_host_na_query_e_recusado(nome, guarda):
    """O falso positivo que a divergência escondia.

    ``url.host`` vem vazio nesta forma, e a cópia do ``b3_precos`` tratava host
    vazio como local. Quem lê a URL inteira vê o ``supabase.co`` lá.
    """
    with pytest.raises(destino_local.DestinoRemotoRecusado):
        guarda(_eng("postgresql+psycopg2://u:s@/postgres"
                    "?host=/cloudsql/db.abc.supabase.co"))


@pytest.mark.parametrize("nome,guarda", GUARDAS)
def test_supabase_direto_continua_recusado(nome, guarda):
    with pytest.raises(destino_local.DestinoRemotoRecusado):
        guarda(_eng("postgresql+psycopg2://u:s@db.abc.supabase.co:5432/postgres"))


@pytest.mark.parametrize("nome,guarda", GUARDAS)
def test_engine_ausente_e_recusada(nome, guarda):
    """``None`` não é "sem destino, então tanto faz"."""
    with pytest.raises(destino_local.DestinoRemotoRecusado):
        guarda(None)


@pytest.mark.parametrize("nome,guarda", GUARDAS)
def test_a_recusa_diz_o_que_estava_sendo_gravado(nome, guarda):
    """"Destino recusado" sem dizer o quê manda quem lê o log ler o código."""
    with pytest.raises(destino_local.DestinoRemotoRecusado) as exc:
        guarda(_eng("postgresql+psycopg2://u:s@db.abc.supabase.co/postgres"))
    assert str(exc.value).strip() != "destino recusado"
    assert len(str(exc.value)) > 40


@pytest.mark.parametrize("nome,guarda", GUARDAS)
def test_a_recusa_nunca_imprime_a_senha(nome, guarda):
    segredo = "s3nh4-que-nao-pode-vazar"
    with pytest.raises(destino_local.DestinoRemotoRecusado) as exc:
        guarda(_eng(f"postgresql+psycopg2://u:{segredo}@db.a.supabase.co/p"))
    assert segredo not in str(exc.value)


# ── unicidade: é a duplicação que produz a divergência ───────────────────────
def test_a_excecao_e_uma_classe_so():
    """``except`` de um módulo tem que pegar o que o outro levanta.

    Com três classes distintas, cada ``except`` pegava só um terço dos casos --
    e em silêncio, porque nada nisso dá erro.
    """
    assert (b3_precos.DestinoRemotoRecusado
            is repositorio.DestinoRemotoRecusado
            is destino_local.DestinoRemotoRecusado)


def test_a_lista_branca_de_hosts_e_uma_so():
    assert b3_precos.HOSTS_LOCAIS is destino_local.HOSTS_LOCAIS
    assert repositorio.HOSTS_LOCAIS is destino_local.HOSTS_LOCAIS


def test_ninguem_mais_define_a_guarda_por_conta_propria():
    """Impede a quarta cópia. Estrutural de propósito.

    Procura por definições de ``e_local``/``exigir_local`` e por listas de host
    local fora de ``core/destino_local.py``. Um teste de comportamento passaria
    feliz no dia em que alguém colasse mais uma cópia -- foi exatamente assim
    que esta divergência nasceu.
    """
    dono = (RAIZ / "core" / "destino_local.py").resolve()
    # Varre os diretorios de codigo, e nao a raiz inteira: `rglob` na raiz
    # entra nas worktrees do `.claude` e leva 90 s neste disco, o que e caro
    # demais para uma suite que ja roda em 7 min.
    fontes = [a for pasta in ("core", "data_pipeline", "scripts", "views",
                              "etl", "design")
              for a in (RAIZ / pasta).rglob("*.py")]
    infratores = []
    for arquivo in fontes:
        if arquivo.resolve() == dono:
            continue
        try:
            arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for no in ast.walk(arvore):
            if isinstance(no, ast.FunctionDef) and no.name in {"e_local"}:
                infratores.append(f"{arquivo.relative_to(RAIZ)}:{no.lineno} "
                                  f"define {no.name}")
            if isinstance(no, ast.Assign):
                for alvo in no.targets:
                    if (isinstance(alvo, ast.Name)
                            and alvo.id in {"HOSTS_LOCAIS",
                                            "FRAGMENTOS_REMOTOS"}):
                        infratores.append(
                            f"{arquivo.relative_to(RAIZ)}:{no.lineno} "
                            f"redefine {alvo.id}")
    quebra = chr(10) + "  "
    assert not infratores, (
        "a guarda de destino local voltou a ter copia:"
        + quebra + quebra.join(infratores))


def test_as_duas_guardas_concordam_em_todo_destino_testado():
    """A propriedade que a divergência quebrava, dita como propriedade."""
    urls = [
        "postgresql://u:s@dfu_warehouse:5432/d",
        "postgresql://u:s@localhost:5433/d",
        "postgresql://u:s@127.0.0.1:5432/d",
        "postgresql://u:s@host.docker.internal:5432/d",
        "postgresql://u:s@db.a.supabase.co/postgres",
        "postgresql://u:s@/p?host=/x/db.a.supabase.co",
        "postgresql://u:s@algum-host-desconhecido/d",
        "postgresql://u:s@x.pooler.supabase.com:6543/postgres",
        "postgresql://u:s@algo.neon.tech/d",
        "sqlite://",
    ]
    for url in urls:
        veredito = []
        for _, guarda in GUARDAS:
            try:
                guarda(_eng(url))
                veredito.append(True)
            except destino_local.DestinoRemotoRecusado:
                veredito.append(False)
        assert len(set(veredito)) == 1, f"guardas discordam sobre {url}"
