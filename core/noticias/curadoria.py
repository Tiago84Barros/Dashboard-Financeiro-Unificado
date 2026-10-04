"""Curadoria do noticiário geral que vai para o prompt: relevância com cota.

Por que existe (auditoria app4, LLM-A5, 04/10/2026)
---------------------------------------------------
O bloco de mercado lia os 150 itens MAIS NOVOS do acervo e ordenava por
``(cita o Brasil, nota)``. Os 150 mais novos cobrem só as últimas horas -- dos
3.388 avaliados nos 3 dias, 4% --, e o Brasil-primeiro punha qualquer nota
baixa com ``BR`` à frente de qualquer nota alta sem. Em 04/10/2026, dia de
eleição, as 12 manchetes que o modelo recebia eram 12 sobre a eleição ou
coluna da NeoFeed. A pergunta "e o Fed?" não tinha o que ler.

Aqui a ordem é pela nota, com três freios:

* **um item por evento** (``evento_id``): a descoberta da Petrobras na Margem
  Equatorial vinha em 4 veículos com o mesmo evento e notas 69-71, e levava
  4 das 12 vagas;
* **cota por veículo e por tipo de evento** (3 cada): nenhum tema ou casa
  editorial ocupa o bloco inteiro. Se as cotas deixarem vaga sobrando -- janela
  pobre --, a segunda passada preenche sem elas: cota é para diversificar, não
  para esconder fato;
* **publieditorial fora**, pelos padrões medidos abaixo.

Puro: sem banco, sem rede.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Mapping

#: Cada padrão foi medido sobre o acervo local inteiro (32.405 itens,
#: 04/10/2026), no TÍTULO -- no resumo os mesmos padrões pegam matéria séria
#: ("alavancagem de até 500 vezes" numa reportagem sobre Forex).
PUBLIEDITORIAL: tuple[tuple[str, re.Pattern], ...] = (
    # 5 títulos, os 5 venda de assinatura (Money Times, Seu Dinheiro):
    # "buscar multiplicações de até 270 vezes para você".
    ("promessa de multiplicação",
     re.compile(r"(?i)\b(?:at[eé]|up to)\s+(?:R\$\s*)?\d[\d.,]*\s*(?:mil\s+)?vezes\b")),
    # 2 títulos, ambos isca de cripto do Seu Dinheiro ("janela de oportunidade
    # pode abrir em outras moedas; veja quais"). Sozinha a expressão aparece em
    # análise séria, por isso só junto de cripto.
    ("isca de cripto",
     re.compile(r"(?i)janela de oportunidade.{0,80}\b(?:moedas?|cripto\w*|bitcoin|tokens?)\b"
                r"|\b(?:bitcoin|cripto\w*|BTC)\b.{0,80}janela de oportunidade")),
    # 26 títulos (Yahoo Finance sindicando 24/7 Wall St., CoinGape): alvo de
    # preço sem fato, "Seagate Stock Will Hit $1000 on This Date". Um deles
    # era pré-venda de token ("Buyers Enter Before Its $0.14 Listing").
    ("previsão de preço sem fato", re.compile(r"(?i)\bprice predictions?\b")),
    ("pré-venda de token", re.compile(r"(?i)before its \$?[\d.]+ listing|\bpre-?sale\b")),
    # 104 títulos, todos captação de cliente por escritório de advocacia
    # (Faruqi, Levi & Korsinsky, Bronstein...): "Investors Have Until October
    # 13th", "Urges ... Investors to Contact the Firm". O processo pode ser
    # fato; o release é anúncio, e as notas 72-79 o punham no topo.
    ("captação de escritório de advocacia",
     re.compile(r"(?i)\b(?:investors?|shareholders?|stockholders?)\b[^.]{0,60}"
                r"\b(?:who (?:lost|suffered)|have until|lost money|to contact)"
                r"|\b(?:urges?|reminds?|encourages?|alerts?)\b[^.]{0,80}"
                r"\b(?:investors?|shareholders?|stockholders?)\b"
                r"|securities (?:fraud|class action)|class action (?:lawsuit|notice)"
                r"|\b(?:shareholder|investor) alert\b|\blead plaintiff\b")),
)
#: Caminho de URL que o próprio veículo usa para conteúdo pago (1 item medido,
#: Automotive News ``/sponsored/``).
_URL_PATROCINADA = re.compile(r"/(?:sponsored|patrocinado|publieditorial|conteudo-patrocinado)/",
                              re.IGNORECASE)

COTA_POR_VEICULO = 3
COTA_POR_TIPO = 3


def motivo_publieditorial(item: Mapping) -> str | None:
    """Por que o item é anúncio, ou ``None``. Só título e URL -- ver acima."""
    titulo = str(item.get("titulo") or "")
    for motivo, padrao in PUBLIEDITORIAL:
        if padrao.search(titulo):
            return motivo
    if _URL_PATROCINADA.search(str(item.get("url") or "")):
        return "URL de conteúdo patrocinado"
    return None


def _nota(item: Mapping) -> float:
    try:
        return float(item.get("nota") or 0)
    except (TypeError, ValueError):
        return 0.0


def _quando(item: Mapping) -> str:
    return str(item.get("publicado_em") or item.get("coletado_em") or "")


def curar(itens: Iterable[Mapping], limite: int, *,
          cota_veiculo: int = COTA_POR_VEICULO,
          cota_tipo: int = COTA_POR_TIPO,
          chave_titulo=None,
          reserva: tuple[Callable[[Mapping], bool], int] | None = None,
          ) -> tuple[list[Mapping], dict[str, int]]:
    """``(escolhidos, descartes)``: até ``limite`` itens, nota primeiro, com cota.

    ``descartes`` conta o que saiu por publieditorial, por motivo -- vai para a
    linha de origem do bloco, porque item filtrado em silêncio é a mesma
    ausência sem nome que o módulo de contexto proíbe.

    ``chave_titulo`` normaliza o título para o dedup (o chamador já tem a sua);
    sem ela, compara o título cru.

    ``reserva=(predicado, n)`` garante até ``n`` vagas, pela nota, aos itens que
    satisfazem o predicado -- o bloco de mercado reserva para o Brasil. É piso,
    não prioridade: o antigo "Brasil primeiro" punha qualquer nota baixa com
    ``BR`` à frente de qualquer nota alta sem.
    """
    chave = chave_titulo or (lambda t: str(t or "").strip())
    # Empate de nota: o mais novo primeiro.
    ordenados = sorted(itens, key=lambda i: (_nota(i), _quando(i)), reverse=True)
    descartes: dict[str, int] = {}
    candidatos: list[Mapping] = []
    titulos: set[str] = set()
    eventos: set[str] = set()
    for item in ordenados:
        motivo = motivo_publieditorial(item)
        if motivo:
            descartes[motivo] = descartes.get(motivo, 0) + 1
            continue
        titulo = chave(item.get("titulo"))
        evento = str(item.get("evento_id") or "")
        if not titulo or titulo in titulos or (evento and evento in eventos):
            continue
        titulos.add(titulo)
        if evento:
            eventos.add(evento)
        candidatos.append(item)

    escolhidos: list[Mapping] = []
    ja: set[int] = set()
    por_veiculo: dict[str, int] = {}
    por_tipo: dict[str, int] = {}

    def _passada(teto: int, aceita) -> None:
        for item in candidatos:
            if len(escolhidos) >= teto:
                return
            if id(item) in ja or not aceita(item):
                continue
            veiculo = str(item.get("veiculo") or "").strip().lower()
            tipo = str(item.get("tipo_evento") or "").strip().lower()
            if veiculo and por_veiculo.get(veiculo, 0) >= cota_veiculo:
                continue
            if tipo and por_tipo.get(tipo, 0) >= cota_tipo:
                continue
            por_veiculo[veiculo] = por_veiculo.get(veiculo, 0) + 1
            por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
            escolhidos.append(item)
            ja.add(id(item))

    if reserva is not None:
        predicado, vagas = reserva
        _passada(min(int(vagas), limite), predicado)
    _passada(limite, lambda _i: True)
    # Janela pobre: cota não pode deixar vaga vazia com fato disponível.
    for item in candidatos:
        if len(escolhidos) >= limite:
            break
        if id(item) not in ja:
            escolhidos.append(item)
            ja.add(id(item))
    escolhidos.sort(key=lambda i: (_nota(i), _quando(i)), reverse=True)
    return escolhidos, descartes
