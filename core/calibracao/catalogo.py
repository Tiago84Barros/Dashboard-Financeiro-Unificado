"""De onde saem os eventos históricos -- e, sobretudo, de onde eles não saem.

A instrução pede um conjunto de validação histórica cobrindo 15 tipos de evento.
O inventário do armazém local, feito em 02/09/2026 antes de escrever qualquer
linha deste módulo, diz que **isso não é possível hoje**, e este módulo existe
para dizer isso em código em vez de em nota de rodapé.

O que o inventário achou
------------------------
Não existe corpus histórico de notícias. ``noticias_itens`` sequer foi criada no
Supabase: a coleta nasce desligada e nenhum provedor gratuito serve arquivo
retroativo. Um conjunto de validação montado a partir de notícias, portanto, não
tem de onde vir -- e inventá-lo seria o defeito de
``memoria: declaracao-de-rigor-nao-verificada`` outra vez.

O que existe, e é bastante, são **eventos datáveis ponto-no-tempo** já no
armazém: proventos com data-ex, fatos relevantes de FII com data de publicação
na fonte, saídas de bolsa nos EUA. É sobre eles que a calibração é possível, e é
só sobre eles que ela é feita.

Cada fonte carrega suas ressalvas
---------------------------------
Nenhuma das três é uma fonte limpa, e as três impurezas mudam o que a medida
significa:

* **Provento mede o efeito mecânico, não o anúncio.** ``declaration_date`` está
  100% nula nas 100.939 linhas de ``market_us.dividends``; sobra a data-ex, em
  que o preço cai pelo provento por construção. Medir "reação" ali mede
  aritmética. Fica como fonte porque é um teste de sanidade valioso -- o motor
  precisa **não** chamar isso de movimento relevante -- e a ressalva viaja
  escrita.
* **Saída de bolsa foi derivada por ausência.** As 12.107 linhas de
  ``market_us.delistings`` têm ``reason`` constante em
  ``ausencia_de_relatorio_anual``: nenhuma veio de item de 8-K. Não dá para
  separar aquisição de falência, que são desfechos opostos com o mesmo registro
  (``memoria: um-numero-para-desfechos-opostos``), e as 293 refutadas são
  excluídas por :data:`SQL_DESLISTAGEM`.
* **Fato relevante de FII começa em 2022.** É a janela em que a ingestão de
  documentos existe. Uma amostra que começa depois de 2020 não viu nenhuma
  crise sistêmica, e qualquer taxa medida nela herda esse recorte.

O que não tem fonte, e por quê
------------------------------
``resultado_trimestral`` é o caso mais frustrante: ``market.cvm_filing_publications``
tem 10.829 linhas com ``primeira_entrega_em`` -- data de entrega de verdade, que
é exatamente o carimbo ponto-no-tempo que se quer -- e **todas** são categoria
``DFP``, que é anual. O ITR não foi ingerido. Chamar DFP de resultado trimestral
para preencher a linha da tabela seria trocar o tipo de evento pelo tipo que
havia.

Macro (``inflacao``, ``juros_politica_monetaria``, ``cambio``) tem série em
``public.info_economica_mensal``, e ela **não serve**: só há a data de
referência. IPCA de agosto é publicado em setembro, e usar agosto como data do
evento é look-ahead de algumas semanas -- precisamente o viés que a instrução
manda evitar. Sem calendário de divulgação, não há evento macro datável.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text

from core.noticias import taxonomia as tax

logger = logging.getLogger(__name__)

#: Data máxima aceita para um evento. Linhas com data futura existem de verdade
#: no armazém (``market.dividends`` tem data-ex até 2027-05-31, porque provento
#: é anunciado com antecedência) e mediriam retorno que ainda não aconteceu.
#: Quem chama passa "hoje"; o padrão é recusar futuro em relação ao relógio.
def _hoje() -> date:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date()


SQL_DIVIDENDO_B3 = """
    SELECT ticker AS simbolo, ex_date AS data, type AS subtipo
      FROM market.dividends
     WHERE ex_date IS NOT NULL AND ex_date <= :ate
       AND amount IS NOT NULL AND amount > 0
     ORDER BY ex_date
"""

SQL_DIVIDENDO_US = """
    SELECT symbol AS simbolo, ex_date AS data, source AS subtipo
      FROM market_us.dividends
     WHERE ex_date IS NOT NULL AND ex_date <= :ate
       AND amount IS NOT NULL AND amount > 0
     ORDER BY ex_date
"""

SQL_FATO_RELEVANTE_FII = """
    SELECT ticker AS simbolo, source_published_at::date AS data,
           document_type AS subtipo
      FROM market.fii_documents
     WHERE document_type = 'FATO RELEV'
       AND source_published_at IS NOT NULL
       AND source_published_at::date <= :ate
     ORDER BY source_published_at
"""

#: ``refuted_form IS NULL``: uma saída refutada é uma empresa que voltou a
#: entregar relatório. Mantê-la seria contar como morte quem estava vivo -- o
#: erro de ``memoria: foto-truncada-vira-evidencia`` na direção oposta.
SQL_DESLISTAGEM = """
    SELECT symbol AS simbolo, delisted_date AS data, reason AS subtipo
      FROM market_us.delistings
     WHERE delisted_date IS NOT NULL AND delisted_date <= :ate
       AND symbol IS NOT NULL AND refuted_form IS NULL
     ORDER BY delisted_date
"""


@dataclass(frozen=True)
class Fonte:
    """Uma origem de eventos datáveis, com o que ela não consegue provar."""

    tipo_evento: str
    mercado: str
    sql: str
    descricao: str
    coluna_pit: str
    ressalvas: tuple[str, ...] = ()

    @property
    def rotulo(self) -> str:
        return f"{self.tipo_evento} <- {self.descricao} ({self.coluna_pit})"


FONTES: tuple[Fonte, ...] = (
    Fonte("dividendo", "b3", SQL_DIVIDENDO_B3, "market.dividends", "ex_date",
          ("data-ex, nao data do anuncio: mede a queda mecanica do provento",
           "tipos misturados (DIVIDENDO, JCP, RENDIMENTO, AMORTIZACAO)")),
    Fonte("dividendo", "us", SQL_DIVIDENDO_US, "market_us.dividends", "ex_date",
          ("declaration_date nula em 100% das linhas: nao ha data de anuncio",
           "data-ex mede a queda mecanica do provento")),
    Fonte("fato_relevante", "fii", SQL_FATO_RELEVANTE_FII,
          "market.fii_documents", "source_published_at",
          ("cobertura comeca em 2022: a amostra nao viu crise sistemica",
           "so FII; nao ha fato relevante de acao ingerido",
           "o tipo do documento nao diz a direcao do fato")),
    Fonte("deslistagem", "us", SQL_DESLISTAGEM, "market_us.delistings",
          "delisted_date",
          ("saida derivada por ausencia de relatorio, nao por item de 8-K",
           "aquisicao e falencia ficam indistinguiveis na mesma linha",
           "a data e a do fim da evidencia, nao a do anuncio")),
)

#: Motivo declarado para cada tipo da taxonomia que **não** tem fonte. A chave é
#: a mesma de ``core.noticias.taxonomia``; o valor é o que falta, não um
#: "indisponível" genérico. Texto genérico envelhece sem ninguém notar --
#: ``memoria: aviso-que-envelhece-invertido``.
SEM_FONTE: dict[str, str] = {
    "resultado_trimestral":
        "market.cvm_filing_publications tem data de entrega real, mas so "
        "categoria DFP (anual); o ITR nao foi ingerido",
    "guidance": "projecao da companhia nao e documento ingerido",
    "fusao_aquisicao":
        "market_us.delistings nao separa aquisicao de falencia (reason "
        "constante em ausencia_de_relatorio_anual)",
    "mudanca_gestao": "sem fonte de fato societario datavel no armazem",
    "emissao_capital": "sem fonte de oferta datavel no armazem",
    "divida_rating": "sem serie de rating de credito no armazem",
    "litigio_regulatorio": "sem fonte de processo ou sancao no armazem",
    "fraude_governanca": "sem fonte; classificacao depende de texto de noticia",
    "operacional": "sem fonte; classificacao depende de texto de noticia",
    "vacancia_locacao":
        "market.fii_metric_observations tem vacancia, mas por competencia do "
        "relatorio e nao por data de divulgacao",
    "recuperacao_judicial":
        "sem fonte; seria separavel de deslistagem so com item de 8-K",
    "regulacao_setorial": "sem fonte de ato regulatorio datavel",
    "commodity": "sem serie de preco de commodity no armazem",
    "concorrencia": "sem fonte; classificacao depende de texto de noticia",
    "juros_politica_monetaria":
        "public.info_economica_mensal so tem data de referencia; usar o mes de "
        "referencia como data do evento seria look-ahead de semanas",
    "inflacao":
        "public.info_economica_mensal so tem data de referencia; IPCA de agosto "
        "e publicado em setembro",
    "cambio":
        "serie de cambio e diaria e continua: nao ha evento pontual a datar",
    "fiscal_politico": "sem fonte de evento politico datavel",
    "atividade_emprego": "sem calendario de divulgacao no armazem",
    "crise_sistemica":
        "definivel a partir da propria serie de precos, nao de um cadastro; "
        "fica para o Motor de Eventos Extremos, que ja mede regime de mercado",
    "geopolitica": "sem fonte de evento geopolitico datavel",
    "pandemia":
        "sem fonte datavel no armazem; a declaracao da OMS e publica mas nao "
        "esta ingerida, e derivar a data do crash de precos seria datar o "
        "efeito e chamar de causa",
    "quebra_bancaria":
        "sem cadastro de intervencao ou liquidacao do Banco Central no "
        "armazem; market_us.delistings nao separa banco de empresa",
    "evento_climatico":
        "sem serie de desastre no armazem; a base publica do EM-DAT nao foi "
        "ingerida e datar pela cobertura jornalistica mediria a manchete",
    "indefinido": "residuo da taxonomia: nao e um tipo a calibrar",
}


@dataclass(frozen=True)
class Cobertura:
    """O que a calibração alcança e o que ela declaradamente não alcança."""

    com_fonte: tuple[str, ...]
    sem_fonte: dict[str, str] = field(default_factory=dict)

    @property
    def total_tipos(self) -> int:
        return len(tax.TIPOS)

    @property
    def fracao(self) -> float:
        return len(self.com_fonte) / max(1, self.total_tipos)

    def resumo(self) -> str:
        return (f"{len(self.com_fonte)} de {self.total_tipos} tipos da "
                f"taxonomia tem fonte historica ponto-no-tempo "
                f"({self.fracao * 100:.0f}%)")


def cobertura() -> Cobertura:
    """Confere as duas listas contra a taxonomia e recusa buraco silencioso.

    Um tipo novo em ``core.noticias.taxonomia`` que não apareça nem em
    :data:`FONTES` nem em :data:`SEM_FONTE` cai aqui como erro. Sem essa
    checagem, subir a taxonomia deixaria o tipo novo fora da calibração sem
    nenhum sinal -- que é a assinatura de
    ``memoria: verificador-e-escritor-listas-diferentes``.
    """
    com_fonte = tuple(sorted({f.tipo_evento for f in FONTES}))
    conhecidos = set(com_fonte) | set(SEM_FONTE)
    faltando = sorted(t.chave for t in tax.TIPOS if t.chave not in conhecidos)
    if faltando:
        raise RuntimeError(
            "tipos da taxonomia sem declaracao de fonte nem de ausencia: "
            + ", ".join(faltando)
            + " -- declare em core.calibracao.catalogo.SEM_FONTE")
    sobrando = sorted(k for k in conhecidos if k not in tax.POR_CHAVE)
    if sobrando:
        raise RuntimeError(
            "declaracao de fonte para tipo que nao existe na taxonomia: "
            + ", ".join(sobrando))
    return Cobertura(com_fonte=com_fonte, sem_fonte=dict(SEM_FONTE))


def carregar(engine, fonte: Fonte, *, ate: date | None = None,
             limite: int | None = None) -> list[dict]:
    """Lê os eventos de uma fonte no formato de ``construir_memoria_mercado``.

    Só lê. A gravação de qualquer coisa derivada daqui continua sendo assunto de
    ``core.memoria_mercado.repositorio``, que recusa destino remoto.
    """
    corte = ate or _hoje()
    sql = fonte.sql
    if limite:
        sql = f"{sql} LIMIT {int(limite)}"
    eventos: list[dict] = []
    with engine.begin() as conn:
        for linha in conn.execute(text(sql), {"ate": corte}).mappings():
            simbolo = str(linha["simbolo"] or "").strip().upper()
            if not simbolo:
                continue
            quando = str(linha["data"])[:10]
            eventos.append({
                "chave": f"{fonte.tipo_evento}:{simbolo}:{quando}",
                "simbolo": simbolo,
                "tipo_evento": fonte.tipo_evento,
                "data": quando,
                "mercado": fonte.mercado,
                "subtipo": linha.get("subtipo"),
                "fonte": fonte.descricao,
                "ressalvas": list(fonte.ressalvas),
            })
    logger.info("catalogo: %s -> %d eventos", fonte.rotulo, len(eventos))
    return eventos


def montar(engine, *, tipos=None, ate: date | None = None,
           limite_por_fonte: int | None = None) -> dict:
    """Conjunto de validação inteiro, com as ressalvas de cada pedaço.

    Devolve ``eventos`` e ``limitacoes``. As limitações não são decoração: são o
    que impede alguém de ler a tabela de métricas como se todas as linhas
    valessem o mesmo.
    """
    alvo = set(tipos) if tipos else None
    eventos: list[dict] = []
    limitacoes: list[str] = []
    por_tipo: dict[str, int] = {}

    for fonte in FONTES:
        if alvo is not None and fonte.tipo_evento not in alvo:
            continue
        lidos = carregar(engine, fonte, ate=ate, limite=limite_por_fonte)
        eventos.extend(lidos)
        por_tipo[fonte.tipo_evento] = por_tipo.get(fonte.tipo_evento, 0) + len(lidos)
        for ressalva in fonte.ressalvas:
            limitacoes.append(f"{fonte.rotulo}: {ressalva}")

    cob = cobertura()
    for tipo_evento, motivo in sorted(cob.sem_fonte.items()):
        if alvo is not None and tipo_evento not in alvo:
            continue
        if tipo_evento == "indefinido":
            continue
        limitacoes.append(f"{tipo_evento}: sem fonte historica -- {motivo}")

    return {
        "eventos": eventos,
        "por_tipo": por_tipo,
        "cobertura": cob,
        "limitacoes": tuple(limitacoes),
    }
