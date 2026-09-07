"""A identidade do erro tem que ser util SEM reabrir o A-013.

O A-013 foi vazamento de excecao crua ao usuario final: a mensagem do
SQLAlchemy carrega a URL de conexao inteira. Estes testes prendem os dois
lados: o relatorio identifica o defeito, e nunca carrega a mensagem.
"""
from __future__ import annotations

import pytest

from core.erro_diagnostico import (
    frames_do_projeto,
    identidade_do_erro,
    relatorio_tecnico,
)

_SEGREDO = (
    "(psycopg2.OperationalError) could not connect to server: "
    "postgresql://postgres:senha_real@db.abcdef.supabase.co:5432/postgres"
)


def _erro_daqui() -> Exception:
    """Excecao levantada NESTE arquivo, para ter frame do projeto garantido."""
    try:
        raise RuntimeError(_SEGREDO)
    except RuntimeError as exc:
        return exc


def test_identidade_traz_tipo_e_local():
    ident = identidade_do_erro(_erro_daqui())
    assert ident.startswith("RuntimeError · ")
    assert "tests/test_erro_diagnostico.py:" in ident


def test_mensagem_da_excecao_nunca_aparece():
    """O que o A-013 proibiu continua proibido."""
    exc = _erro_daqui()
    for texto in (identidade_do_erro(exc), relatorio_tecnico(exc)):
        assert "senha_real" not in texto
        assert "supabase.co" not in texto
        assert "5432" not in texto
        assert _SEGREDO not in texto


def test_frames_de_biblioteca_ficam_de_fora():
    """Frame de dependencia nao diz o que consertar aqui.

    ``json.loads`` de texto invalido levanta LA DENTRO da stdlib: o traceback
    tem frames de ``json/decoder.py`` alem do desta linha. Um teste que usa
    excecao sem frame externo nenhum passaria com o filtro desligado — e
    passou, na verificacao por mutacao.
    """
    import json
    from pathlib import Path

    try:
        json.loads("{isto nao e json")
    except ValueError as exc:
        bruto = [q.filename for q in __import__("traceback").extract_tb(exc.__traceback__)]
        frames = frames_do_projeto(exc)

    externos = [f for f in bruto if "json" in Path(f).parts[-2:][0] or "decoder" in f]
    assert externos, "o traceback precisava ter frame de biblioteca para o teste valer"
    assert frames, "o frame deste proprio arquivo tinha que aparecer"
    assert all("decoder.py" not in f for f in frames)
    assert all("site-packages" not in f for f in frames)
    assert len(frames) < len(bruto), "nenhum frame foi filtrado"


def test_falha_inteiramente_externa_diz_que_e_externa():
    """Mentir um local do projeto seria pior que admitir que nao ha um."""
    exc = RuntimeError("sem traceback nenhum")
    assert identidade_do_erro(exc) == "RuntimeError · fora do projeto"


def test_relatorio_registra_a_causa_encadeada():
    try:
        try:
            raise KeyError("tk")
        except KeyError as raiz:
            raise ValueError("derivado") from raiz
    except ValueError as exc:
        rel = relatorio_tecnico(exc)
    assert "ValueError" in rel
    assert "causada por KeyError" in rel


def test_lista_de_frames_e_limitada():
    """Traceback fundo nao pode virar parede de texto na tela."""
    def fundo(n: int):
        if n == 0:
            raise ZeroDivisionError("fim")
        fundo(n - 1)

    with pytest.raises(ZeroDivisionError) as info:
        fundo(30)
    assert len(frames_do_projeto(info.value)) <= 5


def test_boundary_do_app_mostra_a_identidade():
    """O app tem que CONSUMIR o diagnostico; modulo util e nao chamado e enfeite.

    (``memoria: diagnostico-precisa-porta-de-entrada``)
    """
    import ast
    from pathlib import Path

    fonte = Path("app.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    chamadas = {
        no.func.id
        for no in ast.walk(arvore)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
    }
    assert "identidade_do_erro" in chamadas
    assert "relatorio_tecnico" in chamadas
