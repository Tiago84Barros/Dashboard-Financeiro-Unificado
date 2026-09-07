"""A vitrine do noticiário, contra um Postgres de verdade.

Por que não com dublê
---------------------
O que a vitrine faz é quase tudo SQL de tipo específico: ``JSONB`` de ida e
volta, ``TIMESTAMPTZ`` com fuso, ``= ANY(:simbolos)``, ``ON CONFLICT`` e um
``DELETE`` deliberadamente sem ``WHERE``. Um dublê de engine confirmaria a
aritmética do carimbo e deixaria passar exatamente a metade que decide o que
chega à produção.

Os dois casos que este arquivo existe para proteger
---------------------------------------------------
**Substituição sem filtro.** Já houve neste projeto um ``DELETE`` escopado pela
versão corrente da metodologia: ao subir a versão, as linhas antigas ficaram
fora do alcance do próprio publicador e 70% da vitrine dos EUA virou metodologia
morta e imortal (``memoria: remocao-escopada-pelo-filtro-da-leitura``). Aqui isso
é cobrado publicando em duas versões diferentes.

**Vitrine velha não pode virar conjuntura de hoje.** Como a vitrine é
substituída, ela sai do publicador com a mesma cara todo dia; uma que parou de
ser publicada em julho chega à tela idêntica à de hoje
(``memoria: aviso-que-envelhece-invertido``). O teste envelhece o carimbo e cobra
que o valor saia do denominador — sem apagar os itens, que têm data própria.
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from core.conjuntura import ponte
from core.noticias import vitrine as vit

SCHEMA = "app4_vitrine_teste"


@pytest.fixture(scope="module")
def engine():
    """Armazém local com ``search_path`` no schema descartável.

    O módulo cita ``noticias_vitrine`` sem schema; o ``search_path`` é o que faz
    a consulta cair aqui em vez de na produção — e é também o que faz um erro de
    nome de coluna aparecer como erro em vez de virar zero.
    """
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(
            _warehouse_url(),
            connect_args={"options": f"-csearch_path={SCHEMA},public"})
        with motor.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


@pytest.fixture
def vazia(engine):
    """Schema com as tabelas criadas e sem linha nenhuma."""
    with engine.begin() as conn:
        vit.garantir_schema(conn)
        conn.execute(text(f"TRUNCATE {SCHEMA}.noticias_vitrine"))
        conn.execute(text(f"TRUNCATE {SCHEMA}.noticias_vitrine_meta"))
    return engine


def _item(titulo: str, *, veiculo: str = "Valor", horas: float = 3.0):
    return ponte.ItemNoticiaBruto(
        simbolo="X", titulo=titulo, veiculo=veiculo,
        url=f"https://exemplo/{titulo}",
        publicado_em=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=horas),
        tipo_evento=None, nota=70.0, direcao="alta", confianca=0.8,
        sentimento=0.5)


def _leitura(simbolo: str, valor, *, n=3, itens=(), motivo=""):
    return ponte.LeituraNoticias(simbolo=simbolo, valor=valor, n_itens=n,
                                 itens=tuple(itens), motivo=motivo)


# ── o achatamento, sem banco ────────────────────────────────────────────────

def test_valor_nulo_atravessa_como_nulo():
    """Não medido tem de continuar não medido; 0.0 seria uma leitura neutra."""
    linha = vit.linha_da_leitura(
        _leitura("PETR4", None, n=1, motivo="amostra insuficiente"),
        versao="1.0.0", janela_dias=30, gerada_em=dt.datetime.now(dt.timezone.utc))
    assert linha["valor"] is None
    assert linha["motivo"] == "amostra insuficiente"


def test_guarda_no_maximo_tres_itens():
    linha = vit.linha_da_leitura(
        _leitura("VALE3", 40.0, n=9, itens=[_item(f"n{i}") for i in range(9)]),
        versao="1.0.0", janela_dias=30, gerada_em=dt.datetime.now(dt.timezone.utc))
    import json

    assert len(json.loads(linha["itens"])) == vit.ITENS_POR_ATIVO
    # n_itens continua sendo a amostra inteira: cortar a citação não pode
    # encolher o tamanho da amostra que justificou o valor.
    assert linha["n_itens"] == 9


def test_item_guarda_veiculo_e_data():
    """Procedência é exigência de exibição — se ela não viaja, a tela mente."""
    import json

    linha = vit.linha_da_leitura(
        _leitura("ITUB4", 10.0, itens=[_item("fato", veiculo="Reuters")]),
        versao="1.0.0", janela_dias=30, gerada_em=dt.datetime.now(dt.timezone.utc))
    item = json.loads(linha["itens"])[0]
    assert item["veiculo"] == "Reuters"
    assert item["publicado_em"] is not None


# ── a substituição ──────────────────────────────────────────────────────────

def test_publicar_substitui_a_vitrine_inteira(vazia):
    vit.publicar(vazia, [_leitura("AAA", 10.0), _leitura("BBB", -20.0)],
                 versao="1.0.0", janela_dias=30)
    vit.publicar(vazia, [_leitura("CCC", 5.0)], versao="1.0.0", janela_dias=30)
    linhas, _ = vit.ler(vazia, ["AAA", "BBB", "CCC"])
    assert {linha["simbolo"] for linha in linhas} == {"CCC"}


def test_troca_de_versao_nao_deixa_linha_imortal(vazia):
    """O ``DELETE`` sem ``WHERE`` é o remédio para um defeito já pago aqui."""
    vit.publicar(vazia, [_leitura("AAA", 10.0)], versao="1.0.0", janela_dias=30)
    vit.publicar(vazia, [_leitura("BBB", 10.0)], versao="2.0.0", janela_dias=30)
    with vazia.connect() as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM {SCHEMA}.noticias_vitrine")).scalar()
        versoes = {r[0] for r in conn.execute(text(
            f"SELECT DISTINCT versao_metodologia FROM {SCHEMA}.noticias_vitrine"))}
    assert total == 1 and versoes == {"2.0.0"}


def test_meta_separa_nunca_publicada_de_publicada_vazia(vazia):
    _, meta = vit.ler(vazia, ["AAA"])
    assert meta is None, "sem publicação, a meta tem de ser ausente"

    vit.publicar(vazia, [], versao="1.0.0", janela_dias=30)
    linhas, meta = vit.ler(vazia, ["AAA"])
    assert linhas == () and meta is not None
    assert meta["ativos"] == 0, "publicada e vazia é um estado distinto"


def test_meta_conta_medidos_e_nao_medidos(vazia):
    vit.publicar(vazia, [_leitura("AAA", 10.0), _leitura("BBB", None)],
                 versao="1.0.0", janela_dias=30, itens_no_acervo=7)
    _, meta = vit.ler(vazia, ["AAA"])
    assert (meta["ativos"], meta["ativos_medidos"]) == (2, 1)
    assert meta["itens_no_acervo"] == 7


# ── a leitura ───────────────────────────────────────────────────────────────

def test_ler_falha_levanta_em_vez_de_devolver_vazio(engine):
    """Tabela ausente não pode ter a mesma cara de vitrine sem notícia."""
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {SCHEMA}.noticias_vitrine"))
        conn.execute(text(f"DROP TABLE IF EXISTS {SCHEMA}.noticias_vitrine_meta"))
    with pytest.raises(vit.VitrineIlegivel):
        vit.ler(engine, ["AAA"])


def test_ler_nao_cria_tabela(engine):
    """Ler não pode gastar espaço do Supabase nem apagar a evidência da falta."""
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {SCHEMA}.noticias_vitrine"))
        conn.execute(text(f"DROP TABLE IF EXISTS {SCHEMA}.noticias_vitrine_meta"))
    with pytest.raises(vit.VitrineIlegivel):
        vit.ler(engine, ["AAA"])
    with engine.connect() as conn:
        existe = conn.execute(text("""
            SELECT count(*) FROM information_schema.tables
             WHERE table_schema = :s AND table_name = 'noticias_vitrine'
        """), {"s": SCHEMA}).scalar()
    assert existe == 0


# ── o carimbo, que é onde a vitrine pode enganar ────────────────────────────

def _publicar_com_idade(engine, horas: float):
    gerada = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=horas)
    vit.publicar(engine, [_leitura("AAA", 42.0, itens=[_item("fato")])],
                 versao="1.0.0", janela_dias=30, gerada_em=gerada)
    return gerada


def test_vitrine_fresca_move_peso(vazia):
    _publicar_com_idade(vazia, horas=2)
    leituras, carimbo = ponte._ler_vitrine(vazia, simbolos=["AAA"])
    assert carimbo is not None and carimbo.fresca
    assert leituras["AAA"].valor == pytest.approx(42.0)


def test_vitrine_velha_nao_move_peso_mas_guarda_os_itens(vazia):
    _publicar_com_idade(vazia, horas=ponte.MAX_IDADE_VITRINE_HORAS + 24)
    leituras, carimbo = ponte._ler_vitrine(vazia, simbolos=["AAA"])
    leitura = leituras["AAA"]
    assert not carimbo.fresca
    assert leitura.valor is None, "vitrine velha não é leitura corrente"
    assert leitura.itens, "os itens ficam: cada um traz a própria data"
    assert "vitrine de" in leitura.motivo and "h" in leitura.motivo


def test_o_carimbo_diz_a_data_em_texto(vazia):
    _publicar_com_idade(vazia, horas=5)
    _, carimbo = ponte._ler_vitrine(vazia, simbolos=["AAA"])
    assert "vitrine de" in carimbo.texto and "UTC" in carimbo.texto


def test_carimbo_velho_avisa_no_proprio_texto(vazia):
    _publicar_com_idade(vazia, horas=ponte.MAX_IDADE_VITRINE_HORAS + 1)
    _, carimbo = ponte._ler_vitrine(vazia, simbolos=["AAA"])
    assert "velha demais" in carimbo.texto


# ── a escolha entre acervo e vitrine ────────────────────────────────────────

class _EngineQueLevanta:
    def connect(self):
        raise RuntimeError("banco fora do ar")


def test_acervo_disponivel_tem_precedencia_sobre_a_vitrine(vazia, monkeypatch):
    """Acervo local vazio é um fato desta máquina, não um convite à vitrine."""
    _publicar_com_idade(vazia, horas=1)
    monkeypatch.setattr(
        ponte, "_ler_noticias",
        lambda *a, **k: {"AAA": _leitura("AAA", 7.0, motivo="do acervo")})
    ctx = ponte.carregar(asset_class="acoes_br", ativos={"AAA": "energia"},
                         noticias_engine=vazia, vitrine_engine=vazia)
    assert ctx.fonte_noticias == "acervo"
    assert ctx.carimbo_vitrine is None
    assert ctx.leituras["AAA"].valor == pytest.approx(7.0)


def test_sem_acervo_a_vitrine_entra_e_a_origem_fica_declarada(vazia):
    _publicar_com_idade(vazia, horas=1)
    ctx = ponte.carregar(asset_class="acoes_br", ativos={"AAA": "energia"},
                         noticias_engine=None, vitrine_engine=vazia)
    assert ctx.fonte_noticias == "vitrine"
    assert ctx.leituras["AAA"].valor == pytest.approx(42.0)
    assert any("vindas da vitrine" in lim for lim in ctx.limitacoes)


def test_vitrine_ilegivel_e_falha_declarada_e_nao_calmaria(vazia):
    ctx = ponte.carregar(asset_class="acoes_br", ativos={"AAA": "energia"},
                         noticias_engine=None,
                         vitrine_engine=_EngineQueLevanta())
    assert ctx.acervo_falhou is True
    assert not ctx.leituras
    assert any("não pôde ser lida" in lim for lim in ctx.limitacoes)


def test_nunca_publicada_aparece_como_ausencia_de_publicacao(vazia):
    ctx = ponte.carregar(asset_class="acoes_br", ativos={"AAA": "energia"},
                         noticias_engine=None, vitrine_engine=vazia)
    assert any("nunca foi publicada" in lim for lim in ctx.limitacoes)
    assert ctx.acervo_falhou is False


# ── o prompt ────────────────────────────────────────────────────────────────

def test_o_prompt_diz_que_a_leitura_veio_da_vitrine(vazia):
    _publicar_com_idade(vazia, horas=1)
    ctx = ponte.carregar(asset_class="acoes_br", ativos={"AAA": "energia"},
                         noticias_engine=None, vitrine_engine=vazia)
    bloco = ponte.para_llm(ctx)
    assert "VITRINE publicada" in bloco
    assert "vitrine de" in bloco
    assert "diga a data" in bloco


def test_o_prompt_do_acervo_nao_carimba_vitrine(vazia, monkeypatch):
    monkeypatch.setattr(
        ponte, "_ler_noticias",
        lambda *a, **k: {"AAA": _leitura("AAA", 7.0)})
    ctx = ponte.carregar(asset_class="acoes_br", ativos={"AAA": "energia"},
                         noticias_engine=vazia, vitrine_engine=vazia)
    assert "VITRINE publicada" not in ponte.para_llm(ctx)
