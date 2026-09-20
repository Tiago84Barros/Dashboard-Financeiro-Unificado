"""Aba Cartão de Crédito: o texto que o usuário lê sai acentuado.

Meia dúzia de rótulos da aba nasceram sem acento ("Projecao de faturas
futuras", "Ticket medio", "Cartao de Credito") e nenhum deles quebra nada —
o app roda, o teste passa, e só o usuário vê. Por isso a guarda é estrutural:
varre as chamadas que imprimem texto e reprova palavra portuguesa escrita sem
acento, para que o próximo rótulo a nascer caia na mesma regra.

Chave de dado (nome de coluna crua, slug de categoria, status interno) fica de
fora de propósito: ela não é lida por ninguém e trocá-la quebra comparação.
"""
import ast
import inspect
import re

import pandas as pd

import views.controle_financeiro as cf

# Funções que desenham texto na tela. _kpi_card e _secao_titulo entram porque
# recebem o rótulo como literal na própria chamada.
_IMPRIMEM_TEXTO = {
    "markdown", "caption", "info", "warning", "success", "error", "write",
    "selectbox", "text_input", "number_input", "checkbox", "toggle", "radio",
    "multiselect", "date_input", "expander", "button", "form_submit_button",
    "metric", "subheader", "header", "_kpi_card", "_secao_titulo",
}

# "meses" está fora porque é a grafia correta; "mes" (singular) não é.
_SEM_ACENTO = re.compile(
    r"\b(nao|ha|sao|mes|lancamento|lancamentos|cartao|cartoes|descricao|"
    r"projecao|grafico|graficos|medio|media|liquido|credito|creditos|esta|ja|"
    r"evolucao|transacoes|recorrencia|distribuicao|analise|periodo|proximo|"
    r"minimo|maximo|referencia|codigo|numero|opcao|opcoes)\b", re.I)


def test_nenhum_texto_impresso_sai_sem_acento():
    arvore = ast.parse(inspect.getsource(cf))
    faltas = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        nome = (no.func.attr if isinstance(no.func, ast.Attribute)
                else getattr(no.func, "id", ""))
        if nome not in _IMPRIMEM_TEXTO:
            continue
        for arg in list(no.args) + [kw.value for kw in no.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                achado = _SEM_ACENTO.search(arg.value)
                if achado:
                    faltas.append(f"linha {no.lineno} ({nome}): '{achado.group(0)}' "
                                  f"em {arg.value[:60]!r}")
    assert not faltas, "texto visível sem acento:" + str(faltas)


def test_a_projecao_de_faturas_futuras_esta_acentuada():
    """O rótulo que o usuário apontou — fica preso pelo nome."""
    fonte = inspect.getsource(cf._tab_cartao)
    assert "Projeção de faturas futuras pelas parcelas restantes" in fonte
    assert "Projecao" not in fonte


def test_colunas_das_tabelas_do_cartao_saem_acentuadas():
    """As colunas são rótulo: o usuário as lê no cabeçalho da tabela.

    O quadro vazio é o que define o contrato — é ele que a tela desenha quando
    o filtro não devolve nada, e é dele que saem os nomes no caso cheio.
    """
    vazio = pd.DataFrame(columns=[
        "tipo_lancamento", "valor_fatura", "id", "categoria", "is_parcelada",
        "parcelas_restantes", "possivel_recorrente", "estabelecimento_norm",
        "data_compra", "estabelecimento", "valor_abs",
    ])
    esperado = {
        cf._prepare_category_analysis: ["Transações", "Ticket médio"],
        cf._prepare_recurring_analysis: ["Recorrência", "Valor médio"],
        cf._prepare_future_invoice_projection: ["Mês"],
        cf._prepare_installment_analysis: ["Valor no mês"],
        cf._prepare_non_consumption: ["Descrição"],
    }
    for funcao, colunas in esperado.items():
        publicadas = list(funcao(vazio).columns)
        for coluna in colunas:
            assert coluna in publicadas, f"{funcao.__name__}: {coluna} / {publicadas}"
