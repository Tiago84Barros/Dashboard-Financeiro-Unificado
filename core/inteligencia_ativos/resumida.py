"""
core/inteligencia_ativos/resumida.py
Página resumida da Inteligência dos Ativos (30/09/2026).

A aba ficou carregada: resumo, cartões, cálculos, 13 etapas, Portfolio Fit e
histórico, tudo aberto de uma vez. O usuário desenhou o que quer ver primeiro:

    Reserva de emergência → Tesouro Selic, valor e %
    Renda fixa            → as demais, valor e %
    Ações                 → uma caixa por ativo: % atual, % devida, a ação
                            sugerida (manter, comprar, acompanhar,
                            rebalancear, reduzir parcialmente, vender ou
                            substituir) com convicção, condições e limitações,
                            substituto se sair, dois pares numa tabela,
                            notícias, relatórios e o macro

Este módulo só reagrupa o que ``analisar_carteira`` já montou; não lê banco
nem chama LLM. O único número novo é a % devida sugerida (``alvos_sugeridos``,
pedido do usuário em 30/09/2026): o alvo da classe repartido pelo inverso da
volatilidade, com teto por ativo. O detalhe completo continua na aba, abaixo,
sob demanda.

Coberto por tests/test_inteligencia_ativos_resumida.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.cenario import modelo as cen
from core.estrategia import politica as pol
from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import recomendacao as rec
from core.inteligencia_ativos.fundamentos import _tem_valor, formatar

# -- grupos da página, na ordem da tela ------------------------------------------

RESERVA = "reserva"
RENDA_FIXA = "renda_fixa"
ACOES = "acoes_br"
FIIS = "fiis"
EXTERIOR = "exterior"
OUTROS = "outros"

GRUPOS: tuple[tuple[str, str], ...] = (
    (RESERVA, "Reserva de emergência"),
    (RENDA_FIXA, "Renda fixa"),
    (ACOES, "Ações"),
    (FIIS, "Fundos imobiliários"),
    (EXTERIOR, "Internacional"),
    (OUTROS, "Outros ativos"),
)
ROTULO_GRUPO = dict(GRUPOS)
# Grupos que aparecem como lista simples (valor e %). Os demais ganham uma
# caixa de expansão por ativo.
GRUPOS_EM_LISTA = (RESERVA, RENDA_FIXA)

# -- a recomendação ------------------------------------------------------------------

MANTER = "manter"
COMPRAR = "comprar"
ACOMPANHAR = "acompanhar"
REBALANCEAR = "rebalancear"
REDUZIR_PARCIAL = "reduzir_parcial"
VENDER = "vender"
SUBSTITUIR = "substituir"
ROTULO_DECISAO = {MANTER: "Manter", COMPRAR: "Comprar",
                  ACOMPANHAR: "Acompanhar", REBALANCEAR: "Rebalancear",
                  REDUZIR_PARCIAL: "Reduzir parcialmente", VENDER: "Vender",
                  SUBSTITUIR: "Substituir"}
# Só estas vendem alguma coisa. Rebalancear é mudar para onde vão os próximos
# aportes, sem vender; acompanhar é investigar antes de mexer.
MOVIMENTA = (REDUZIR_PARCIAL, VENDER, SUBSTITUIR)

# A ação da análise é leitura de adequação, com sete estados; aqui ela é o
# ponto de partida quando não há % devida. Até 29/10/2026 três deles viravam
# "Vender" direto (tese em dúvida, concentração, comparar alternativas). O
# usuário pediu cautela: limite estourado é alerta para investigar, não ordem.
DECISAO: dict[str, tuple[str, str]] = {
    m.APORTE_COMPATIVEL: (COMPRAR, "novos aportes cabem na estratégia"),
    m.REAVALIAR_TESE: (ACOMPANHAR, "reavaliar a tese: ela está em dúvida"),
    m.REDUZIR_CONCENTRACAO: (REBALANCEAR, "rebalancear: reduzir concentração"),
    m.COMPARAR_ALTERNATIVAS: (ACOMPANHAR, "comparar com alternativas"),
    m.REAVALIAR_APORTES: (MANTER, "manter, sem novos aportes por ora"),
    m.EXPOSICAO_ADEQUADA: (MANTER, "exposição já adequada"),
    m.MANTER: (MANTER, "nenhuma regra pede mudança"),
}

# Redução parcial: no máximo um terço da posição por vez, e 20% quando o
# motivo é só o preço esticado. Venda total fica para alerta eliminatório,
# tese quebrada com qualidade frágil ou alternativa claramente superior.
# As frações são convenção do sistema, não calibradas.
FRACAO_MAX_REDUCAO = 1 / 3
FRACAO_REDUCAO_ESTICADO = 0.20
# Acima de 1,5 vez o limite por ativo, o sistema deixa de contar só com os
# aportes para corrigir o peso e sugere reduzir parte, mesmo com a empresa
# sólida. Também convenção.
FATOR_TETO = 1.5
# Quantos pares fora da carteira o confronto examina (os mais próximos).
N_CANDIDATOS_TROCA = 5
_GATILHOS_FUNDAMENTOS = ("reducao_dividendos", "aumento_divida",
                         "perda_qualidade", "mudanca_estrategia")

AVISO_DECISAO = ("Recomendação por regras, não ordem automática: junta a "
                 "adequação à sua estratégia (% devida, limites e tese) com a "
                 "avaliação do ativo (qualidade contra os pares, valuation "
                 "contra o próprio histórico e os pares, notícias e risco de "
                 "mercado). Peso acima da % devida, preço no topo do "
                 "histórico ou queda forte são alertas para investigar: só "
                 "viram venda com piora dos fundamentos, tese quebrada ou "
                 "alternativa claramente superior, depois de custos e "
                 "impostos.")
AVISO_SUBSTITUTO = ("Candidatos do mesmo grupo de comparação, fora da sua "
                    "carteira. A troca só é sugerida quando um deles supera o "
                    "ativo na qualidade sem perder no preço nem no risco; "
                    "potencial de alta maior, sozinho, não basta. Compare na "
                    "tabela antes de decidir.")

N_PARES_TABELA = 2
N_NOTICIAS = 3
N_RELATORIOS = 3


def _eh_selic(a: m.AnaliseAtivo) -> bool:
    texto = f"{a.ativo.nome} {a.ativo.ticker}".upper()
    return "SELIC" in texto or "LFT" in texto.split()


def grupo(a: m.AnaliseAtivo) -> str:
    """Em qual bloco da página o ativo aparece. Puro.

    Reserva de emergência é o Tesouro Selic (o do rascunho) ou o que o
    usuário declarar com esse papel. O resto da renda fixa é "Renda fixa".
    """
    if (any(p.codigo == "emergency_reserve" for p in a.papeis)
            or (a.ativo.classe_politica == "renda_fixa" and _eh_selic(a))):
        return RESERVA
    cp = a.ativo.classe_politica
    if cp in (RENDA_FIXA, ACOES, FIIS, EXTERIOR):
        return cp
    return OUTROS


@dataclass(frozen=True)
class Decisao:
    codigo: str          # uma das chaves de ROTULO_DECISAO
    detalhe: str         # a ação em poucas palavras
    motivo: str          # a justificativa principal
    avaliacao: av_.Avaliacao | None = None
    conviccao: str = rec.MODERADA
    porque_conviccao: str = ""
    justificativas: tuple[str, ...] = ()   # os fatores pesados
    condicoes: tuple[str, ...] = ()        # o que mudaria a recomendação
    limitacoes: tuple[str, ...] = ()       # dado ausente que pesa
    alertas: tuple[str, ...] = ()          # para investigar, não são ordens
    contexto: tuple[str, ...] = ()         # cenário, notícias, valorização
    proporcao: float | None = None         # fração da posição a vender
    custos: rec.CustoVenda | None = None
    substituto: rec.Confronto | None = None

    @property
    def rotulo(self) -> str:
        return ROTULO_DECISAO[self.codigo]

    @property
    def movimenta(self) -> bool:
        return self.codigo in MOVIMENTA

    @property
    def rotulo_conviccao(self) -> str:
        return rec.ROTULO_CONVICCAO[self.conviccao]

    @property
    def argumentos(self) -> tuple[str, ...]:
        """Uma linha por dimensão avaliada, com os critérios que pesaram."""
        return av_.argumentos(self.avaliacao) if self.avaliacao else ()


def alvo_do_ativo(a: m.AnaliseAtivo,
                  sugerido: "AlvoSugerido | None" = None) -> float | None:
    """A % devida que a decisão compara: a faixa do usuário, senão a sugestão."""
    if a.faixa.alvo_ativo is not None:
        return float(a.faixa.alvo_ativo)
    return None if sugerido is None else float(sugerido.peso)


def tolerancia_pp(alvo: float) -> float:
    """Folga em torno da % devida antes de pedir mudança: 20% do alvo, no
    mínimo meio ponto. Sem ela, todo ativo viraria comprar ou rebalancear por
    centésimos."""
    return max(0.5, 0.2 * alvo)


def decisao(a: m.AnaliseAtivo,
            sugerido: "AlvoSugerido | None" = None,
            avaliacao: av_.Avaliacao | None = None,
            na_carteira=None,
            ctx: m.ContextoInvestidor | None = None) -> Decisao:
    """Manter, comprar, acompanhar, rebalancear, reduzir parcialmente,
    vender ou substituir, ativo a ativo. Puro.

    Pedido do usuário (29/10/2026): nada de vender uma empresa de qualidade
    porque a cotação ou o peso passou de um limite. A árvore, nesta ordem:

    1. Alerta eliminatório do balanço → vender, ou substituir se um par fora
       da carteira for claramente superior (``rec.confrontar``). Alerta só
       do noticiário → acompanhar: a manchete pede confirmação oficial.
    2. Gatilho da tese disparado → reduzir 1/3; com qualidade frágil, sair.
    3. Ativo fora da função na carteira → acompanhar (reavaliar a tese); com
       qualidade frágil, sair.
    4. Qualidade frágil → substituir se houver par superior; reduzir parte se
       o preço também está caro ou o peso passou do devido; senão acompanhar.
    5. Empresa sólida:
       * acima da % devida ou de um limite → rebalancear pelos aportes, sem
         vender. Reduzir parte só se o preço também esticou contra o próprio
         histórico, ou se o peso passou de 1,5× o limite por ativo;
       * abaixo → comprar (salvo classe acima do alvo ou preço muito
         esticado);
       * na % devida → manter; realizar ~20% só com o preço muito esticado.

    ``na_carteira`` (os tickers da carteira) tira os pares que já estão nela
    da busca de substituto; ``ctx`` traz o cenário para o contexto e as
    limitações. Cada decisão sai com convicção, condições que a mudariam,
    limitações, alertas para investigar (separados da ação) e, quando vende,
    a proporção e o custo estimado da venda.
    """
    av = avaliacao if avaliacao is not None else av_.avaliar(a)
    s = _situacao(a, sugerido, av, na_carteira)
    return _completar(a, s, av, _escolher(a, s, av), ctx)


# -- o que a árvore olha --------------------------------------------------------------

@dataclass(frozen=True)
class _Situacao:
    atual: float
    alvo: float | None
    dif: float | None                # atual − alvo, em p.p.
    acima: bool                      # além da folga da % devida
    abaixo: bool
    concentracao: tuple[str, ...]    # limites estourados (ativo, setor, classe)
    excesso_teto: float | None       # atual − limite por ativo, se acima
    severa: bool                     # acima de FATOR_TETO × limite por ativo
    fora: str | None                 # a função na carteira não se cumpre
    quebra: tuple[str, ...]          # gatilhos de fundamentos disparados
    est: rec.Esticamento
    confronto: rec.Confronto | None

    @property
    def pesado(self) -> bool:
        return self.acima or bool(self.concentracao)

    @property
    def excesso(self) -> float | None:
        """O maior excesso de peso, em p.p., sobre a % devida ou o limite."""
        xs = [x for x in ((self.dif if self.acima else None),
                          self.excesso_teto) if x]
        return max(xs) if xs else None


def _sem_prefixo(texto: str, prefixo: str) -> str:
    if texto.startswith(prefixo):
        texto = texto[len(prefixo):]
    return texto.strip().rstrip(".")


def _situacao(a: m.AnaliseAtivo, sugerido, av: av_.Avaliacao,
              na_carteira) -> _Situacao:
    atual = float(a.ativo.peso_atual)
    alvo = alvo_do_ativo(a, sugerido)
    dif = None if alvo is None else atual - alvo
    folga = None if alvo is None else tolerancia_pp(alvo)
    gat = {g.codigo: g for g in a.tese.gatilhos}
    just = a.acao.justificativas

    conc: list[str] = []
    gc = gat.get("concentracao")
    if gc is not None and gc.disparado:
        conc += [t.strip() for t in gc.detalhe.split(";") if t.strip()]
    conc += [_sem_prefixo(j, "") for j in just if j.startswith("A classe soma")]
    if not conc and a.acao.estado == m.REDUZIR_CONCENTRACAO:
        conc.append(_sem_prefixo(just[0], "Concentração:") if just
                    else "concentração acima do seu limite")
    teto = a.faixa.teto_ativo
    excesso_teto = (atual - float(teto)
                    if teto is not None and atual > float(teto) else None)

    gf = gat.get("funcao_original")
    fora = gf.detalhe if gf is not None and gf.disparado else None
    if fora is None and a.acao.estado == m.REAVALIAR_TESE:
        fora = next((_sem_prefixo(j, "Tese em dúvida:") for j in just
                     if j.startswith("Tese em dúvida:")),
                    _sem_prefixo(just[0], "") if just
                    else "a tese está em dúvida")
    quebra = tuple(g.descricao + (f": {g.detalhe}" if g.detalhe else "")
                   for g in a.tese.gatilhos
                   if g.codigo in _GATILHOS_FUNDAMENTOS and g.disparado)

    confronto = None
    if av.criticos or av.qualidade == av_.FRAGIL or quebra:
        # A troca só é procurada quando sair já está em pauta: "outro
        # parece melhor" não é motivo de venda.
        fora_da_busca = set(na_carteira or ()) | {a.ativo.ticker}
        confronto = rec.melhor_substituto(
            a, substitutos(a, fora_da_busca, N_CANDIDATOS_TROCA))
    return _Situacao(
        atual, alvo, dif, dif is not None and dif > folga,
        dif is not None and dif < -folga, tuple(dict.fromkeys(conc)),
        excesso_teto, teto is not None and atual > FATOR_TETO * float(teto),
        fora, quebra, rec.esticamento(a, av), confronto)


# -- a árvore ------------------------------------------------------------------------

@dataclass(frozen=True)
class _Rascunho:
    codigo: str
    detalhe: str
    motivo: str
    razoes: tuple[str, ...] = ()        # sinais a favor próprios do ramo
    condicoes: tuple[str, ...] = ()
    proporcao: float | None = None
    substituto: rec.Confronto | None = None
    teto: str | None = None             # convicção máxima
    motivo_teto: str = ""


def _motivo_qualidade(av: av_.Avaliacao) -> str:
    contra = [c.texto for c in av.dimensao(av_.QUALIDADE).criterios
              if c.sinal < 0][:2]
    return ("Qualidade frágil: " + "; ".join(contra) + ".") if contra else (
        "Qualidade frágil.")


def _motivo_acao(a: m.AnaliseAtivo) -> str:
    return a.acao.justificativas[0] if a.acao.justificativas else ""


def _frase_solida(av: av_.Avaliacao) -> str:
    if av.qualidade in (av_.FORTE, av_.ADEQUADA):
        return (f"A qualidade é {av_.ROTULO_LEITURA[av.qualidade].lower()} e "
                "nenhum gatilho da tese disparou")
    return ("Sem dado para medir a qualidade e nenhum gatilho da tese "
            "disparou")


def _frase_preco(s: _Situacao) -> str:
    if s.est.nivel:
        return f"o preço está {s.est.rotulo} contra o próprio histórico"
    if s.est.medido:
        return "o preço não está esticado contra o próprio histórico"
    return "não há histórico de múltiplos para dizer se o preço esticou"


def _arredonda(fracao: float) -> float:
    if fracao >= FRACAO_MAX_REDUCAO - 1e-9:
        return FRACAO_MAX_REDUCAO
    return max(0.05, round(fracao * 20) / 20)


def _texto_fracao(fracao: float) -> str:
    if abs(fracao - FRACAO_MAX_REDUCAO) < 1e-9:
        return "cerca de 1/3"
    return f"cerca de {fracao * 100:.0f}%"


def _reduzir(fracao: float, razao: str, motivo: str, razoes=(),
             condicoes=()) -> _Rascunho:
    fr = _arredonda(fracao)
    return _Rascunho(REDUZIR_PARCIAL,
                     f"reduzir {_texto_fracao(fr)} da posição: {razao}",
                     motivo, tuple(razoes), tuple(condicoes), proporcao=fr)


def _sair(s: _Situacao, razao: str, motivo: str, razoes=(),
          condicoes=()) -> _Rascunho:
    cf = s.confronto
    if cf is not None:
        return _Rascunho(
            SUBSTITUIR, f"substituir por {cf.ticker}: {razao}",
            f"{motivo} {cf.resumo}: supera na qualidade sem perder no preço "
            "nem no risco.",
            tuple(razoes) + (f"{cf.ticker} claramente superior",),
            tuple(condicoes) + (
                f"Se {cf.ticker} perder a vantagem na qualidade, ou ficar mais "
                "caro ou mais arriscado que o ativo, a troca deixa de valer e "
                "a saída vira venda simples.",),
            proporcao=1.0, substituto=cf, teto=rec.MODERADA,
            motivo_teto=(f"{cf.ticker} foi comparado só pelas métricas "
                         "publicadas dos pares, sem a avaliação completa"))
    return _Rascunho(
        VENDER, f"vender: {razao}",
        f"{motivo} Nenhum par fora da carteira é claramente superior: a "
        "saída não vem com troca.", tuple(razoes), tuple(condicoes),
        proporcao=1.0)


def _escolher(a: m.AnaliseAtivo, s: _Situacao,
              av: av_.Avaliacao) -> _Rascunho:
    if av.criticos:
        return _por_alerta(s, av)
    fragil = av.qualidade == av_.FRAGIL
    if s.quebra:
        if fragil:
            return _sair(s, "tese quebrada e qualidade frágil",
                         f"Gatilho da tese disparado: {s.quebra[0]}. "
                         f"{_motivo_qualidade(av)}",
                         ("gatilho da tese disparado",))
        return _reduzir(
            FRACAO_MAX_REDUCAO, "gatilho da tese disparado",
            f"Gatilho da tese disparado: {s.quebra[0]}. A qualidade ainda não "
            "é frágil: reduzir parte e esperar o próximo balanço antes de "
            "decidir o resto.", ("gatilho da tese disparado",),
            ("Se o próximo balanço mostrar que o gatilho foi pontual, a "
             "recomendação volta a manter.",))
    if s.fora:
        if fragil:
            return _sair(s, "fora da função e qualidade frágil",
                         f"Tese em dúvida: {s.fora}. {_motivo_qualidade(av)}",
                         ("a função na carteira não se cumpre",))
        return _Rascunho(
            ACOMPANHAR, "reavaliar a tese: o ativo deixou de cumprir a função",
            f"Tese em dúvida: {s.fora}. A qualidade não é frágil: a dúvida é "
            "sobre o papel do ativo na carteira, não sobre a empresa.",
            ("a função na carteira não se cumpre",),
            ("Se a saída for intencional, reduzir em etapas (cerca de 1/3 por "
             "vez), pesando o imposto de cada etapa.",
             "Se o papel do ativo mudou de propósito, atualizar a estratégia "
             "desfaz a dúvida."))
    if fragil:
        return _fragil(s, av)
    return _solida(a, s, av)


def _por_alerta(s: _Situacao, av: av_.Avaliacao) -> _Rascunho:
    al = av.criticos[0]
    texto = f"{al.texto[:1].upper()}{al.texto[1:]}"
    if all(x.noticia for x in av.criticos):
        return _Rascunho(
            ACOMPANHAR, "investigar já: alerta eliminatório no noticiário",
            f"{texto}. Manchete não é fato confirmado (o nome citado pode não "
            "ser o sujeito): a venda espera o documento oficial.",
            ("alerta eliminatório no noticiário",),
            ("Se o fato relevante ou o comunicado à CVM confirmar, a "
             "recomendação passa a vender, ou substituir se houver par "
             "claramente superior.",
             "Se a notícia for desmentida ou não tratar desta empresa, o "
             "alerta cai e a recomendação volta a seguir fundamentos, preço e "
             "peso."),
            teto=rec.MODERADA,
            motivo_teto=("o alerta veio só do noticiário e não foi confirmado "
                         "em documento oficial"))
    return _sair(s, "alerta eliminatório",
                 f"{texto}. Nenhum ponto bom compensa esse alerta.",
                 ("alerta eliminatório",),
                 ("Se o alerta for resolvido (por exemplo, a dívida voltar "
                  "para baixo do teto do setor), a recomendação volta a "
                  "seguir fundamentos, preço e peso.",))


def _fragil(s: _Situacao, av: av_.Avaliacao) -> _Rascunho:
    mq = _motivo_qualidade(av)
    razoes = ("qualidade frágil",)
    if s.confronto is not None:
        return _sair(s, "qualidade frágil", mq, razoes)
    if s.est.nivel or av.preco == av_.CARO:
        return _reduzir(
            FRACAO_MAX_REDUCAO, "qualidade frágil e preço acima do normal",
            f"{mq} O preço ainda está acima do normal: reduzir parte realiza "
            "esse preço sem apostar a posição inteira na piora.", razoes,
            ("Se a qualidade voltar a adequada no próximo balanço, a "
             "recomendação volta a manter.",))
    if s.pesado and s.excesso:
        return _reduzir(
            s.excesso / s.atual, "qualidade frágil e peso acima do devido",
            f"{mq} Com a qualidade frágil, vender o excesso é mais prudente "
            "que esperar os aportes trazerem o peso de volta.", razoes)
    if av.mercado == av_.NEGATIVO:
        return _Rascunho(
            ACOMPANHAR, "acompanhar: qualidade frágil e mercado negativo",
            f"{mq} O noticiário também pesa contra: ler as notícias e o "
            "próximo balanço antes de decidir.", razoes,
            ("Se o próximo balanço confirmar a piora, ou surgir par "
             "claramente superior, a recomendação passa a reduzir ou "
             "substituir.",))
    return _Rascunho(
        ACOMPANHAR, "qualidade frágil: não aportar por ora", mq, razoes,
        ("Se a qualidade voltar a adequada, os aportes voltam a caber; se "
         "piorar com preço caro, ou surgir par claramente superior, a "
         "recomendação passa a reduzir ou substituir.",))


def _comprar(detalhe: str, motivo: str, s: _Situacao,
             av: av_.Avaliacao, razao: str) -> _Rascunho:
    notas = []
    if av.qualidade == av_.FORTE and av.preco == av_.BARATO:
        notas.append("qualidade forte e preço abaixo do normal")
    elif s.est.nivel or av.preco == av_.CARO:
        notas.append("preço acima do normal: aportar aos poucos")
    if av.mercado == av_.NEGATIVO:
        notas.append("mercado e notícias negativos: conferir antes")
    return _Rascunho(COMPRAR, "; ".join([detalhe] + notas), motivo, (razao,))


def _solida(a: m.AnaliseAtivo, s: _Situacao,
            av: av_.Avaliacao) -> _Rascunho:
    base = _frase_solida(av)
    devida = f"% devida {_pct(s.alvo)}"
    contra_alvo = (f" contra {_pct(s.alvo)} devidos" if s.alvo is not None
                   else "")
    if s.pesado:
        peso_txt = (f"{_pp(s.dif)[1:]} acima da {devida}" if s.acima
                    else s.concentracao[0])
        if s.est.nivel:
            fr = (s.excesso / s.atual if s.excesso
                  else FRACAO_REDUCAO_ESTICADO)
            return _reduzir(
                fr, f"peso acima do devido e preço {s.est.rotulo}",
                f"Pesa {_pct(s.atual)} da carteira{contra_alvo}. {base}, mas "
                f"{_frase_preco(s)} ({s.est.evidencias[0]}): reduzir o "
                "excesso realiza parte do ganho sem abandonar a tese.",
                ("peso acima do devido",))
        if s.severa:
            teto = float(a.faixa.teto_ativo)
            return _reduzir(
                s.excesso_teto / s.atual,
                f"{_pct(s.atual)} contra o limite de {_pct(teto)}",
                f"Pesa {_pct(s.atual)}, mais de {av_._br(FATOR_TETO)}× o seu "
                f"limite por ativo ({_pct(teto)}). {base}; {_frase_preco(s)}"
                ", mas acima desse ponto o sistema não conta só com os "
                "aportes para corrigir o peso.", ("peso muito acima do limite",))
        return _Rascunho(
            REBALANCEAR, f"rebalancear: {peso_txt}",
            f"Pesa {_pct(s.atual)} da carteira{contra_alvo}. {base}; "
            f"{_frase_preco(s)}: parar de aportar nele e aportar no resto da "
            "classe reduz o peso sem vender, sem imposto nem custo de saída.",
            ("peso acima do devido",))
    if s.abaixo:
        falta = f"faltam {_pp(-s.dif)[1:]} para a {devida}"
        if a.acao.estado == m.REAVALIAR_APORTES:
            return _Rascunho(
                MANTER, f"abaixo da {devida}, mas a classe já passou do alvo",
                _motivo_acao(a), ("classe acima do alvo",))
        if s.est.nivel == rec.MUITO_ESTICADO:
            return _Rascunho(
                MANTER, f"{falta}, mas o preço está muito esticado: esperar "
                "para aportar",
                f"Pesa {_pct(s.atual)} contra {_pct(s.alvo)} devidos, mas "
                f"{s.est.evidencias[0]}. Aportar no topo do histórico encarece "
                "a posição.", ("tese válida",))
        return _comprar(falta, f"Pesa {_pct(s.atual)} da carteira contra "
                        f"{_pct(s.alvo)} devidos.", s, av, "abaixo da % devida")

    codigo, detalhe = DECISAO.get(a.acao.estado, (MANTER, a.acao.rotulo))
    if s.est.nivel == rec.MUITO_ESTICADO:
        return _reduzir(
            FRACAO_REDUCAO_ESTICADO, "preço muito esticado; manter o núcleo",
            f"{base}, mas {_frase_preco(s)} ("
            f"{'; '.join(s.est.evidencias[:2])}). Realizar uma parte trava "
            "parte do ganho e mantém o núcleo da posição na tese.")
    if s.est.nivel == rec.ESTICADO:
        return _Rascunho(
            MANTER, "manter, sem novos aportes por ora: preço esticado",
            f"{base}; {_frase_preco(s)} ({s.est.evidencias[0]}), mas não a "
            "ponto de realizar: manter e direcionar os aportes para outro "
            "ativo.", ("tese válida",))
    if codigo == COMPRAR:
        return _comprar(detalhe, _motivo_acao(a), s, av, "classe abaixo do alvo")
    if codigo == ACOMPANHAR:
        return _Rascunho(ACOMPANHAR, detalhe, _motivo_acao(a))
    if av.mercado == av_.NEGATIVO:
        return _Rascunho(
            ACOMPANHAR, "acompanhar: mercado e notícias negativos",
            f"{base}, mas o noticiário e o risco de mercado pesam contra: ler "
            "as notícias antes de aportar ou mexer.")
    if s.alvo is not None:
        return _Rascunho(MANTER, f"na {devida}", _motivo_acao(a) or (
            f"Pesa {_pct(s.atual)}, dentro da folga da % devida."),
            ("peso na % devida",))
    return _Rascunho(codigo, detalhe, _motivo_acao(a))


# -- o que acompanha a recomendação --------------------------------------------------

def _sinais(s: _Situacao, av: av_.Avaliacao) -> tuple[list[str], list[str]]:
    """(pedem cautela, sustentam a posição)."""
    cautela, sustentam = [], []
    if av.criticos:
        cautela.append("alerta eliminatório no noticiário"
                       if all(x.noticia for x in av.criticos)
                       else "alerta eliminatório")
    elif av.qualidade == av_.FRAGIL:
        cautela.append("qualidade frágil")
    if s.est.nivel:
        cautela.append(f"preço {s.est.rotulo} contra o próprio histórico")
    elif av.preco == av_.CARO:
        cautela.append("preço acima do normal")
    if av.mercado == av_.NEGATIVO:
        cautela.append("mercado e notícias negativos")
    if s.quebra:
        cautela.append("gatilho da tese disparado")
    if av.qualidade in (av_.FORTE, av_.ADEQUADA):
        sustentam.append(
            f"qualidade {av_.ROTULO_LEITURA[av.qualidade].lower()}")
    if av.preco == av_.BARATO:
        sustentam.append("preço abaixo do normal")
    elif av.preco == av_.JUSTO:
        sustentam.append("preço em linha com o normal")
    if av.mercado == av_.POSITIVO:
        sustentam.append("mercado e notícias positivos")
    return cautela, sustentam


def _conviccao(s: _Situacao, av: av_.Avaliacao,
               r: _Rascunho) -> rec.Conviccao:
    cautela, sustentam = _sinais(s, av)
    razoes = list(r.razoes)
    if r.codigo in (VENDER, SUBSTITUIR, ACOMPANHAR):
        favor, contra = razoes + cautela, sustentam
    elif r.codigo == REDUZIR_PARCIAL:
        # Qualidade boa não pesa contra reduzir parte: é por ela que a
        # redução é parcial.
        favor = razoes + cautela
        contra = [x for x in sustentam if not x.startswith("qualidade")]
    elif r.codigo == REBALANCEAR:
        favor = razoes + [x for x in sustentam if x.startswith("qualidade")]
        contra = cautela
    else:
        favor, contra = razoes + sustentam, cautela
    teto, motivo = r.teto, r.motivo_teto
    if teto is None and r.codigo == ACOMPANHAR:
        teto = rec.MODERADA
        motivo = ("acompanhar é provisório: depende do que a investigação "
                  "encontrar")
    return rec.conviccao(list(dict.fromkeys(favor)),
                         list(dict.fromkeys(contra)), av, teto, motivo)


def _justificativas(a: m.AnaliseAtivo, s: _Situacao,
                    av: av_.Avaliacao) -> tuple[str, ...]:
    out = [x for x in av_.argumentos(av) if not x.startswith("Alerta")]
    if s.alvo is not None:
        out.append(f"Peso na carteira: {_pct(s.atual)} contra "
                   f"{_pct(s.alvo)} devidos ({_pp(s.dif)}).")
    else:
        out.append(f"Peso na carteira: {_pct(s.atual)}, sem % devida para "
                   "comparar.")
    if s.quebra:
        out.append(f"Tese: gatilho disparado — {s.quebra[0]}.")
    elif s.fora:
        out.append(f"Tese: em dúvida — {s.fora}.")
    else:
        pend = any(g.estado == m.PENDENTE for g in a.tese.gatilhos)
        out.append("Tese: nenhum gatilho avaliado disparou"
                   + (" (os de fundamentos ainda não são avaliados)"
                      if pend else "") + ".")
    return tuple(dict.fromkeys(out))


def _condicoes(a: m.AnaliseAtivo, s: _Situacao,
               r: _Rascunho) -> tuple[str, ...]:
    c = list(r.condicoes)
    parado = r.codigo in (MANTER, COMPRAR, REBALANCEAR, ACOMPANHAR)
    if parado:
        c.append("Se a qualidade piorar (alerta eliminatório, qualidade "
                 "frágil) ou um gatilho da tese disparar, a recomendação "
                 "passa a reduzir ou vender.")
    if r.codigo in (MANTER, COMPRAR) and not s.est.nivel and s.est.medido:
        c.append("Se os múltiplos chegarem ao topo do próprio histórico "
                 f"(percentil {rec.PERCENTIL_MUITO_ESTICADO:.0f}) com o preço "
                 "caro, cabe realizar parte.")
    if r.codigo in (MANTER, COMPRAR) and s.alvo is not None:
        c.append(f"Se o peso passar de {_pct(s.alvo + tolerancia_pp(s.alvo))}"
                 ", a recomendação passa a rebalancear pelos aportes.")
    if r.codigo == COMPRAR and s.alvo is not None:
        c.append(f"Ao chegar à % devida ({_pct(s.alvo)}), a recomendação "
                 "volta a manter.")
    if r.codigo == REBALANCEAR:
        c.append("Se o peso voltar para dentro da folga da % devida e dos "
                 "limites, a recomendação volta a manter.")
        if s.est.medido:
            c.append("Se o preço esticar contra o próprio histórico, cabe "
                     "reduzir parte em vez de só redirecionar os aportes.")
        teto = a.faixa.teto_ativo
        if teto is not None and not s.severa:
            c.append(f"Se o peso passar de {_pct(FATOR_TETO * float(teto))} "
                     f"({av_._br(FATOR_TETO)}× o seu limite por ativo), cabe "
                     "reduzir parte.")
    if r.codigo == REDUZIR_PARCIAL:
        c.append("Se o preço voltar para perto da mediana do próprio "
                 "histórico, ou o peso voltar à % devida, o resto da posição "
                 "fica.")
        c.append("Se os fundamentos piorarem ou a tese quebrar, a "
                 "recomendação passa a vender.")
    if r.codigo in MOVIMENTA and a.ativo.classe_politica == ACOES:
        c.append("Vendas de ações acima de R$ 20 mil no mês pagam 15% de IR "
                 "sobre o ganho: dividir a venda entre meses pode preservar "
                 "a isenção.")
    return tuple(dict.fromkeys(c))


_DIMENSAO_DA_FALTA = {"qualidade": av_.QUALIDADE, "valuation": av_.VALUATION,
                      "mercado": av_.MERCADO}


def _limitacoes(a: m.AnaliseAtivo, s: _Situacao, av: av_.Avaliacao,
                r: _Rascunho, custos: rec.CustoVenda | None,
                ctx) -> tuple[str, ...]:
    out = []
    for f in rec.cobertura(av)[1]:
        nota = av.dimensao(_DIMENSAO_DA_FALTA[f]).nota
        out.append(f"Sem leitura de {f}."
                   + (f" {nota.rstrip('.')}." if nota else ""))
    pend = [g.descricao.lower() for g in a.tese.gatilhos
            if g.estado == m.PENDENTE]
    if pend:
        out.append(f"Gatilhos da tese ainda não avaliados ({', '.join(pend)})"
                   ": a quebra da tese só aparece pelos alertas e pela função "
                   "na carteira.")
    if not s.est.medido:
        out.append("Sem percentil do próprio histórico nos múltiplos: não dá "
                   "para dizer se o preço esticou.")
    if s.alvo is None:
        out.append("Sem % devida para o ativo: o peso entrou só pelos limites "
                   "da estratégia.")
    if r.proporcao is not None and r.proporcao < 1.0:
        out.append("A proporção sugerida é convenção do sistema, não "
                   "calibrada: no máximo 1/3 da posição por vez, e 20% quando "
                   "o motivo é só o preço esticado.")
    if custos is not None:
        out += list(custos.limitacoes)
    if r.substituto is not None:
        out.append(f"{r.substituto.ticker} foi comparado só pelas métricas "
                   "publicadas dos pares: notícias, alertas e o histórico de "
                   "valuation dele não entraram.")
    if ctx is not None and macro(a, ctx).sem_cenario:
        out.append("Cenário econômico não lido: o contexto macro não entrou "
                   "nesta recomendação.")
    return tuple(dict.fromkeys(out))


def _alertas(s: _Situacao, av: av_.Avaliacao) -> tuple[str, ...]:
    """Sinais que pedem olhar mais de perto. Nenhum é ordem de venda."""
    out = []
    if s.acima:
        out.append(f"Peso de {_pct(s.atual)} contra {_pct(s.alvo)} devidos "
                   f"({_pp(s.dif)}): rever os aportes, não vender por isso.")
    out += [f"Concentração: {c}." for c in s.concentracao]
    out += [f"Preço no topo do histórico: {e}." for e in s.est.evidencias]
    if (s.est.excesso_pp is not None
            and s.est.excesso_pp >= av_.MOMENTO_DIFERENCA_PP):
        out.append(f"Valorização forte: {s.est.valorizacao}. Subida sozinha "
                   "não é motivo de venda: conferir se os fundamentos "
                   "acompanharam.")
    for al in av.alertas:
        texto = f"{al.texto[:1].upper()}{al.texto[1:]}"
        if al.critico and al.noticia:
            out.append(f"A confirmar em documento oficial: {al.texto}.")
        elif not al.critico:
            out.append(f"{texto}.")
    if av.mercado == av_.NEGATIVO:
        out.append("Mercado e notícias negativos: ler as notícias antes de "
                   "qualquer movimento.")
    if s.fora:
        out.append(f"Função na carteira: {s.fora}.")
    return tuple(dict.fromkeys(out))


def _contexto(a: m.AnaliseAtivo, s: _Situacao, av: av_.Avaliacao,
              ctx) -> tuple[str, ...]:
    """Cenário, tom do noticiário e valorização: o pano de fundo."""
    out = []
    if ctx is not None:
        mc = macro(a, ctx)
        if mc.premissas:
            out.append("Cenário que mais pesa na classe: "
                       + "; ".join(mc.premissas[:3]) + ".")
        out += [f"Sinal do cenário: {x}" for x in mc.sinais[:2]]
    out += [f"{c.texto}." for c in av.dimensao(av_.MERCADO).criterios
            if "notícia" in c.texto or "noticiário" in c.texto][:2]
    if s.est.valorizacao:
        out.append(f"{s.est.valorizacao}.")
    return tuple(dict.fromkeys(out))


def _completar(a: m.AnaliseAtivo, s: _Situacao, av: av_.Avaliacao,
               r: _Rascunho, ctx) -> Decisao:
    custos = (rec.custo_da_venda(a, r.proporcao)
              if r.codigo in MOVIMENTA and r.proporcao else None)
    cv = _conviccao(s, av, r)
    return Decisao(
        r.codigo, r.detalhe, r.motivo, av, conviccao=cv.grau,
        porque_conviccao=cv.porque,
        justificativas=_justificativas(a, s, av),
        condicoes=_condicoes(a, s, r),
        limitacoes=_limitacoes(a, s, av, r, custos, ctx),
        alertas=_alertas(s, av), contexto=_contexto(a, s, av, ctx),
        proporcao=r.proporcao, custos=custos, substituto=r.substituto)


# -- porcentagem devida ------------------------------------------------------------

def _pct(x: float | None, casas: int = 1) -> str:
    return "—" if x is None else f"{x:.{casas}f}%".replace(".", ",")


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f} pp".replace(".", ",")


@dataclass(frozen=True)
class PesoDevido:
    valor: str           # o número principal ("5,0%" ou "até 10%")
    detalhe: str         # de onde vem


@dataclass(frozen=True)
class AlvoSugerido:
    """Quanto o ativo deveria pesar para a classe ficar equilibrada em risco.

    O alvo da classe vem da sua estratégia; a divisão entre os ativos da
    classe é do sistema (30/09/2026, pedido do usuário: "deve-se sim sugerir
    a porcentagem que o ativo deve atingir"). Cada ativo recebe uma fatia
    proporcional ao inverso da volatilidade, para que todos contribuam com
    risco parecido. O limite por ativo da estratégia é teto, e o que passa
    dele vai para os outros.
    """
    peso: float                   # % da carteira
    volatilidade: float | None    # % a.a. usada no cálculo (None = mediana)
    limitado: bool                # bateu no limite por ativo
    alvo_classe: float
    metodo: str                   # "inverso da volatilidade" | "pesos iguais"


METODO_VOL = "inverso da volatilidade"
METODO_IGUAL = "pesos iguais"


def volatilidade(a: m.AnaliseAtivo) -> float | None:
    """Volatilidade anualizada do ativo (%), lida da comparação com pares."""
    c = _comparacao(a)
    if c is None:
        return None
    for ln in c.linhas:
        if ln.chave == "volatilidade" and _tem_valor(ln.valor) and ln.valor > 0:
            return float(ln.valor)
    return None


def _distribuir(cotas: dict[str, float], total: float,
                tetos: dict[str, float | None]) -> tuple[dict[str, float], set]:
    """Divide ``total`` proporcionalmente às cotas, respeitando tetos; o que
    sobra de quem bateu no teto vai para os demais (water-filling)."""
    pesos: dict[str, float] = {}
    presos: set[str] = set()
    livres = dict(cotas)
    resto = total
    while livres:
        soma = sum(livres.values())
        tentativa = {k: resto * v / soma for k, v in livres.items()}
        estourou = {k for k, w in tentativa.items()
                    if tetos.get(k) is not None and w > tetos[k] + 1e-9}
        if not estourou:
            pesos.update(tentativa)
            break
        for k in estourou:
            pesos[k] = float(tetos[k])
            resto -= pesos[k]
            presos.add(k)
            livres.pop(k)
    return pesos, presos


def alvos_sugeridos(b: "Bloco") -> dict[str, AlvoSugerido]:
    """Alvo sugerido de cada ativo do bloco. Vazio sem alvo da classe. Puro.

    Sem volatilidade de um ativo, ele recebe a mediana dos demais; sem
    nenhuma, os pesos ficam iguais. Reserva e renda fixa não entram: lá a
    estratégia fala em classe e em meses de reserva, não em risco de preço.
    """
    if b.alvo_classe is None or b.chave in GRUPOS_EM_LISTA or not b.analises:
        return {}
    vols = {a.ativo.ticker: volatilidade(a) for a in b.analises}
    conhecidas = sorted(v for v in vols.values() if v)
    if conhecidas:
        meio = len(conhecidas) // 2
        mediana = (conhecidas[meio] if len(conhecidas) % 2
                   else (conhecidas[meio - 1] + conhecidas[meio]) / 2)
        cotas = {tk: 1.0 / (v or mediana) for tk, v in vols.items()}
        metodo = METODO_VOL
    else:
        cotas = {tk: 1.0 for tk in vols}
        metodo = METODO_IGUAL
    tetos = {a.ativo.ticker: a.faixa.teto_ativo for a in b.analises}
    pesos, presos = _distribuir(cotas, float(b.alvo_classe), tetos)
    return {tk: AlvoSugerido(pesos[tk], vols[tk], tk in presos,
                             float(b.alvo_classe), metodo)
            for tk in vols}


def peso_devido(a: m.AnaliseAtivo,
                sugerido: AlvoSugerido | None = None) -> PesoDevido:
    """O quanto o ativo deveria pesar.

    1. Faixa que o usuário definiu para o ativo, se houver.
    2. Senão, o alvo sugerido (alvo da classe dividido por risco).
    3. Sem alvo da classe, só o limite por ativo, ou nada.
    """
    fx = a.faixa
    if fx.alvo_ativo is not None:
        return PesoDevido(_pct(fx.alvo_ativo),
                          f"alvo que você definiu (faixa {_pct(fx.piso_ativo)}"
                          f" a {_pct(fx.teto_ativo)})")
    if sugerido is not None:
        dif = sugerido.peso - a.ativo.peso_atual
        if abs(dif) < 0.5:
            onde = "já está no alvo"
        elif dif > 0:
            onde = f"faltam {_pp(dif)[1:]}"
        else:
            onde = f"{_pp(-dif)[1:]} acima"
        base = (f"inverso da volatilidade ({_pct(sugerido.volatilidade)} a.a.)"
                if sugerido.metodo == METODO_VOL and sugerido.volatilidade
                else "volatilidade mediana da classe"
                if sugerido.metodo == METODO_VOL else "pesos iguais")
        teto = (f"; travado no limite por ativo de {_pct(fx.teto_ativo, 0)}"
                if sugerido.limitado else "")
        return PesoDevido(_pct(sugerido.peso),
                          f"sugestão: alvo da classe ({_pct(sugerido.alvo_classe, 0)}"
                          f") dividido pelo {base}{teto}. {onde.capitalize()}.")
    if fx.teto_ativo is not None:
        return PesoDevido(f"até {_pct(fx.teto_ativo, 0)}",
                          "limite por ativo da sua estratégia; folga de "
                          f"{_pp(fx.folga_ativo)}. A estratégia não tem alvo "
                          "para a classe.")
    return PesoDevido("sem alvo",
                      "a sua estratégia não define alvo para esta classe")


# -- blocos da página ---------------------------------------------------------------

@dataclass(frozen=True)
class Linha:
    ticker: str
    nome: str
    moeda: str
    valor: float | None
    peso: float


@dataclass(frozen=True)
class Bloco:
    chave: str
    rotulo: str
    peso: float                         # % da carteira
    valor: float                        # soma do valor de mercado
    alvo_classe: float | None           # alvo da classe da política
    peso_classe: float | None           # % atual da classe inteira
    analises: tuple[m.AnaliseAtivo, ...]

    @property
    def alvos(self) -> dict[str, AlvoSugerido]:
        return alvos_sugeridos(self)

    @property
    def linhas(self) -> tuple[Linha, ...]:
        return tuple(Linha(a.ativo.ticker, a.ativo.nome, a.ativo.moeda,
                           a.ativo.valor_mercado, a.ativo.peso_atual)
                     for a in self.analises)


def blocos(analises, ctx: m.ContextoInvestidor) -> list[Bloco]:
    """Os ativos agrupados na ordem da página, cada grupo por peso. Puro."""
    por_grupo: dict[str, list[m.AnaliseAtivo]] = {}
    for a in analises:
        por_grupo.setdefault(grupo(a), []).append(a)
    saida = []
    for chave, rotulo in GRUPOS:
        itens = sorted(por_grupo.get(chave, ()),
                       key=lambda a: (-a.ativo.peso_atual, a.ativo.ticker))
        if not itens:
            continue
        classe = "renda_fixa" if chave == RESERVA else chave
        saida.append(Bloco(
            chave, rotulo,
            peso=sum(a.ativo.peso_atual for a in itens),
            valor=sum(a.ativo.valor_mercado or 0.0 for a in itens),
            alvo_classe=ctx.alocacao_alvo.get(classe),
            peso_classe=ctx.peso_por_classe.get(classe),
            analises=tuple(itens)))
    return saida


def meta_reserva(politica: dict | None) -> str | None:
    """"6 meses de gastos" se a estratégia disser; senão None."""
    if not politica:
        return None
    meses = pol.valor(politica, "emergency_reserve_months")
    if meses:
        return f"{meses:g} meses de gastos, segundo a sua estratégia"
    if pol.valor(politica, "has_emergency_reserve") is False:
        return "A sua estratégia registra que ainda não há reserva separada"
    return None


# -- pares e substitutos --------------------------------------------------------------

@dataclass(frozen=True)
class Ponto:
    ticker: str
    texto: str                          # valor formatado
    posicao: float                      # 0 a 100, na faixa da métrica
    eh_ativo: bool


@dataclass(frozen=True)
class MetricaPares:
    """Uma métrica no painel de comparação: o ativo, os pares na mesma
    régua e a mediana do segmento inteiro. Sem "melhor" nem "pior": a leitura
    é a neutra de ``pares.LEITURA``."""
    chave: str
    rotulo: str
    texto_ativo: str
    texto_mediana: str
    n_pares: int
    posicao: str                        # valuation.ACIMA/ABAIXO/EM_LINHA/SEM_DADO
    pontos: tuple[Ponto, ...]
    mediana_pos: float | None           # 0 a 100
    leitura: str


ROTULO_POSICAO = {prs.ACIMA: "acima da mediana", prs.ABAIXO: "abaixo da mediana",
                  prs.EM_LINHA: "em linha com a mediana",
                  prs.SEM_DADO: "sem dado do ativo"}


@dataclass(frozen=True)
class TabelaPares:
    titulo: str                         # "mesmo segmento (Bancos)"
    colunas: tuple[str, ...]            # métricas
    linhas: tuple[tuple[str, tuple[str, ...]], ...]   # (ticker, valores)
    motivo: str | None = None           # por que não há tabela
    metricas: tuple[MetricaPares, ...] = ()
    nomes: tuple[tuple[str, str], ...] = ()           # (ticker, nome)
    n_grupo: int = 0                    # pares no segmento inteiro


def _metrica_pares(ln, pares, ticker: str, moeda: str) -> MetricaPares:
    brutos = [(ticker, ln.valor, True)] + [
        (p.ticker, p.metricas.get(ln.chave), False) for p in pares]
    valores = [float(v) for _, v, _ in brutos if _tem_valor(v)]
    if _tem_valor(ln.mediana_pares):
        valores.append(float(ln.mediana_pares))
    lo, hi = (min(valores), max(valores)) if valores else (0.0, 0.0)

    def _pos(v: float) -> float:
        return 50.0 if hi - lo < 1e-12 else 100.0 * (v - lo) / (hi - lo)
    pontos = tuple(Ponto(tk, formatar(v, ln.unidade, moeda), _pos(float(v)), eh)
                   for tk, v, eh in brutos if _tem_valor(v))
    med = ln.mediana_pares if _tem_valor(ln.mediana_pares) else None
    return MetricaPares(
        ln.chave, ln.metrica,
        formatar(ln.valor, ln.unidade, moeda) if _tem_valor(ln.valor) else "—",
        formatar(med, ln.unidade, moeda) if med is not None else "—",
        ln.n_pares, ln.posicao, pontos,
        None if med is None else _pos(float(med)),
        prs.LEITURA.get(ln.chave, ""))


def _comparacao(a: m.AnaliseAtivo) -> prs.ComparacaoPares | None:
    if not a.pares.dados:
        return None
    return prs.ComparacaoPares.de_dict(a.pares.dados)


def tabela_pares(a: m.AnaliseAtivo, n: int = N_PARES_TABELA) -> TabelaPares:
    """O ativo e os ``n`` pares mais próximos, lado a lado, métrica a
    métrica. Só colunas com algum dado. Puro."""
    c = _comparacao(a)
    if c is None or not c.grupo.pares:
        motivo = ((c.motivo or c.grupo.motivo) if c else None) or a.pares.resumo
        return TabelaPares("", (), (), motivo or inf.NAO_DISPONIVEL)
    pares = sorted(c.grupo.pares, key=lambda p: p.distancia)[:n]
    metricas = [ln for ln in c.linhas
                if ln.valor is not None
                or any(_tem_valor(p.metricas.get(ln.chave)) for p in pares)]
    if not metricas:
        return TabelaPares("", (), (), "Nenhuma métrica com dado para "
                                       "comparar com os pares.")

    def _val(v, ln) -> str:
        return formatar(v, ln.unidade, c.moeda) if _tem_valor(v) else "—"
    linhas = [(a.ativo.ticker, tuple(_val(ln.valor, ln) for ln in metricas))]
    linhas += [(p.ticker, tuple(_val(p.metricas.get(ln.chave), ln)
                                for ln in metricas)) for p in pares]
    titulo = f"mesmo {c.grupo.nivel} ({c.grupo.valor})"
    nomes = ((a.ativo.ticker, a.ativo.nome or ""),) + tuple(
        (p.ticker, p.nome or "") for p in pares)
    return TabelaPares(
        titulo, tuple(ln.metrica for ln in metricas), tuple(linhas),
        metricas=tuple(_metrica_pares(ln, pares, a.ativo.ticker, c.moeda)
                       for ln in metricas),
        nomes=nomes, n_grupo=len(c.grupo.pares))


def substitutos(a: m.AnaliseAtivo, na_carteira, n: int = N_PARES_TABELA
                ) -> tuple[prs.Par, ...]:
    """Pares do mesmo grupo que não estão na carteira, os mais próximos
    primeiro. Proximidade é de perfil (porte, risco), não de qualidade."""
    c = _comparacao(a)
    if c is None:
        return ()
    fora = [p for p in c.grupo.pares if p.ticker not in set(na_carteira)]
    return tuple(sorted(fora, key=lambda p: p.distancia)[:n])


# -- notícias e relatórios ------------------------------------------------------------

def noticias(a: m.AnaliseAtivo, n: int = N_NOTICIAS):
    """(itens, motivo): as ``n`` manchetes mais recentes, ou por que não há."""
    if not a.noticias.dados:
        return (), a.noticias.resumo
    ns = inf.Noticias.de_dict(a.noticias.dados)
    itens = sorted(ns.itens, key=lambda i: i.date or "", reverse=True)[:n]
    return tuple(itens), (None if itens else (ns.motivo or a.noticias.resumo))


def noticias_setor(a: m.AnaliseAtivo, n: int = N_NOTICIAS):
    """(itens, segmento): notícias dos pares quando o ativo não tem as suas."""
    if not a.noticias.dados:
        return (), None
    ns = inf.Noticias.de_dict(a.noticias.dados)
    if ns.itens:
        return (), None
    return ns.setor[:n], ns.setor_rotulo


def precisa_noticiario_geral(a: m.AnaliseAtivo, n: int = N_NOTICIAS) -> bool:
    """A caixa nunca fica em branco: com menos de ``n`` manchetes do ativo e
    do segmento, ela completa com o cenário econômico e político."""
    return len(noticias(a, n)[0]) + len(noticias_setor(a, n)[0]) < n


# -- cenário econômico e político (complemento das notícias) --------------------------

# Canal do cenário (core.cenario.modelo) -> tipos de evento da taxonomia de
# notícias que falam dele. É a ponte entre "o que pesa na classe" e "qual
# manchete mostrar".
TIPOS_POR_CANAL: dict[str, tuple[str, ...]] = {
    "interest_rate": ("juros_politica_monetaria",),
    "interest_rate_outlook": ("juros_politica_monetaria",),
    "inflation": ("inflacao",),
    "inflation_outlook": ("inflacao",),
    "economic_activity": ("atividade_emprego",),
    "fx": ("cambio",),
    "fiscal_policy": ("fiscal_politico",),
    "credit": ("juros_politica_monetaria", "quebra_bancaria"),
    "commodities": ("commodity",),
    "global_economy": ("juros_politica_monetaria", "atividade_emprego",
                       "inflacao"),
    "geopolitical_risk": ("geopolitica", "fiscal_politico"),
    "capital_markets": ("crise_sistemica",),
}
# Fato macro que pesa em qualquer ativo, mesmo fora dos canais da classe.
TIPOS_MACRO: frozenset[str] = frozenset({
    "juros_politica_monetaria", "inflacao", "cambio", "fiscal_politico",
    "atividade_emprego", "crise_sistemica", "geopolitica", "pandemia"})
ROTULO_TIPO: dict[str, str] = {
    "juros_politica_monetaria": "juros", "inflacao": "inflação",
    "cambio": "câmbio", "fiscal_politico": "fiscal e política",
    "atividade_emprego": "atividade e emprego", "crise_sistemica": "crise",
    "geopolitica": "geopolítica", "pandemia": "saúde pública",
    "commodity": "commodities", "quebra_bancaria": "crédito e bancos"}


@dataclass(frozen=True)
class ItemCenario:
    titulo: str
    url: str | None
    veiculo: str | None
    data: str            # dd/mm/aaaa ou "—"
    tema: str            # rótulo curto do tipo de evento
    pais: str | None     # "BR", "US"... o primeiro citado


def tipos_do_ativo(classe_politica: str | None) -> tuple[str, ...]:
    """Tipos de evento na ordem do que mais pesa para a classe. Puro."""
    saida: list[str] = []
    for canal in cen.relevantes(classe_politica):
        for t in TIPOS_POR_CANAL.get(canal, ()):
            if t not in saida:
                saida.append(t)
    return tuple(saida)


def _data_item(item) -> str:
    valor = item.get("publicado_em") or item.get("coletado_em")
    if hasattr(valor, "isoformat"):
        valor = valor.isoformat()
    texto = str(valor or "")
    return inf._data_br(texto) if len(texto) >= 10 and texto[4] == "-" else "—"


def noticias_cenario(a: m.AnaliseAtivo, itens, n: int = N_NOTICIAS
                     ) -> tuple[ItemCenario, ...]:
    """As ``n`` manchetes macro e políticas que mais pesam no ativo. Puro.

    Só entra fato de cenário (juros, inflação, câmbio, fiscal, política,
    atividade, geopolítica) e, quando a classe é sensível a eles, commodities
    e crédito. Matéria centrada em outra empresa (cita ticker) fica de fora:
    o complemento é sobre o país, não sobre o vizinho. A ordem é: tema que
    pesa na classe, depois país do ativo (Brasil; EUA para exterior), depois
    a nota de relevância.
    """
    prioridade = tipos_do_ativo(a.ativo.classe_politica)
    aceitos = TIPOS_MACRO | set(prioridade)
    pais_alvo = "US" if a.ativo.classe_politica == "exterior" else "BR"
    candidatos = []
    vistos: set[str] = set()
    for item in itens or ():
        tipo = str(item.get("tipo_evento") or "")
        titulo = str(item.get("titulo") or "").strip()
        if tipo not in aceitos or not titulo or titulo in vistos:
            continue
        ent = item.get("entidades") or {}
        if not isinstance(ent, dict):
            ent = {}
        if ent.get("tickers") or item.get("com_ticker"):
            continue
        vistos.add(titulo)
        paises = list(ent.get("paises") or ())
        rank = (prioridade.index(tipo) if tipo in prioridade
                else len(prioridade))
        candidatos.append(((rank == len(prioridade), pais_alvo not in paises,
                            -float(item.get("nota") or 0)),
                           ItemCenario(titulo, item.get("url") or None,
                                       item.get("veiculo") or None,
                                       _data_item(item),
                                       ROTULO_TIPO.get(tipo, tipo),
                                       paises[0] if paises else None)))
    candidatos.sort(key=lambda c: c[0])
    return tuple(c[1] for c in candidatos[:n])


def temas_do_ativo(classe_politica: str | None) -> str:
    """"juros, câmbio, fiscal e política" — os temas que guiaram a escolha."""
    rotulos = list(dict.fromkeys(ROTULO_TIPO[t] for t in
                                 tipos_do_ativo(classe_politica)
                                 if t in ROTULO_TIPO))
    return ", ".join(rotulos)


def relatorios(a: m.AnaliseAtivo, n: int = N_RELATORIOS):
    """(documentos, motivo): os ``n`` documentos oficiais mais recentes."""
    if not a.relatorios.dados:
        return (), a.relatorios.resumo
    r = inf.Relatorios.de_dict(a.relatorios.dados)
    docs = sorted(r.documentos, key=lambda d: d.reference_date or "",
                  reverse=True)[:n]
    return tuple(docs), (None if docs else (r.motivo or a.relatorios.resumo))


# -- macro --------------------------------------------------------------------------

@dataclass(frozen=True)
class Macro:
    canais: tuple[str, ...]              # variáveis que mais pesam na classe
    premissas: tuple[str, ...]           # o que o usuário registrou delas
    sinais: tuple[str, ...]              # divergência calculada pelo código
    sem_cenario: bool
    lido_dos_dados: bool = False


def macro(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor) -> Macro:
    """O que do ambiente econômico mais pesa para a classe do ativo e o que
    o cenário (lido dos dados desde 30/09/2026) diz sobre isso. A leitura do
    impacto é da LLM (Portfolio Fit); aqui só o dado. Puro."""
    chaves = cen.relevantes(a.ativo.classe_politica)
    canais = tuple(cen.ROTULO[k] for k in chaves)
    c = ctx.cenario
    dados = c is not None and c.origem == cen.DADOS
    if c is None or c.vazio:
        return Macro(canais, (), (), True, dados)
    premissas = []
    for k in chaves:
        it = c.item(k)
        if not it.preenchido:
            continue
        direcao = cen.ROTULO_DIRECAO.get(it.expected_direction)
        premissas.append(f"{cen.ROTULO[k]}: {it.current_value}"
                         + (f" · tendência {direcao.lower()}" if direcao else ""))
    sinais = tuple(s.texto for s in ctx.sinais_cenario if s.chave in chaves)
    return Macro(canais, tuple(premissas), sinais, False, dados)


# -- cenário econômico (lido dos dados) ---------------------------------------------

@dataclass(frozen=True)
class LinhaCenario:
    rotulo: str
    valor: str | None          # None: sem dado
    tendencia: str | None      # alta / estavel / queda / incerta
    fonte: str
    referencia: str | None     # DD/MM/AAAA


def cenario_atual(c) -> tuple[LinhaCenario, ...]:
    """As 12 variáveis do cenário em linhas de tela, na ordem do modelo. Puro."""
    if c is None:
        return ()
    saida = []
    for k in cen.CHAVES:
        it = c.item(k)
        ref = cen.data_iso(it.last_updated)
        saida.append(LinhaCenario(
            cen.ROTULO[k], it.current_value or None,
            it.expected_direction or None, it.source or "",
            ref.strftime("%d/%m/%Y") if ref else None))
    return tuple(saida)
