"""Task 7 — tela mostra DY sustentável e etiqueta o líder admitido por
viabilidade.

A montagem de ``motivos`` e dos avisos de transparência do piso mora dentro
de ``render()``, uma função gigante que precisa de Streamlit + banco reais
(nem Supabase nem o warehouse local estão alcançáveis neste ambiente). Por
isso a Task 7 extraiu a lógica de exibição para três funções puras —
``_motivo_afrouxamento_lider``, ``_motivos_dy_sustentavel`` e
``_avisos_afrouxamento_piso`` — e ``render()`` apenas as chama. Testar essas
funções prova exatamente o que aparece na tela, porque são a MESMA linha de
produção, não uma reimplementação paralela.
"""
import pandas as pd

from views.portfolio_b3 import (
    _avisos_afrouxamento_piso,
    _motivo_afrouxamento_lider,
    _motivos_dy_sustentavel,
)


def _df_mult(**linhas: dict) -> pd.DataFrame:
    registros = []
    for tk, campos in linhas.items():
        registro = {"Ticker": tk, "DY": campos.get("DY", 0.05),
                    "payout_sustentabilidade": campos.get("payout_sustentabilidade"),
                    "dy_sustentavel": campos.get("dy_sustentavel"),
                    "n_anos_payout": campos.get("n_anos_payout", 0)}
        registros.append(registro)
    return pd.DataFrame.from_records(registros)


# ── (a) DY sustentável aparece quando há sustentabilidade ────────────────────

def test_motivos_dy_sustentavel_mostra_linha_quando_sustentavel():
    df = _df_mult(PETR4={
        "DY": 0.10, "payout_sustentabilidade": 0.8,
        "dy_sustentavel": 0.08, "n_anos_payout": 5,
    })

    motivos = _motivos_dy_sustentavel("PETR4", df)

    assert len(motivos) == 1
    assert "DY sustentável 8.0%" in motivos[0]
    assert "divulgado 10.0%" in motivos[0]
    assert "sustentabilidade 80%" in motivos[0]
    assert "5 anos" in motivos[0]


# ── (b) indisponibilidade aparece (não é omitida) quando faltam anos ─────────

def test_motivos_dy_sustentavel_mostra_indisponibilidade_sem_omitir():
    df = _df_mult(VALE3={
        "DY": 0.06, "payout_sustentabilidade": float("nan"),
        "dy_sustentavel": float("nan"), "n_anos_payout": 1,
    })

    motivos = _motivos_dy_sustentavel("VALE3", df)

    assert len(motivos) == 1
    assert "indisponível" in motivos[0]
    assert "menos de 3 anos" in motivos[0]


def test_motivos_dy_sustentavel_vazio_quando_ticker_ausente_do_universo():
    df = _df_mult(VALE3={"DY": 0.06, "payout_sustentabilidade": 0.5,
                          "dy_sustentavel": 0.03, "n_anos_payout": 4})

    assert _motivos_dy_sustentavel("PETR4", df) == []


# ── (c) aviso de afrouxamento aparece quando há item ──────────────────────────

def test_motivo_afrouxamento_lider_presente_quando_marcado():
    piso_log = {"afrouxado_por_viabilidade": [
        {"tk": "LIDER3", "segmento": "Papel e Celulose",
         "motivo": "nenhum candidato do segmento sobreviveu"},
    ]}

    motivo = _motivo_afrouxamento_lider("LIDER3", piso_log)

    assert motivo is not None
    assert "Entrou com ressalva" in motivo


def test_motivo_afrouxamento_lider_ausente_quando_nao_marcado():
    piso_log = {"afrouxado_por_viabilidade": []}

    assert _motivo_afrouxamento_lider("LIDER3", piso_log) is None


def test_avisos_afrouxamento_piso_gera_mensagem_por_item():
    piso_log = {"afrouxado_por_viabilidade": [
        {"tk": "LIDER3", "segmento": "Papel e Celulose",
         "motivo": "nenhum candidato do segmento sobreviveu à confirmação "
                   "histórica de payout"},
    ]}

    avisos = _avisos_afrouxamento_piso(piso_log)

    assert len(avisos) == 1
    assert "Papel e Celulose" in avisos[0]
    assert "LIDER3" in avisos[0]
    assert "MARCADO" in avisos[0]
    assert "não confirmada" in avisos[0]


def test_avisos_afrouxamento_piso_vazio_quando_sem_afrouxamento():
    assert _avisos_afrouxamento_piso({"afrouxado_por_viabilidade": []}) == []
    # log sem a chave (compat com quem monta piso_log fora do padrão da Task 5)
    assert _avisos_afrouxamento_piso({}) == []


# ── I-A: os quatro call sites da Task 7 ──────────────────────────────────────
# Os testes acima exercitam os três helpers puros, mas a revisão final provou
# por mutação que apagar a FIAÇÃO inteira dentro de ``render()`` deixava a
# suíte verde: motor de análise que ninguém consulta é decoração. ``render()``
# tem ~1.100 linhas e exige Streamlit + banco reais, então a cobertura segue o
# padrão já aceito na Task 6 (tests/test_b3_renda_sustentavel.py): regex
# específica sobre ``inspect.getsource`` da função chamadora. Cada regex é
# estreita o bastante para não casar se a linha sair, mudar de alvo ou mudar
# de argumentos — verificado por mutação, uma de cada vez.


def _fonte_render() -> str:
    import inspect

    import views.portfolio_b3 as pb3

    return inspect.getsource(pb3.render)


def _indentacao(fonte: str, padrao: str) -> int:
    import re

    m = re.search(r"^([ ]*)" + padrao, fonte, re.MULTILINE)
    assert m, f"linha não encontrada em render(): {padrao}"
    return len(m.group(1))


def test_render_liga_dy_sustentavel_aos_motivos_do_card():
    import re

    fonte = _fonte_render()
    assert re.search(
        r"motivos\.extend\(\s*_motivos_dy_sustentavel\(\s*tk,\s*"
        r"df_mult_todos\s*\)\s*\)",
        fonte,
    ), ("render() não liga mais _motivos_dy_sustentavel aos motivos do card "
        "do líder — a linha de DY sustentável some da tela em silêncio")


def test_render_chama_dy_sustentavel_fora_do_if_piso_ativo():
    # A chamada é INCONDICIONAL de propósito: a sustentabilidade é evidência
    # sobre a empresa, não consequência do piso estar ligado. Se voltar para
    # dentro de `if _piso_ativo:`, ela fica um nível mais indentada que a
    # chamada do afrouxamento (essa sim condicionada ao piso).
    fonte = _fonte_render()
    dy = _indentacao(fonte, r"motivos\.extend\(_motivos_dy_sustentavel")
    afr = _indentacao(fonte, r"_motivo_afr = _motivo_afrouxamento_lider")
    assert dy < afr, (
        "a chamada de _motivos_dy_sustentavel voltou para dentro de um bloco "
        "condicional: o DY sustentável passaria a depender do piso estar ativo"
    )


def test_render_etiqueta_o_lider_admitido_por_viabilidade():
    import re

    fonte = _fonte_render()
    assert re.search(
        r"_motivo_afr\s*=\s*_motivo_afrouxamento_lider\(\s*tk,\s*piso_log\s*\)"
        r"\s*\n\s*if\s+_motivo_afr:\s*\n\s*motivos\.append\(\s*_motivo_afr\s*\)",
        fonte,
    ), ("render() não etiqueta mais o líder readmitido pela guarda de "
        "viabilidade — a ressalva some do card")


def test_render_publica_os_avisos_de_afrouxamento_do_piso():
    import re

    fonte = _fonte_render()
    assert re.search(
        r"for\s+_msg_afr\s+in\s+_avisos_afrouxamento_piso\(\s*piso_log\s*\)\s*:"
        r"\s*\n\s*st\.warning\(\s*_msg_afr",
        fonte,
    ), ("a seção de transparência do piso não imprime mais "
        "_avisos_afrouxamento_piso — o afrouxamento vira silêncio")


def test_portao_de_transparencia_abre_so_por_afrouxamento():
    # Sem este termo, um afrouxamento SOZINHO (sem reprovado nem vaga vazia)
    # não abre a seção, e o aviso do teste anterior nunca é alcançado.
    import re

    fonte = _fonte_render()
    assert re.search(
        r"if\s+_piso_ativo\s+and\s+\([^()]*afrouxado_por_viabilidade[^()]*\)\s*:",
        fonte,
    ), ("o portão da seção de transparência do piso não considera mais "
        "afrouxado_por_viabilidade — afrouxamento isolado fica invisível")


# ── I-C: a linha do DY sustentável não pode zerar nem mentir ─────────────────

def test_dy_sustentavel_sem_coluna_dy_nao_derruba_a_criacao():
    """A chamada é incondicional no caminho de criação de carteira: um
    KeyError aqui zeraria o portfólio, que é a restrição inviolável do
    projeto. Sem a coluna DY, a linha diz que faltou evidência."""
    df = pd.DataFrame({"Ticker": ["XPTO3"], "payout_sustentabilidade": [0.80],
                       "n_anos_payout": [8]})

    motivos = _motivos_dy_sustentavel("XPTO3", df)

    assert len(motivos) == 1
    assert "indisponível" in motivos[0]
    assert "nan" not in motivos[0].lower()


def test_dy_sustentavel_com_dy_ausente_declara_a_ausencia():
    """Sustentabilidade presente com DY ausente ocorre em 81 das 426 linhas
    do universo real (27 aprovadas pelo piso) e saía como 'DY sustentável
    nan% (divulgado nan% × sustentabilidade 80% em 8 anos)' — ausência virando
    ruído, contra a promessa da própria docstring do helper."""
    df = _df_mult(XPTO3={"DY": float("nan"), "payout_sustentabilidade": 0.80,
                         "dy_sustentavel": float("nan"), "n_anos_payout": 8})

    motivos = _motivos_dy_sustentavel("XPTO3", df)

    assert len(motivos) == 1, "a linha não pode sumir: ausência é informação"
    assert "nan" not in motivos[0].lower()
    assert "indisponível" in motivos[0]
    # a evidência que EXISTE continua à vista
    assert "80%" in motivos[0] and "8 anos" in motivos[0]
