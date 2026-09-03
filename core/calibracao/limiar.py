"""O que é "movimento relevante" -- por classe de ativo, não por número único.

A instrução é literal: *"Defina formalmente o que é movimento relevante por
classe de ativo, considerando volatilidade. Não use um único limiar absoluto
para todos."* O código tinha exatamente o que ela proíbe:
``core.memoria_mercado.ponte_noticias.LIMIAR_RELEVANTE_PADRAO = 0.03`` -- 3% para
qualquer coisa.

Por que 3% para tudo estava errado nas duas direções
----------------------------------------------------
Um FII de tijolo líquido tem desvio diário perto de 0,8%. Um movimento de 3% é
quase quatro desvios: acontece poucas vezes por ano, e a probabilidade de
"movimento relevante" calculada com esse limiar sai perto de zero para todos os
eventos -- o motor fica mudo justamente onde tem mais dado. Uma small cap da B3
com desvio diário de 4% cruza 3% em qualquer terça-feira sem notícia nenhuma: o
mesmo limiar transforma ruído em sinal e enche a tela de alarme.

O mesmo número produzia, portanto, os dois defeitos que a instrução manda
evitar ao mesmo tempo: silêncio onde havia informação, alarme onde não havia.

A definição
-----------
Movimento relevante no horizonte ``h`` é o movimento que excede

    limiar = k * sigma_diario * sqrt(h)

com ``sigma_diario`` estimado **na janela que termina antes do evento** e ``k``
por classe de ativo. É a definição em desvios: "relevante" passa a significar
*incomum para este ativo*, que é o que a palavra deveria significar desde o
começo.

O piso e o teto não são estética
--------------------------------
A escala de sigma quebra nas duas pontas, e cada quebra tem nome no repositório:

* **Sigma baixo demais.** Papel que quase não negocia tem série quase constante
  e sigma artificialmente pequeno -- não porque seja estável, mas porque não há
  preço novo. Sem piso, o limiar cairia para 0,4% e qualquer coisa viraria
  movimento relevante. É o parente do defeito de ``memoria:
  media-ponderada-compensa-defeito-eliminatorio``: preço parado tratado como
  preço bem-comportado.
* **Sigma alto demais.** Ativo em colapso tem sigma de 12% ao dia; ``k*sigma``
  exigiria 25% para chamar de relevante, e o motor ficaria calado durante a
  única semana em que ele importa.

Piso e teto por classe cortam as duas pontas, e ambos são declarados, não
escondidos: :class:`Limiar` carrega qual dos três caminhos produziu o número.

Prior, e prior marcado como tal
-------------------------------
Sem histórico suficiente para estimar sigma, a função devolve o prior da classe
com ``estimado=False``. Ela **não** devolve 3% silenciosamente. O ``False``
viaja até quem publica, pela mesma razão que ``calibrado=False`` viaja em
``core.memoria_mercado.calibracao``: um número sem procedência é indistinguível
de um número medido, e é aí que a evidência falsa nasce.

Os ``k`` e os limites desta tabela também são priores declarados. Eles são
revistos por :mod:`core.calibracao.metricas`, não por opinião.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

#: Classes de ativo reconhecidas. São as que o app efetivamente carrega; não há
#: classe hipotética aqui, porque classe sem ativo é linha de tabela que nunca
#: é exercitada e envelhece errada.
CLASSE_ACAO_B3 = "acao_b3"
CLASSE_FII = "fii"
CLASSE_ACAO_US = "acao_us"
CLASSE_INDICE = "indice"
CLASSE_DESCONHECIDA = "desconhecida"

CLASSES = (CLASSE_ACAO_B3, CLASSE_FII, CLASSE_ACAO_US, CLASSE_INDICE,
           CLASSE_DESCONHECIDA)

#: Mínimo de retornos diários para estimar sigma. Abaixo disto o desvio amostral
#: é ruído com nome de estatística: com 15 observações o intervalo de confiança
#: do desvio passa de 30% para cada lado.
MINIMO_OBSERVACOES = 60

#: Origem do número, sempre publicada junto com ele.
ORIGEM_ESTIMADO = "volatilidade_do_ativo"
ORIGEM_PISO = "piso_da_classe"
ORIGEM_TETO = "teto_da_classe"
ORIGEM_PRIOR = "prior_da_classe"


@dataclass(frozen=True)
class Parametros:
    """Regra de uma classe de ativo. Tudo em fração, nunca em pontos."""

    classe: str
    k: float
    piso: float
    teto: float
    sigma_tipico: float
    motivo: str

    def prior(self, pregoes: int) -> float:
        """Limiar da classe sem olhar o ativo. É o que sai sem histórico."""
        return self._limitar(self.k * self.sigma_tipico * sqrt(max(1, pregoes)))

    def _limitar(self, valor: float) -> float:
        return max(self.piso, min(self.teto, valor))


#: Priores declarados. ``sigma_tipico`` é o desvio diário mediano observado na
#: classe; ``k`` é quantos desvios um movimento precisa ter para deixar de ser
#: rotina. ``k`` maior em ação da B3 do que em FII é deliberado: a distribuição
#: de retorno de ação tem cauda mais pesada, e o mesmo número de desvios é um
#: evento mais frequente lá.
PARAMETROS: dict[str, Parametros] = {
    CLASSE_FII: Parametros(
        CLASSE_FII, k=2.0, piso=0.010, teto=0.060, sigma_tipico=0.008,
        motivo="FII negocia em faixa estreita; 3% era quase 4 desvios e "
               "silenciava o motor onde ele tem mais evento medido"),
    CLASSE_ACAO_B3: Parametros(
        CLASSE_ACAO_B3, k=1.75, piso=0.020, teto=0.150, sigma_tipico=0.022,
        motivo="cauda pesada e dispersao alta entre papeis; small cap cruza 3% "
               "sem noticia e large cap raramente cruza"),
    CLASSE_ACAO_US: Parametros(
        CLASSE_ACAO_US, k=1.75, piso=0.015, teto=0.120, sigma_tipico=0.018,
        motivo="mesma logica da B3 com volatilidade tipica menor; o universo do "
               "modulo exclui REIT e SPAC (core/us_instrumento.py)"),
    CLASSE_INDICE: Parametros(
        CLASSE_INDICE, k=2.0, piso=0.008, teto=0.080, sigma_tipico=0.011,
        motivo="indice e media de carteira: sigma menor por construcao, e um "
               "movimento de 2% nele e um evento de mercado"),
    CLASSE_DESCONHECIDA: Parametros(
        CLASSE_DESCONHECIDA, k=1.75, piso=0.015, teto=0.120, sigma_tipico=0.020,
        motivo="classe nao resolvida: parametros conservadores e a limitacao "
               "declarada na saida"),
}


@dataclass(frozen=True)
class Limiar:
    """Limiar publicado, com tudo que é preciso para contestá-lo."""

    valor: float
    classe: str
    horizonte_pregoes: int
    k: float
    sigma_diario: float | None
    n_observacoes: int
    estimado: bool
    origem: str
    limitacoes: tuple[str, ...] = ()

    @property
    def em_pontos(self) -> float:
        """O mesmo número em pontos percentuais.

        ``core.noticias.impacto.BaseHistorica`` trabalha em pontos e este pacote
        em fração. A conversão mora aqui e em ``ponte_noticias``, nos dois
        lugares em que a fronteira é atravessada, e em nenhum outro.
        """
        return self.valor * 100.0

    def descrever(self) -> str:
        base = (f"movimento relevante em {self.horizonte_pregoes} pregao(es): "
                f"{self.em_pontos:.2f}% ({self.classe})")
        if self.estimado and self.sigma_diario is not None:
            return (f"{base}; {self.k:.2f} desvios de "
                    f"{self.sigma_diario * 100:.2f}% ao dia, "
                    f"n={self.n_observacoes}")
        return f"{base}; prior da classe, volatilidade nao estimada"


def classificar(simbolo: str | None, *, mercado: str | None = None) -> str:
    """Classe de ativo a partir do que o chamador sabe.

    ``mercado`` é o caminho confiável, e é o que os scripts do repositório já
    carregam (``--mercado us|fii|b3``). O ticker é o caminho de recurso: um FII
    da B3 termina em ``11``, uma ação em ``3``/``4``/``5``/``6``/``11``. O ``11``
    aparece nos dois -- é unit e é FII --, e por isso o palpite por ticker
    devolve :data:`CLASSE_DESCONHECIDA` em vez de escolher errado com confiança.
    """
    normalizado = str(mercado or "").strip().lower()
    if normalizado in {"us", "eua", "acao_us"}:
        return CLASSE_ACAO_US
    if normalizado in {"fii", "fiis"}:
        return CLASSE_FII
    if normalizado in {"b3", "acao_b3", "br"}:
        return CLASSE_ACAO_B3
    if normalizado in {"indice", "index", "benchmark"}:
        return CLASSE_INDICE

    ticker = str(simbolo or "").strip().upper()
    if not ticker:
        return CLASSE_DESCONHECIDA
    if ticker.endswith(("3", "4", "5", "6")) and len(ticker) >= 5:
        return CLASSE_ACAO_B3
    if ticker.isalpha():
        # Sem sufixo numérico: convenção de bolsa americana.
        return CLASSE_ACAO_US
    return CLASSE_DESCONHECIDA


def desvio_diario(retornos) -> tuple[float | None, int]:
    """Desvio-padrão amostral dos retornos diários, e quantos entraram.

    Devolve ``(None, n)`` quando não há observação suficiente. ``None`` aqui é
    "não medido", nunca "zero" -- a distinção é lei do projeto e o motivo é que
    zero é um número que se comporta como resposta.
    """
    limpos = [float(r) for r in (retornos or ())
              if r is not None and isfinite(float(r))]
    n = len(limpos)
    if n < MINIMO_OBSERVACOES:
        return None, n
    media = sum(limpos) / n
    variancia = sum((r - media) ** 2 for r in limpos) / (n - 1)
    sigma = sqrt(variancia)
    if not isfinite(sigma) or sigma <= 0:
        # Série constante: papel que não negociou. Não é ativo estável, é ativo
        # sem preço novo -- e chamar isso de sigma zero produziria limiar zero.
        return None, n
    return sigma, n


def calcular(*, classe: str, horizonte_pregoes: int = 1,
             retornos_diarios=None, sigma_diario: float | None = None,
             n_observacoes: int | None = None) -> Limiar:
    """Limiar de movimento relevante para uma classe e um horizonte.

    Args:
        classe: uma de :data:`CLASSES`. Desconhecida cai no prior conservador.
        horizonte_pregoes: janela em pregões. O escalonamento é ``sqrt(h)``,
            que assume retornos independentes -- aproximação declarada em
            :data:`AVISO_RAIZ`, não silenciosa.
        retornos_diarios: janela **anterior ao evento**. Quem chama é
            responsável por não passar retorno posterior; o módulo não tem como
            saber a data e não finge saber.
        sigma_diario: alternativa a ``retornos_diarios`` quando o desvio já foi
            calculado. Usar os dois: ``sigma_diario`` vence.
    """
    parametros = PARAMETROS.get(classe) or PARAMETROS[CLASSE_DESCONHECIDA]
    h = max(1, int(horizonte_pregoes))
    limitacoes: list[str] = []

    if classe not in PARAMETROS:
        limitacoes.append(
            f"classe '{classe}' nao reconhecida: limiar do prior conservador")

    n = int(n_observacoes or 0)
    if sigma_diario is None and retornos_diarios is not None:
        sigma_diario, n = desvio_diario(retornos_diarios)

    if sigma_diario is None or not isfinite(sigma_diario) or sigma_diario <= 0:
        if n and n < MINIMO_OBSERVACOES:
            limitacoes.append(
                f"volatilidade nao estimada: {n} retornos diarios, "
                f"minimo {MINIMO_OBSERVACOES}")
        else:
            limitacoes.append("volatilidade nao estimada: sem serie utilizavel")
        return Limiar(valor=parametros.prior(h), classe=parametros.classe,
                      horizonte_pregoes=h, k=parametros.k, sigma_diario=None,
                      n_observacoes=n, estimado=False, origem=ORIGEM_PRIOR,
                      limitacoes=tuple(limitacoes))

    if h > 1:
        limitacoes.append(AVISO_RAIZ)

    bruto = parametros.k * sigma_diario * sqrt(h)
    valor = max(parametros.piso, min(parametros.teto, bruto))
    if valor > bruto:
        origem = ORIGEM_PISO
        limitacoes.append(
            f"limiar elevado ao piso da classe ({parametros.piso * 100:.2f}%): "
            f"volatilidade medida de {sigma_diario * 100:.2f}% ao dia sugeriria "
            f"{bruto * 100:.2f}%, e limiar abaixo do piso transformaria ruido "
            "de papel pouco negociado em movimento relevante")
    elif valor < bruto:
        origem = ORIGEM_TETO
        limitacoes.append(
            f"limiar rebaixado ao teto da classe ({parametros.teto * 100:.2f}%): "
            f"volatilidade medida de {sigma_diario * 100:.2f}% ao dia sugeriria "
            f"{bruto * 100:.2f}%, e exigir isso calaria o motor justamente no "
            "ativo em colapso")
    else:
        origem = ORIGEM_ESTIMADO

    return Limiar(valor=valor, classe=parametros.classe, horizonte_pregoes=h,
                  k=parametros.k, sigma_diario=sigma_diario, n_observacoes=n,
                  estimado=True, origem=origem, limitacoes=tuple(limitacoes))


#: O escalonamento por ``sqrt(h)`` supõe retornos diários independentes. Eles não
#: são: há autocorrelação e agrupamento de volatilidade. A aproximação subestima
#: o limiar em horizonte longo -- e portanto **superestima** a probabilidade de
#: movimento relevante, que é o erro que produz alarme. Fica declarado em vez de
#: corrigido por um fator inventado: corrigir exigiria medir a autocorrelação por
#: classe, e isso é trabalho de :mod:`core.calibracao.metricas` com amostra.
AVISO_RAIZ = ("limiar de horizonte multiplo escalado por raiz do tempo: supoe "
              "retornos independentes e tende a subestimar o limiar")
