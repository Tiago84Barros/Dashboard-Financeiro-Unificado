"""Cessão gradual da exigência do usuário quando o universo não sustenta carteira.

As cessões que já existiam — forma (``fii_portfolio_v4``), proteção na
elegibilidade (``fii_carteira_protegida``) e piso de cardinalidade — trabalham
todas **dentro** do conjunto de candidatos que a elegibilidade entregou. Nenhuma
delas consegue aumentá-lo. Quando é o próprio ajuste dos controles da tela que
esvazia o universo, elas não têm o que ceder, e a tela entrega o que sobrou.

Medido no snapshot publicado (394 fundos), varrendo 216 combinações realistas
dos controles (liquidez × renda × histórico × drawdown × P/VP<1):

* 36 deixam **zero** candidatos — a carteira voltava vazia, ``status=blocked``,
  contra a regra permanente de nunca zerar a criação de portfólio;
* 84 deixam **dois ou menos** — "carteira" de um ou dois fundos, que não
  diversifica coisa nenhuma;
* 126 deixam **menos de cinco**, abaixo do piso da própria casa
  (``MINIMO_DE_ATIVOS_SEM_SELO``).

Falta o degrau que este módulo acrescenta: quando o usuário pediu **mais** rigor
que o padrão da casa e o universo não sustenta uma carteira saudável, a
exigência volta em degraus na direção do padrão da casa — **nunca além dele** —
até que entrem FIIs suficientes para diversificar.

Por que voltar ao padrão da casa não expõe o investidor a risco desnecessário:
o padrão da casa é o nível **validado** da metodologia, o mesmo contra o qual o
backtest point-in-time foi medido e o mesmo que o portão de publicação cobra.
Afrouxar até ele desfaz um aperto discricionário; não abre um portão de risco.
Por isso o teto da escada é o padrão, e um eixo em que o usuário já está mais
frouxo que a casa não gera degrau nenhum — a escada só desfaz aperto, nunca
aperta e nunca afrouxa além do validado.

A ordem dos degraus é a ordem do risco, do mais barato ao mais caro. É
julgamento declarado, não medida:

1. exigências patrimoniais opcionais e P/VP < 1 — são preferências de seleção,
   não portões de risco: desligá-las não admite fundo mais arriscado, só para
   de recusar fundo que não atende a uma preferência;
2. renda recorrente mínima — exigir menos renda admite fundo que paga menos, o
   que não é risco maior; exigir renda alta é que seleciona ativamente yield
   que só se sustenta com risco;
3. histórico mínimo — mede confiança no dado, não risco do ativo;
4. liquidez diária mínima — é risco real (capacidade de sair), mas o piso da
   casa, 1 M/dia, continua sendo piso de liquidez de verdade;
5. drawdown máximo — o degrau mais caro, e por isso o último: voltar ao padrão
   admite os fundos que mais caíram dentro da faixa admissível da casa.

A altura do degrau se escolhe **contando candidatos**, que é barato; montar a
carteira é que custa minutos. Por isso a escada não monta carteira nenhuma: ela
devolve o universo e o otimizador roda uma vez só, como já rodava. Cada degrau
efetivamente usado é declarado — exigência cedida não é ausência de risco.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from core.fii_integrated_model import (
    IntegratedEligibilityPolicy,
    apply_integrated_eligibility,
)
from core.fii_portfolio_v4 import MINIMO_DE_ATIVOS_SEM_SELO, PortfolioPolicy

# O piso é o da casa, não um número novo: `fii_portfolio_v4` já usa
# MINIMO_DE_ATIVOS_SEM_SELO para decidir quando a carteira entregue deixa de
# estar sob o padrão de diversificação e passa a ser declarada como exceção.
# Dois nomes para a mesma linha divergiriam no primeiro ajuste de um deles.
#
# A aritmética sustenta o número. Com correlação média de ~0,5 entre FIIs, a
# volatilidade da carteira em relação à de um ativo isolado cai 1,00 → 0,84
# (n=2) → 0,775 (n=5) → 0,75 (n=8) → 0,73 (n=14): quase todo o benefício de
# diversificação está capturado por volta de cinco a oito nomes, e o que vem
# depois é refinamento. Cinco é o piso do que merece o nome de carteira.
PISO_DE_DIVERSIFICACAO = MINIMO_DE_ATIVOS_SEM_SELO


@dataclass(frozen=True)
class _Eixo:
    """Um eixo de exigência que pode voltar em direção ao padrão da casa."""

    atributo: str
    rotulo: str
    # 1 = chave liga/desliga. Acima disso, o caminho de volta é percorrido em
    # degraus iguais — "afrouxe GRADATIVAMENTE" quer dizer que o primeiro
    # degrau que bastar é o que vale, não o salto inteiro até o padrão.
    degraus: int

    def formata(self, valor: Any) -> str:
        if isinstance(valor, bool):
            return "exigido" if valor else "não exigido"
        if self.atributo == "min_daily_liquidity":
            return f"{float(valor) / 1e6:.2f} M/dia".replace(".", ",")
        if self.atributo == "min_history_months":
            return f"{int(round(float(valor)))} meses"
        return f"{float(valor):.1%}".replace(".", ",")


EIXOS: tuple[_Eixo, ...] = (
    _Eixo("require_min_properties", "exigência de número mínimo de imóveis", 1),
    _Eixo("require_multicategory", "exigência de múltiplas categorias", 1),
    _Eixo("require_multi_region", "exigência de múltiplas regiões", 1),
    _Eixo("require_pvp_below_one", "exigência de P/VP abaixo de 1", 1),
    _Eixo("min_recurrent_dy_12m", "renda recorrente mínima", 4),
    _Eixo("min_history_months", "histórico mínimo", 4),
    _Eixo("min_daily_liquidity", "liquidez diária mínima", 4),
    _Eixo("max_drawdown", "drawdown máximo tolerado", 4),
)


@dataclass(frozen=True)
class UniversoCedido:
    """O universo que a carteira vai usar, e o que custou chegar nele."""

    elegiveis: list[dict]
    relatorio: dict
    politica: IntegratedEligibilityPolicy
    degraus: tuple[str, ...]
    candidatos: int
    candidatos_no_pedido: int
    piso: int

    @property
    def cedeu(self) -> bool:
        return bool(self.degraus)

    @property
    def piso_alcancado(self) -> bool:
        return self.candidatos >= self.piso

    def resumo(self) -> dict[str, Any]:
        return {
            "usada": self.cedeu,
            "degraus": list(self.degraus),
            "piso": int(self.piso),
            "candidatos": int(self.candidatos),
            "candidatos_no_pedido": int(self.candidatos_no_pedido),
            "piso_alcancado": self.piso_alcancado,
        }


def _mais_estrito(eixo: _Eixo, do_usuario: Any, da_casa: Any) -> bool:
    """O usuário apertou este eixo em relação ao padrão da casa?"""
    if isinstance(da_casa, bool):
        return bool(do_usuario) and not bool(da_casa)
    if eixo.atributo == "max_drawdown":
        # Tolerar MENOS queda é apertar.
        return float(do_usuario) < float(da_casa) - 1e-12
    return float(do_usuario) > float(da_casa) + 1e-12


def _interpola(eixo: _Eixo, do_usuario: Any, da_casa: Any, passo: int) -> Any:
    """Valor do eixo no ``passo``-ésimo degrau de volta ao padrão da casa."""
    if eixo.degraus <= 1 or isinstance(da_casa, bool):
        return da_casa
    fracao = passo / eixo.degraus
    valor = float(do_usuario) + fracao * (float(da_casa) - float(do_usuario))
    if eixo.atributo == "min_history_months":
        # Arredonda na direção do padrão da casa; um degrau que não muda o
        # valor inteiro não é degrau, é uma contagem repetida.
        return int(round(valor))
    return valor


def escada(
    do_usuario: IntegratedEligibilityPolicy,
    da_casa: IntegratedEligibilityPolicy | None = None,
) -> list[tuple[IntegratedEligibilityPolicy, tuple[str, ...]]]:
    """Política de cada degrau, cumulativa, e a descrição do que já cedeu.

    Cumulativa e na ordem do risco: o eixo seguinte só começa a ceder quando o
    anterior voltou inteiro ao padrão da casa. Eixo em que o usuário não
    apertou não entra na escada, e o último degrau é exatamente o padrão da
    casa — a escada não tem nada acima dele.
    """
    da_casa = da_casa or IntegratedEligibilityPolicy()
    degraus: list[tuple[IntegratedEligibilityPolicy, tuple[str, ...]]] = []
    corrente = do_usuario
    descricoes: list[str] = []
    for eixo in EIXOS:
        valor_usuario = getattr(do_usuario, eixo.atributo)
        valor_casa = getattr(da_casa, eixo.atributo)
        if not _mais_estrito(eixo, valor_usuario, valor_casa):
            continue
        anterior = valor_usuario
        for passo in range(1, eixo.degraus + 1):
            valor = _interpola(eixo, valor_usuario, valor_casa, passo)
            if isinstance(valor_casa, bool):
                repetido = bool(valor) == bool(anterior)
            else:
                repetido = abs(float(valor) - float(anterior)) <= 1e-12
            if repetido:
                continue
            anterior = valor
            corrente = replace(corrente, **{eixo.atributo: valor})
            prefixo = eixo.rotulo + ":"
            descricoes = [
                texto for texto in descricoes if not texto.startswith(prefixo)
            ] + [
                f"{eixo.rotulo}: {eixo.formata(valor_usuario)} → "
                f"{eixo.formata(valor)}"
            ]
            degraus.append((corrente, tuple(descricoes)))
    return degraus


def ajustar_politica_de_carteira(
    policy: PortfolioPolicy, elegibilidade: IntegratedEligibilityPolicy,
) -> PortfolioPolicy:
    """Mantém a política de carteira coerente com a exigência cedida.

    ``PortfolioPolicy.min_daily_liquidity`` alimenta o teto de posição ilíquida
    do MILP. Ceder a liquidez na elegibilidade sem ceder aqui readmitiria o
    fundo só para barrá-lo adiante por iliquidez: a cessão pagaria o preço em
    proteção e não entregaria o ativo.
    """
    if policy.min_daily_liquidity <= elegibilidade.min_daily_liquidity + 1e-9:
        return policy
    return replace(
        policy, min_daily_liquidity=float(elegibilidade.min_daily_liquidity))


def ceder_exigencia_ate_diversificar(
    registros: Sequence[dict],
    politica: IntegratedEligibilityPolicy,
    *,
    piso: int = PISO_DE_DIVERSIFICACAO,
    elegibilidade: Callable[..., tuple[list[dict], dict]] = apply_integrated_eligibility,
) -> UniversoCedido:
    """Devolve o universo que sustenta ``piso`` candidatos, cedendo o mínimo.

    Roda primeiro exatamente no nível que o usuário pediu. Se ele já entregar
    ``piso`` candidatos ou mais, nada cede e nada muda — é o caminho comum, e é
    por isso que a escada não altera o backtest nem qualquer chamador que já
    esteja no padrão da casa.

    "Candidato" aqui é o material de que a carteira pode se servir: os
    elegíveis mais a fila de concessão de proteção, que o orquestrador readmite
    em ordem de severidade. Contar só os estritos subestimaria o universo e
    faria a escada ceder exigência que não precisava ceder.

    Não monta carteira. O otimizador roda uma vez, no universo escolhido,
    exatamente como já rodava — a lentidão de que o usuário reclamou não pode
    ser o preço da diversificação.
    """
    linhas = [dict(registro) for registro in registros]

    def _conta(pol: IntegratedEligibilityPolicy) -> tuple[list[dict], dict, int]:
        elegiveis, relatorio = elegibilidade(linhas, pol)
        fila = relatorio.get("concession_candidates") or ()
        return elegiveis, relatorio, len(elegiveis) + len(tuple(fila))

    elegiveis, relatorio, candidatos = _conta(politica)
    no_pedido = candidatos
    if candidatos >= piso:
        return UniversoCedido(
            elegiveis, relatorio, politica, (), candidatos, no_pedido, piso)

    melhor = (elegiveis, relatorio, politica, (), candidatos)
    for degrau, descricoes in escada(politica):
        elegiveis, relatorio, candidatos = _conta(degrau)
        # Só troca quando o degrau ganha candidato: ceder exigência sem ganhar
        # ativo é pagar proteção por nada, e aconteceria em todo degrau
        # intermediário cujo valor ainda não atravessa nenhuma métrica.
        if candidatos > melhor[4]:
            melhor = (elegiveis, relatorio, degrau, descricoes, candidatos)
        if candidatos >= piso:
            break
    # Se nem o padrão da casa alcança o piso, fica o degrau de maior universo:
    # a melhor carteira disponível continua sendo entregue, e a declaração
    # abaixo diz que o mercado desta data é que não comportava o piso.
    elegiveis, relatorio, escolhida, descricoes, candidatos = melhor
    return UniversoCedido(
        elegiveis, relatorio, escolhida, descricoes, candidatos, no_pedido, piso)


def nota_de_cessao(universo: UniversoCedido) -> str:
    """Declara o que a exigência do usuário cedeu, e até onde.

    Separada de :func:`nota_de_concentracao` porque as duas respondem a
    perguntas diferentes e são publicadas em pontos diferentes da tela: esta
    explica o universo, antes da carteira existir; a outra descreve a carteira
    entregue, e só quem a montou sabe quantos ativos ela tem.
    """
    if not universo.cedeu:
        return ""
    return (
        "Exigência dos seus controles cedida em direção ao padrão da casa "
        f"porque o seu nível deixava apenas {universo.candidatos_no_pedido} "
        f"candidato(s), abaixo do piso de {universo.piso} que uma carteira "
        "precisa para diluir risco: "
        + "; ".join(universo.degraus)
        + f". O universo passou a {universo.candidatos} candidatos. A cessão "
        "para no padrão da casa e nunca vai além dele — é o nível validado "
        "da metodologia, não um portão aberto. Exigência cedida não é "
        "ausência de risco."
    )


def nota_de_concentracao(universo: UniversoCedido, entregues: int) -> str:
    """Declara a concentração quando nem a escada alcançou o piso.

    ``entregues`` é o número de ativos que a carteira de fato tem: candidato
    não é ativo, e o otimizador pode entregar menos do que recebeu. Quem sabe
    esse número é quem montou a carteira.
    """
    if entregues >= universo.piso:
        return ""
    return (
        f"A carteira fica em {entregues} ativo(s), abaixo do piso de "
        f"{universo.piso}: "
        + ("nem no padrão da casa o universo desta data comportava a "
           "diversificação mínima"
           if universo.cedeu else
           "seus controles já estão no padrão da casa ou abaixo dele, e "
           "afrouxar além do validado exporia você a risco que a "
           "metodologia não avalia")
        + ". A concentração é o risco dominante desta carteira."
    )
