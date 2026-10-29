"""Relatório de analista por ativo: ação da B3, ação americana ou FII.

Pedido do usuário (29/10/2026): "Faça com que as seções de Empresas B3,
Empresas Americanas e Seleção de FIIs se tornem analistas profissionais".

O que um analista entrega, por ativo: a régua que usou, a conclusão e o porquê,
a tese (o que sustenta), os riscos (o que pode dar errado), onde o preço está
contra o próprio passado, o que o faria mudar de opinião e o que ele não sabe.
O relatório sai da mesma avaliação por regras da Inteligência dos Ativos
(``avaliacao.avaliar``) e do mesmo limite (``veredito.limite``): as telas de
mercado e a aba Inteligência não podem dar vereditos diferentes sobre o mesmo
ativo. Não usa o score de ranking da B3, que não foi validado como previsor.
Puro: os leitores são os da Inteligência, injetáveis.

O relatório descreve o ativo; não estima quanto ele vai render. Nenhuma das
regras dele foi testada contra o retorno seguinte, e ele não usa os scores de
ranking dos mercados (B3, EUA, FII). O aviso da tela diz isso em uma frase; o
que a medição gravada diz do score do mercado vai para as limitações
(``limitacao_previsao``, lido de ``data/vantagem_oos.json``), que a tela manda
para Configurações → Restrições e o texto da LLM carrega inteiro.

Decisões de método:

* **Faixa observada, não alvo.** A faixa de preço é o que o preço seria se o
  múltiplo voltasse ao interquartil do próprio histórico, mantido o lucro, o
  patrimônio ou o provento de hoje. Supõe que o denominador não muda e que o
  passado é régua do futuro; por isso não é preço-alvo. Só entram múltiplos
  proporcionais ao preço (P/L, P/VP, dividend yield): no EV/EBIT a dívida não
  acompanha o preço, e o cap rate implícito também a carrega. Em cíclica o
  lucro de hoje pode ser de pico, e a faixa pelo P/L herda o erro: o texto
  diz. P/L abaixo de 3x costuma vir de lucro não recorrente e não gera faixa.
* **Gatilho é a regra eliminatória, com o número de hoje.** Os limites são as
  constantes de ``avaliacao``, para o gatilho não divergir da regra que de
  fato elimina; cada um mostra o valor atual e se já disparou.
* **Procedência fica fora da tela.** O valor de hoje (``atual``) e a
  referência dele (período, fonte) andam em campos separados: a tela mostra
  só o valor, o texto da LLM leva os dois. Nos critérios da tese e dos
  riscos, que a avaliação escreve com a referência entre parênteses,
  ``para_tela`` tira exatamente esses parênteses. O alerta de fontes
  divergentes (a razão do provedor contradiz o balanço) é falta de leitura,
  e vai para as limitações.
* **Faixas que não se sobrepõem são ditas.** Quando o P/L aponta para um lado
  e o dividend yield para outro, o relatório não escolhe uma: diz que lucro,
  patrimônio e provento contam histórias diferentes.
* **Sem dado é limitação declarada.** Seção sem dado, indicador ausente,
  faixa que não se calcula, gatilho que não se confere e a nota que explica
  a falta de leitura viram linha em ``limitacoes``, nunca somem. A nota que
  explica uma decisão do motor (P/L baixo que não contou como barato,
  armadilha de valor) fica na leitura: é análise, não lacuna.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import fontes_fundamentos as ff
from core.inteligencia_ativos import fundamentos as fnd
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import valuation as val
from core.inteligencia_ativos import veredito as vd

# P/L abaixo disto não gera faixa (o mesmo corte da avaliação).
PL_MINIMO_FAIXA = 3.0
# Notícias de impacto que o relatório lista (as mais recentes primeiro).
MAX_NOTICIAS = 5

# chave → o que se supõe constante na faixa.
_DENOMINADOR = {"p_l": "o lucro por ação", "p_vp": "o patrimônio por ação",
                "dividend_yield": "o provento dos últimos 12 meses"}
_DENOMINADOR_FII = {"p_vp": "o valor patrimonial da cota",
                    "dividend_yield": "o rendimento dos últimos 12 meses"}

AVISO = ("Apoio analítico por regras sobre os dados do app, não recomendação "
         "nem previsão de retorno: as regras descrevem o ativo e não foram "
         "testadas como previsoras; as faixas são observadas no histórico, não "
         "preço-alvo; os gatilhos são as regras eliminatórias da avaliação. A "
         "decisão é sua.")

_PREFIXO_PREVISAO = (
    "Previsão de retorno: nenhuma regra deste relatório foi testada contra o "
    "retorno seguinte do ativo, e leitura favorável não é expectativa de "
    "retorno maior. O relatório não usa o score de ranking do mercado.")
_SCORE_DO_MERCADO = {"b3": "O score de ranking da B3",
                     "us": "O score de ranking das ações americanas",
                     "fii": "O score de ranking dos FIIs"}


def _num_br(x: float, casas: int) -> str:
    return f"{x:.{casas}f}".replace("-", "−").replace(".", ",")


def _coef(x: float) -> str:
    # 4 casas perto do zero, onde mora a decisão: −0,0004 com 3 casas sairia
    # "−0,000" e o número que sustenta o veredito sumiria.
    return _num_br(x, 3 if abs(x) >= 0.001 else 4)


def _pct(x: float) -> str:
    return _num_br(x * 100, 1 if abs(x) >= 0.001 else 2) + "%"


def _float(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _data_br(iso: str) -> str:
    partes = str(iso).split("-")
    return "/".join(reversed(partes)) if len(partes) == 3 else str(iso)


def limitacao_previsao(mercado: str, caminho=None) -> str:
    """O que a medição gravada diz sobre o score de ranking do mercado.

    Lida de ``data/vantagem_oos.json`` a cada chamada, nunca escrita à mão: um
    texto fixo com o número de uma medição envelhece invertido quando o motor
    é remedido (``memoria: aviso-que-envelhece-invertido``). O veredito é o de
    ``vantagem_oos.avaliar``, o mesmo do Grau de Confiança. Sem medição desta
    versão do motor, o texto diz que não há evidência, nunca que há.
    """
    from core import vantagem_oos as vo

    if mercado not in _SCORE_DO_MERCADO:
        return _PREFIXO_PREVISAO
    motor, score = mercado, _SCORE_DO_MERCADO[mercado]
    try:
        versao = vo.versao_corrente(motor)
    except Exception:  # noqa: BLE001 -- motor que não importa não tem versão
        versao = ""
    estado, _ = vo.avaliar(motor, versao, caminho)
    med = vo.carregar_medicao(motor, caminho) or {}
    low, high = _float(med.get("ic_low")), _float(med.get("ic_high"))
    if low is None or high is None:
        estado = vo.NAO_APURADO
    if estado is vo.NAO_APURADO:
        medida = str(med.get("versao_metodologia") or "")
        if med and medida and versao and medida != versao:
            motivo = (f"a última medição de vantagem fora da amostra "
                      f"({_data_br(med.get('medido_em') or '?')}) é da "
                      f"metodologia {medida}, e ele roda a {versao}")
        else:
            motivo = "não tem medição de vantagem fora da amostra"
        return (f"{_PREFIXO_PREVISAO} {score} {motivo}: não há evidência de "
                f"que ele preveja retorno.")

    coef = str(med.get("formato")) == vo.FORMATO_COEFICIENTE
    fmt = _coef if coef else _pct
    metrica = ("o Rank-IC médio (correlação entre o score e o retorno do "
               "período seguinte, no universo)" if coef else
               "o excesso médio por período sobre a carteira igualmente "
               "ponderada do universo")
    media = _float(med.get("media"))
    numeros = (f"na medição de {_data_br(med.get('medido_em') or '?')} "
               f"(metodologia {med.get('versao_metodologia')}, "
               f"{int(med.get('n_periodos') or 0)} períodos), {metrica} foi "
               f"{fmt(media) if media is not None else 'não informado'}, com "
               f"intervalo de 95% de {fmt(low)} a {fmt(high)}")
    if estado is vo.APROVADO:
        return (f"{_PREFIXO_PREVISAO} {score} demonstrou vantagem fora da "
                f"amostra: {numeros}, acima do zero. É vantagem do ranking "
                f"sobre o universo, não de um ativo isolado.")
    lado = "inteiro abaixo do zero" if high < 0 else "que cruza o zero"
    texto = (f"{_PREFIXO_PREVISAO} {score} não demonstrou vantagem fora da "
             f"amostra: {numeros}, {lado}.")
    sig = vo.rank_ic_significativo(med)
    if sig is not None and not coef:
        texto += (f" Ainda assim ele ordena o universo (Rank-IC médio "
                  f"{_coef(sig[0])}, t = {_num_br(sig[1], 2)}), mas a carteira "
                  f"montada com ele não superou a igualmente ponderada.")
    return texto


@dataclass(frozen=True)
class FaixaImplicita:
    chave: str
    rotulo: str
    atual: str            # o múltiplo de hoje (só o valor)
    intervalo: str        # o interquartil do histórico
    periodo: str          # n observações e período
    variacao_min: float   # % de variação do preço no extremo baixo
    variacao_max: float
    premissa: str
    referencia: str = ""  # período ou fonte do múltiplo de hoje (só na LLM)


@dataclass(frozen=True)
class Gatilho:
    indicador: str
    regra: str
    atual: str               # o valor de hoje, sem procedência
    disparado: bool | None   # None: sem dado para conferir
    elimina: bool = True     # False: alerta de revisão, não eliminação
    referencia: str = ""     # período e fonte do valor (só no texto da LLM)


@dataclass(frozen=True)
class Leitura:
    dimensao: str
    leitura: str          # rótulo
    favoraveis: int
    desfavoraveis: int
    nota: str | None = None


@dataclass(frozen=True)
class RelatorioAnalista:
    ticker: str
    nome: str
    mercado: str
    moeda: str
    perfil: str           # rótulo da régua setorial
    limite: str           # código de ``veredito``
    motivo_limite: str
    leituras: tuple[Leitura, ...]
    tese: tuple[str, ...]
    riscos: tuple[str, ...]
    contexto: tuple[str, ...]
    faixas: tuple[FaixaImplicita, ...]
    gatilhos: tuple[Gatilho, ...]
    noticias: tuple[str, ...]
    limitacoes: tuple[str, ...]
    resumo: str
    aviso: str = AVISO
    leitura_faixas: str = ""   # quando as faixas não se sobrepõem
    # " (referência)" que a avaliação cola nos critérios; a mais longa
    # primeiro, porque uma referência pode conter outra entre parênteses.
    procedencias: tuple[str, ...] = ()

    @property
    def rotulo_limite(self) -> str:
        return vd.ROTULO_LIMITE[self.limite]

    @property
    def disparados(self) -> tuple[Gatilho, ...]:
        return tuple(g for g in self.gatilhos if g.disparado)

    def para_tela(self, texto: str) -> str:
        """O texto sem a procedência entre parênteses (regra de 05/10/2026:
        procedência é registrada, não exibida). O texto da LLM usa o
        original."""
        for ref in self.procedencias:
            texto = texto.replace(ref, "")
        return texto


# -- faixas pelo histórico -----------------------------------------------------------

def _com_ref(texto: str, ref: str | None) -> str:
    return f"{texto} ({ref})" if ref else texto


def divergencia_das_faixas(faixas) -> str:
    """Frase para quando as faixas não se cruzam: cada múltiplo supõe um
    denominador constante, e eles discordam. Vazia se há sobreposição."""
    if len(faixas) < 2:
        return ""
    if max(fx.variacao_min for fx in faixas) <= min(fx.variacao_max
                                                     for fx in faixas):
        return ""
    rotulos = ", ".join(fx.rotulo for fx in faixas)
    return (f"As faixas pelo {rotulos} não se sobrepõem: cada múltiplo supõe "
            "constante um denominador diferente (lucro, patrimônio, provento), "
            "e eles contam histórias diferentes. Nenhuma faixa resume o preço "
            "sozinha.")


def faixas_implicitas(v: val.Valuation, perfil: str
                      ) -> tuple[tuple[FaixaImplicita, ...], tuple[str, ...]]:
    """(faixas, limitações): o preço se o múltiplo voltasse ao interquartil
    do próprio histórico, mantido o denominador de hoje."""
    faixas: list[FaixaImplicita] = []
    lim: list[str] = []
    denominadores = _DENOMINADOR_FII if perfil == av_.FII else _DENOMINADOR
    for chave, denominador in denominadores.items():
        ln = v.linha(chave)
        if ln is None:
            continue
        if not ln.aplicavel or ln.atual is None or ln.atual <= 0:
            lim.append(f"{ln.rotulo}: sem faixa de preço"
                       + (f" — {ln.motivo}" if ln.motivo else
                          " — sem valor atual."))
            continue
        est = ln.historico
        if est is None or est.p25 <= 0 or est.p75 <= 0:
            motivo = (ln.comparacao_historica
                      if ln.comparacao_historica != val.NAO_DISPONIVEL
                      else "sem histórico do próprio ativo.")
            lim.append(f"{ln.rotulo}: sem faixa de preço — {motivo}")
            continue
        if chave == "p_l" and ln.atual < PL_MINIMO_FAIXA:
            lim.append(f"P/L de {av_._x(ln.atual)}: abaixo de "
                       f"{av_._x(PL_MINIMO_FAIXA)} costuma vir de lucro não "
                       "recorrente; faixa pelo P/L omitida.")
            continue
        if chave == "dividend_yield":
            # Preço = provento / DY: DY maior, preço menor.
            vmin, vmax = ln.atual / est.p75 - 1, ln.atual / est.p25 - 1
        else:
            vmin, vmax = est.p25 / ln.atual - 1, est.p75 / ln.atual - 1
        obs = val.JANELA.get(est.frequencia or "", (0, 0, "", "observações"))[3]
        quando = val._periodo(est)
        premissa = (f"Se o {ln.rotulo} voltasse ao interquartil do próprio "
                    f"histórico, mantido {denominador} de hoje. Faixa "
                    "observada, não alvo.")
        if chave == "p_l" and perfil == av_.CICLICA:
            premissa += (" Em empresa cíclica o lucro de hoje pode ser de "
                         "pico ou de vale do ciclo, e a faixa herda o erro.")
        faixas.append(FaixaImplicita(
            chave, ln.rotulo, av_._fmt(ln.atual, ln.unidade),
            f"{av_._fmt(est.p25, ln.unidade)} a {av_._fmt(est.p75, ln.unidade)}",
            f"{est.n} {obs}" + (f", {quando}" if quando else ""),
            round(vmin * 100, 1), round(vmax * 100, 1), premissa,
            referencia=ln.referencia or ""))
    return tuple(faixas), tuple(lim)


# -- gatilhos de revisão -------------------------------------------------------------

def _valor(f: fnd.Fundamentos, chave: str) -> tuple[float | None, str]:
    """(valor, referência crua): a referência vai para ``Gatilho.referencia``,
    nunca para o ``atual`` que a tela mostra."""
    i = f.indicador(chave)
    x = av_._num(i.valor) if i is not None else None
    return x, (i.referencia or "") if i is not None else ""


def _noticias_criticas(nots: inf.Noticias) -> list[inf.Noticia]:
    """Fato novo de fraude ou recuperação judicial; a notícia que fala da
    saída (``avaliacao._TERMOS_SAIDA``) não conta, como na avaliação."""
    return [n for n in nots.itens if n.categoria in av_.CATEGORIAS_CRITICAS
            and not any(t in av_._norm(n.headline)
                        for t in av_._TERMOS_SAIDA)]


def gatilhos(perfil: str, f: fnd.Fundamentos, v: val.Valuation,
             nots: inf.Noticias, teto_pl: tuple[float, str] | None,
             c: prs.ComparacaoPares | None = None, *,
             tipo: str | None = None, noticias_lidas: bool = True
             ) -> tuple[Gatilho, ...]:
    """As regras eliminatórias da avaliação para o perfil, com o número de
    hoje. ``disparado`` None quando falta o dado para conferir.

    ``tipo`` é o da classe do ativo, para quando nenhuma seção trouxe dado:
    o gatilho aparece como "sem dado" em vez de sumir. ``noticias_lidas``
    False (a seção de notícias não leu nada) faz o noticiário valer "sem
    dado", não "nenhum fato"."""
    g: list[Gatilho] = []
    sem = "sem dado"
    tipo = f.tipo or v.tipo or tipo
    fii = perfil == av_.FII or tipo == fnd.FII
    # As regras de balanço só rodam para ação (``avaliacao.avaliar``).
    acao = not fii and tipo == fnd.ACAO

    if acao and perfil != av_.FINANCEIRA:
        x, ref = _valor(f, "divida_liquida_ebitda")
        ind = f.indicador("divida_liquida_ebitda")
        ebitda_negativo = (x is None and ind is not None and
                           (ind.nota or "").startswith(ff.NOTA_EBITDA_NEGATIVO))
        if perfil == av_.INCORPORADORA:
            # Na incorporadora a razão só informa; o EBITDA negativo elimina.
            g.append(Gatilho(
                "EBITDA de 12 meses",
                "negativo elimina (na incorporadora a dívida líquida/EBITDA "
                "só informa; a régua de dívida é a bruta/patrimônio)",
                "negativo" if ebitda_negativo else
                "positivo" if x is not None else sem,
                True if ebitda_negativo else None if x is None else False))
        else:
            critico = av_.LIMITES_ALAVANCAGEM.get(
                perfil, av_.LIMITES_ALAVANCAGEM[av_.GERAL])[2]
            regua = ("régua de setor regulado" if perfil == av_.REGULADA else
                     "régua de cíclica" if perfil == av_.CICLICA else
                     "régua geral")
            if ebitda_negativo:
                atual, disp = "EBITDA de 12 meses negativo", True
            elif x is None:
                atual, disp = sem, None
            else:
                atual, disp = av_._x(x), x > critico
            g.append(Gatilho("Dívida líquida/EBITDA",
                             f"acima de {av_._x(critico)} ou EBITDA negativo "
                             f"elimina ({regua})", atual, disp,
                             referencia=ref if x is not None else ""))
    if perfil == av_.INCORPORADORA and teto_pl is not None:
        teto, origem = teto_pl
        x, ref = _valor(f, "divida_bruta_patrimonio")
        lim = av_.FATOR_CRITICO_DIVIDA_PL * teto
        g.append(Gatilho("Dívida bruta/patrimônio",
                         f"acima de {av_._x(lim)} elimina "
                         f"({av_._br(av_.FATOR_CRITICO_DIVIDA_PL, 0)} vezes o "
                         f"teto de {av_._x(teto)}, {origem})",
                         av_._x(x) if x is not None else sem,
                         None if x is None else x > lim,
                         referencia=ref if x is not None else ""))
    if acao and perfil != av_.FINANCEIRA:
        x, ref = _valor(f, "cobertura_juros")
        lim = av_.COBERTURA_JUROS[1]
        g.append(Gatilho("Cobertura de juros",
                         f"abaixo de {av_._x(lim)} elimina (o resultado "
                         "operacional não paga os juros)",
                         av_._x(x) if x is not None else sem,
                         None if x is None else x < lim,
                         referencia=ref if x is not None else ""))
        payout, ref_p = _valor(f, "payout")
        fcl, _ = _valor(f, "fluxo_caixa_livre")
        regulada = perfil == av_.REGULADA
        if payout is None or fcl is None:
            atual, disp, ref_p = sem, None, ""
        else:
            atual = (f"payout de {av_._pct(payout)}, caixa livre "
                     f"{'negativo' if fcl < 0 else 'positivo'}")
            disp = payout > av_.PAYOUT_ACIMA_DO_LUCRO and fcl < 0
        g.append(Gatilho(
            "Dividendo sem lucro nem caixa",
            f"payout acima de {av_._pct(av_.PAYOUT_ACIMA_DO_LUCRO, 0)} com "
            "caixa livre negativo "
            + ("pede revisão (no setor regulado costuma ser ciclo de "
               "investimento, não elimina)" if regulada else "elimina"),
            atual, disp, elimina=not regulada, referencia=ref_p))
    if fii:
        x, _ = _valor(f, "vacancia_financeira")
        rot = "Vacância financeira"
        if x is None:
            x, _ = _valor(f, "vacancia_fisica")
            rot = "Vacância física" if x is not None else "Vacância"
        g.append(Gatilho(rot, f"acima de {av_._pct(av_.VACANCIA_FII[1], 0)} "
                              "elimina",
                         av_._pct(x) if x is not None else sem,
                         None if x is None else x > av_.VACANCIA_FII[1]))
        x, _ = _valor(f, "inadimplencia")
        g.append(Gatilho("Inadimplência",
                         f"acima de {av_._pct(av_.INADIMPLENCIA_FII[1], 0)} "
                         "elimina", av_._pct(x) if x is not None else sem,
                         None if x is None else x > av_.INADIMPLENCIA_FII[1]))

    pvp = v.linha("p_vp")
    if any(ln.motivo == val.NAO_SE_APLICA["patrimonio_negativo"]
           for ln in v.linhas):
        atual, disp = "negativo", True
    elif pvp is not None and pvp.aplicavel and pvp.atual is not None:
        atual, disp = "positivo", False
    else:
        atual, disp = sem, None
    g.append(Gatilho("Patrimônio líquido", "negativo elimina", atual, disp))

    criticas = _noticias_criticas(nots)
    janela = nots.janela_dias or 60
    if criticas:
        atual, disp = (f"{criticas[0].rotulo_categoria.lower()} em "
                       f"{_data(criticas[0].date)}: {criticas[0].headline}",
                       True)
    elif nots.itens or noticias_lidas:
        atual, disp = f"nenhum fato em {janela} dias", False
    else:
        atual, disp = sem, None
    g.append(Gatilho("Fraude ou recuperação judicial",
                     "fato novo no noticiário elimina", atual, disp))

    ln = c.linha("retorno_12m") if c is not None else None
    r = av_._num(ln.valor) if ln is not None else None
    g.append(Gatilho(
        "Queda do preço em 12 meses",
        f"queda de {av_._pct(-av_.MOMENTO_QUEDA_ALERTA, 0)} ou mais (com "
        "proventos) pede revisão da tese; não elimina",
        (f"retorno de {'+' if r > 0 else ''}{av_._pct(r)}" if r is not None
         else sem),
        None if r is None else r <= av_.MOMENTO_QUEDA_ALERTA, elimina=False))
    return tuple(g)


# -- relatório -----------------------------------------------------------------------

def _data(iso: str | None) -> str:
    s = str(iso or "")[:10]
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if len(s) == 10 else "sem data"


def noticias_de_impacto(nots: inf.Noticias, n: int = MAX_NOTICIAS
                        ) -> tuple[str, ...]:
    """As de impacto alto e médio, as mais recentes primeiro."""
    itens = [x for x in nots.itens if x.impact_level in (inf.HIGH, inf.MEDIUM)]
    itens.sort(key=lambda x: str(x.date or ""), reverse=True)
    return tuple(
        f"{_data(x.date)} · {inf.ROTULO_NIVEL[x.impact_level]} · "
        f"{x.rotulo_categoria} · {x.headline}"
        + (f" ({x.source})" if x.source else "") for x in itens[:n])


_TIPO_DA_CLASSE = {"Ações BR": fnd.ACAO, "Ações EUA": fnd.ACAO, "FII": fnd.FII}


def _secao_lida(sec) -> bool:
    return sec is not None and sec.estado == m.DISPONIVEL


def _nota_de_lacuna(d: av_.Dimensao) -> bool:
    """A nota explica a falta de leitura, não uma decisão do motor.

    No mercado a nota só fala da cobertura do noticiário (sem notícia própria,
    léxico que não mede); sem leitura ou com dado insuficiente, ela é o
    motivo da ausência. As demais (P/L abaixo de 3x, P/L de cíclica,
    armadilha de valor) dizem o que o motor fez com o número."""
    return d.chave == av_.MERCADO or d.leitura in (av_.SEM_LEITURA,
                                                   av_.INSUFICIENTE)


def _limitacoes_de_secao(insumos) -> list[str]:
    saida = []
    for sec in (insumos.fundamentos, insumos.valuation, insumos.pares,
                insumos.noticias):
        if sec is not None and sec.estado != m.DISPONIVEL:
            saida.append(f"{sec.titulo}: {sec.resumo}")
    return saida


def montar_relatorio(ticker: str, nome: str | None = None,
                     setor: str | None = None, *, mercado: str = "b3",
                     **leitores) -> RelatorioAnalista:
    """O relatório do ativo pelos provedores da Inteligência (leitores
    injetáveis, como em ``veredito.insumos_do_ativo``)."""
    ins = vd.insumos_do_ativo(ticker, nome, setor, mercado=mercado, **leitores)
    return relatorio_dos_insumos(ins, mercado=mercado)


def relatorio_dos_insumos(ins, *, mercado: str = "b3") -> RelatorioAnalista:
    av = av_.avaliar(ins)
    cod, motivo = vd.limite(av)
    f = fnd.Fundamentos.de_dict(av_._secao(ins.fundamentos))
    v = val.Valuation.de_dict(av_._secao(ins.valuation))
    dp = av_._secao(ins.pares)
    c = prs.ComparacaoPares.de_dict(dp) if dp else None
    nots = inf.Noticias.de_dict(av_._secao(ins.noticias))

    tese, riscos, contexto = [], [], []
    for d in av.dimensoes:
        for cr in d.criterios:
            (tese if cr.sinal > 0 else riscos if cr.sinal < 0
             else contexto).append(cr.texto)
    # O eliminatório abre a lista de riscos; o alerta comum vem depois. O de
    # fontes divergentes é indicador sem leitura: limitação, não risco.
    sem_leitura = [a.texto for a in av.alertas if not a.critico
                   and ff.NOTA_FONTES_DIVERGENTES in a.texto]
    riscos = ([f"Eliminatório: {a.texto}" for a in av.criticos] + riscos
              + [f"Alerta: {a.texto}" for a in av.alertas
                 if not a.critico and a.texto not in sem_leitura])

    teto = (av_.teto_divida_patrimonio(ins, f, c)
            if av.perfil == av_.INCORPORADORA else None)
    faixas, lim_faixas = faixas_implicitas(v, av.perfil)
    lim = _limitacoes_de_secao(ins) + sem_leitura
    if f.ausentes:
        lim.append("Indicadores sem dado: "
                   + ", ".join(i.rotulo for i in f.ausentes) + ".")
    lim += list(lim_faixas)
    lim.append(limitacao_previsao(mercado))
    if av.perfil == av_.FINANCEIRA:
        lim.append("Banco e seguradora: o app não tem regra eliminatória de "
                   "balanço (a dívida é matéria-prima); só o noticiário "
                   "(fraude, recuperação judicial) elimina.")
    if av.perfil == av_.INCORPORADORA and teto is None:
        lim.append("Sem teto de dívida bruta/patrimônio para o segmento: o "
                   "gatilho de dívida da incorporadora não se confere.")

    leituras = []
    for d in av.dimensoes:
        lacuna = bool(d.nota) and _nota_de_lacuna(d)
        if lacuna:
            lim.append(f"{d.rotulo}: {d.nota}")
        leituras.append(Leitura(d.rotulo, d.rotulo_leitura, d.favoraveis,
                                d.desfavoraveis, None if lacuna else d.nota))
    info = ins.ativo
    gts = gatilhos(av.perfil, f, v, nots, teto, c,
                   tipo=_TIPO_DA_CLASSE.get(info.classe),
                   noticias_lidas=_secao_lida(ins.noticias))
    lim += [f"Gatilho {g.indicador}: sem dado para conferir."
            for g in gts if g.disparado is None]
    refs = {av_._ref(i) for i in f.indicadores if i.referencia}
    refs |= {f" ({ln.referencia})" for ln in v.linhas if ln.referencia}
    return RelatorioAnalista(
        ticker=info.ticker, nome=info.nome or info.ticker, mercado=mercado,
        moeda=info.moeda or "BRL", perfil=av.rotulo_perfil, limite=cod,
        motivo_limite=motivo, leituras=tuple(leituras), tese=tuple(tese),
        riscos=tuple(riscos), contexto=tuple(contexto), faixas=faixas,
        gatilhos=gts, noticias=noticias_de_impacto(nots),
        limitacoes=tuple(dict.fromkeys(lim)),
        resumo=av.resumo(), leitura_faixas=divergencia_das_faixas(faixas),
        procedencias=tuple(sorted(refs, key=len, reverse=True)))


def _sinal(p: float) -> str:
    return f"{'+' if p > 0 else ''}{av_._br(p)}%"


def texto(r: RelatorioAnalista) -> str:
    """Bloco para a LLM: o relatório inteiro, com a régua e as limitações."""
    linhas = [f"[Relatório de analista (regras) — {r.ticker}]",
              f"Régua setorial: {r.perfil}.",
              f"LIMITE da recomendação: {r.rotulo_limite}"
              + (f" ({r.motivo_limite})" if r.motivo_limite else "") + "."]
    for le in r.leituras:
        linhas.append(f"{le.dimensao}: {le.leitura} ({le.favoraveis} a favor, "
                      f"{le.desfavoraveis} contra)"
                      + (f" — {le.nota}" if le.nota else ""))
    for titulo, itens in (("Tese", r.tese), ("Riscos", r.riscos),
                          ("Contexto", r.contexto)):
        if itens:
            linhas.append(f"{titulo}:")
            linhas += [f"  - {t}" for t in itens]
    for fx in r.faixas:
        linhas.append(f"Faixa pelo {fx.rotulo}: atual "
                      f"{_com_ref(fx.atual, fx.referencia)}; "
                      f"interquartil {fx.intervalo} ({fx.periodo}) → preço "
                      f"{_sinal(fx.variacao_min)} a {_sinal(fx.variacao_max)}."
                      f" {fx.premissa}")
    if r.leitura_faixas:
        linhas.append(r.leitura_faixas)
    for gt in r.gatilhos:
        estado = ("DISPARADO" if gt.disparado else "sem dado"
                  if gt.disparado is None else "não disparado")
        linhas.append(f"Gatilho {gt.indicador}: {gt.regra}; hoje "
                      f"{_com_ref(gt.atual, gt.referencia)} [{estado}]")
    if r.noticias:
        linhas.append("Notícias de impacto:")
        linhas += [f"  - {n}" for n in r.noticias]
    for li in r.limitacoes:
        linhas.append(f"Limitação: {li}")
    linhas.append(r.aviso)
    return "\n".join(linhas)
