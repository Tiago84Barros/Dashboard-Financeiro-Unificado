"""Contexto determinístico de uma classe da carteira para o chat.

Duas decisões deliberadas:

* **Peso por padrão, saldo só a pedido.** O contexto traz a participação
  percentual de cada ativo dentro da classe. O percentual é o que responde às
  perguntas de concentração e exposição, e o saldo é dado pessoal: mandá-lo
  para um serviço externo sem necessidade é publicá-lo. Quando o dono da
  carteira marca o toggle da sub-aba, ``valores_reais`` liga e o valor de
  mercado **por posição daquela classe** entra — o patrimônio consolidado e as
  outras classes continuam fora em qualquer caso.
* **Ausência declarada.** Cobertura, ativos sem dado e universo indisponível
  entram no texto. Média calculada sobre metade da classe e média calculada
  sobre a classe inteira não podem chegar à LLM com a mesma cara.
"""
from __future__ import annotations

from core.portfolio_valuations import CLASS_LABELS, metric_spec, texto_marcacao_tesouro


def _n(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _pct(value, casas: int = 1) -> str:
    value = _n(value)
    return "ausente" if value is None else f"{value:.{casas}f}%"


def _frac(value) -> str:
    value = _n(value)
    return "ausente" if value is None else f"{value:.1%}"


def _pesos(posicoes) -> list[tuple[str, float]]:
    acumulado: dict[str, float] = {}
    for pos in posicoes or ():
        valor = _n(pos.get("valor_mercado")) or 0.0
        if valor <= 0:
            continue
        ticker = str(pos.get("ticker") or "").strip().upper()
        acumulado[ticker] = acumulado.get(ticker, 0.0) + valor
    total = sum(acumulado.values())
    if not total:
        return []
    return sorted(((t, v / total) for t, v in acumulado.items()),
                  key=lambda par: (-par[1], par[0]))


def _valores(posicoes) -> list[tuple[str, float]]:
    acumulado: dict[str, float] = {}
    for pos in posicoes or ():
        valor = _n(pos.get("valor_mercado")) or 0.0
        if valor <= 0:
            continue
        ticker = str(pos.get("ticker") or "").strip().upper()
        acumulado[ticker] = acumulado.get(ticker, 0.0) + valor
    return sorted(acumulado.items(), key=lambda par: (-par[1], par[0]))


def _bloco_valores(posicoes) -> list[str]:
    """Valor de mercado por posição — só quando o dono da carteira pediu.

    Vai o valor de cada ativo DESTA classe e nada mais. O total da classe é
    derivável da soma, e isso é aceito; o que não sai é o patrimônio
    consolidado nem qualquer posição de outra classe.
    """
    valores = _valores(posicoes)
    if not valores:
        return []
    linhas = ["VALOR DE MERCADO POR POSIÇÃO (enviado a pedido do dono da "
              "carteira; cobre SOMENTE esta classe):"]
    linhas += [f"- {ticker}: R$ {valor:,.2f}".replace(",", "@")
               .replace(".", ",").replace("@", ".") for ticker, valor in valores]
    linhas.append("- O patrimônio total do usuário NÃO está neste contexto e "
                  "não pode ser estimado a partir daqui: as demais classes "
                  "ficaram de fora.")
    return linhas


def _bloco_pares(pares, rotulo: str) -> list[str]:
    """Candidatos de fora da carteira, do mesmo grupo dos que estão dentro."""
    if not pares:
        return []
    linhas = [f"PARES DO UNIVERSO FORA DA CARTEIRA — {rotulo} (mesmo "
              "setor/tipo dos ativos que o usuário tem, ordenados por nota):"]
    for par in pares:
        partes = [f"- {par['ticker']}"]
        if par.get("nome"):
            partes.append(str(par["nome"]))
        score = _n(par.get("score"))
        if score is not None:
            partes.append(f"nota {score:.1f}/100")
        if par.get("grupo"):
            partes.append(f"grupo {par['grupo']}")
        cobertura = _n(par.get("cobertura"))
        if cobertura is not None:
            partes.append(f"cobertura {cobertura:.0f}%" if cobertura > 1
                          else f"cobertura {cobertura:.0%}")
        if par.get("status_publicacao"):
            partes.append(f"status {par['status_publicacao']}")
        linhas.append(" | ".join(partes))
    linhas.append("- Estes ativos NÃO estão na carteira. A nota vem do mesmo "
                  "motor que pontuou os que estão, então é comparável; ela não "
                  "leva em conta preço de entrada, liquidez do dia nem o "
                  "objetivo do usuário.")
    return linhas


def _bloco_documentos(documentos) -> list[str]:
    """Evidência documental — CVM/IPE ou notícias, conforme a classe."""
    if not documentos:
        return []
    if documentos.get("erro"):
        return ["EVIDÊNCIA DOCUMENTAL: " + str(documentos["erro"]) +
                " Não conclua nada sobre eventos recentes a partir daqui."]
    itens = list(documentos.get("itens") or ())
    if not itens and documentos.get("nota"):
        # Classe sem corpus (Tesouro). Dizer "nada na janela" aqui seria
        # sugerir uma janela que não existe.
        return ["EVIDÊNCIA DOCUMENTAL: " + str(documentos["nota"])]
    if not itens:
        return ["EVIDÊNCIA DOCUMENTAL: nenhum documento ou notícia na janela "
                "coletada para estes ativos. Isso significa fora da janela de "
                "coleta, NÃO ausência de fato relevante."]
    linhas = [f"EVIDÊNCIA DOCUMENTAL — {documentos.get('fonte', 'fonte não declarada')}:"]
    linhas += [f"- {item}" for item in itens]
    sem = list(documentos.get("sem_corpus") or ())
    if sem:
        linhas.append("- Sem nenhum documento na janela: " + ", ".join(sem) +
                      ". Trate como desconhecido, não como 'nada aconteceu'.")
    return linhas


def _bloco_valuations(valuations) -> list[str]:
    if not valuations:
        return ["MÉDIAS DA CLASSE: nenhuma apurada."]
    linhas = ["MÉDIAS DA CLASSE (ponderadas pelo valor de mercado, só sobre os "
              "ativos com dado válido):"]
    for chave, item in valuations.items():
        spec = metric_spec(chave)
        valor = _n(item.get("value"))
        texto = "ausente" if valor is None else f"{valor:.2f}{spec.unit}"
        linhas.append(
            f"- {spec.label}: {texto} | cobertura {_frac(item.get('coverage'))} "
            f"do valor da classe | {int(item.get('assets') or 0)} ativo(s)")
    return linhas


def _bloco_db(db, classe: str) -> list[str]:
    if not db:
        return []
    if db.get("erro"):
        return [f"BANCO ({classe}): {db['erro']} Nenhuma nota comparativa nesta sessão."]
    linhas = [f"COMPARAÇÃO COM O UNIVERSO DO BANCO — {db.get('referencia', '')}:".strip()]
    for linha in db.get("linhas") or ():
        partes = [f"- {linha['ticker']}"]
        score = _n(linha.get("score"))
        if score is not None:
            partes.append(f"nota {score:.1f}/100")
        if linha.get("classificacao"):
            partes.append(str(linha["classificacao"]))
        if linha.get("tipo"):
            partes.append(f"tipo {linha['tipo']}")
        if linha.get("setor"):
            partes.append(f"setor {linha['setor']}")
        percentil = _n(linha.get("percentil"))
        if percentil is not None:
            partes.append(f"percentil {percentil:.0%} do universo")
        cobertura = _n(linha.get("cobertura"))
        if cobertura is not None:
            partes.append(f"cobertura {cobertura:.0f}%" if cobertura > 1
                          else f"cobertura {cobertura:.0%}")
        confianca = _n(linha.get("confianca"))
        if confianca is not None:
            partes.append(f"confiança {confianca:.0%}")
        if linha.get("status_publicacao"):
            partes.append(f"status {linha['status_publicacao']}")
        linhas.append(" | ".join(partes))
    ausentes = list(db.get("ausentes") or ())
    if ausentes:
        linhas.append(
            "- Sem nota apurada no universo: " + ", ".join(ausentes) +
            ". Ausência de nota NÃO é nota mediana nem sinal negativo; significa "
            "que o ativo não está no corte transversal do banco.")
    return linhas


def _bloco_tesouro(tesouro, macro) -> list[str]:
    linhas: list[str] = []
    if tesouro:
        linhas.append("POSIÇÃO EM TESOURO DIRETO (agregada):")
        linhas.append(f"- Títulos distintos: {tesouro.get('titulos', 0)}")
        retorno = _n(tesouro.get("retorno_pct"))
        linhas.append("- Retorno mercado sobre custo acumulado: "
                      + (f"{retorno:.2f}%" if retorno is not None else "ausente")
                      + " (acumulado desde o aporte, não taxa ao ano)")
        prazo = _n(tesouro.get("prazo_medio_anos"))
        linhas.append("- Prazo médio até o vencimento: "
                      + (f"{prazo:.1f} anos" if prazo is not None else "ausente")
                      + f" | cobertura {_frac(tesouro.get('cobertura_prazo'))}")
        composicao = tesouro.get("por_indexador") or {}
        if composicao:
            linhas.append("- Composição por indexador: " + ", ".join(
                f"{k} {v:.1%}" for k, v in composicao.items()))
        marcacao = texto_marcacao_tesouro(tesouro.get("marcacao"))
        if marcacao:
            linhas.append(f"- MARCAÇÃO: {marcacao}")
    if macro and not macro.get("erro"):
        atual = macro.get("atual") or {}
        linhas.append(f"CONJUNTURA (public.macro, ano {atual.get('ano', '—')}):")
        # A coluna ``juros_real_ex_ante`` é EX POST (inflação já ocorrida), e no
        # ano corrente o IPCA é o acumulado no ano, não 12 meses (auditoria
        # app4, LLM-A2): o rótulo diz isso para a LLM não comparar com o
        # IPCA+ do Tesouro como se fosse expectativa.
        for campo, rotulo in (("selic", "Selic"),
                              ("ipca", "IPCA (no ano corrente: acumulado no ano até o "
                                       "último mês divulgado)"),
                              ("juros_real_ex_ante", "Juro real ex post (realizado)")):
            valor = _n(atual.get(campo))
            linhas.append(f"- {rotulo}: " + (f"{valor:.2f}" if valor is not None
                                             else "ausente"))
        linhas.append("- Unidades conforme gravadas em public.macro; confira a "
                      "escala antes de comparar com taxa contratada.")
    elif macro and macro.get("erro"):
        linhas.append(f"CONJUNTURA: {macro['erro']}")
    return linhas


def _data_br(dia) -> str:
    return dia.strftime("%d/%m/%Y") if hasattr(dia, "strftime") else "data ausente"


def _qtd_hoje_por_chave(posicoes) -> dict:
    """Quantidade da carteira de hoje por chave do Tesouro; None se a foto não a traz.

    A chave é a da curva e do extrato: a foto chama o título de cupom pelo
    ticker do principal (TIPCA2032), o extrato o chama de TIPCAJ2032.
    """
    from core.tesouro_curva import chave_da_curva

    qtd: dict = {}
    for pos in posicoes or ():
        if (_n(pos.get("valor_mercado")) or 0.0) <= 0:
            continue
        chave = chave_da_curva(pos.get("ticker"), pos.get("nome"))
        valor = _n(pos.get("quantidade"))
        # Uma linha sem quantidade (a corretora arredonda) torna a soma da
        # chave desconhecida, não menor.
        if valor is None or valor <= 0 or (chave in qtd and qtd[chave] is None):
            qtd[chave] = None
        else:
            qtd[chave] = (qtd.get(chave) or 0.0) + valor
    return qtd


def _cobertura_dos_lotes(chave: str, qtd_lotes: float, qtd_hoje: dict) -> str:
    if chave not in qtd_hoje:
        return ("cobertura: o título não está na carteira atual (vendido, resgatado ou "
                "vencido depois do extrato); os lotes descrevem uma posição passada.")
    hoje = qtd_hoje[chave]
    if hoje is None or qtd_lotes <= 0:
        return "cobertura não medida: a posição atual não traz a quantidade."
    if abs(qtd_lotes - hoje) <= max(0.01, 0.01 * hoje):
        return "cobertura: os lotes cobrem a posição atual inteira."
    razao = qtd_lotes / hoje
    if razao < 1:
        return (f"cobertura: os lotes somam {razao:.0%} da quantidade da posição atual; o "
                "restante foi comprado depois do extrato ou não está nele, e para essa "
                "parte a taxa contratada e a data de compra são desconhecidas.")
    return (f"cobertura: os lotes somam {razao:.0%} da quantidade da posição atual; parte "
            "foi vendida ou resgatada depois, e não se sabe de quais lotes.")


def _bloco_lotes_tesouro(lotes_tesouro, posicoes, valores_reais: bool) -> list[str]:
    """Data e taxa contratada de cada compra, título a título.

    Sem este bloco a LLM via só o agregado e respondia que a taxa contratada e
    a data de compra não existiam, com o Extrato Analítico importado no banco.
    O peso de cada lote vai em percentual; quantidade e reais, só a pedido.
    """
    if lotes_tesouro is None:
        return []
    cabeca = "LOTES DO TESOURO DIRETO (data de compra e TAXA CONTRATADA de cada aplicação)"
    if lotes_tesouro.get("erro"):
        return [f"{cabeca}: a leitura falhou nesta sessão ({lotes_tesouro['erro']}). "
                "O dado pode existir no banco: diga que não foi carregado aqui, NÃO que "
                "não existe."]
    from core.tesouro_posicao import _texto_taxa

    titulos = list(lotes_tesouro.get("titulos") or ())
    diagnostico = lotes_tesouro.get("diagnostico") or {}
    divergentes = [str(d[0]) for d in diagnostico.get("divergentes") or ()]
    fora = ([f"- Fora dos lotes por não fecharem com a custódia da B3: "
             f"{', '.join(divergentes)}. A Movimentação não explica a quantidade de "
             "hoje; para eles a taxa contratada e a data de compra são desconhecidas."]
            if divergentes else [])
    if not titulos:
        return [f"{cabeca}: nenhum lote lido. Não há Extrato Analítico do Tesouro "
                "importado nem compras do Tesouro na Movimentação que fechem com a "
                "custódia. A taxa contratada e a data de compra destes títulos são "
                "desconhecidas; o caminho é importar o Extrato Analítico."] + fora

    qtd_hoje = _qtd_hoje_por_chave(posicoes)
    linhas = [f"{cabeca}:"]
    for titulo in titulos:
        lotes = sorted(titulo.lotes, key=lambda lote: lote.data_aplicacao)
        qtd_lotes = sum(_n(lote.quantidade) or 0.0 for lote in lotes)
        if titulo.origem_lotes == "movimentacao":
            fonte = ("compras da Movimentação da B3; taxa DEDUZIDA do PU pago contra a "
                     "curva do Tesouro do dia da compra (aproximação, não a taxa impressa)")
        else:
            fonte = (f"Extrato Analítico de {_data_br(titulo.report_date)} (taxa impressa "
                     "pelo Tesouro)")
        linhas.append(f"- {titulo.security_key} ({titulo.titulo}) | vence "
                      f"{_data_br(titulo.vencimento)} | {len(lotes)} lote(s) | fonte: {fonte}")
        venda = _n(titulo.taxa_mercado_venda)
        if venda is None:
            linhas.append("  taxa de hoje: ausente na curva do Tesouro; sem marcação.")
        else:
            compra = _n(titulo.taxa_mercado_compra)
            linhas.append(
                f"  taxa de hoje (curva de {_data_br(titulo.data_curva)}): venda (recompra) "
                f"{_texto_taxa(titulo.indexador, venda)}"
                + (f" | compra {_texto_taxa(titulo.indexador, compra)}"
                   if compra is not None else ""))
        mtm = _n(titulo.mtm_pct)
        if titulo.marcado_a_mercado and mtm is not None:
            linhas.append(
                f"  marcação a mercado: {mtm * 100:+.2f}%".replace(".", ",")
                + " sobre o valor do título na taxa contratada (positivo = taxa de hoje "
                "abaixo da contratada)" + ("; aproximada" if titulo.aproximado else ""))
        for lote in lotes:
            taxa = lote.taxa_contratada
            partes = [f"  - aplicado em {_data_br(lote.data_aplicacao)} a "
                      + (taxa.texto if taxa is not None else "taxa não lida")]
            qtd = _n(lote.quantidade) or 0.0
            if qtd_lotes > 0:
                partes.append(f"{qtd / qtd_lotes:.0%} da quantidade do título")
            if valores_reais:
                partes.append(f"quantidade {qtd:.2f}".replace(".", ","))
                investido = _n(lote.valor_investido)
                if investido is not None:
                    partes.append(f"investido {_brl(investido)}")
            linhas.append(" | ".join(partes))
        linhas.append("  " + _cobertura_dos_lotes(titulo.security_key, qtd_lotes, qtd_hoje))

    com_lote = {titulo.security_key for titulo in titulos}
    sem_lote = sorted(chave for chave in qtd_hoje if chave not in com_lote)
    if sem_lote:
        linhas.append(f"- Posições do Tesouro sem nenhum lote lido: {', '.join(sem_lote)}. "
                      "Para elas a taxa contratada e a data de compra são desconhecidas.")
    linhas += fora
    linhas.append("- Use estes lotes para responder sobre taxa contratada, data de compra "
                  "e carregar até o vencimento vs. vender. Com eles aqui, NÃO diga que "
                  "esses dados não existem.")
    return linhas


def build_carteira_classe_context(
    classe: str,
    posicoes,
    *,
    valuations=None,
    db=None,
    tesouro=None,
    macro=None,
    fundamentos=None,
    documentos=None,
    valores_reais: bool = False,
    lotes_tesouro=None,
) -> str:
    """Monta o contexto textual de uma sub-aba da Análise do Portfólio.

    ``lotes_tesouro`` é a saída de ``core.tesouro_posicao.lotes_para_contexto``
    e só vale para a classe Tesouro.
    """
    rotulo = CLASS_LABELS.get(classe, classe)
    pesos = _pesos(posicoes)
    blocos: list[str] = [
        f"CLASSE ANALISADA: {rotulo}",
        f"Ativos com posição: {len(pesos)}",
        "",
        # O parêntese acompanha o toggle: dizer "valores não são enviados"
        # logo acima de um bloco que os traz é contradizer o próprio contexto.
        "COMPOSIÇÃO DENTRO DA CLASSE (participação percentual"
        + ("):" if valores_reais else "; valores em reais não são enviados):"),
    ]
    blocos += [f"- {ticker}: {peso:.1%}" for ticker, peso in pesos] or ["- nenhuma posição"]
    if pesos:
        blocos.append(f"- Maior posição: {pesos[0][0]} com {pesos[0][1]:.1%} da classe")
        blocos.append("- Soma das cinco maiores: "
                      f"{sum(p for _, p in pesos[:5]):.1%}")
    if valores_reais:
        blocos.append("")
        blocos += _bloco_valores(posicoes)
    blocos.append("")
    if classe == "tesouro":
        blocos += _bloco_tesouro(tesouro, macro)
        lotes = _bloco_lotes_tesouro(lotes_tesouro, posicoes, valores_reais)
        if lotes:
            blocos.append("")
            blocos += lotes
    else:
        blocos += _bloco_valuations(valuations)
    blocos.append("")
    blocos += _bloco_db(db, rotulo)

    pares = (db or {}).get("pares") if isinstance(db, dict) else None
    if pares:
        blocos.append("")
        blocos += _bloco_pares(pares, rotulo)

    documental = _bloco_documentos(documentos)
    if documental:
        blocos.append("")
        blocos += documental

    if fundamentos:
        blocos.append("")
        blocos.append("INDICADORES POR ATIVO (apenas os presentes na fonte):")
        for ticker, dados in sorted(fundamentos.items()):
            campos = []
            for chave, valor in (dados or {}).items():
                numero = _n(valor)
                if numero is None:
                    continue
                spec = metric_spec(chave)
                campos.append(f"{spec.label}={numero:.4g}{spec.unit}")
            if campos:
                blocos.append(f"- {ticker}: " + ", ".join(campos[:14]))

    blocos += [
        "",
        "REGRAS DE LEITURA DESTE CONTEXTO:",
        "- As médias são aritméticas ponderadas pelo valor de mercado dos ativos "
        "COM dado válido; não são múltiplos contábeis consolidados da classe.",
        "- Cobertura abaixo de 100% significa que parte da classe ficou de fora "
        "da média. Não trate o resultado como se descrevesse a classe inteira.",
        "- Ausência de dado nunca equivale a zero, a valor neutro ou a risco baixo.",
        "- Não há neste contexto preço-alvo, projeção de lucro nem recomendação.",
    ]
    return "\n".join(blocos)


def _brl(valor) -> str:
    return (f"R$ {valor:,.2f}".replace(",", "@").replace(".", ",")
            .replace("@", "."))


def build_carteira_geral_context(carteira: dict, proventos: dict | None = None,
                                 *, valores_reais: bool = False,
                                 risco: dict | None = None,
                                 atribuicao: dict | None = None) -> str:
    """Contexto da sub-aba Visão Geral: a carteira inteira, entre classes.

    Os pesos são os mesmos ``pct_carteira`` que a tela mostra — sobre o valor
    de mercado consolidado —, para a LLM e o dono da carteira lerem o mesmo
    número. A renda de 12 meses sai como percentual do custo; em reais só com
    o toggle ligado, junto com os totais.

    ``risco`` é o resultado de ``core.carteira_risco.get_risco_carteira()``:
    TWR, volatilidade, queda máxima, VaR, Sharpe e beta da série diária, com a
    cobertura e o período. Sem ele, a LLM respondia "qual o risco da minha
    carteira" pela concentração, sem um único número de oscilação.

    ``atribuicao`` é ``core.carteira_atribuicao.get_atribuicao_carteira()``:
    alocação, seleção e interação por classe contra a meta. Vai com uma frase
    de resumo pronta e uma regra de POSIÇÃO ("comece com o Resumo") — dado só no
    contexto saiu 0 de 6 citações; imperativo de posição, 3 de 3 (PR #416).
    """
    carteira = carteira or {}
    proventos = proventos or {}
    posicoes = list(carteira.get("posicoes") or ())
    por_classe = list(carteira.get("por_classe") or ())
    por_setor = list(carteira.get("por_setor") or ())

    n_curva = int(carteira.get("n_cotacoes_curva") or 0)
    n_curva_antiga = int(carteira.get("n_cotacoes_curva_antiga") or 0)
    # Curva parada (job sem rodar) não é cotação do dia: vai nomeada à parte.
    marcacao_curva = "; ".join(
        ([f"{n_curva} título(s) do Tesouro pela curva oficial"] if n_curva else [])
        + ([f"{n_curva_antiga} título(s) do Tesouro pela última curva oficial, "
            "desatualizada"] if n_curva_antiga else [])
    )

    blocos: list[str] = [
        "ESCOPO: carteira inteira (todas as classes), como carregada na Visão Geral.",
        f"Ativos com posição: {len(posicoes)}",
        "Cotações do dia: " + ("disponíveis" if carteira.get("cotacoes_disponiveis")
                               else "indisponíveis — valor de mercado pode ser o custo")
        + (f" ({marcacao_curva})" if marcacao_curva else ""),
        "",
        "ALOCAÇÃO POR CLASSE (percentual do valor de mercado):",
    ]
    for cls in por_classe:
        rentab = _n(cls.get("rentab_pct"))
        blocos.append(
            f"- {cls.get('nome')}: {_pct(cls.get('pct_carteira'))} | "
            f"{int(cls.get('num_ativos') or 0)} ativo(s) | mercado/custo "
            + ("ausente" if rentab is None else f"{rentab:+.1f}%"))
    if not por_classe:
        blocos.append("- sem dado de alocação")

    ordenadas = sorted(posicoes, key=lambda p: (-(_n(p.get("pct_carteira")) or 0.0),
                                                str(p.get("ticker") or "")))
    blocos += ["", "POSIÇÕES (peso na carteira | classe | setor | mercado/custo):"]
    for pos in ordenadas:
        rentab = _n(pos.get("rentab_pct"))
        if pos.get("retorno_brl_disponivel") is False:
            rentab = None
        blocos.append(
            f"- {str(pos.get('ticker') or '').upper()}: {_pct(pos.get('pct_carteira'))} | "
            f"{pos.get('classe') or 'classe ausente'} | {pos.get('setor') or 'setor ausente'} | "
            + ("ausente" if rentab is None else f"{rentab:+.1f}%"))
    pesos = [(_n(p.get("pct_carteira")) or 0.0) / 100 for p in ordenadas]
    if pesos:
        hhi = sum(w * w for w in pesos)
        blocos += [
            f"- Soma das cinco maiores: {sum(pesos[:5]):.1%}",
            "- Posições acima de 10%: " + (", ".join(
                str(p.get("ticker")).upper() for p, w in zip(ordenadas, pesos) if w > .10)
                or "nenhuma"),
            f"- Número efetivo de ativos (1/HHI): {1 / hhi:.1f}" if hhi else "",
        ]

    if por_setor:
        blocos += ["", "EXPOSIÇÃO POR SETOR (percentual do valor de mercado):"]
        blocos += [f"- {s.get('nome')}: {_pct(s.get('pct_carteira'))}" for s in por_setor]

    blocos += ["", "RETORNO E RENDA:"]
    if carteira.get("rentabilidade_total_disponivel", True):
        blocos.append("- Retorno mercado/custo da carteira: "
                      f"{_pct(carteira.get('rentabilidade_total_pct'))} "
                      "(não inclui proventos nem ajusta aportes/resgates)")
    else:
        blocos.append("- Retorno mercado/custo da carteira: indisponível em BRL "
                      "(falta câmbio histórico de aquisição de posição em USD)")
    custo = _n(carteira.get("total_investido"))
    renda = _n(proventos.get("total_12m", proventos.get("total_ano")))
    if renda is not None and custo:
        blocos.append(f"- Renda recebida em 12 meses sobre o custo: {renda / custo:.2%}")
    else:
        blocos.append("- Renda recebida em 12 meses: ausente")
    por_ativo = [a for a in proventos.get("por_ativo_12m") or () if _n(a.get("total"))]
    if por_ativo and renda:
        blocos.append("- Participação na renda de 12 meses: " + ", ".join(
            f"{str(a.get('ticker')).upper()} {_n(a.get('total')) / renda:.1%}"
            for a in sorted(por_ativo, key=lambda a: -_n(a.get("total")))[:10]))

    if valores_reais:
        blocos += ["", "VALORES EM REAIS (enviados a pedido do dono da carteira):",
                   f"- Custo total: {_brl(custo or 0.0)}",
                   f"- Valor de mercado: {_brl(_n(carteira.get('total_mercado')) or 0.0)}",
                   f"- Renda recebida em 12 meses: {_brl(renda or 0.0)}"]
        blocos += [f"- {str(p.get('ticker') or '').upper()}: "
                   f"{_brl(_n(p.get('valor_mercado')) or 0.0)}" for p in ordenadas]
    else:
        blocos += ["", "Valores em reais e quantidades não são enviados."]

    if risco is not None:
        from core.carteira_risco import bloco_risco_para_prompt

        blocos += ["", bloco_risco_para_prompt(risco)]
    if atribuicao is not None:
        from core.carteira_atribuicao import bloco_atribuicao_para_prompt

        blocos += ["", bloco_atribuicao_para_prompt(atribuicao)]

    blocos += [
        "",
        "REGRAS DE LEITURA DESTE CONTEXTO:",
        "- Retorno mercado/custo não é rentabilidade: ignora proventos, aportes "
        "e resgates ao longo do tempo.",
        "- Este contexto não traz múltiplos, notas nem documentos por ativo; "
        "esse detalhe está nas sub-abas de cada classe.",
        "- Ausência de dado nunca equivale a zero, a valor neutro ou a risco baixo.",
    ]
    if risco is not None:
        from core.carteira_risco import REGRA_RISCO

        blocos.append(REGRA_RISCO)
    if atribuicao is not None:
        from core.carteira_atribuicao import REGRA_ATRIBUICAO

        blocos.append(REGRA_ATRIBUICAO)
    return "\n".join(linha for linha in blocos if linha is not None)
