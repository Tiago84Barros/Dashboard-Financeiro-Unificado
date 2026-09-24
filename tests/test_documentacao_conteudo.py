"""Documentação: a estrutura do fluxograma fecha e o texto sai acentuado.

A tela é dirigida por dados — FlowSpec/FlowNode — e não por chamadas st.*.
Um id citado em `rows` que não existe em `nodes` derruba a aba inteira em
runtime, e um rótulo sem acento não quebra nada: o app roda, e só o usuário vê.
As duas guardas moram aqui.

Chave de dicionário e id de nó ficam de fora de propósito: ninguém os lê na
tela e trocá-los quebraria o vínculo entre `rows`, `nodes` e os overrides.
"""
import inspect
import re

import views.documentacao as doc

# "meses" e "medida" estão fora porque são grafia correta; "mes" e "media" não.
_SEM_ACENTO = re.compile(
    r"\b(nao|sao|ha|ate|mes|voce|tambem|analise|analises|posicao|posicoes|"
    r"criterio|criterios|periodo|periodos|metrica|metricas|media|medias|"
    r"medio|medios|nivel|niveis|numero|numeros|indice|indices|historico|"
    r"historica|serie|series|padrao|padroes|relacao|decisao|decisoes|"
    r"informacao|informacoes|confianca|referencia|estrategia|liquida|liquido|"
    r"divida|preco|precos|cartao|acao|acoes|selecao|secao|secoes|versao|"
    r"validacao|limitacao|limitacoes|restricao|restricoes|otimizacao|"
    r"simulacao|exposicao|concentracao|alocacao|avaliacao|projecao|"
    r"publicacao|ingestao|evidencia|frequencia|tendencia|patrimonio|"
    r"portfolio|dolar|inflacao|calculo|maximo|minimo|proximo|ultimo|unico|"
    r"obvio|codigo|modulo|rotulo|rotulos|possivel|disponivel|variavel|"
    r"estavel|confiavel|comparavel|cadencia|recencia|correlacao|saida|"
    r"saidas|abrangencia|diligencia|so|ja)\b", re.I)

_CAMPOS_DETALHE = ("titulo", "objetivo", "dados", "formula", "exemplo",
                   "interpretacao", "impacto", "limitacao")


def _textos_visiveis():
    """Todo literal que a Documentação desenha, com a origem junto."""
    for flow in doc.FLOWS:
        yield f"{flow.key}.title", flow.title
        yield f"{flow.key}.subtitle", flow.subtitle
        for camada, _ids in flow.rows:
            yield f"{flow.key}.camada", camada
        for nota in flow.notes:
            yield f"{flow.key}.nota", nota
        for nid, no in flow.nodes.items():
            yield f"{flow.key}/{nid}.title", no.title
            yield f"{flow.key}/{nid}.layer", no.layer
            yield f"{flow.key}/{nid}.summary", no.summary
            yield f"{flow.key}/{nid}.why", no.why
            for item in no.contains:
                yield f"{flow.key}/{nid}.contains", item
            for campo, valor in doc._generic_flow_detail(flow, no).items():
                yield f"{flow.key}/{nid}.{campo}", valor
    for chave, rotulo in doc.ORDEM_ANALISE_AVANCADA:
        yield f"ordem/{chave}", rotulo
    for chave, etapa in doc.ETAPAS_ANALISE_AVANCADA.items():
        for campo, valor in etapa.items():
            yield f"etapa/{chave}.{campo}", valor


def test_nenhum_texto_da_documentacao_sai_sem_acento():
    faltas = []
    for origem, texto in _textos_visiveis():
        achado = _SEM_ACENTO.search(texto)
        if achado:
            faltas.append(f"{origem}: '{achado.group(0)}' em {texto[:60]!r}")
    assert not faltas, "texto visível sem acento: " + str(faltas)


def test_todo_id_citado_no_fluxograma_tem_no():
    """`rows` e `nodes` precisam descrever o mesmo grafo, nos dois sentidos."""
    for flow in doc.FLOWS:
        citados = [nid for _camada, grupo in flow.rows for nid in grupo]
        assert not set(citados) - set(flow.nodes), f"{flow.key}: id sem nó"
        assert not set(flow.nodes) - set(citados), f"{flow.key}: nó fora de rows"
        assert flow.default in flow.nodes, f"{flow.key}: default inválido"
        for nid, no in flow.nodes.items():
            assert no.id == nid, f"{flow.key}: chave {nid} != id {no.id}"


def test_todo_no_entrega_o_painel_completo():
    for flow in doc.FLOWS:
        for nid, no in flow.nodes.items():
            detalhe = doc._generic_flow_detail(flow, no)
            faltando = [c for c in _CAMPOS_DETALHE if not detalhe.get(c)]
            assert not faltando, f"{flow.key}/{nid}: sem {faltando}"


def test_nenhum_override_aponta_para_no_inexistente():
    """Override órfão é texto escrito que nunca aparece na tela."""
    todos = {nid for flow in doc.FLOWS for nid in flow.nodes}
    orfaos = [nid for nid in doc._FLOW_DETAIL_OVERRIDES if nid not in todos]
    assert not orfaos, f"override sem nó: {orfaos}"


def test_as_chaves_de_flow_sao_unicas():
    """A chave escolhe o ramo de `impacto` em _generic_flow_detail."""
    chaves = [flow.key for flow in doc.FLOWS]
    assert len(set(chaves)) == len(chaves), chaves


def test_os_modulos_novos_tem_aba():
    """Flow criado e não pendurado nas abas é documentação invisível.

    As abas moram em ``render_corpo`` desde que a Documentação virou aba de
    Configurações; ``render`` só acrescenta o cabeçalho da tela avulsa.
    """
    fonte = inspect.getsource(doc.render_corpo)
    for nome in ("FLOW_SELECAO_FIIS", "FLOW_EMPRESAS_EUA",
                 "FLOW_PORTFOLIO_GLOBAL", "FLOW_QUALIDADE_DADOS"):
        assert nome in fonte, f"{nome} sem aba"
    assert fonte.count("with tabs[") == len(doc.FLOWS) + 1  # +1: Indicadores


def test_os_uploads_sao_descritos_em_configuracoes():
    """A fatura e o extrato saíram das abas e vivem em Configurações.

    O texto antigo mandava o usuário para a aba Cartão de Crédito e para
    "Extratos Bancários", telas que já não recebem arquivo.
    """
    nos = doc.FLOW_CONTROLE_FINANCEIRO.nodes
    for nid in ("fatura_cc", "extrato"):
        assert "Configurações" in nos[nid].summary, nid
    assert "Extratos Bancários" not in nos["extrato"].summary
