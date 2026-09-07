"""Contexto determinístico de uma classe da carteira para o chat.

Duas decisões deliberadas:

* **Peso, não saldo.** O contexto enviado ao provedor de LLM traz a
  participação percentual de cada ativo dentro da classe, nunca o valor em
  reais nem a quantidade. O percentual é o que responde às perguntas de
  concentração e exposição; o saldo é dado pessoal e sair daqui é publicá-lo
  num serviço externo sem que isso melhore a resposta.
* **Ausência declarada.** Cobertura, ativos sem dado e universo indisponível
  entram no texto. Média calculada sobre metade da classe e média calculada
  sobre a classe inteira não podem chegar à LLM com a mesma cara.
"""
from __future__ import annotations

from core.portfolio_valuations import CLASS_LABELS, metric_spec


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
        linhas.append(
            "- LIMITAÇÃO: o valor de mercado destas posições vem do saldo "
            "informado pela corretora, não de marcação a mercado independente "
            "título a título neste app.")
    if macro and not macro.get("erro"):
        atual = macro.get("atual") or {}
        linhas.append(f"CONJUNTURA (public.macro, ano {atual.get('ano', '—')}):")
        for campo, rotulo in (("selic", "Selic"), ("ipca", "IPCA"),
                              ("juros_real_ex_ante", "Juro real ex-ante")):
            valor = _n(atual.get(campo))
            linhas.append(f"- {rotulo}: " + (f"{valor:.2f}" if valor is not None
                                             else "ausente"))
        linhas.append("- Unidades conforme gravadas em public.macro; confira a "
                      "escala antes de comparar com taxa contratada.")
    elif macro and macro.get("erro"):
        linhas.append(f"CONJUNTURA: {macro['erro']}")
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
) -> str:
    """Monta o contexto textual de uma sub-aba da Análise do Portfólio."""
    rotulo = CLASS_LABELS.get(classe, classe)
    pesos = _pesos(posicoes)
    blocos: list[str] = [
        f"CLASSE ANALISADA: {rotulo}",
        f"Ativos com posição: {len(pesos)}",
        "",
        "COMPOSIÇÃO DENTRO DA CLASSE (participação percentual; valores em reais "
        "não são enviados):",
    ]
    blocos += [f"- {ticker}: {peso:.1%}" for ticker, peso in pesos] or ["- nenhuma posição"]
    if pesos:
        blocos.append(f"- Maior posição: {pesos[0][0]} com {pesos[0][1]:.1%} da classe")
        blocos.append("- Soma das cinco maiores: "
                      f"{sum(p for _, p in pesos[:5]):.1%}")
    blocos.append("")
    if classe == "tesouro":
        blocos += _bloco_tesouro(tesouro, macro)
    else:
        blocos += _bloco_valuations(valuations)
    blocos.append("")
    blocos += _bloco_db(db, rotulo)

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
