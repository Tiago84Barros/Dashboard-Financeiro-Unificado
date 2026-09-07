"""Onde a safra da Memória de Mercado mora -- e por que isso virou teste.

Em 06/09/2026 o construtor gravou 4.463 eventos e o leitor reportou "memoria de
mercado sem safra construida". Nenhum dos dois estava errado: eles resolviam
**bancos diferentes no mesmo container** (`postgres` na gravação, `noticias` na
leitura). A frase do leitor era verdadeira sobre o lugar errado, e por isso
indistinguível de "ainda não foi construída".

Os testes abaixo defendem *propriedades*, não números: que escritor e leitor
passem pela mesma função de endereço, e que a limitação diga onde procurou.
Instância de ``memoria: verificador-e-escritor-listas-diferentes``.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from core.memoria_mercado import destino as dst

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _chamadas(caminho: str) -> set[str]:
    """Nomes de função chamados no arquivo, pela AST.

    Pela AST e não por ``grep`` de propósito: uma segunda cópia da resolução de
    endereço, escrita com outro nome, passaria despercebida por busca textual.
    Mesmo motivo de ``memoria: guarda-duplicada-diverge``.
    """
    arvore = ast.parse((RAIZ / caminho).read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            alvo = no.func
            if isinstance(alvo, ast.Name):
                nomes.add(alvo.id)
            elif isinstance(alvo, ast.Attribute):
                nomes.add(alvo.attr)
    return nomes


def test_escritor_e_leitor_passam_pela_mesma_funcao():
    escritor = _chamadas("scripts/construir_memoria_mercado.py")
    leitor = _chamadas("core/noticias/bases_historicas.py")
    assert "engine_memoria" in escritor, (
        "o construtor precisa resolver o destino por core.memoria_mercado."
        "destino, nunca pela engine de preços")
    assert "engine_memoria" in leitor, (
        "o leitor precisa resolver o destino pela mesma função do construtor")


def test_url_memoria_segue_o_acervo_quando_nao_ha_override(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "MEMORIA_LOCAL_DB_URL", "", raising=False)
    from core.noticias import destino as ndst
    assert dst.url_memoria() == ndst.url_acervo()


def test_override_separa_os_bancos_sem_tocar_em_chamador(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "MEMORIA_LOCAL_DB_URL",
                        "postgresql://u:p@localhost:5433/outra", raising=False)
    assert dst.url_memoria().endswith("/outra")
    assert "outra" in dst.rotulo_do_destino()
    assert "p@" not in dst.rotulo_do_destino(), "o rótulo não pode vazar senha"


def test_rotulo_nunca_levanta_com_url_ilegivel(monkeypatch):
    monkeypatch.setattr(dst, "url_memoria", lambda: "isto nao e uma url")
    assert isinstance(dst.rotulo_do_destino(), str)


def test_limitacao_de_safra_vazia_nomeia_o_banco_consultado(monkeypatch):
    """A frase que sobreviveu um dia inteiro dizia a verdade sobre o outro banco.

    Safra vazia e safra procurada no banco errado produzem a MESMA leitura:
    zero linhas. Só o rótulo do destino distingue as duas, e é por isso que ele
    entra na mensagem.
    """
    from core.memoria_mercado import destino as d
    from core.memoria_mercado import repositorio as repo
    from core.noticias import bases_historicas as bh

    monkeypatch.setattr(d, "rotulo_do_destino", lambda: "localhost:5433/xyz")
    monkeypatch.setattr(repo, "carregar_eventos", lambda engine: [])

    bases, limitacoes = bh.carregar(engine=object())
    assert bases == {}
    assert limitacoes, "safra ausente tem que virar limitação declarada"
    assert any("xyz" in item for item in limitacoes), (
        "a limitação precisa dizer ONDE procurou; sem isso ela fica verdadeira "
        "sobre o lugar errado")


def test_sem_armazem_configurado_nao_devolve_base_vazia_silenciosa(monkeypatch):
    from core.memoria_mercado import destino as d
    from core.noticias import bases_historicas as bh

    monkeypatch.setattr(d, "engine_memoria", lambda: None)
    bases, limitacoes = bh.carregar()
    assert bases == {} and limitacoes


@pytest.mark.parametrize("proibida", ["disponivel_em"])
def test_fonte_anual_nao_usa_a_data_da_ultima_versao(proibida):
    """`disponivel_em` é a data da ÚLTIMA versão do arquivo.

    Usá-la dataria o evento depois de o mercado já poder ter reagido, e a
    medição chamaria de reação um movimento anterior ao "evento". Erro
    silencioso e sempre a favor da conclusão.
    """
    from core.calibracao import catalogo as cat
    assert "primeira_entrega_em" in cat.SQL_RESULTADO_ANUAL_B3
    assert proibida not in cat.SQL_RESULTADO_ANUAL_B3


def test_identidade_do_fato_nao_e_a_chave_de_texto():
    """A `chave` é texto composto pelo chamador -- não pode ser a identidade.

    Em 06/09/2026 o mesmo resultado anual da BBDC4 entrou duas vezes porque o
    construtor mudou o formato da chave (``cvm:BBDC4:2010`` virou
    ``resultado_anual:BBDC4:2011-01-31``). Nenhum erro; 8.923 linhas para 4.463
    eventos, e a base histórica publicou ``n`` dobrado. Instância de
    ``memoria: procedencia-na-chave-aceita-o-fato-de-novo``.

    O teste é sobre a estrutura, não sobre o dado: o alvo do ``ON CONFLICT``
    tem que ser a identidade do fato, e o índice único tem que existir.
    """
    from core.memoria_mercado import repositorio as repo

    alvo = "ON CONFLICT (versao_metodologia, tipo_evento, simbolo, data_evento)"
    assert alvo in repo._UPSERT, (
        "o upsert de eventos precisa colidir pelo fato; colidir pela `chave` "
        "re-admite o acervo inteiro quando o formato da chave muda")

    ddl = "\n".join(repo.DDL_SQL)
    assert "ux_mm_eventos_fato" in ddl, (
        "sem o índice único, o ON CONFLICT pelo fato nem sequer executa")
    assert "(versao_metodologia, tipo_evento, simbolo, data_evento)" in ddl


def test_relatorio_conta_fatos_e_nao_linhas_enviadas():
    """Relatar o tamanho do lote diria 4.463 onde existem 4.460."""
    import inspect

    from core.memoria_mercado import repositorio as repo
    fonte = inspect.getsource(repo.gravar)
    assert "fatos_repetidos_no_lote" in fonte
    assert '"linhas": len(fatos)' in fonte


def test_rotulo_sobrevive_a_configuracao_reduzida(monkeypatch):
    """Compor o texto de uma limitação não pode derrubar a coleta.

    Em 06/09/2026 o ``except`` de ``rotulo_do_destino`` cobria só o parse da
    URL; a **resolução** dela ficava fora. Uma ``settings`` reduzida -- as que
    os testes de unidade e os jobs usam -- não tem ``NOTICIAS_LOCAL_DB_URL``, e
    o ``AttributeError` subia até ``job.run``: 17 testes de infraestrutura
    caíam porque o rótulo de uma frase informativa não sabia se calar.
    """
    from core.memoria_mercado import destino as dst

    class _SettingsReduzida:
        pass

    monkeypatch.setattr("core.config.settings", _SettingsReduzida(),
                        raising=False)
    assert isinstance(dst.rotulo_do_destino(), str)
