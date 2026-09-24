"""Piloto do Jev na categorização do cartão: cliente HTTP, pergunta e métrica.

Tudo offline: a chamada HTTP é injetada.
"""
from __future__ import annotations

import pytest

from core.card_categorization import (
    DESCRICAO_CATEGORIA_JEV,
    REVIEW_SENTINEL,
    categorias_disponiveis,
    ler_resposta_jev_categoria,
    pergunta_jev_categoria,
)
from core.jev import JevErro, system_one
from scripts.avaliar_jev_cartao import origem_do_rotulo, resumir, rotulados_unicos


class _Resp:
    def __init__(self, status: int, corpo=None, json_invalido: bool = False):
        self.status_code = status
        self._corpo = corpo
        self._json_invalido = json_invalido

    def json(self):
        if self._json_invalido:
            raise ValueError("não é JSON")
        return self._corpo


# ── cliente ──────────────────────────────────────────────────────────────────

def test_system_one_envia_bearer_e_le_answers_e_usage():
    visto = {}

    def post(url, json, headers, timeout):
        visto.update(url=url, json=json, headers=headers)
        return _Resp(200, {"model": "jev-1.13.0",
                           "answers": {"q": {"type": "noul", "noul": 0.9}},
                           "usage": {"input_tokens": 210, "output_tokens": 31}})

    r = system_one({"a": 1}, {"q": {"type": "noul", "instructions": "x"}},
                   api_key="sk-teste", http_post=post)
    assert visto["url"].endswith("/v1/systemone")
    assert visto["headers"]["Authorization"] == "Bearer sk-teste"
    assert visto["json"]["model"] == "jev-latest"
    assert r.answers["q"]["noul"] == 0.9
    assert r.modelo == "jev-1.13.0"
    assert r.tokens_entrada == 210


def test_system_one_sem_chave_nao_chama():
    def post(*a, **k):  # pragma: no cover - não pode ser chamado
        raise AssertionError("chamou sem chave")

    with pytest.raises(JevErro):
        system_one({}, {}, api_key="", http_post=post)


@pytest.mark.parametrize("resp", [
    _Resp(401, {"error": "bad key"}),
    _Resp(200, json_invalido=True),
    _Resp(200, {"model": "jev"}),
])
def test_system_one_falhas_viram_joverro_sem_ecoar_a_chave(resp):
    with pytest.raises(JevErro) as exc:
        system_one({}, {}, api_key="sk-segredo", http_post=lambda *a, **k: resp)
    assert "sk-segredo" not in str(exc.value)


def test_falha_de_rede_nao_ecoa_a_chave():
    def post(*a, **k):
        raise ConnectionError("Authorization: Bearer sk-segredo")

    with pytest.raises(JevErro) as exc:
        system_one({}, {}, api_key="sk-segredo", http_post=post)
    assert "sk-segredo" not in str(exc.value)
    assert exc.value.__cause__ is None


# ── pergunta e leitura ───────────────────────────────────────────────────────

def test_opcoes_do_jev_sao_as_categorias_de_gasto_sem_as_estruturais():
    esperadas = set(categorias_disponiveis(incluir_estruturais=False)) - {"Tarifas & Anuidade"}
    assert set(DESCRICAO_CATEGORIA_JEV) == esperadas


def test_pergunta_normaliza_descricao_e_usa_choice():
    state, q = pergunta_jev_categoria("  PADARIA   SAO  JOAO ", -12.345)
    assert state == {"estabelecimento": "PADARIA SAO JOAO", "valor_brl": 12.35}
    assert q["categoria"]["type"] == "choice"
    assert set(q["categoria"]["criteria"]) == set(DESCRICAO_CATEGORIA_JEV)


def test_leitura_prefere_probabilidade_da_opcao_escolhida():
    ans = {"categoria": {"choice": "Mercado", "confidence": 0.4,
                         "probabilities": {"Mercado": 0.83, "Alimentação": 0.17}}}
    assert ler_resposta_jev_categoria(ans) == ("Mercado", 0.83)


def test_leitura_cai_na_confidence_e_depois_em_none():
    assert ler_resposta_jev_categoria(
        {"categoria": {"choice": "Mercado", "confidence": 0.6}}) == ("Mercado", 0.6)
    assert ler_resposta_jev_categoria({"categoria": {"choice": "Mercado"}}) == ("Mercado", None)


@pytest.mark.parametrize("answers", [{}, None, {"categoria": "x"},
                                     {"categoria": {"choice": "Inventada", "confidence": 0.99}}])
def test_resposta_invalida_volta_para_revisao_sem_confianca(answers):
    assert ler_resposta_jev_categoria(answers) == (REVIEW_SENTINEL, None)


# ── métrica ──────────────────────────────────────────────────────────────────

def test_origem_do_rotulo():
    assert origem_do_rotulo("usuario:PADARIA") == "usuario"
    assert origem_do_rotulo("merchant:IFOOD") == "merchant"
    assert origem_do_rotulo("ja-catalogada") == "categoria"


def _it(desc, cat, regra="merchant:X", valor=10.0):
    return {"descricao": desc, "categoria_atual": cat, "regra": regra, "valor": valor}


def test_rotulados_unicos_agrega_e_descarta_ambiguos():
    items = [
        _it("IFOOD", "Alimentação"), _it("ifood", "Alimentação"),
        _it("LOJA X", "Compras / Varejo"), _it("LOJA X", "Casa & Construção"),
        _it("PAG FATURA", "Pagamento de Cartão"), _it("NOVO", REVIEW_SENTINEL),
    ]
    out, ambiguos = rotulados_unicos(items, DESCRICAO_CATEGORIA_JEV)
    assert ambiguos == 1
    assert [(r["descricao"], r["n"]) for r in out] == [("IFOOD", 2)]


def test_resumir_calibracao_e_limiar():
    base = {"origem": "usuario", "latencia_s": 0.2, "tokens": 100}
    res = [
        {**base, "rotulo": "Mercado", "sugestao": "Mercado", "prob": 0.95},
        {**base, "rotulo": "Mercado", "sugestao": "Alimentação", "prob": 0.55},
        {**base, "rotulo": "Mercado", "sugestao": "Mercado", "prob": None},
    ]
    txt = "\n".join(resumir(res))
    assert "Acerto geral: 2/3" in txt
    assert "[0.9, 1.0)    1/1" in txt
    assert "[0.5, 0.7)    0/1" in txt
    assert "sem probabilidade: 1" in txt
    # Limiar 0.9 cobre 1 de 3 e acerta o único que cobre.
    assert ">= 0.9: cobre  33.3%  precisão 100.0%" in txt
    assert "Mercado -> Alimentação" in txt


def test_resumir_sem_respostas():
    assert resumir([]) == ["Nenhuma resposta válida do Jev."]
