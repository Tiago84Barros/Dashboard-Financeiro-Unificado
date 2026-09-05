"""O motor de portoes passa a ter chamador em producao (A-140/A-141).

Por que este arquivo existe
---------------------------
``core.noticias.portoes`` implementa a trava central do requisito -- *uma
noticia com nota superior a 80 nao pode, sozinha, alterar a carteira* -- e ate
05/09/2026 nenhum codigo de producao o chamava. A revisao de 02/09 registrou o
achado como A-140: motor correto, testado, e sem porta de entrada. Motor que
ninguem consulta na decisao e decoracao, e decoracao nao deixa erro no log.

A-141 e o mesmo defeito um nivel abaixo: ``confirmacao_quantitativa`` nunca era
preenchida, o portao ficava em ``None``, e ``None`` nao aprova -- logo
``sugerir_revisao`` era inalcancavel mesmo com os outros cinco portoes abertos.
Criterio que so pode dar False nunca e revisto.

O que se cobra aqui:

1. **A coleta produz veredito** para cada noticia avaliada.
2. **A entrada quantitativa e uma medicao de fora**, com os tres estados
   preservados: sem base e ``None``, base pequena e ``None``, base que nao
   corrobora e ``False``.
3. **O veredito e persistido** com a trilha dos seis portoes.
4. **O teto de acao continua onde estava**: nenhum veredito compra ou vende.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from core.noticias import armazenamento as arm
from core.noticias import coleta as col
from core.noticias import portoes
from core.noticias.impacto import BaseHistorica
from tests.apoio_noticias import AGORA, quando

SCHEMA = "app4_veredito_teste"


# ───────────────────────── a entrada do portao quantitativo ──────────────────

def _base(n, prob):
    return BaseHistorica(tipo_evento="resultado_trimestral", n_observacoes=n,
                         limiar_relevante=3.0, horizonte="curto",
                         prob_movimento_relevante=prob,
                         fonte="memoria_mercado:retorno_anormal")


def test_sem_base_o_portao_fica_nao_medido_e_nao_reprovado():
    """``None`` e ``False`` sao coisas diferentes, e o portao depende disso.

    Devolver ``False`` na ausencia de base transformaria "nao medimos" em
    "medimos e nao corrobora" -- e a diferenca importa porque ``False`` e uma
    afirmacao sobre o mundo que ninguem apurou.
    """
    assert col.confirmacao_quantitativa(None) is None


def test_base_pequena_demais_nao_vira_medicao():
    """Probabilidade com tres observacoes e ruido com tres casas decimais."""
    assert col.confirmacao_quantitativa(_base(3, 0.9)) is None
    assert col.MIN_OBSERVACOES_QUANTITATIVO > 3


def test_base_suficiente_corrobora_ou_contradiz():
    """Com amostra, o portao responde -- e responde nos dois sentidos.

    O caso ``False`` e o que prova que a entrada nao e um preenchedor de lacuna:
    ela pode contradizer a noticia, e nao so confirmar quando conveniente.
    """
    assert col.confirmacao_quantitativa(_base(30, 0.80)) is True
    assert col.confirmacao_quantitativa(_base(30, 0.10)) is False


def test_probabilidade_ausente_em_base_grande_ainda_e_nao_medido():
    assert col.confirmacao_quantitativa(_base(30, None)) is None


# ───────────────────────────── a fiacao na coleta ────────────────────────────

class _Prov:
    nome = "falso"
    janela_s = 0.0

    def __init__(self, itens):
        self._itens = itens

    def buscar(self, consulta):
        from core.noticias.provedores.base import (
            ORIGEM_REDE,
            RespostaProvedor,
        )
        return RespostaProvedor(
            provedor=self.nome, itens=tuple(self._itens), origem=ORIGEM_REDE,
            consultado_em=AGORA, dados_de=AGORA)


def _itens_brutos():
    from core.noticias.provedores.base import ItemBruto
    return [ItemBruto(
        titulo="Alfa anuncia aquisicao da Beta por dois bilhoes de reais",
        url="https://veiculo.teste/alfa-beta", resumo="Fato relevante.",
        publicado_em=quando(1), veiculo="Veiculo Teste", tickers=("ALFA3",))]


def _coletar(**kw):
    from core.noticias.provedores.base import Consulta
    return col.coletar(Consulta(tickers=("ALFA3",), limite=10),
                       [_Prov(_itens_brutos())], agora=AGORA, **kw)


def test_a_coleta_produz_veredito_para_cada_avaliada():
    """O teste que teria pego A-140 no dia em que o motor foi escrito.

    Falha se ``pt_mod.avaliar`` sair de ``coletar``: sem ele o dicionario fica
    vazio e a trava de aporte deixa de existir sem produzir erro nenhum.
    """
    r = _coletar()

    assert r.avaliadas, "cenario invalido: nada foi avaliado"
    for avaliada in r.avaliadas:
        veredito = r.vereditos.get(avaliada.noticia.id_dedup)
        assert veredito is not None, (
            "noticia avaliada sem veredito: os portoes nao rodaram")
        assert len(veredito.portoes) == 6


def test_nenhum_veredito_da_coleta_compra_ou_vende():
    """A trava do requisito, medida na saida real e nao no dataclass."""
    r = _coletar()
    permitidas = {portoes.ACAO_INFORMAR, portoes.ACAO_OBSERVAR,
                  portoes.ACAO_SUGERIR_REVISAO}
    for veredito in r.vereditos.values():
        assert veredito.acao in permitidas
        assert "compr" not in veredito.acao and "vend" not in veredito.acao


def test_a_base_historica_chega_ao_portao_quantitativo():
    """Com base no argumento ``bases``, o portao sai de ``None``.

    Enquanto ``bases`` nao tinha fonte em producao, corrigir so o portao teria
    mudado o lugar do problema, nao o problema: criterio inalcancavel continua
    inalcancavel se a entrada nunca chega.
    """
    r = _coletar(bases={"fusao_aquisicao": _base(30, 0.8)})

    quantitativos = [p for v in r.vereditos.values() for p in v.portoes
                     if p.chave == portoes.PORTAO_QUANTITATIVO]
    assert quantitativos, "cenario invalido: nenhum portao quantitativo"
    assert any(p.satisfeito is True for p in quantitativos), (
        "a base historica nao chegou ao portao: ele continua indeterminado")


# ─────────────────────────── a trilha persistida ─────────────────────────────

@pytest.fixture()
def acervo():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(
            _warehouse_url(),
            connect_args={"options": f"-csearch_path={SCHEMA},public"})
        with motor.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    except Exception as exc:  # noqa: BLE001 - sem armazem, nao medimos
        pytest.skip(f"armazem local indisponivel: {exc}")
    yield motor
    with motor.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    motor.dispose()


def test_o_veredito_vai_para_o_acervo_com_os_seis_portoes(acervo):
    """Trilha de auditoria: a decisao gravada carrega por que foi tomada.

    Os tres estados de ``satisfeito`` sobrevivem ao JSON. Colapsar ``null`` em
    ``false`` no armazenamento apagaria a distincao entre "medimos e nao passou"
    e "nao medimos" -- a mesma distincao de que o motor inteiro depende.
    """
    r = _coletar()
    arm.gravar(r, engine=acervo)

    with acervo.connect() as conn:
        linhas = conn.execute(text(
            "SELECT acao, portoes FROM noticias_avaliacoes")).fetchall()

    assert linhas, "nada foi gravado"
    for acao, trilha in linhas:
        assert acao, "acao nao persistida: a decisao ficou sem trilha"
        registrados = trilha if isinstance(trilha, list) else json.loads(trilha)
        assert len(registrados) == 6
        assert {type(p["satisfeito"]) for p in registrados} <= {bool, type(None)}


def test_regravacao_sem_veredito_nao_apaga_o_veredito_gravado(acervo):
    """Omissao de quem regrava nao pode destruir evidencia de quem gravou.

    E o mesmo modo de falha do ``resumo`` congelado, invertido: la o upsert nao
    reescrevia derivado; aqui ele nao pode zerar o que ja foi apurado.
    """
    r = _coletar()
    arm.gravar(r, engine=acervo)
    arm.gravar(col.ResultadoColeta(avaliadas=r.avaliadas), engine=acervo)

    with acervo.connect() as conn:
        acoes = conn.execute(text(
            "SELECT acao FROM noticias_avaliacoes")).scalars().all()
    assert all(acoes), "a regravacao apagou a acao gravada"


def test_o_ddl_roda_em_cada_destino_e_nao_uma_vez_por_processo(acervo):
    """Migration nova precisa alcancar destino que ja existia.

    O controle era um booleano de processo: bastava um ``garantir_schema``
    anterior para que toda coluna adicionada depois nunca mais fosse criada
    naquele processo -- migration registrada e nunca executada.
    """
    with acervo.begin() as conn:
        arm.garantir_schema(conn)

    outro = create_engine(
        acervo.url,
        connect_args={"options": f"-csearch_path={SCHEMA}_b,public"})
    try:
        with outro.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA}_b CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {SCHEMA}_b"))
        with outro.begin() as conn:
            arm.garantir_schema(conn)
        with outro.connect() as conn:
            cols = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'noticias_avaliacoes'"
            ), {"s": f"{SCHEMA}_b"}).scalars().all()
        assert "acao" in cols and "portoes" in cols, (
            "o segundo destino foi dado como pronto sem ter sido tocado")
    finally:
        with outro.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA}_b CASCADE"))
        outro.dispose()
