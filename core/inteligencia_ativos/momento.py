"""
core/inteligencia_ativos/momento.py
O momento do ativo antes de uma venda (pedido do usuário de 08/11/2026):
uma queda de preço pode ser passageira, e vender nela, mesmo em parte,
transforma em perda o que talvez se recuperasse. Puro, sem I/O.

O que se mede, com o dado que a análise já tem:

* **resultado da posição**: valor de mercado ÷ valor investido − 1. Abaixo
  de ``PERDA_RELEVANTE_PCT``, vender realiza um prejuízo relevante;
* **retorno de 12 meses** (com proventos) e a **mediana dos pares** no mesmo
  período, da seção de pares. Abaixo de ``QUEDA_12M_RELEVANTE`` é queda forte;
  perto da mediana dos pares, o segmento caiu junto (sinal de movimento de
  mercado, não da empresa);
* **preço abaixo do normal** (``avaliacao.BARATO``): a queda deixou o preço
  barato contra o próprio histórico e os pares;
* **piora nos fundamentos**: alerta eliminatório do balanço, qualidade
  frágil ou gatilho de fundamentos da tese disparado.

A leitura separa três casos (``tipo``):

* ``QUEDA_DE_PRECO``: caiu, e nenhum fundamento piorou. Vender aí realiza a
  perda num preço possivelmente deprimido sem motivo da empresa, e a regra é
  não vender por isso;
* ``QUEDA_COM_DETERIORACAO``: caiu e algum fundamento piorou. Vender pode ser
  certo mesmo com prejuízo, mas a queda já precificou parte da piora: a
  saída vai em etapas (menos com alerta eliminatório do balanço, em que
  esperar costuma custar mais);
* ``QUEDA_SEM_LEITURA``: caiu, mas não há leitura de qualidade para dizer se
  é só preço. Cautela nos dois sentidos: venda em etapas.

Limitação: não há retorno de curto prazo (1 a 3 meses) na análise, só o de
12 meses e o resultado da posição. "Momentâneo" é uma inferência: o sistema
não sabe se o preço vai voltar; ele só evita que a venda aconteça sem motivo
de fundamento. A decisão que usa isto é ``resumida.decisao``, que alimenta a
tela, as LLMs (``veredito``) e as substituições (``confronto_modelo``).

Coberto por tests/test_inteligencia_ativos_momento.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs

# Convenções do sistema, não calibradas.
PERDA_RELEVANTE_PCT = -10.0     # resultado da posição, em %
QUEDA_12M_RELEVANTE = -20.0     # retorno de 12 meses com proventos, em %
# Retorno a menos disso da mediana dos pares conta como "o segmento caiu
# junto" (a mesma régua de momento da avaliação).
JUNTO_COM_PARES_PP = av_.MOMENTO_DIFERENCA_PP

SEM_QUEDA = "sem_queda"
QUEDA_DE_PRECO = "queda_de_preco"
QUEDA_COM_DETERIORACAO = "queda_com_deterioracao"
QUEDA_SEM_LEITURA = "queda_sem_leitura"
ROTULO_TIPO = {
    SEM_QUEDA: "sem queda relevante",
    QUEDA_DE_PRECO: "queda de preço sem piora nos fundamentos",
    QUEDA_COM_DETERIORACAO: "queda com piora nos fundamentos",
    QUEDA_SEM_LEITURA: "queda sem leitura de qualidade",
}


def _br(x: float, casas: int = 1) -> str:
    return f"{x:.{casas}f}".replace(".", ",")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None   # NaN fica de fora


@dataclass(frozen=True)
class Momento:
    resultado_pct: float | None          # posição: valor de mercado ÷ investido − 1
    retorno_12m: float | None            # % com proventos
    mediana_pares_12m: float | None
    n_pares: int
    preco_abaixo_do_normal: bool
    deterioracao: tuple[str, ...]        # o que piorou nos fundamentos
    alerta_do_balanco: bool              # alerta eliminatório confirmado
    sem_leitura_de_qualidade: bool

    @property
    def em_prejuizo_relevante(self) -> bool:
        return (self.resultado_pct is not None
                and self.resultado_pct <= PERDA_RELEVANTE_PCT)

    @property
    def queda_forte_12m(self) -> bool:
        return (self.retorno_12m is not None
                and self.retorno_12m <= QUEDA_12M_RELEVANTE)

    @property
    def em_queda(self) -> bool:
        """Prejuízo relevante na posição, queda forte em 12 meses, ou preço
        abaixo do normal depois de cair (resultado ou retorno negativos)."""
        caiu = ((self.resultado_pct is not None and self.resultado_pct < 0)
                or (self.retorno_12m is not None and self.retorno_12m < 0))
        return (self.em_prejuizo_relevante or self.queda_forte_12m
                or (self.preco_abaixo_do_normal and caiu))

    @property
    def segmento_caiu_junto(self) -> bool | None:
        """O retorno ficou perto da mediana dos pares, que também caiu.
        None sem pares para comparar."""
        if (self.retorno_12m is None or self.mediana_pares_12m is None
                or not self.n_pares):
            return None
        return (self.mediana_pares_12m < 0 and abs(
            self.retorno_12m - self.mediana_pares_12m) < JUNTO_COM_PARES_PP)

    @property
    def tipo(self) -> str:
        if not self.em_queda:
            return SEM_QUEDA
        if self.deterioracao:
            return QUEDA_COM_DETERIORACAO
        if self.sem_leitura_de_qualidade:
            return QUEDA_SEM_LEITURA
        return QUEDA_DE_PRECO

    @property
    def rotulo(self) -> str:
        return ROTULO_TIPO[self.tipo]

    def fatos(self) -> tuple[str, ...]:
        """Os números da queda, sem conclusão."""
        out = []
        if self.resultado_pct is not None and self.resultado_pct < 0:
            out.append(f"a posição está {_br(-self.resultado_pct)}% abaixo do "
                       "valor investido")
        if self.retorno_12m is not None and self.retorno_12m < 0:
            txt = f"o preço caiu {_br(-self.retorno_12m)}% em 12 meses (com proventos)"
            junto = self.segmento_caiu_junto
            if junto is True:
                txt += (f", perto da mediana de {self.n_pares} pares "
                        f"({_br(self.mediana_pares_12m)}%): o segmento caiu junto")
            elif junto is False and self.mediana_pares_12m is not None:
                txt += (f", contra {_br(self.mediana_pares_12m)}% da mediana de "
                        f"{self.n_pares} pares: caiu mais que o segmento")
            out.append(txt)
        if self.preco_abaixo_do_normal:
            out.append("o preço está abaixo do normal contra o próprio "
                       "histórico e os pares")
        return tuple(out)

    def linha(self) -> str | None:
        """A leitura do momento para o contexto da recomendação."""
        if self.tipo == SEM_QUEDA:
            return None
        fatos = "; ".join(self.fatos())
        fatos = fatos[:1].upper() + fatos[1:] if fatos else ""
        if self.tipo == QUEDA_DE_PRECO:
            return (f"Momento: {fatos}. Nenhum fundamento piorou: a queda "
                    "parece de preço, não da empresa, e não é motivo de "
                    "venda; vender agora realizaria a perda num preço "
                    "possivelmente deprimido.")
        if self.tipo == QUEDA_COM_DETERIORACAO:
            return (f"Momento: {fatos}. Há piora nos fundamentos "
                    f"({'; '.join(self.deterioracao)}): a queda pode ter "
                    "motivo, mas parte da piora já está no preço.")
        return (f"Momento: {fatos}. Sem leitura de qualidade, não dá para "
                "dizer se a queda é só de preço.")

    def como_dict(self) -> dict:
        return {"tipo": self.rotulo,
                "resultado_da_posicao_pct": (None if self.resultado_pct is None
                                             else round(self.resultado_pct, 1)),
                "retorno_12m_pct": self.retorno_12m,
                "mediana_pares_12m_pct": self.mediana_pares_12m,
                "segmento_caiu_junto": self.segmento_caiu_junto,
                "preco_abaixo_do_normal": self.preco_abaixo_do_normal,
                "piora_nos_fundamentos": list(self.deterioracao),
                "leitura": self.linha()}


def resultado_da_posicao(info: m.InfoBasica) -> float | None:
    vm, vi = _num(info.valor_mercado), _num(info.valor_investido)
    if vm is None or vi is None or vi <= 0:
        return None
    return (vm / vi - 1.0) * 100.0


def medir(a: m.AnaliseAtivo, av: av_.Avaliacao,
          quebra: tuple[str, ...] = ()) -> Momento:
    """O momento do ativo da carteira. ``quebra`` = os gatilhos de
    fundamentos da tese disparados (``resumida._situacao``)."""
    ret = med = None
    n = 0
    if a.pares.dados:
        ln = prs.ComparacaoPares.de_dict(a.pares.dados).linha("retorno_12m")
        if ln is not None:
            ret, med, n = _num(ln.valor), _num(ln.mediana_pares), ln.n_pares
    balanco = [al for al in av.criticos if not al.noticia]
    noticia = [al for al in av.criticos if al.noticia]
    piora = [f"alerta eliminatório: {al.texto}" for al in balanco]
    if not balanco and noticia:
        # Alerta crítico do noticiário também põe a qualidade em frágil; o
        # que piorou é a manchete, ainda sem documento oficial.
        piora.append(f"alerta no noticiário, a confirmar: {noticia[0].texto}")
    elif not balanco and av.qualidade == av_.FRAGIL:
        piora.append("qualidade frágil")
    piora += [f"gatilho da tese: {q}" for q in quebra]
    return Momento(
        resultado_pct=resultado_da_posicao(a.ativo), retorno_12m=ret,
        mediana_pares_12m=med, n_pares=int(n or 0),
        preco_abaixo_do_normal=av.preco == av_.BARATO,
        deterioracao=tuple(piora), alerta_do_balanco=bool(balanco),
        sem_leitura_de_qualidade=av.qualidade in (av_.INSUFICIENTE,
                                                 av_.SEM_LEITURA))
