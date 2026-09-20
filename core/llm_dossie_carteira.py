"""Dossiê estruturado de uma classe da carteira.

O chat livre responde o que o usuário sabe perguntar. O dossiê responde o que
ele talvez não soubesse perguntar: as mesmas doze seções, sempre na mesma
ordem, em qualquer sub-aba da Análise. O esqueleto fixo é o ponto — é ele que
permite comparar a leitura de Ações com a de FIIs sem reler tudo.

O que muda entre classes é o conteúdo de cada seção, não a lista delas. Três
seções mudam de verdade:

* **concentração** é setorial em ações e exterior, por tipo em FII, por
  indexador e vencimento em Tesouro;
* **substituição** compara com pares do mesmo setor ou do mesmo tipo, e em
  Tesouro vira escolha de indexador — não existe "par" de um título público,
  o emissor é um só;
* **rebalanceamento** carrega tributação, e os quatro regimes são diferentes.

Sobre tributação, uma regra que vale a pena ler antes de mexer: nenhuma
alíquota e nenhum limite de isenção estão escritos aqui. Eles não vêm do
contexto — são conhecimento externo do modelo — e gravá-los no prompt os
congelaria numa constante que envelhece sem parecer errada. A instrução exige
que o modelo nomeie o regime que aplicou e o marque como premissa externa.
"""
from __future__ import annotations

from core.llm_b3 import _chat_complete, _report_model
from core.portfolio_valuations import CLASS_LABELS

# Cada entrada é (título da seção, instrução para o modelo). A ordem é a ordem
# de saída; os títulos são o contrato visual entre as sub-abas.
_CONCENTRACAO = {
    "acoes": (
        "1. Concentração Setorial Atual",
        "Agrupe as posições por setor e diga o peso de cada setor dentro da "
        "classe. Nomeie a maior posição individual e o peso dela. Diga o que "
        "essa concentração expõe em comum — mesmo ciclo, mesmo regulador, "
        "mesma sensibilidade a Selic ou câmbio — em vez de só repetir os "
        "percentuais.",
    ),
    "fiis": (
        "1. Composição por Tipo e Segmento",
        "Agrupe por tipo (tijolo, papel, FoF, híbrido) e, dentro de tijolo, "
        "por segmento (lajes, logística, shoppings, renda urbana). Papel e "
        "tijolo não correm o mesmo risco: diga qual parte da renda depende de "
        "crédito indexado e qual depende de ocupação física. Se o contexto "
        "trouxer a administradora ou gestora, avalie a concentração nela — "
        "ela é um fator de risco que não aparece no segmento.",
    ),
    "tesouro": (
        "1. Composição por Indexador e Vencimento",
        "Agrupe por indexador (Selic, IPCA+, Prefixado) e diga o peso de cada "
        "um. Descreva a distribuição de vencimentos e o prazo médio, com a "
        "cobertura que o contexto declarar. Concentração em um único indexador "
        "é uma aposta direcional sobre juro e inflação — diga qual aposta é, "
        "em palavras.",
    ),
    "exterior": (
        "1. Composição por Moeda, Veículo e Setor",
        "Separe o que é ação individual do que é ETF ou fundo de índice: ETF "
        "não tem demonstração de companhia e não admite leitura de múltiplo. "
        "Diga o peso por setor no que for ação e a exposição cambial total da "
        "classe. Se o contexto não trouxer a composição interna de um ETF, "
        "declare que a exposição setorial dele é desconhecida.",
    ),
}

_SUBSTITUICAO = {
    "acoes": (
        "5. Possíveis Substituições e Inclusões",
        "Use a lista de PARES DO UNIVERSO. Para cada troca que considerar, diga "
        "qual posição sairia, qual entraria, em que o par é melhor pela nota e "
        "pelas trilhas, e o que se perderia na troca. Compare dentro do mesmo "
        "setor — trocar uma elétrica por um banco muda a exposição, não "
        "melhora a qualidade. Se não houver par melhor no setor, diga isso.",
    ),
    "fiis": (
        "5. Possíveis Substituições e Inclusões",
        "Use a lista de PARES DO UNIVERSO, comparando SEMPRE dentro do mesmo "
        "tipo: um fundo de papel não substitui um de tijolo, mesmo com nota "
        "maior. Considere status de publicação e confiança do par antes de "
        "sugeri-lo — par com nota alta e só diligência não é recomendação "
        "pronta.",
    ),
    "tesouro": (
        "5. Ajuste de Indexador e Prazo",
        "Não existe par: o emissor é único e não há corte transversal para "
        "ranquear. A escolha é entre indexadores e prazos. Diga que troca faz "
        "sentido pela conjuntura do contexto — juro real, Selic, IPCA — e "
        "lembre que trocar antes do vencimento realiza marcação a mercado, "
        "com resultado que pode ser negativo.",
    ),
    "exterior": (
        "5. Possíveis Substituições e Inclusões",
        "Use a lista de PARES DO UNIVERSO, dentro do mesmo setor. Para "
        "posições em ETF, não proponha substituição por ação individual como "
        "se fosse equivalente: são exposições de natureza diferente. Diga "
        "quando a substituição implicaria concentrar o que hoje é "
        "diversificado.",
    ),
}

_REBALANCEAMENTO = {
    "acoes": (
        "7. Rebalanceamento: Aporte Novo ou Venda",
        "Compare corrigir o peso por aporte novo contra corrigir por venda. "
        "Ao falar de tributação de venda de ação no Brasil, NOMEIE o regime "
        "que está aplicando (alíquota e eventual limite de isenção mensal) e "
        "marque-o explicitamente como premissa externa ao contexto, sujeita a "
        "conferência — esse dado não está nos dados desta carteira. Se o "
        "usuário tiver enviado valores em reais, use-os; se não, trabalhe em "
        "percentual e diga que o corte tributário depende do valor.",
    ),
    "fiis": (
        "7. Rebalanceamento: Aporte Novo ou Venda",
        "Compare aporte novo contra venda. Atenção ao ponto em que FII difere "
        "de ação: o rendimento mensal e o ganho de capital na venda têm "
        "tratamentos distintos. NOMEIE o regime que está aplicando e marque-o "
        "como premissa externa ao contexto, sujeita a conferência. Considere "
        "também a liquidez do fundo — sair de um FII pouco negociado custa "
        "spread, não só imposto.",
    ),
    "tesouro": (
        "7. Rebalanceamento: Novo Aporte, Carregar ou Vender Antes",
        "Três caminhos, não dois: aportar em título novo, carregar até o "
        "vencimento ou vender antes. Vender antes realiza marcação a mercado "
        "e o resultado pode ser negativo mesmo em título 'seguro'. NOMEIE o "
        "regime tributário que está aplicando (tabela regressiva, IOF em "
        "resgate curto) e marque-o como premissa externa ao contexto.",
    ),
    "exterior": (
        "7. Rebalanceamento: Aporte Novo ou Venda",
        "Compare aporte novo contra venda, e some o custo de câmbio, que não "
        "existe nas classes em reais. NOMEIE o regime tributário de "
        "investimento no exterior que está aplicando e marque-o como premissa "
        "externa ao contexto, sujeita a conferência — a legislação mudou nos "
        "últimos anos e o contexto não traz essa informação.",
    ),
}

_DOCUMENTOS = {
    "acoes": (
        "8. Histórico CVM/IPE Recente",
        "Use APENAS o bloco EVIDÊNCIA DOCUMENTAL. Cite data e tipo do "
        "documento em cada afirmação. Se o bloco disser que não há corpus "
        "para um ativo, diga isso no lugar de inferir da cotação.",
    ),
    "fiis": (
        "8. Notícias e Comunicados Recentes",
        "Use APENAS o bloco EVIDÊNCIA DOCUMENTAL. Cite a data de cada item. "
        "Fundo sem item no acervo não é fundo sem notícia — é fundo fora da "
        "janela de coleta, e a diferença precisa aparecer no texto.",
    ),
    "tesouro": (
        "8. Evidência Documental",
        "Não há corpus de documentos para título público neste app. Declare "
        "isso em uma linha e não preencha a seção com conjuntura genérica.",
    ),
    "exterior": (
        "8. Notícias Recentes",
        "Use APENAS o bloco EVIDÊNCIA DOCUMENTAL. Cite a data de cada item. "
        "Ausência de item significa fora da janela de coleta, não ausência de "
        "fato relevante.",
    ),
}

# Seções cujo texto não muda entre classes.
_COMUNS: dict[int, tuple[str, str]] = {
    2: (
        "2. Comparação Dentro da Classe e com o Universo",
        "Monte uma tabela com uma linha por ativo da carteira: peso, nota do "
        "universo quando houver, posição relativa e os indicadores que o "
        "contexto trouxer. Ativo sem nota entra como SEM NOTA — nunca como "
        "nota mediana. Depois compare os ativos entre si e diga quem puxa "
        "cada média para cima e para baixo.",
    ),
    3: (
        "3. Pontos Fortes",
        "O que esta parte da carteira tem de bom, com a evidência ao lado. "
        "Cada ponto forte precisa citar o número ou o fato do contexto que o "
        "sustenta. Sem evidência no contexto, não é ponto forte — é impressão.",
    ),
    4: (
        "4. Pontos de Atenção",
        "O que preocupa, com a mesma exigência de evidência. Inclua "
        "concentração, dependência de um único fator e qualquer indicador "
        "fora da faixa dos pares. Cobertura parcial de uma média é ponto de "
        "atenção por si só.",
    ),
    6: (
        "6. Deterioração da Tese ou Problema Temporário",
        "Para cada ponto de atenção da seção 4, diga o que distinguiria uma "
        "deterioração estrutural de um problema de ciclo, e qual dado "
        "resolveria a dúvida. Se o contexto não permite separar os dois casos, "
        "diga isso em vez de escolher um.",
    ),
    9: (
        "9. Tabela Final: Peso Atual, Peso Sugerido e Ação",
        "Uma tabela com uma linha por ativo: peso atual, peso sugerido, ação "
        "(aumentar, manter, reduzir, sair) e o motivo em uma frase. Os pesos "
        "sugeridos têm que somar 100% da classe. Se você não tiver base para "
        "mexer em algum ativo, mantenha o peso e diga que é por falta de "
        "base, não por convicção.",
    ),
    10: (
        "10. Plano para os Próximos Aportes",
        "Em que ordem o próximo dinheiro entraria e por quê. Seja concreto "
        "sobre a sequência. Se o usuário enviou valores em reais, pode usar "
        "reais; caso contrário, use percentuais do aporte.",
    ),
    11: (
        "11. Riscos desta Parte da Carteira",
        "Os riscos que sobram depois de tudo acima, inclusive os que nenhuma "
        "recomendação sua resolve. Concentração, correlação e desempenho "
        "passado não são previsão.",
    ),
    12: (
        "12. Limitações dos Dados",
        "O que faltou para esta análise: cobertura parcial das médias, ativos "
        "sem nota, campos ausentes, corpus documental vazio. Para cada "
        "limitação, diga como a conclusão mudaria se o dado existisse. Esta "
        "seção não é aviso legal genérico — derive cada linha do que o "
        "contexto declarou faltando.",
    ),
}


def secoes_da_classe(classe: str) -> tuple[tuple[str, str], ...]:
    """As doze seções do dossiê, na ordem, com a instrução de cada uma."""
    chave = str(classe or "").lower()
    if chave not in _CONCENTRACAO:
        chave = "acoes"
    especificas = {
        1: _CONCENTRACAO[chave],
        5: _SUBSTITUICAO[chave],
        7: _REBALANCEAMENTO[chave],
        8: _DOCUMENTOS[chave],
    }
    return tuple(especificas.get(i) or _COMUNS[i] for i in range(1, 13))


def _roteiro(classe: str) -> str:
    return "\n\n".join(
        f"### {titulo}\n{instrucao}" for titulo, instrucao in secoes_da_classe(classe)
    )


def prompt_do_dossie(context: str, *, classe: str) -> str:
    """O system prompt completo — separado da chamada para ser testável."""
    from core.llm_carteira import escopo_da_classe, regras_da_analise

    rotulo = CLASS_LABELS.get(str(classe or "").lower(), classe)
    return (
        "Você é um analista quantitativo sênior escrevendo um dossiê sobre a "
        f"parte da carteira do seu cliente alocada em {rotulo}. O escopo é: "
        f"{escopo_da_classe(classe)} Escreva em português do Brasil.\n\n"
        f"{regras_da_analise()}\n\n"
        "ESTRUTURA OBRIGATÓRIA — escreva as doze seções abaixo, nesta ordem, "
        "com exatamente estes títulos em markdown (##). Não acrescente seções, "
        "não junte duas numa só e não pule nenhuma. Seção sem base no contexto "
        "é escrita mesmo assim, dizendo o que falta:\n\n"
        f"{_roteiro(classe)}\n\n"
        f"=== CONTEXTO DA CLASSE ===\n{context}"
    )


def gerar_dossie_classe(context: str, *, classe: str,
                        model: str | None = None) -> str:
    """Gera o dossiê estruturado de uma classe a partir do contexto auditável."""
    messages = [
        {"role": "system", "content": prompt_do_dossie(context, classe=classe)},
        {"role": "user", "content": (
            "Escreva o dossiê completo desta classe, com as doze seções na "
            "ordem definida.")},
    ]
    return _chat_complete(messages, temperature=.2, json_mode=False,
                          primary_model=model or _report_model())
