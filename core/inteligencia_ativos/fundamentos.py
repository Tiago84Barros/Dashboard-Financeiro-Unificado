"""
core/inteligencia_ativos/fundamentos.py
Fundamentos por classe de ativo: o catálogo do que se mede em cada classe,
o formato do dado e o texto que a LLM interpreta. Puro, sem I/O.

Cada classe tem o seu conjunto de indicadores (``CATALOGO``); uma ação não é
lida com as métricas de um FII, nem um CDB com as de uma ação. Quem busca os
números são os leitores de ``fontes_fundamentos``; aqui eles são encaixados
no catálogo da classe, na ordem dele.

Regra única: nenhum número é inventado. Indicador sem dado sai com
``valor=None`` e é mostrado como ``NAO_DISPONIVEL`` ("Dado não disponível."),
na tela e no texto da LLM. O backend entrega o DADO; a INTERPRETAÇÃO é da LLM,
e o texto separa os dois blocos.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ACAO = "acao"
FII = "fii"
RENDA_FIXA = "renda_fixa"
ETF = "etf"

ROTULO_TIPO = {ACAO: "Ação", FII: "Fundo imobiliário", RENDA_FIXA: "Renda fixa",
               ETF: "ETF"}

NAO_DISPONIVEL = "Dado não disponível."

# unidades
PCT = "pct"          # 12.5 = 12,5%  (leitores convertem fração → %)
MOEDA = "moeda"      # valor absoluto na moeda do ativo
X = "x"              # múltiplo (dívida/EBITDA 2,1x)
ANOS = "anos"
DATA = "data"        # ISO AAAA-MM-DD
TEXTO = "texto"
NUMERO = "numero"


@dataclass(frozen=True)
class Metrica:
    chave: str
    rotulo: str
    unidade: str


CATALOGO: dict[str, tuple[Metrica, ...]] = {
    ACAO: (
        Metrica("receita", "Receita líquida", MOEDA),
        Metrica("crescimento_receita", "Crescimento da receita", PCT),
        Metrica("crescimento_lucro", "Crescimento do lucro", PCT),
        Metrica("margem_bruta", "Margem bruta", PCT),
        Metrica("margem_ebit", "Margem EBIT", PCT),
        Metrica("margem_liquida", "Margem líquida", PCT),
        Metrica("lucro_liquido", "Lucro líquido", MOEDA),
        Metrica("roe", "ROE", PCT),
        Metrica("roic", "ROIC", PCT),
        Metrica("fluxo_caixa_operacional", "Geração de caixa operacional", MOEDA),
        Metrica("fluxo_caixa_livre", "Fluxo de caixa livre", MOEDA),
        Metrica("divida_bruta", "Dívida bruta", MOEDA),
        Metrica("divida_liquida", "Dívida líquida", MOEDA),
        Metrica("divida_liquida_ebitda", "Dívida líquida/EBITDA", X),
        Metrica("cobertura_juros", "Cobertura de juros (EBIT/despesa financeira)", X),
        Metrica("payout", "Payout", PCT),
        Metrica("dividend_yield", "Dividend yield", PCT),
        Metrica("dividendos_12m", "Proventos pagos em 12 meses (por ação)", MOEDA),
        Metrica("guidance", "Guidance", TEXTO),
    ),
    FII: (
        Metrica("p_vp", "P/VP", X),
        Metrica("dividend_yield", "Dividend yield (12 meses)", PCT),
        Metrica("ocupacao", "Ocupação", PCT),
        Metrica("vacancia_fisica", "Vacância física", PCT),
        Metrica("vacancia_financeira", "Vacância financeira", PCT),
        Metrica("inadimplencia", "Inadimplência", PCT),
        Metrica("concentracao_locatarios", "Concentração de locatários", TEXTO),
        Metrica("concentracao_geografica", "Concentração geográfica", TEXTO),
        Metrica("prazo_contratos", "Prazo dos contratos", TEXTO),
        Metrica("revisional", "Revisional", TEXTO),
        Metrica("wault", "WAULT", ANOS),
        Metrica("emissoes", "Emissões de cotas", TEXTO),
        Metrica("alavancagem", "Dívida/alavancagem", PCT),
        Metrica("qualidade_ativos", "Qualidade dos ativos", TEXTO),
    ),
    RENDA_FIXA: (
        Metrica("emissor", "Emissor", TEXTO),
        Metrica("risco_credito", "Risco de crédito", TEXTO),
        Metrica("indexador", "Indexador", TEXTO),
        Metrica("duration", "Duration", ANOS),
        Metrica("vencimento", "Vencimento", DATA),
        Metrica("liquidez", "Liquidez", TEXTO),
        Metrica("cobertura_fgc", "Cobertura do FGC", TEXTO),
        Metrica("taxa_contratada", "Taxa contratada", TEXTO),
        Metrica("taxa_mercado", "Taxa de mercado", TEXTO),
        Metrica("marcacao_mercado", "Marcação a mercado", MOEDA),
    ),
    ETF: (
        Metrica("indice", "Índice de referência", TEXTO),
        Metrica("composicao", "Composição", TEXTO),
        Metrica("concentracao", "Concentração", TEXTO),
        Metrica("expense_ratio", "Taxa de administração (expense ratio)", PCT),
        Metrica("tracking_error", "Tracking error", PCT),
        Metrica("liquidez", "Liquidez", TEXTO),
        Metrica("exposicao_geografica", "Exposição geográfica", TEXTO),
        Metrica("exposicao_setorial", "Exposição setorial", TEXTO),
    ),
}

# O que a LLM deve interpretar em cada classe. Vai no bloco INTERPRETAÇÃO:
# são perguntas, não conclusões.
GUIA_INTERPRETACAO: dict[str, tuple[str, ...]] = {
    ACAO: (
        "A empresa cresce com rentabilidade (margens e ROE/ROIC) ou só cresce?",
        "O lucro vira caixa? Compare lucro líquido com a geração de caixa.",
        "O endividamento é confortável (dívida líquida/EBITDA, cobertura de juros)?",
        "Os dividendos são sustentáveis diante do payout e do caixa livre?",
    ),
    FII: (
        "O rendimento é recorrente, dado vacância e inadimplência?",
        "O preço (P/VP) é coerente com a qualidade e o risco do portfólio?",
        "Há risco de concentração em poucos locatários ou regiões?",
        "Emissões e alavancagem diluem ou fortalecem o cotista?",
    ),
    RENDA_FIXA: (
        "O risco de crédito do emissor é compatível com a taxa contratada?",
        "A duration e o vencimento combinam com o horizonte do investidor?",
        "A marcação a mercado indica ganho ou perda em caso de venda antecipada?",
        "A cobertura do FGC e a liquidez atendem à necessidade de liquidez?",
    ),
    ETF: (
        "O índice e a composição entregam a exposição que o papel pede?",
        "O custo (taxa e tracking error) é razoável para essa exposição?",
        "A exposição duplica o que a carteira já tem em outros ativos?",
    ),
}


@dataclass(frozen=True)
class Dado:
    """Um valor lido de uma fonte, com a procedência junto."""
    valor: object
    fonte: str
    referencia: str | None = None   # data ou período do dado
    nota: str | None = None


@dataclass(frozen=True)
class Indicador:
    chave: str
    rotulo: str
    unidade: str
    valor: object = None
    fonte: str | None = None
    referencia: str | None = None
    nota: str | None = None

    @property
    def disponivel(self) -> bool:
        return _tem_valor(self.valor)

    def texto(self, moeda: str = "BRL") -> str:
        if not self.disponivel:
            return NAO_DISPONIVEL
        return formatar(self.valor, self.unidade, moeda)


@dataclass(frozen=True)
class Fundamentos:
    tipo: str | None
    moeda: str
    indicadores: tuple[Indicador, ...] = ()
    motivo: str | None = None    # por que não há catálogo (tipo não coberto)

    @property
    def disponiveis(self) -> tuple[Indicador, ...]:
        return tuple(i for i in self.indicadores if i.disponivel)

    @property
    def ausentes(self) -> tuple[Indicador, ...]:
        return tuple(i for i in self.indicadores if not i.disponivel)

    @property
    def fontes(self) -> tuple[str, ...]:
        vistas: list[str] = []
        for i in self.disponiveis:
            if i.fonte and i.fonte not in vistas:
                vistas.append(i.fonte)
        return tuple(vistas)

    @classmethod
    def de_dict(cls, d: dict | None) -> "Fundamentos":
        """Inverso de ``como_dict`` (a ``Secao`` guarda o dict)."""
        d = d or {}
        return cls(d.get("tipo"), d.get("moeda") or "BRL", tuple(
            Indicador(i["chave"], i["rotulo"], i["unidade"], i.get("valor"),
                      i.get("fonte"), i.get("referencia"), i.get("nota"))
            for i in d.get("indicadores") or ()), d.get("motivo"))

    def indicador(self, chave: str) -> Indicador | None:
        return next((i for i in self.indicadores if i.chave == chave), None)

    def como_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "moeda": self.moeda,
            "motivo": self.motivo,
            "fontes": list(self.fontes),
            "indicadores": [
                {"chave": i.chave, "rotulo": i.rotulo, "unidade": i.unidade,
                 "valor": i.valor if i.disponivel else None,
                 "texto": i.texto(self.moeda), "fonte": i.fonte,
                 "referencia": i.referencia, "nota": i.nota}
                for i in self.indicadores],
        }


def _tem_valor(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def formatar(valor, unidade: str, moeda: str = "BRL") -> str:
    if not _tem_valor(valor):
        return NAO_DISPONIVEL
    if unidade in (TEXTO, DATA):
        return str(valor)
    v = float(valor)
    if unidade == PCT:
        return f"{v:.1f}%".replace(".", ",")
    if unidade == X:
        return f"{v:.2f}x".replace(".", ",")
    if unidade == ANOS:
        return f"{v:.1f} anos".replace(".", ",")
    if unidade == MOEDA:
        simbolo = "US$" if moeda == "USD" else "R$"
        return f"{simbolo} {_compacto(v)}"
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _compacto(v: float) -> str:
    a = abs(v)
    for limite, sufixo in ((1e12, " tri"), (1e9, " bi"), (1e6, " mi"),
                           (1e3, " mil")):
        if a >= limite:
            return f"{v / limite:.2f}{sufixo}".replace(".", ",")
    return f"{v:.2f}".replace(".", ",")


def montar(tipo: str | None, dados: dict[str, Dado], *, moeda: str = "BRL",
           motivo: str | None = None) -> Fundamentos:
    """Encaixa os dados lidos no catálogo da classe, na ordem do catálogo.

    Chave desconhecida levanta: um leitor que devolve métrica de outra classe
    é defeito, não dado a ignorar em silêncio.
    """
    if tipo is None:
        return Fundamentos(None, moeda, (), motivo or
                           "Classe de ativo sem catálogo de fundamentos.")
    catalogo = CATALOGO[tipo]
    conhecidas = {mt.chave for mt in catalogo}
    estranhas = set(dados) - conhecidas
    if estranhas:
        raise ValueError(f"métricas fora do catálogo de {tipo}: "
                         f"{sorted(estranhas)}")
    indicadores = []
    for mt in catalogo:
        d = dados.get(mt.chave)
        if d is None or not _tem_valor(d.valor):
            indicadores.append(Indicador(mt.chave, mt.rotulo, mt.unidade,
                                         nota=d.nota if d else None))
        else:
            indicadores.append(Indicador(mt.chave, mt.rotulo, mt.unidade,
                                         d.valor, d.fonte, d.referencia, d.nota))
    return Fundamentos(tipo, moeda, tuple(indicadores), motivo)


def resumo(f: Fundamentos) -> str:
    """Uma linha para o cartão e para a resposta da pergunta de fundamentos."""
    if f.tipo is None:
        return f.motivo or NAO_DISPONIVEL
    n, total = len(f.disponiveis), len(f.indicadores)
    if n == 0:
        return (f"{ROTULO_TIPO[f.tipo]}: nenhum dos {total} indicadores da "
                f"classe tem dado disponível.")
    destaques = "; ".join(f"{i.rotulo} {i.texto(f.moeda)}"
                          for i in f.disponiveis[:3])
    return (f"{ROTULO_TIPO[f.tipo]}: {n} de {total} indicadores com dado "
            f"({destaques}). A interpretação cabe à análise por LLM.")


def texto(f: Fundamentos, ticker: str) -> str:
    """Bloco da LLM: DADO (do backend) separado de INTERPRETAÇÃO (dela)."""
    linhas = [f"=== FUNDAMENTOS: {ticker} ==="]
    if f.tipo is None:
        linhas.append(f.motivo or NAO_DISPONIVEL)
        return "\n".join(linhas)
    linhas.append(f"Classe: {ROTULO_TIPO[f.tipo]} (indicadores próprios "
                  "desta classe)")
    linhas.append("[DADO — fornecido pelo sistema; não invente valores "
                  f"ausentes: onde estiver \"{NAO_DISPONIVEL}\", diga isso]")
    for i in f.indicadores:
        linha = f"- {i.rotulo}: {i.texto(f.moeda)}"
        extras = [x for x in (i.referencia and f"ref. {i.referencia}",
                              i.fonte and f"fonte: {i.fonte}", i.nota) if x]
        if extras:
            linha += f" ({'; '.join(extras)})"
        linhas.append(linha)
    linhas.append("[INTERPRETAÇÃO — sua tarefa, usando só o bloco DADO]")
    linhas += [f"- {q}" for q in GUIA_INTERPRETACAO[f.tipo]]
    return "\n".join(linhas)


_SEM_CATALOGO = ("Cripto", "Outros")


def tipo_do_ativo(classe: str | None, moeda: str | None = "BRL") -> str | None:
    """Catálogo que se aplica a um rótulo de classe da carteira.

    Os rótulos vêm do mapa único ``calculos.CLASSE_POLITICA``. ETF vem antes
    de tudo: ``ETF Internacional`` é exterior, mas é lido como ETF, não como
    ação. Cripto e Outros não têm catálogo.
    """
    from core.inteligencia_ativos.calculos import CLASSE_POLITICA
    classe = (classe or "").strip()
    if classe.upper().startswith("ETF"):
        return ETF
    politica = CLASSE_POLITICA.get(classe)
    if politica == "renda_fixa":
        return RENDA_FIXA
    if politica == "fiis":
        return FII
    if politica in ("acoes_br", "exterior"):
        return ACAO  # Ações BR e BDR
    if classe not in _SEM_CATALOGO and (moeda or "BRL").upper() != "BRL":
        return ACAO  # ação listada fora do Brasil
    return None
