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


# ── system em lista de partes (formato aceito pela API) ──────────────────────

def test_system_em_lista_recebe_a_regra_como_nova_parte():
    original = [{"role": "system", "content": [{"type": "text", "text": "S"}]},
                {"role": "user", "content": "P"}]
    saida = com_regra_escrita(original)
    assert saida[0]["content"] == [{"type": "text", "text": "S"},
                                   {"type": "text", "text": REGRA_ESCRITA_SIMPLES}]
    assert saida[1] == {"role": "user", "content": "P"}
    assert original[0]["content"] == [{"type": "text", "text": "S"}]  # intacto


def test_system_em_lista_nao_repete_a_regra():
    uma_vez = com_regra_escrita([{"role": "system",
                                  "content": [{"type": "text", "text": "S"}]}])
    assert com_regra_escrita(uma_vez) == uma_vez


def test_system_vazio_ou_sem_texto_fica_so_com_a_regra():
    for vazio in ("", None):
        saida = com_regra_escrita([{"role": "system", "content": vazio},
                                   {"role": "user", "content": "P"}])
        assert saida[0]["content"] == REGRA_ESCRITA_SIMPLES


def test_idempotencia_pede_a_regra_inteira_e_nao_so_um_trecho():
    """Um system que cite só o começo da regra não conta como já tendo a regra."""
    citado = {"role": "system", "content": "ESCRITA SIMPLES: (citação parcial)"}
    saida = com_regra_escrita([citado])
    assert REGRA_ESCRITA_SIMPLES in saida[0]["content"]


def test_portao_aceita_system_em_lista(cliente):
    msgs = [{"role": "system", "content": [{"type": "text", "text": "S"}]},
            {"role": "user", "content": "P"}]
    llm_b3._chat_complete(msgs)  # não pode levantar TypeError ao anexar
    enviado = cliente.recebido["messages"]
    assert {"type": "text", "text": REGRA_ESCRITA_SIMPLES} in enviado[0]["content"]


def test_instrucao_de_lacunas_aceita_system_em_lista():
    msgs = [{"role": "system", "content": [{"type": "text", "text": "S"}]},
            {"role": "user", "content": "P"}]
    saida = llm_b3._com_instrucao_lacunas(msgs)
    assert saida[0]["content"][0] == {"type": "text", "text": "S"}
    assert saida[0]["content"][-1]["type"] == "text"


def test_elo_claude_junta_system_em_lista_como_texto():
    from core import llm_anthropic

    system, conversa = llm_anthropic.separar([
        {"role": "system", "content": [{"type": "text", "text": "A"},
                                       {"type": "text", "text": "B"}]},
        {"role": "user", "content": "P"}])
    assert system == "A\n\nB"
    assert conversa == [{"role": "user", "content": "P"}]


# ── a regra chega ao Claude e ao Gemini, também quando o prompt é só do usuário

def _ligar_cadeia(monkeypatch, **clientes):
    from tests.test_llm_provider_fallback import _PROVEDORES

    for getter in _PROVEDORES:
        monkeypatch.setattr(llm_b3, getter, lambda: None)
    monkeypatch.setattr(llm_b3, "_anthropic_model", lambda: "claude-sonnet-5-5")
    monkeypatch.setattr(llm_b3, "_anthropic_effort", lambda: "medium")
    monkeypatch.setattr(llm_b3, "_gemini_model", lambda: "gemini-test")
    monkeypatch.setattr(llm_b3, "_sessao_interativa", lambda: True)
    for nome, cli in clientes.items():
        monkeypatch.setattr(llm_b3, f"_get_{nome}_client", lambda c=cli: c)
    llm_b3._limpar_estado_provedores()


@pytest.fixture
def limpa_estado():
    yield
    llm_b3._limpar_estado_provedores()


def _claude_falso(texto="{}"):
    from tests.test_llm_anthropic import _Claude, _resp, _texto

    return _Claude(_resp(_texto(texto)))


def test_claude_recebe_a_regra_no_parametro_system_prompt_so_do_usuario(
        monkeypatch, limpa_estado):
    """``_call_llm`` manda só uma mensagem de usuário: o system nasce na hora."""
    claude = _claude_falso()
    _ligar_cadeia(monkeypatch, anthropic=claude)
    llm_b3._call_llm("prompt de relatório")
    pedido = claude.pedidos[0]
    assert REGRA_ESCRITA_SIMPLES in pedido["system"]       # parâmetro próprio da API
    assert pedido["messages"] == [{"role": "user", "content": "prompt de relatório"}]
    assert pedido["system"].count("ESCRITA SIMPLES:") == 1
    assert "JSON" in pedido["system"]  # a instrução de JSON do elo segue presente


def test_claude_em_texto_livre_leva_a_regra_junto_do_system_existente(
        monkeypatch, limpa_estado):
    claude = _claude_falso("resposta")
    _ligar_cadeia(monkeypatch, anthropic=claude)
    llm_b3._chat_complete([{"role": "system", "content": f"S\n\n{REGRA_CONTEXTO_MERCADO}"},
                           {"role": "user", "content": "P"}])
    pedido = claude.pedidos[0]
    assert pedido["system"].startswith("S")
    assert REGRA_CONTEXTO_MERCADO in pedido["system"]
    assert REGRA_ESCRITA_SIMPLES in pedido["system"]
    assert pedido["messages"] == [{"role": "user", "content": "P"}]


def test_claude_com_system_em_lista_recebe_texto_com_a_regra(monkeypatch, limpa_estado):
    claude = _claude_falso("resposta")
    _ligar_cadeia(monkeypatch, anthropic=claude)
    llm_b3._chat_complete([{"role": "system", "content": [{"type": "text", "text": "S"}]},
                           {"role": "user", "content": "P"}])
    pedido = claude.pedidos[0]
    assert isinstance(pedido["system"], str)
    assert pedido["system"].startswith("S")
    assert REGRA_ESCRITA_SIMPLES in pedido["system"]


@pytest.mark.parametrize("json_mode", [False, True])
def test_gemini_recebe_a_regra_no_primeiro_system(monkeypatch, limpa_estado, json_mode):
    """O Gemini fala o protocolo da OpenAI (sem adaptador próprio)."""
    gemini = _Cliente()
    _ligar_cadeia(monkeypatch, gemini=gemini)
    llm_b3._chat_complete([{"role": "user", "content": "P"}], json_mode=json_mode)
    enviado = gemini.recebido["messages"]
    assert enviado[0]["role"] == "system"
    assert REGRA_ESCRITA_SIMPLES in enviado[0]["content"]
    assert enviado[-1] == {"role": "user", "content": "P"}


# ── nenhum caminho fura o portão ─────────────────────────────────────────────

#: Fora do app (avaliação offline de provedor, sem usuário final): chamada direta
#: de propósito, para medir o provedor sem a regra de estilo.
_ISENTOS = {"scripts/avaliar_provedor_llm.py"}
#: Só estes dois falam com o provedor.
_PORTAO = {"core/llm_b3.py", "core/llm_anthropic.py"}
_SKIP_DIRS = {"tests", ".git", ".venv", "venv", "env", "node_modules", "site-packages",
              ".cache_oos", "__pycache__"}
_CHAMADA = re.compile(
    r"chat\.completions\.create|\.messages\.(?:create|stream)\(|"
    r"\.responses\.create\(|generate_content(?:_stream)?\(")


def _arquivos_python():
    for arq in RAIZ.rglob("*.py"):
        rel = arq.relative_to(RAIZ)
        if set(rel.parts) & _SKIP_DIRS:
            continue
        yield arq, rel.as_posix()


def test_nenhum_arquivo_do_repo_fala_com_o_provedor_por_fora_do_portao():
    """Varre o repositório inteiro (views, scripts, etl, design...), não só core/.

    Chamada direta ao provedor furaria a regra de estilo. Só o portão e o elo
    Claude podem chamar; exceções entram em ``_ISENTOS``, com motivo.
    """
    furos = []
    for arq, rel in _arquivos_python():
        if rel in _PORTAO or rel in _ISENTOS:
            continue
        if _CHAMADA.search(arq.read_text(encoding="utf-8", errors="ignore")):
            furos.append(rel)
    assert furos == [], f"chamam o provedor sem passar por _chat_complete: {furos}"


def test_isencoes_existem_e_ainda_chamam_o_provedor():
    """Isenção velha esconde furo novo: se o arquivo não chama mais, tire-o da lista."""
    for rel in _ISENTOS:
        arq = RAIZ / rel
        assert arq.exists(), f"isenção sem arquivo: {rel}"
        assert _CHAMADA.search(arq.read_text(encoding="utf-8", errors="ignore")), rel


def test_em_llm_b3_toda_chamada_ao_provedor_roda_depois_do_portao():
    """No ``llm_b3.py`` a chamada só vale dentro de funções que ``_chat_complete``
    alcança *depois* de anexar a regra.

    * a chamada ao provedor mora só em ``_chat_complete``, ``_com_ferramentas``
      e ``_responder_claude``;
    * ``_com_ferramentas`` e ``_responder_claude`` são usadas só por ``_chat_complete``;
    * a regra é anexada antes de qualquer chamada, dentro de ``_chat_complete``.
    """
    import ast

    fonte = (RAIZ / "core/llm_b3.py").read_text(encoding="utf-8")
    funcoes = {n.name: n for n in ast.walk(ast.parse(fonte))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def usa(no, nome):
        return any((isinstance(n, ast.Name) and n.id == nome)
                   or (isinstance(n, ast.Attribute) and n.attr == nome)
                   for n in ast.walk(no))

    com_chamada = {nome for nome, no in funcoes.items()
                   if _CHAMADA.search(ast.get_source_segment(fonte, no) or "")
                   or usa(no, "completar")}
    assert com_chamada == {"_chat_complete", "_com_ferramentas", "_responder_claude"}, com_chamada

    for auxiliar in ("_com_ferramentas", "_responder_claude"):
        donos = {nome for nome, no in funcoes.items()
                 if nome != auxiliar and usa(no, auxiliar)}
        assert donos == {"_chat_complete"}, (auxiliar, donos)

    corpo = ast.get_source_segment(fonte, funcoes["_chat_complete"])
    regra = corpo.index("com_regra_escrita(")
    assert regra < corpo.index("_responder_claude(")
    assert regra < corpo.index("_com_ferramentas(")
    assert regra < corpo.index("chat.completions.create")


# ── textos de tela que a simplificação não pode deixar enganar (revisão do PR) ─

def _fonte(rel: str) -> str:
    # Junta as linhas das strings literais para achar frases partidas no código.
    bruto = (RAIZ / rel).read_text(encoding="utf-8")
    return re.sub(r'"\s*\n\s*"', "", bruto)


def test_legenda_da_evolucao_avisa_que_cupom_do_tesouro_nao_entra():
    """``retorno_encadeado`` usa só o preço de resgate: a linha cai no cupom."""
    fonte = _fonte("views/investimentos.py")
    assert "Tesouro com cupom: o cupom não entra no retorno, e a linha cai no dia do pagamento" in fonte
    assert "Tesouro com cupom conta no dia do pagamento" not in fonte


def test_legenda_dos_fatores_eua_mantem_acima_do_mercado_nos_dois_retornos():
    fonte = _fonte("views/empresas_americanas.py")
    assert "rendeu 4,1% ao ano acima do mercado" in fonte
    assert "rendeu 3,7% ao ano acima do mercado" in fonte
