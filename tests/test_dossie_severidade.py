"""Severidade das linhas de ``red_flags`` da B3 — a classificação e os TRÊS
consumidores.

O achado I-E da revisão final tinha duas metades. A primeira (o rótulo
factualmente errado) foi fechada na rodada anterior. A segunda é esta: os
prefixos ``CONTEXTO:`` e ``COBERTURA:`` existiam e **ninguém os lia**, então
as 423 empresas que emitem alguma linha chegavam ao gate e às duas telas sob
"RED FLAGS DETERMINÍSTICAS", sendo que só 191 têm alguma linha de risco
confirmado.

Há um teste por consumidor de propósito: guarda no consumidor não cobre os
outros consumidores, e foi exatamente assim que a rodada anterior tratou
``views/empresas_b3.py`` e esqueceu ``views/analise_portfolio_b3.py``.

Os testes dos dois consumidores de UI são ESTRUTURAIS (``inspect.getsource``):
o código de render mora dentro de funções gigantes que exigem Streamlit e
banco reais. É o mesmo padrão já aceito na Task 6 e na rodada anterior —
prova que a linha está escrita como está escrita, e morre quando ela é
revertida (verificado por mutação, registrada no relatório da rodada).
"""
from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path

import pandas as pd

from core.dossie_b3 import (
    _PROMPT_PARECER,
    SEVERIDADE_COBERTURA,
    SEVERIDADE_CONTEXTO,
    SEVERIDADE_RISCO,
    agrupa_flags_por_severidade,
    dossie_to_text,
    severidade_flag,
)

_RAIZ = Path(__file__).resolve().parents[1]

_RISCO = (
    "PATRIMÔNIO EM QUEDA COM LUCRO POSITIVO em 5 de 6 pares de anos (83%): "
    "padrão persistente de distribuição acima do lucro."
)
_CONTEXTO = (
    "CONTEXTO: patrimônio em queda com lucro positivo em 1 de 7 pares de anos "
    "(14%): episódio, não padrão. Observação medida, não risco confirmado."
)
_COBERTURA = (
    "COBERTURA: patrimônio em queda com lucro positivo em 3 de 4 pares de anos "
    "(75%), amostra abaixo dos 5 pares exigidos — observação NÃO CONFIRMÁVEL."
)


# ── (1) a função pura: a regra de classificação ─────────────────────────────

def test_severidade_separa_os_tres_casos_da_linha_patrimonial():
    assert severidade_flag(_RISCO) == SEVERIDADE_RISCO
    assert severidade_flag(_CONTEXTO) == SEVERIDADE_CONTEXTO
    assert severidade_flag(_COBERTURA) == SEVERIDADE_COBERTURA


def test_cobertura_que_ja_existia_antes_do_ramo_tambem_nao_e_risco():
    """``COBERTURA:`` já rotulava lacunas antes deste ramo (sem DFC, sem
    EBITDA, sem documento CVM, histórico curto). Nenhuma delas é risco
    confirmado, e a classificação não pode quebrar o que já existia."""
    antigas = [
        "COBERTURA: sem demonstração de fluxo de caixa no banco — qualidade do lucro não verificável.",
        "COBERTURA: EBITDA ausente na DRE estruturada.",
        "COBERTURA: nenhum documento CVM indexado — parecer sem base documental.",
        "COBERTURA: apenas 2 ano(s) de DRE anual — histórico curto.",
    ]
    for linha in antigas:
        assert severidade_flag(linha) == SEVERIDADE_COBERTURA


def test_prefixo_desconhecido_cai_no_lado_seguro():
    """Um prefixo novo tem de aparecer para quem decide, não sumir."""
    assert severidade_flag("NOVIDADE: qualquer coisa") == SEVERIDADE_RISCO
    assert severidade_flag("PREJUÍZO CONTÁBIL EM 7 DOS 10 ANOS.") == SEVERIDADE_RISCO
    assert severidade_flag("") == SEVERIDADE_RISCO


def test_agrupamento_entrega_sempre_as_tres_chaves():
    vazio = agrupa_flags_por_severidade(None)
    assert set(vazio) == {SEVERIDADE_RISCO, SEVERIDADE_CONTEXTO, SEVERIDADE_COBERTURA}
    assert all(v == [] for v in vazio.values())
    grupos = agrupa_flags_por_severidade([_RISCO, _CONTEXTO, _COBERTURA])
    assert grupos[SEVERIDADE_RISCO] == [_RISCO]
    assert grupos[SEVERIDADE_CONTEXTO] == [_CONTEXTO]
    assert grupos[SEVERIDADE_COBERTURA] == [_COBERTURA]


# ── (2) consumidor 1: o texto que a LLM do gate recebe ──────────────────────

def _dossie_minimo(flags: list[str]) -> dict:
    return {
        "ticker": "TEST3", "nome": "Teste", "setor": "X", "subsetor": "Y",
        "segmento": "Z", "serie_anual": [], "trimestres": {}, "dividendos": {},
        "valuation": {}, "metricas_banco": {}, "sensibilidade_juros": {},
        "eventos_societarios": {}, "red_flags": flags,
    }


def test_prompt_recebe_os_tres_blocos_apartados():
    txt = dossie_to_text(_dossie_minimo([_RISCO, _CONTEXTO, _COBERTURA]))
    i_risco = txt.index("RED FLAGS DETERMINÍSTICAS — RISCO CONFIRMADO")
    i_ctx = txt.index("OBSERVAÇÕES DE CONTEXTO")
    i_cob = txt.index("LIMITAÇÕES DE COBERTURA")
    assert i_risco < i_ctx < i_cob
    # a linha de contexto e a de cobertura ficam DEPOIS do cabeçalho delas,
    # não sob o cabeçalho de risco confirmado
    assert txt.index(_CONTEXTO) > i_ctx
    assert txt.index(_COBERTURA) > i_cob
    assert i_risco < txt.index(_RISCO) < i_ctx
    # nenhuma linha se perdeu — omitir se lê como "nada encontrado"
    for linha in (_RISCO, _CONTEXTO, _COBERTURA):
        assert linha in txt


def test_empresa_so_com_contexto_nao_abre_secao_de_risco_confirmado():
    """222 das 423 empresas do armazém local estão exatamente aqui."""
    txt = dossie_to_text(_dossie_minimo([_CONTEXTO, _COBERTURA]))
    assert "RISCO CONFIRMADO" not in txt
    assert "OBSERVAÇÕES DE CONTEXTO" in txt
    assert _CONTEXTO in txt


def test_prompt_ensina_que_contexto_e_cobertura_nao_reprovam():
    assert "5.4." in _PROMPT_PARECER
    trecho = _PROMPT_PARECER[_PROMPT_PARECER.index("5.4."):]
    trecho = trecho[:trecho.index("CONTEXTO DE PORTFÓLIO")]
    assert "OBSERVAÇÕES DE CONTEXTO" in trecho
    assert "LIMITAÇÕES DE COBERTURA" in trecho
    assert "vetar" in trecho and "ressalvar" in trecho
    # a numeração 4/5/5.1/5.2/5.3 não foi mexida
    for regra in ("\n4. ", "\n5. ", "\n5.1.", "\n5.2.", "\n5.3."):
        assert regra in _PROMPT_PARECER


# ── (3) consumidor 2: views/empresas_b3._render_b3_dossie ───────────────────

def test_empresas_b3_avisa_so_o_risco_confirmado():
    from views.empresas_b3 import _render_b3_dossie

    src = inspect.getsource(_render_b3_dossie)
    assert "agrupa_flags_por_severidade" in src
    # st.warning só dentro do laço de risco confirmado
    assert "for flag in _grupos[SEVERIDADE_RISCO]:\n            st.warning(flag)" in src
    assert "for flag in _grupos[SEVERIDADE_CONTEXTO]:\n            st.info(flag)" in src
    assert "for flag in _grupos[SEVERIDADE_COBERTURA]:\n            st.caption(flag)" in src
    # o laço antigo, que dava st.warning em TODA linha, não pode voltar
    assert 'for flag in dossie.get("red_flags", []):' not in src


# ── (4) consumidor 3: views/analise_portfolio_b3 ────────────────────────────

def test_analise_portfolio_b3_poe_bandeira_so_no_risco_confirmado():
    fonte = (_RAIZ / "views" / "analise_portfolio_b3.py").read_text(encoding="utf-8")
    assert "agrupa_flags_por_severidade" in fonte
    assert 'for f_ in _grupos[SEVERIDADE_RISCO]:\n                st.markdown(f"🚩 {f_}")' in fonte
    assert "for f_ in _grupos[SEVERIDADE_CONTEXTO]:" in fonte
    assert "for f_ in _grupos[SEVERIDADE_COBERTURA]:" in fonte
    # o laço antigo, que punha 🚩 em toda linha sob um cabeçalho único
    assert 'flags = d.get("red_flags") or []' not in fonte
    assert '**Red flags determinísticas (verificadas em código)**' not in fonte
    # os outros dois blocos NÃO levam bandeira
    assert 'for f_ in _grupos[SEVERIDADE_CONTEXTO]:\n                st.markdown(f"ℹ️ {f_}")' in fonte
    assert 'for f_ in _grupos[SEVERIDADE_COBERTURA]:\n                st.caption(f_)' in fonte


# ── (5) unicidade da regra, verificada por AST ──────────────────────────────

def _py_versionados() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_RAIZ, capture_output=True, text=True,
    )
    return [_RAIZ / p for p in out.stdout.splitlines() if p.strip()]


def test_regra_de_prefixo_existe_uma_unica_vez_no_repositorio():
    """Três cópias da mesma guarda com duas divergências já aconteceram nesta
    base. Quem quiser a severidade chama ``severidade_flag``; ninguém mais
    compara os prefixos."""
    culpados: list[str] = []
    for caminho in _py_versionados():
        # só código de produção: um teste PODE citar o prefixo como dado de
        # entrada (é o que este arquivo faz).
        if "tests" in caminho.parts or caminho.name == "dossie_b3.py":
            continue
        try:
            arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Constant) or not isinstance(no.value, str):
                continue
            if no.value.strip() in ("CONTEXTO:", "COBERTURA:", "MOMENTUM:", "DADOS:"):
                culpados.append(f"{caminho.relative_to(_RAIZ)}:{no.lineno}")
    assert not culpados, f"prefixo comparado fora de core/dossie_b3.py: {culpados}"


# ── (6) resíduo do I-C: NaN em n_anos_payout não pode zerar a carteira ──────

def test_n_anos_payout_ausente_nao_derruba_a_criacao_de_carteira():
    from views.portfolio_b3 import _motivos_dy_sustentavel

    for n_anos in (float("nan"), None):
        df = pd.DataFrame.from_records([{
            "Ticker": "PETR4", "DY": 0.10, "payout_sustentabilidade": 0.8,
            "dy_sustentavel": 0.08, "n_anos_payout": n_anos,
        }])
        linhas = _motivos_dy_sustentavel("PETR4", df)
        assert linhas, "a linha nunca pode sumir"
        assert "nan" not in linhas[0].lower()
        assert "não informado" in linhas[0]


# ── (7) período isolado e defeito do nosso banco não são risco da empresa ──

def test_momentum_de_um_trimestre_nao_e_risco_confirmado():
    """`MOMENTUM:` olha UM trimestre a/a. A instrução que governa este ramo é
    julgar a qualidade histórica, não condenar por um período isolado — e 94
    das 426 empresas do armazém estavam em bandeira vermelha só por esta
    linha. Ela permanece visível, sob o cabeçalho honesto."""
    flag = "MOMENTUM: lucro do último trimestre caiu -40.0% a/a (2026T2)."
    assert severidade_flag(flag) == SEVERIDADE_CONTEXTO


def test_defeito_do_nosso_banco_nao_marca_a_empresa_de_perigosa():
    """`DADOS:` descreve inconsistência da NOSSA ingestão, não risco da
    companhia. 50 das 426 estavam em vermelho só por isso."""
    for flag in (
        "DADOS: proventos com valores distintos na mesma data-ex — provável eco.",
        "DADOS: métrica DY do banco (9.0%) diverge do recomputado (4.0%).",
    ):
        assert severidade_flag(flag) == SEVERIDADE_COBERTURA


def test_linha_reclassificada_continua_chegando_ao_parecer():
    """Reclassificar não pode virar silenciar: silêncio no dossiê lê-se como
    "nada encontrado"."""
    texto = dossie_to_text({
        "ticker": "XPTO3",
        "red_flags": [
            "MOMENTUM: lucro do último trimestre caiu -40.0% a/a (2026T2).",
            "DADOS: métrica DY do banco (9.0%) diverge do recomputado (4.0%).",
        ],
    })
    assert "MOMENTUM: lucro do último trimestre caiu -40.0%" in texto
    assert "DADOS: métrica DY do banco" in texto
    assert "RED FLAGS DETERMINÍSTICAS" not in texto
