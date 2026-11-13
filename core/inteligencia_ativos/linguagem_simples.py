"""
core/inteligencia_ativos/linguagem_simples.py
A recomendação contada como conversa, para o investidor iniciante
(pedido do usuário de 12/11/2026).

A caixa de cada ativo falava a língua de quem montou a regra: "alerta
eliminatório", "percentil 0 do próprio histórico", "inverso da
volatilidade", "Dívida líquida/EBITDA de 7,07x (brapi financialData...)".
Estava certo e era ilegível para quem está começando. Este módulo reconta a
mesma decisão em frases curtas: por quê, o que eu vi (empresa, preço,
bolsa), na prática, a favor e contra, o que mudaria e o que vale conferir.

Regras do texto (o usuário pediu também objetividade, 12/11/2026):

* **Nada novo.** Só reconta o que ``resumida.decisao`` e ``avaliacao``
  decidiram; não há regra aqui. Número que aparece é o mesmo da análise.
* **Sem repetir.** O que os cartões de cima já mostram (peso hoje, tamanho
  ideal, ação, certeza) não volta no texto, e um número dito numa seção não
  reaparece na seguinte (``_ja_dito``). Sem aviso genérico de metodologia.
* **Sigla só com tradução**, entre parênteses, depois do que ela mede.
* **O texto técnico não some.** Ele continua indo para as LLMs
  (``veredito``) e aparece na tela num bloco recolhido, "Ver a análise
  técnica completa". Este módulo é só para os olhos.

Puro: sem banco, sem LLM, sem Streamlit.
Coberto por tests/test_inteligencia_linguagem_simples.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import momento as mo
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import recomendacao as rec
from core.inteligencia_ativos import resumida as rs

# -- vocabulário ------------------------------------------------------------------

CERTEZA = {rec.ALTA: "Alta", rec.MODERADA: "Média", rec.BAIXA: "Baixa"}

_LEITURA = {
    av_.FORTE: "boa", av_.ADEQUADA: "razoável", av_.FRAGIL: "preocupante",
    av_.INSUFICIENTE: "sem dados suficientes",
    av_.BARATO: "barato", av_.JUSTO: "nem caro nem barato",
    av_.CARO: "caro", av_.SEM_LEITURA: "sem dados suficientes",
    av_.POSITIVO: "favorável", av_.NEUTRO: "neutro",
    av_.NEGATIVO: "desfavorável", av_.MISTO: "dividido",
}
_DIMENSAO = {av_.QUALIDADE: "Saúde da empresa",
             av_.VALUATION: "Preço",
             av_.MERCADO: "Na bolsa e no noticiário"}

# Os sinais que a convicção conta (``resumida._sinais`` e as razões de cada
# ramo), em palavras de quem não é do mercado.
_SINAL = {
    "alerta eliminatório": "um problema grave no balanço",
    "alerta eliminatório no noticiário": "uma notícia grave, ainda não "
                                         "confirmada",
    "qualidade frágil": "a saúde financeira fraca",
    "qualidade forte": "a empresa saudável",
    "qualidade adequada": "a empresa saudável",
    "preço acima do normal": "o preço acima do normal",
    "preço abaixo do normal": "o preço abaixo do normal",
    "preço em linha com o normal": "o preço dentro do normal",
    "mercado e notícias negativos": "o mercado e o noticiário desfavoráveis",
    "mercado e notícias positivos": "o mercado e o noticiário favoráveis",
    "gatilho da tese disparado": "um dos motivos para ter o ativo deixou de "
                                 "valer",
    "a função na carteira não se cumpre": "o ativo não está cumprindo o "
                                          "papel dele na carteira",
    "peso acima do devido": "o peso acima do ideal na carteira",
    "peso muito acima do limite": "o peso bem acima do seu limite",
    "abaixo da % devida": "o peso abaixo do ideal",
    "peso na % devida": "o peso no ideal",
    "classe abaixo do alvo": "a classe abaixo da meta da sua estratégia",
    "classe acima do alvo": "a classe acima da meta da sua estratégia",
    "tese válida": "o motivo para ter o ativo continua de pé",
    "queda sem piora nos fundamentos": "a queda sem piora na empresa",
}
_ESTICADO = re.compile(r"^preço (muito )?esticado contra o próprio histórico$")
_SUPERIOR = re.compile(r"^(\S+) claramente superior$")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def _pontos(x: float) -> str:
    v = f"{abs(x):.1f}".replace(".", ",")
    return f"{v} ponto" if v == "1,0" else f"{v} pontos"


def _reais(x: float) -> str:
    return "R$ " + f"{x:,.0f}".replace(",", ".")


def _lista(xs) -> str:
    xs = [x for x in xs if x]
    if not xs:
        return ""
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " e " + xs[-1]


def _frase(t: str) -> str:
    """Primeira letra maiúscula e ponto final."""
    t = t.strip()
    if not t:
        return ""
    t = t[:1].upper() + t[1:]
    return t if t.endswith((".", "!", "?", '"')) else t + "."


def _mercado_tem_noticia(av: av_.Avaliacao) -> bool:
    return any(("notícia" in c.texto or "noticiário" in c.texto)
               for c in av.dimensao(av_.MERCADO).criterios if c.sinal)


def sinal_simples(sinal: str, av: av_.Avaliacao | None = None) -> str:
    """Um sinal da convicção dito sem jargão. Desconhecido volta como está."""
    if sinal in ("mercado e notícias positivos", "mercado e notícias negativos") \
            and av is not None and not _mercado_tem_noticia(av):
        # Sem notícia medida, o "mercado" é só o comportamento do preço:
        # dizer "noticiário favorável" seria inventar notícia.
        bom = sinal.endswith("positivos")
        return ("o preço se comportando melhor que o das concorrentes" if bom
                else "o preço se comportando pior que o das concorrentes")
    if _ESTICADO.match(sinal):
        return "o preço alto em relação ao próprio passado"
    sup = _SUPERIOR.match(sinal)
    if sup:
        return f"{sup.group(1)} parecer melhor"
    return _SINAL.get(sinal, sinal)


# -- a conversa ---------------------------------------------------------------------

@dataclass(frozen=True)
class Leitura:
    rotulo: str          # "Saúde da empresa"
    leitura: str         # "preocupante"
    frases: tuple[str, ...]
    tom: int             # +1 bom, -1 ruim, 0 neutro/sem dado


@dataclass(frozen=True)
class Conversa:
    porque: tuple[str, ...]          # o motivo principal, em 1 a 2 frases
    leituras: tuple[Leitura, ...]    # empresa, preço, bolsa
    pratica: tuple[str, ...]         # quanto vender e o imposto, ou o aporte
    pesos: tuple[str, ...]           # a favor e contra
    mudaria: tuple[str, ...]         # o que mudaria a sugestão
    investigar: tuple[str, ...]      # alertas, sem ordem
    # Para os cartões de cima (curtos)
    acao_curta: str
    devida_curta: str
    certeza_curta: str


_NUMERO = re.compile(r"(\d+(?:,\d+)?)\s?(%|vezes|x)")


def _numeros(texto: str) -> set[tuple[float, str]]:
    """Os números com unidade de um texto, normalizados: "30,00x" e "30,0x"
    são o mesmo número; "vezes" e "x" a mesma unidade."""
    return {(round(float(n.replace(",", ".")), 1), "%" if u == "%" else "x")
            for n, u in _NUMERO.findall(texto)}


def _ja_dito(frase: str, dito: str) -> bool:
    """A frase repete um número que o texto já disse."""
    return bool(_numeros(frase) & _numeros(dito))


def conversar(a: m.AnaliseAtivo, d: rs.Decisao,
              sugerido: rs.AlvoSugerido | None = None) -> Conversa:
    """A decisão ``d`` do ativo ``a`` contada em linguagem simples. Puro."""
    av = d.avaliacao if d.avaliacao is not None else av_.avaliar(a)
    causa, porque = _porque(a, d, av)
    dito = " ".join(porque)
    leituras = _leituras(av, dito)
    dito += " ".join(f for lt in leituras for f in lt.frases)
    investigar = tuple(x for x in _investigar(d, av) if not _ja_dito(x, dito))
    return Conversa(
        porque=porque, leituras=leituras, pratica=_pratica(a, d),
        pesos=_pesos(d, av), mudaria=_mudaria(d, av), investigar=investigar,
        acao_curta=causa, devida_curta=_devida_curta(a, d),
        certeza_curta=_certeza_curta(d))


# -- por quê -----------------------------------------------------------------------

def _simples_alerta(al: av_.Alerta) -> str:
    return al.simples or al.texto


def _porque(a: m.AnaliseAtivo, d: rs.Decisao,
            av: av_.Avaliacao) -> tuple[str, tuple[str, ...]]:
    """(motivo em poucas palavras, as frases do porquê). Segue a ordem da
    árvore de ``resumida.decisao``."""
    out: list[str] = []
    criticos = av.criticos
    if criticos and all(x.noticia for x in criticos):
        curta = "notícia grave a confirmar"
        out.append(_frase(_simples_alerta(criticos[0])))
        out.append("Notícia ainda não é fato: espere o comunicado oficial da "
                   "empresa antes de vender.")
    elif criticos:
        curta = _curta_alerta(criticos[0])
        out.append(_frase(_simples_alerta(criticos[0])))
        out.append("Um problema desse tamanho pesa mais que os pontos "
                   "positivos.")
    elif d.tese_quebrada:
        curta = "um motivo para ter o ativo deixou de valer"
        out.append("Um dos motivos para ter este ativo deixou de valer: "
                   f"{d.tese_quebrada[0].lower().rstrip('.')}.")
    elif d.fora_da_funcao:
        curta = "não cumpre o papel na carteira"
        out.append("O ativo não está cumprindo o papel que você deu a ele: "
                   f"{d.fora_da_funcao.rstrip('.')}.")
    elif av.qualidade == av_.FRAGIL:
        curta = "saúde financeira fraca"
        neg = [c.simples for c in av.dimensao(av_.QUALIDADE).criterios
               if c.sinal < 0 and c.simples][:2]
        out.append("A saúde financeira está fraca"
                   + (f": {_lista(neg)}." if neg else "."))
    else:
        curta, frases = _porque_solida(d, av)
        out += frases
    if d.codigo == rs.VENDER and d.substituto is None and criticos \
            and not all(x.noticia for x in criticos):
        out.append("Não há concorrente claramente melhor para trocar.")
    sub = d.substituto
    if sub is not None and d.codigo == rs.SUBSTITUIR:
        out += _porque_troca(sub)
    if d.antes_da_queda and d.momento is not None:
        fatos = _lista(_fatos_queda(d.momento))
        if d.codigo in (rs.MANTER, rs.REBALANCEAR):
            curta = "não vender na queda"
            out.append(f"{_frase(fatos)[:-1]}, mas a empresa não piorou: "
                       "vender agora transformaria a queda em prejuízo.")
        elif d.codigo == rs.REDUZIR_PARCIAL:
            out.append(f"{_frase(fatos)[:-1]}: melhor sair aos poucos do que "
                       "vender tudo no preço de hoje.")
    atual, alvo = d.peso_atual, d.peso_alvo
    if atual is not None and alvo is not None \
            and alvo - atual > rs.tolerancia_pp(alvo) \
            and d.codigo in (rs.VENDER, rs.SUBSTITUIR, rs.REDUZIR_PARCIAL):
        out.append("Pelo tamanho caberia comprar mais, mas esse motivo fala "
                   "mais alto.")
    return curta, tuple(out)


def _curta_alerta(al: av_.Alerta) -> str:
    t = (al.simples or al.texto).lower()
    if "dívida" in t or "juros" in t:
        return "dívida alta demais"
    if "patrimônio" in t:
        return "empresa deve mais do que tem"
    if "vago" in t or "aluguéis" in t:
        return "problema grave nos imóveis"
    return "problema grave na empresa"


def _fatos_queda(mom: mo.Momento) -> list[str]:
    out = []
    if mom.resultado_pct is not None and mom.resultado_pct < 0:
        out.append(f"a posição está {_pct(-mom.resultado_pct)} abaixo do que "
                   "você investiu")
    if mom.retorno_12m is not None and mom.retorno_12m < 0:
        txt = f"o preço caiu {_pct(-mom.retorno_12m)} em 12 meses"
        if mom.segmento_caiu_junto:
            txt += ", junto com as concorrentes"
        out.append(txt)
    return out or ["o preço caiu"]


def _porque_solida(d: rs.Decisao,
                   av: av_.Avaliacao) -> tuple[str, list[str]]:
    saude = ("A empresa está saudável" if av.qualidade in (av_.FORTE,
                                                           av_.ADEQUADA)
             else "Nada grave apareceu")
    est = d.esticamento
    caro = est is not None and est.nivel > 0
    atual, alvo = d.peso_atual, d.peso_alvo
    acima = atual is not None and alvo is not None and atual > alvo
    abaixo = atual is not None and alvo is not None and atual < alvo
    if d.codigo == rs.REDUZIR_PARCIAL:
        if caro and acima:
            return "pesa demais e o preço subiu muito", [
                f"{saude}, mas já pesa mais do que deveria e o preço subiu "
                "bem acima do normal. Vender uma parte garante parte do lucro."]
        if caro:
            return "preço muito acima do normal", [
                f"{saude}, mas o preço está muito acima do normal. Vender uma "
                "parte garante parte do lucro."]
        return "pesa muito acima do seu limite", [
            f"{saude}, mas pesa bem mais que o seu limite: só os aportes não "
            "bastam para corrigir."]
    if d.codigo == rs.REBALANCEAR:
        return "já pesa mais do que deveria", [
            f"{saude}, só está grande demais na carteira."]
    if d.codigo == rs.COMPRAR:
        frases = [f"{saude} e ainda pesa menos que o ideal." if abaixo
                  else f"{saude} e a classe dela está abaixo da sua meta."]
        if av.preco == av_.BARATO and av.qualidade == av_.FORTE:
            frases.append("E o preço está abaixo do normal.")
        elif caro or av.preco == av_.CARO:
            frases.append("Como o preço está acima do normal, compre aos "
                          "poucos.")
        if av.mercado == av_.NEGATIVO:
            frases.append("O noticiário está desfavorável: leia antes de "
                          "comprar.")
        return "abaixo do peso ideal", frases
    if d.codigo == rs.ACOMPANHAR:
        if av.mercado == av_.NEGATIVO:
            return "notícias e mercado desfavoráveis", [
                f"{saude}, mas o noticiário e o preço estão desfavoráveis: "
                "leia as notícias antes de mexer."]
        return "há pontos para conferir", [
            f"{saude}, mas há pontos para conferir antes de mexer."]
    if caro:
        return "preço acima do normal", [
            f"{saude}, mas o preço está acima do normal: não vale comprar "
            "mais agora, e também não é caso de vender."]
    if abaixo:
        return "classe já acima da meta", [
            f"{saude}, mas a classe dela já passou da meta da sua "
            "estratégia: por ora, nem comprar nem vender."]
    return "está no tamanho certo", [f"{saude} e no tamanho certo."]


# -- o que eu vi --------------------------------------------------------------------

def _leituras(av: av_.Avaliacao, dito: str = "") -> tuple[Leitura, ...]:
    out = []
    for dim in av.dimensoes:
        lido = dim.leitura
        tom = (1 if lido in (av_.FORTE, av_.BARATO, av_.POSITIVO) else
               -1 if lido in (av_.FRAGIL, av_.CARO, av_.NEGATIVO) else 0)
        pos = [c.simples for c in dim.criterios if c.sinal > 0 and c.simples]
        neg = [c.simples for c in dim.criterios if c.sinal < 0 and c.simples]
        # O lado da leitura primeiro; o que o porquê já disse não volta.
        ordem = [x for x in (neg + pos if tom < 0 else pos + neg)
                 if not _ja_dito(x, dito)]
        rotulo = _LEITURA.get(lido, lido)
        if lido == av_.JUSTO and pos and neg:
            rotulo = "sinais mistos"
        if dim.chave == av_.MERCADO and lido in (av_.POSITIVO, av_.NEGATIVO) \
                and not _mercado_tem_noticia(av):
            rotulo += " (sem notícias medidas)"
        out.append(Leitura(_DIMENSAO[dim.chave], rotulo,
                           tuple(_frase(x) for x in ordem[:2]), tom))
    return tuple(out)


def _devida_curta(a: m.AnaliseAtivo, d: rs.Decisao) -> str:
    atual = d.peso_atual if d.peso_atual is not None else a.ativo.peso_atual
    alvo = d.peso_alvo
    if alvo is None:
        if a.faixa.teto_ativo is not None:
            return f"sem ideal definido; seu limite é {_pct(a.faixa.teto_ativo)}"
        return "a sua estratégia não define"
    dif = atual - alvo
    if abs(dif) <= rs.tolerancia_pp(alvo):
        return "no tamanho certo"
    return f"faltam {_pontos(dif)}" if dif < 0 else f"{_pontos(dif)} acima"


# -- na prática -------------------------------------------------------------------

def _pratica(a: m.AnaliseAtivo, d: rs.Decisao) -> tuple[str, ...]:
    c = d.custos
    if d.codigo in rs.MOVIMENTA and d.proporcao:
        tudo = d.proporcao >= 0.999
        quanto = ("toda a posição" if tudo else
                  "um terço da posição" if abs(d.proporcao - rs.FRACAO_MAX_REDUCAO)
                  < 1e-9 else f"cerca de {d.proporcao * 100:.0f}% da posição")
        valor = (f" (≈ {_reais(c.valor_bruto)})"
                 if c is not None and c.valor_bruto else "")
        troca = (f" e comprar {d.substituto.ticker}"
                 if d.codigo == rs.SUBSTITUIR and d.substituto is not None
                 else "")
        return (f"Vender {quanto}{valor}{troca}.",) + tuple(_imposto(a, c))
    if d.codigo == rs.COMPRAR:
        alvo = d.peso_alvo
        return ("Coloque os próximos aportes nele"
                + (f" até chegar perto de {_pct(alvo)}." if alvo is not None
                   else "."),)
    if d.codigo == rs.REBALANCEAR:
        return ("Não compre mais por enquanto; coloque os próximos aportes nos "
                "outros ativos da classe. Não precisa vender.",)
    return ()


def _imposto(a: m.AnaliseAtivo, c: rec.CustoVenda | None) -> list[str]:
    if c is None or c.ganho is None:
        return []
    if c.ganho <= 0:
        return [f"Prejuízo de {_reais(-c.ganho)}: sem imposto, e ele abate "
                "lucros futuros."]
    if c.ir is None:
        return []
    if a.ativo.classe_politica == rs.ACOES and c.valor_bruto is not None \
            and c.valor_bruto <= 20000:
        return [f"Lucro de {_reais(c.ganho)}: isento se suas vendas de ações no "
                f"mês ficarem até R$ 20 mil; acima disso, imposto de "
                f"≈ {_reais(c.ir)}."]
    out = [f"Lucro de {_reais(c.ganho)}: imposto de ≈ {_reais(c.ir)}."]
    if a.ativo.classe_politica == rs.ACOES:
        out.append("Dividir a venda em meses de até R$ 20 mil evita o imposto.")
    return out


# -- a favor e contra, o que mudaria, o que conferir --------------------------------

def _pesos(d: rs.Decisao, av: av_.Avaliacao) -> tuple[str, ...]:
    if rec.cobertura(av)[0] <= 1:
        return ("Há poucos dados sobre este ativo.",)
    favor = _lista([sinal_simples(x, av) for x in d.sinais_favor])
    contra = _lista([sinal_simples(x, av) for x in d.sinais_contra])
    out = []
    if favor:
        out.append(f"A favor: {favor}.")
    if contra:
        out.append(f"Contra: {contra}.")
    return tuple(out)


def _limite_simples(motivo: str) -> str:
    if "noticiário" in motivo:
        return "veio só de notícia"
    if "acompanhar é provisório" in motivo:
        return "depende do que você encontrar"
    if "queda é passageira" in motivo:
        return "ninguém sabe se o preço volta"
    if "métricas publicadas" in motivo:
        return "a alternativa foi comparada só por alguns números"
    return motivo


def _mudaria(d: rs.Decisao, av: av_.Avaliacao) -> tuple[str, ...]:
    criticos = av.criticos
    alvo = d.peso_alvo
    if criticos and all(x.noticia for x in criticos):
        return ("Se a empresa confirmar a notícia, vender; se for desmentida, "
                "o alerta cai.",)
    if criticos:
        return (("Se a dívida voltar a um nível aceitável."
                 if _curta_alerta(criticos[0]) == "dívida alta demais"
                 else "Se o problema for resolvido."),)
    out: list[str] = []
    if d.codigo == rs.REDUZIR_PARCIAL:
        out += ["Se o preço voltar ao normal ou o peso ao ideal, o resto fica.",
                "Se a empresa piorar, vender tudo."]
    elif d.codigo == rs.REBALANCEAR:
        out.append("Quando o peso voltar ao ideal, volta a ser manter.")
    elif d.codigo == rs.COMPRAR and alvo is not None:
        out.append(f"Ao chegar a {_pct(alvo)}, volta a ser manter.")
    if d.codigo in (rs.MANTER, rs.COMPRAR, rs.REBALANCEAR, rs.ACOMPANHAR):
        out.append("Se a saúde da empresa piorar, vender uma parte ou tudo.")
    if d.codigo == rs.SUBSTITUIR and d.substituto is not None:
        out.append(f"Se {d.substituto.ticker} deixar de sair melhor na "
                   "comparação, só vender.")
    return tuple(dict.fromkeys(out))


def _investigar(d: rs.Decisao, av: av_.Avaliacao) -> tuple[str, ...]:
    """Os alertas da decisão, em linguagem simples. Nenhum é ordem. O peso
    acima do ideal não entra: o cartão de cima já mostra."""
    out: list[str] = []
    est = d.esticamento
    if est is not None:
        out += list(est.evidencias_simples or est.evidencias)
        if (est.excesso_pp is not None
                and est.excesso_pp >= av_.MOMENTO_DIFERENCA_PP):
            out.append("O preço subiu bem mais que o das concorrentes: confira "
                       "se os resultados acompanharam.")
    for al in av.alertas:
        if al.critico and al.noticia:
            out.append(f"Confirmar em documento oficial: {_simples_alerta(al)}")
        elif not al.critico:
            out.append(_simples_alerta(al))
    if av.mercado == av_.NEGATIVO and _mercado_tem_noticia(av):
        out.append("As notícias recentes estão mais negativas.")
    return tuple(dict.fromkeys(_frase(x) for x in out))


def _certeza_curta(d: rs.Decisao) -> str:
    if d.conviccao_limitada:
        return _limite_simples(d.conviccao_limitada)
    if d.conviccao_sustentada:
        return "o alerta grave pesa mais que o resto"
    if d.sinais_contra and d.sinais_favor:
        return "há sinais nos dois sentidos"
    if d.sinais_contra:
        return "há sinais contra"
    if d.conviccao == rec.ALTA:
        return "os sinais apontam para o mesmo lado"
    return "dados incompletos"


# -- o que cada indicador da comparação mede -----------------------------------------

O_QUE_MEDE = {
    "p_l": "quantos anos de lucro o preço paga; menor é mais barato",
    "p_vp": "preço comparado ao patrimônio; menor é mais barato",
    "ev_ebit": "valor da empresa, com a dívida, sobre o lucro da operação; "
               "menor é mais barato",
    "dy_mediano_5a": "dividendos típicos dos últimos 5 anos, em % do preço",
    "dividend_yield": "dividendos dos últimos 12 meses, em % do preço",
    "cap_rate": "aluguel recebido por ano, em % do preço",
    "roe": "lucro por ano sobre o dinheiro dos sócios; maior é melhor",
    "margem_liquida": "quanto sobra de lucro a cada R$ 100 vendidos",
    "volatilidade": "o quanto o preço sobe e desce num ano; menor é mais "
                    "tranquilo",
    "retorno_12m": "quanto rendeu em 12 meses, contando os dividendos",
    "alavancagem": "quanto o fundo deve, em % do patrimônio",
    "max_drawdown": "a maior queda do preço no período",
    "liquidez_diaria": "quanto é negociado por dia; mais é mais fácil vender",
    "porte": "o tamanho: quanto vale a empresa inteira na bolsa (no fundo "
             "imobiliário, o patrimônio dele)",
    "taxa_mercado": "quanto o título paga hoje, se comprado agora",
    "prazo": "quanto tempo falta para o título vencer",
}

# -- a comparação com as parecidas, em palavras (pedido de 13/11/2026) -------------
#
# A comparação e a troca falavam "mediana do segmento", "EV/EBIT",
# "drawdown", "porte 3,2x o do ativo". Aqui ficam as palavras; a conta é a
# de ``pares`` e de ``recomendacao.diferencas``, sem regra nova.

ROTULO_SIMPLES = {
    "p_l": "Preço sobre o lucro (P/L)",
    "p_vp": "Preço sobre o patrimônio (P/VP)",
    "ev_ebit": "Valor da empresa sobre o lucro da operação (EV/EBIT)",
    "dy_mediano_5a": "Dividendos num ano típico",
    "dividend_yield": "Rendimentos dos últimos 12 meses",
    "cap_rate": "Aluguel sobre o preço",
    "roe": "Lucro sobre o dinheiro dos sócios (ROE)",
    "margem_liquida": "Margem de lucro",
    "volatilidade": "Sobe e desce do preço",
    "retorno_12m": "Quanto rendeu em 12 meses",
    "alavancagem": "Dívida do fundo",
    "max_drawdown": "Maior queda",
    "liquidez_diaria": "Negociado por dia",
    "porte": "Tamanho",
    "taxa_mercado": "Taxa de hoje",
    "prazo": "Prazo até vencer",
}

TIPICO = "típico do setor"
ROTULO_POSICAO_SIMPLES = {
    "acima": f"acima do {TIPICO}", "abaixo": f"abaixo do {TIPICO}",
    "em_linha": f"parecido com o {TIPICO}", "sem_dado": "sem dado deste ativo",
}
RODAPE_PARES = ("O \"típico do setor\" é o valor do meio entre as empresas "
                "parecidas. Um número melhor que o delas não quer dizer, "
                "sozinho, que o investimento seja melhor: isto é para "
                "comparar, não é recomendação.")


def rotulo_metrica(chave: str, tecnico: str = "") -> str:
    return ROTULO_SIMPLES.get(chave, tecnico or chave)


# O que o par tem a mais (melhor) ou a menos (pior), dito como coisa que se
# "tem": "BBSE3 tem mais lucro sobre o dinheiro dos sócios".
_TROCA = {
    "roe": ("mais lucro sobre o dinheiro dos sócios",
            "menos lucro sobre o dinheiro dos sócios"),
    "margem_liquida": ("mais lucro em cada venda", "menos lucro em cada venda"),
    "p_l": ("preço mais baixo em relação ao lucro",
            "preço mais alto em relação ao lucro"),
    "p_vp": ("preço mais baixo em relação ao patrimônio",
             "preço mais alto em relação ao patrimônio"),
    "ev_ebit": ("preço mais baixo em relação ao lucro da operação",
                "preço mais alto em relação ao lucro da operação"),
    "dy_mediano_5a": ("mais dividendos num ano típico",
                      "menos dividendos num ano típico"),
    "dividend_yield": ("mais rendimentos nos últimos 12 meses",
                       "menos rendimentos nos últimos 12 meses"),
    "cap_rate": ("mais aluguel em relação ao preço",
                 "menos aluguel em relação ao preço"),
    "volatilidade": ("preço mais estável", "preço que sobe e desce mais"),
    "alavancagem": ("menos dívida", "mais dívida"),
    "max_drawdown": ("queda menor no pior momento",
                     "queda maior no pior momento"),
}


def _troca_texto(df: rec.Diferenca) -> str:
    melhor, pior = _TROCA.get(df.chave, (f"{df.metrica} melhor",
                                         f"{df.metrica} pior"))
    return melhor if df.par_melhor else pior


@dataclass(frozen=True)
class Troca:
    """Uma alternativa de troca contada em palavras: quem é, o que tem de
    melhor, o que tem de pior e se passa no critério da troca."""
    ticker: str
    nome: str
    apresentacao: str                # "Do mesmo setor (Seguros), bem maior."
    vantagens: tuple[str, ...]       # "mais lucro sobre ... (84,4% contra 22,8%)"
    desvantagens: tuple[str, ...]
    conclusao: str


def _comparacao(a: m.AnaliseAtivo) -> prs.ComparacaoPares | None:
    return prs.ComparacaoPares.de_dict(a.pares.dados) if a.pares.dados else None


def _tamanho(a: m.AnaliseAtivo, par: prs.Par) -> str:
    """"bem maior", "menor"... pelo porte do par sobre o do ativo."""
    c = _comparacao(a)
    ln = c.linha("porte") if c is not None else None
    if ln is None:
        return ""
    r = prs._razao(ln.valor, par.metricas.get("porte"))
    if r is None:
        return ""
    return ("bem maior" if r >= 2 else "maior" if r >= 1.25 else
            "de tamanho parecido" if r > 0.8 else "menor" if r > 0.5
            else "bem menor")


def trocar_por(a: m.AnaliseAtivo, par: prs.Par) -> Troca:
    """O par ``par`` como alternativa ao ativo ``a``, em palavras. A conta é
    a de ``recomendacao.diferencas`` e o veredito o de
    ``Confronto.claramente_superior``: aqui não se decide nada. Puro."""
    tk = a.ativo.ticker
    c = _comparacao(a)
    setor = c.grupo.valor if c is not None and c.grupo.valor else ""
    tam = _tamanho(a, par)
    apresentacao = _frase(
        "Do mesmo setor" + (f" ({setor})" if setor else "")
        + (f", {tam} que {tk}" if tam and tam != "de tamanho parecido"
           else f", {tam} de {tk}" if tam else ""))
    difs = rec.diferencas(a, par)
    vant = tuple(f"{_troca_texto(df)} ({df.valor_par} contra {df.valor_ativo} "
                 f"de {tk})" for df in difs if df.par_melhor is True)
    desv = tuple(f"{_troca_texto(df)} ({df.valor_par} contra {df.valor_ativo} "
                 f"de {tk})" for df in difs if df.par_melhor is False)
    cf = rec.confrontar(a, par)
    if cf is None or cf.n < rec.MIN_METRICAS_TROCA:
        conclusao = "Há poucos números para comparar: não dá para dizer que é melhor."
    elif cf.claramente_superior:
        conclusao = (f"No conjunto, é mais saudável que {tk} e, somando os "
                     "números de preço e os de risco, não sai pior: passa no "
                     "critério de troca"
                     + (", mesmo perdendo nos pontos marcados com ✗."
                        if desv else "."))
    elif cf.saldo_qualidade <= 0:
        conclusao = f"Não é mais saudável que {tk}: a troca não compensa."
    elif cf.saldo_valuation < 0:
        conclusao = ("É mais saudável, mas, somando os números de preço, sai "
                     "mais cara: a troca não compensa.")
    else:
        conclusao = ("É mais saudável, mas o preço sobe e desce mais: a troca "
                     "não compensa.")
    return Troca(par.ticker, par.nome or "", apresentacao, vant, desv,
                 conclusao)


def _porque_troca(sub: rec.Confronto) -> list[str]:
    """O porquê da troca sem número (o cartão da troca mostra os números) e
    sem esconder o que o substituto tem de pior: a regra soma as métricas
    de preço e de risco, então uma delas pode ser pior (13/11/2026)."""
    vant = [_troca_texto(df) for df in sub.diferencas if df.par_melhor is True]
    desv = [_troca_texto(df) for df in sub.diferencas if df.par_melhor is False]
    if not vant:
        return [f"{sub.ticker}, do mesmo setor, sai melhor na comparação."]
    out = [f"{sub.ticker}, do mesmo setor, sai melhor na comparação: tem "
           f"{_lista(vant[:3])}."]
    if desv:
        out.append(f"Em troca, tem {_lista(desv[:2])}.")
    return out


# -- a economia, em palavras (pedido de 13/11/2026) ---------------------------------

TENDENCIA_SIMPLES = {"alta": "subindo", "queda": "caindo",
                     "estavel": "estável", "incerta": "sem direção clara"}

ROTULO_CENARIO = {
    "interest_rate": "Juros no Brasil (Selic)",
    "interest_rate_outlook": "Para onde vão os juros",
    "inflation": "Inflação",
    "inflation_outlook": "Inflação esperada",
    "economic_activity": "Crescimento da economia",
    "fx": "Dólar",
    "fiscal_policy": "Contas do governo",
    "credit": "Juro acima da inflação",
    "commodities": "Petróleo, minério e grãos",
    "global_economy": "Economia dos EUA",
    "geopolitical_risk": "Guerras e tensões",
    "capital_markets": "Apetite por risco",
}

O_QUE_E = {
    "interest_rate": "Os juros básicos do país. Definem quanto rende a "
                     "renda fixa e quanto custa o dinheiro.",
    "interest_rate_outlook": "O que os títulos do Tesouro mostram sobre os "
                             "juros nos próximos anos.",
    "inflation": "Quanto os preços subiram (IPCA, o índice oficial).",
    "inflation_outlook": "Quanto de inflação o mercado espera à frente.",
    "economic_activity": "Se o país está produzindo e consumindo mais.",
    "fx": "Quanto custa um dólar em reais.",
    "fiscal_policy": "O tamanho da dívida do governo.",
    "credit": "Quanto se ganha, em títulos do Tesouro, além da inflação. "
              "É o custo real do dinheiro.",
    "commodities": "Preços de matérias-primas que o Brasil exporta.",
    "global_economy": "Os juros dos EUA, que puxam o dinheiro do mundo "
                      "inteiro.",
    "geopolitical_risk": "Conflitos e eleições que assustam os investidores.",
    "capital_markets": "Se os investidores estão aceitando risco ou fugindo "
                       "dele.",
}

# Por que a variável pesa na classe do ativo. Regras gerais de economia, não
# conta do app: por isso o "costuma".
POR_QUE_PESA = {
    "renda_fixa": {
        "interest_rate": "Define quanto rendem o Tesouro Selic e os CDBs que "
                         "acompanham o CDI.",
        "interest_rate_outlook": "Se o mercado espera juros menores, os "
                                 "prefixados já comprados costumam se "
                                 "valorizar; se espera maiores, perdem valor "
                                 "antes do vencimento.",
        "inflation": "Títulos atrelados ao IPCA repõem a inflação; nos "
                     "outros, inflação alta come o ganho.",
        "inflation_outlook": "Quanto mais inflação se espera, mais um "
                             "prefixado precisa pagar para valer a pena.",
        "fiscal_policy": "Governo mais endividado costuma ter de pagar juros "
                         "maiores para pegar dinheiro emprestado.",
        "credit": "É a régua para saber se um título paga bem acima da "
                  "inflação.",
    },
    "acoes_br": {
        "interest_rate_outlook": "Juros caindo costumam ajudar a bolsa: as "
                                 "empresas pagam menos pelas dívidas e a "
                                 "renda fixa fica menos atraente.",
        "economic_activity": "Economia crescendo costuma trazer mais vendas e "
                             "mais lucro.",
        "fx": "Dólar alto ajuda quem exporta e atrapalha quem tem dívida ou "
              "custo em dólar.",
        "fiscal_policy": "Contas do governo desarrumadas costumam puxar juros "
                         "e dólar para cima, o que pesa na bolsa.",
        "commodities": "Mexem direto no lucro de petroleiras, mineradoras e "
                       "do agronegócio.",
        "capital_markets": "Quando o investidor aceita mais risco, costuma "
                           "sobrar dinheiro para a bolsa.",
    },
    "fiis": {
        "interest_rate": "Com juros altos, a renda fixa disputa com os "
                         "rendimentos dos fundos, e a cota costuma cair.",
        "interest_rate_outlook": "Expectativa de juros menores costuma "
                                 "valorizar as cotas.",
        "inflation": "Muitos aluguéis e os títulos dos fundos de papel são "
                     "corrigidos pela inflação.",
        "credit": "Pesa nos fundos que emprestam dinheiro a empresas e "
                  "incorporadoras.",
        "economic_activity": "Economia forte costuma encher escritórios, "
                             "galpões e shoppings.",
        "capital_markets": "Quando o investidor aceita mais risco, os fundos "
                           "costumam ficar mais procurados.",
    },
    "exterior": {
        "fx": "O investimento é em dólar: dólar subindo aumenta o valor em "
              "reais; caindo, diminui.",
        "global_economy": "Os juros dos EUA mexem no preço das ações e dos "
                          "títulos de lá.",
        "geopolitical_risk": "Tensões costumam derrubar as bolsas e "
                             "fortalecer o dólar.",
        "capital_markets": "Mostra se o investidor lá fora está com apetite "
                           "ou com medo de risco.",
    },
}


def por_que_pesa(classe: str | None, chave: str) -> str:
    return POR_QUE_PESA.get(classe or "", {}).get(chave) or O_QUE_E.get(chave, "")


_N = r"([+-]?\d[\d.]*(?:,\d+)?)"   # número no formato brasileiro


def _dolar(m_: re.Match) -> str:
    v = float(m_.group(1).replace(".", "").replace(",", "."))
    return f"o dólar vale R$ {v:.2f}".replace(".", ",")


def _pib(m_: re.Match) -> str:
    ano, v = m_.group(1), m_.group(2)
    neg = v.startswith("-")
    return (f"a economia {'encolheu' if neg else 'cresceu'} "
            f"{v.lstrip('+-')}% em {ano} (PIB)")


# Em ordem: os padrões de ``core/cenario/automatico.py`` primeiro, os
# genéricos (a.a., pp) por último. Texto que não casa passa como está.
_GLOSSARIO: tuple[tuple[re.Pattern, object], ...] = tuple(
    (re.compile(p), r) for p, r in (
        (rf"^Selic {_N}% a\.a\. \({_N}% em (\d{{4}})\)",
         r"Selic em \1% ao ano (era \2% em \3)"),
        (rf"^Selic {_N}% a\.a\.", r"Selic em \1% ao ano"),
        (rf"prefixado de ~1 ano {_N}% a\.a\.",
         r"o Tesouro prefixado de 1 ano paga \1% ao ano"),
        (rf" contra Selic {_N}%: mercado precifica corte",
         r", abaixo da Selic (\1%): o mercado espera juros caindo"),
        (rf" contra Selic {_N}%: mercado precifica alta",
         r", acima da Selic (\1%): o mercado espera juros subindo"),
        (rf" contra Selic {_N}%: mercado precifica juros estáveis",
         r", perto da Selic (\1%): o mercado espera juros parados"),
        (rf"; ~5 anos {_N}%", r". O de 5 anos paga \1%"),
        (rf"IPCA acumulado em (\d{{4}}) até o último mês divulgado: {_N}%",
         r"os preços subiram \2% em \1 até o último mês divulgado"),
        (rf"^IPCA (\d{{4}}): {_N}%", r"os preços subiram \2% em \1"),
        (rf"; anos fechados (\d{{4}}) {_N}% → (\d{{4}}) {_N}%",
         r". Nos anos anteriores: \2% em \1 e \4% em \3"),
        (rf"inflação implícita de ~2 anos {_N}% a\.a\. contra IPCA (\d{{4}}) {_N}%",
         r"o mercado espera inflação de \1% ao ano nos próximos 2 anos "
         r"(em \2 foi \3%)"),
        (rf"inflação implícita de ~2 anos {_N}% a\.a\.",
         r"o mercado espera inflação de \1% ao ano nos próximos 2 anos"),
        (rf"US\$ 1 = R\$ {_N}", _dolar),
        (rf"PIB (\d{{4}}) {_N}%", _pib),
        (rf"confiança do consumidor {_N} \({_N} pontos em (\d{{4}})\)",
         r"a confiança do consumidor está em \1 pontos (\2 em \3)"),
        (rf"dívida do governo central {_N}% do PIB em (\d{{4}}) \({_N}% em "
         r"(\d{4})\)",
         r"a dívida do governo equivale a \1% de tudo o que o país produz "
         r"em um ano (dado de \2); era \3% em \4"),
        (rf"dívida do governo central {_N}% do PIB em (\d{{4}})",
         r"a dívida do governo equivale a \1% de tudo o que o país produz "
         r"em um ano (dado de \2)"),
        (rf"juro real de mercado \(IPCA\+ ~3 anos\) {_N}% a\.a\.; Selic {_N}%",
         r"o Tesouro IPCA+ de 3 anos paga \1% ao ano acima da inflação "
         r"(a Selic está em \2%)"),
        (rf"juro real de mercado \(IPCA\+ ~3 anos\) {_N}% a\.a\.",
         r"o Tesouro IPCA+ de 3 anos paga \1% ao ano acima da inflação"),
        (rf"juro do Fed {_N}% \(Fed Funds efetivo {_N}%; {_N}% em (\S+)\)",
         r"juro básico dos EUA em \1% (era \3% em \4)"),
        (rf"juro do Fed {_N}%", r"juro básico dos EUA em \1%"),
        (rf"Treasury 10 anos {_N}%",
         r"o título de 10 anos do governo americano paga \1%"),
        (rf"spread high yield EUA {_N} pp \({_N} pp em (\S+)\)",
         r"empresas arriscadas dos EUA pagam \1 pontos a mais que o governo "
         r"de lá (eram \2 em \3)"),
        (rf"spread high yield EUA {_N} pp",
         r"empresas arriscadas dos EUA pagam \1 pontos a mais que o governo "
         r"de lá"),
        (r"; spread menor = mais apetite a risco",
         r". Quanto menor essa diferença, mais apetite por risco"),
        (r" a\.a\.", r" ao ano"),
        (r" pp\b", r" pontos"),
    ))


def valor_simples(texto: str | None) -> str:
    """O valor de uma variável do cenário sem sigla de mercado. Só troca
    palavras: os números são os mesmos do texto original. Puro."""
    if not texto:
        return ""
    t = texto
    for padrao, troca in _GLOSSARIO:
        t = padrao.sub(troca, t)
    t = t.replace("; ", ". ")
    t = re.sub(r"\. ([a-zà-ú])", lambda m_: ". " + m_.group(1).upper(), t)
    return _frase(t)
