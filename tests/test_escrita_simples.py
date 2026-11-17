"""Regra única de escrita simples (17/11/2026): vai a toda chamada de LLM.

O portão único é ``core.llm_b3._chat_complete``. Estes testes garantem:
* a regra chega a toda chamada, em texto livre e em JSON, com ou sem system;
* nenhum módulo do app fala com o provedor por fora do portão;
* a regra é só de estilo e não enfraquece as outras (contexto, varredura, veto).
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

import core.llm_b3 as llm_b3
from core.contexto_mercado import REGRA_CONTEXTO_MERCADO, REGRA_VARREDURA_ARMAZEM
from core.escrita_simples import REGRA_ESCRITA_SIMPLES, com_regra_escrita

RAIZ = Path(__file__).resolve().parents[1]


class _Cliente:
    def __init__(self):
        self.recebido = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.recebido = kw
        msg = types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))
        return types.SimpleNamespace(choices=[msg])


@pytest.fixture
def cliente(monkeypatch):
    c = _Cliente()
    monkeypatch.setattr(llm_b3, "_provider_chain", lambda _m=None: [("openai", c, "m")])
    return c


# ── o texto da regra ─────────────────────────────────────────────────────────

def test_regra_nao_enfraquece_as_outras():
    texto = REGRA_ESCRITA_SIMPLES
    for trava in ("número", "fonte", "data", "veto", "ressalva", "só de estilo"):
        assert trava in texto
    # Não pode dizer "omita", "ignore" ou "resuma" o que as outras regras exigem.
    assert not re.search(r"\b(omita|ignore|ignorar)\b", texto, re.I)


def test_regra_e_curta_e_nao_duplica_a_de_contexto():
    assert len(REGRA_ESCRITA_SIMPLES) < 1400
    # Texto único: a regra de contexto não carrega uma cópia.
    assert "ESCRITA SIMPLES" not in REGRA_CONTEXTO_MERCADO
    assert REGRA_VARREDURA_ARMAZEM in REGRA_CONTEXTO_MERCADO  # segue intacta


# ── com_regra_escrita ────────────────────────────────────────────────────────

def test_anexa_ao_system_existente_sem_alterar_a_lista_original():
    original = [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]
    saida = com_regra_escrita(original)
    assert saida[0]["content"].startswith("S")
    assert REGRA_ESCRITA_SIMPLES in saida[0]["content"]
    assert saida[1] == {"role": "user", "content": "P"}
    assert original[0]["content"] == "S"


def test_sem_system_cria_um_no_inicio():
    saida = com_regra_escrita([{"role": "user", "content": "P"}])
    assert saida[0] == {"role": "system", "content": REGRA_ESCRITA_SIMPLES}
    assert saida[1] == {"role": "user", "content": "P"}


def test_nao_repete_quando_ja_esta_no_prompt():
    uma_vez = com_regra_escrita([{"role": "system", "content": "S"}])
    duas_vezes = com_regra_escrita(uma_vez)
    assert duas_vezes == uma_vez
    assert duas_vezes[0]["content"].count("ESCRITA SIMPLES:") == 1


# ── o portão único ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("com_system", [False, True])
def test_toda_chamada_leva_a_regra(cliente, json_mode, com_system):
    msgs = [{"role": "user", "content": "P"}]
    if com_system:
        msgs.insert(0, {"role": "system", "content": f"S\n\n{REGRA_CONTEXTO_MERCADO}"})
    llm_b3._chat_complete(msgs, json_mode=json_mode)
    enviado = cliente.recebido["messages"]
    assert enviado[0]["role"] == "system"
    assert REGRA_ESCRITA_SIMPLES in enviado[0]["content"]
    if com_system:  # as regras anteriores seguem inteiras no mesmo system
        assert REGRA_CONTEXTO_MERCADO in enviado[0]["content"]
    assert enviado[-1] == {"role": "user", "content": "P"}


def test_call_llm_dos_relatorios_em_json_tambem_leva(cliente):
    llm_b3._call_llm("prompt de relatório")
    enviado = cliente.recebido["messages"]
    assert REGRA_ESCRITA_SIMPLES in enviado[0]["content"]
    assert enviado[-1]["content"] == "prompt de relatório"


def test_nenhum_modulo_fala_com_o_provedor_por_fora_do_portao():
    """Chamada direta ao provedor furaria a regra. Só o portão e o elo Claude."""
    permitidos = {"core/llm_b3.py", "core/llm_anthropic.py"}
    padrao = re.compile(r"chat\.completions\.create|\.messages\.create\(|generate_content\(")
    furos = []
    for arq in (RAIZ / "core").rglob("*.py"):
        rel = arq.relative_to(RAIZ).as_posix()
        if rel in permitidos:
            continue
        if padrao.search(arq.read_text(encoding="utf-8", errors="ignore")):
            furos.append(rel)
    assert furos == [], f"chamam o provedor sem passar por _chat_complete: {furos}"
