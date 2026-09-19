"""Orquestrador único da carteira de FIIs sob a proteção ao investidor.

Os portões de proteção acrescentados na metodologia (renda recorrente,
concentração de locatário, vencimentos em 24m) encolheram o universo elegível
abaixo do que bandas de tipo e cardinalidade conseguem resolver: a carteira
zerou. A regra permanente é que nenhuma criação de portfólio termine vazia, e a
resposta é a mesma que a Task 5 deu ao teto de peso por ativo — **a proteção
cede o mínimo necessário e cede visivelmente**, nunca em silêncio.

O que este módulo faz, e por que ele é único:

* tenta primeiro o universo estrito, e o prefere sempre que ele é viável;
* se o estrito voltar ``blocked`` ou com menos de ``policy.max_assets`` ativos,
  readmite candidatos da concessão em ordem crescente de severidade, um a um,
  parando no primeiro número que viabiliza a carteira;
* registra cada readmitido em ``viability_notes`` — mesmo vocabulário da cessão
  de teto da Task 5 — e em ``protecao_cedida_na_elegibilidade``, ticker a
  ticker, com o portão que ele reprovou.

Portões duros (liquidez, histórico, drawdown, faixa de P/VP, teto de
plausibilidade do DY) nunca entram na concessão: quem reprova neles não chega
a ser candidato, porque ``apply_integrated_eligibility`` só oferece à concessão
os fundos reprovados EXCLUSIVAMENTE pelos portões de proteção.

A regra vive aqui e só aqui. Tê-la em cada consumidor é o defeito que já fez a
vitrine publicar número que a tela não reconhecia.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from core.fii_portfolio_v4 import PortfolioPolicy, optimize_diligence_portfolio

STATUS_READMITIDO = "readmitido_por_concessao"


def _sem_pontuacao(rows: list[dict]) -> list[dict]:
    """Pontuação já vem pronta (backtest PIT lê ``type_score`` do snapshot)."""
    return rows


def _tickers(rows: Iterable[dict]) -> list[str]:
    return [str(row.get("ticker") or "") for row in rows]


def _viavel(resultado: dict, policy: PortfolioPolicy) -> bool:
    itens = resultado.get("items") or []
    if resultado.get("status") == "blocked" or not itens:
        return False
    return len(itens) >= int(policy.max_assets)


def montar_carteira_com_concessao(
    estritos: Iterable[dict],
    concessao: Iterable[dict],
    scenario: Any,
    *,
    policy: PortfolioPolicy | None = None,
    score: Callable[[list[dict]], Iterable[dict]] | None = None,
    optimizer_kwargs: dict | Callable[[list[dict]], dict] | None = None,
    optimizer: Callable[..., dict] = optimize_diligence_portfolio,
) -> dict[str, Any]:
    """Monta a carteira cedendo o mínimo de proteção que a viabilidade exigir.

    ``estritos`` são as linhas elegíveis; ``concessao`` são os candidatos de
    ``report["concession_candidates"]``, já em ordem crescente de severidade.
    ``score`` recebe o conjunto de linhas de CADA tentativa — a pontuação é
    relativa ao universo considerado, então readmitir um fundo tem de repontuar
    a tentativa em que ele entra, e a tentativa estrita continua pontuada
    exatamente como antes desta mudança. ``optimizer_kwargs`` pode ser um dicto
    fixo ou um callable que recebe as linhas pontuadas da tentativa (é assim que
    a correlação passa a cobrir os readmitidos).
    """
    policy = policy or PortfolioPolicy()
    score = score or _sem_pontuacao
    estritos = [dict(row) for row in estritos]
    fila: Sequence[dict] = [dict(row) for row in concessao]

    def _tentar(readmitidos: Sequence[dict]) -> dict:
        # O readmitido entra na carteira como readmitido, não como "excluded":
        # o status vinha da elegibilidade estrita e viajava até os itens
        # publicados, onde dizia o contrário do que a carteira fez com ele.
        # `protecao_cedivel` já viaja na linha e é o que a tela, a IA e as
        # explicações leem — não há segunda chave com o mesmo assunto.
        linhas = estritos + [
            {**row, "eligibility_status": STATUS_READMITIDO}
            for row in readmitidos
        ]
        pontuadas = list(score(linhas))
        extras = (optimizer_kwargs(pontuadas) if callable(optimizer_kwargs)
                  else dict(optimizer_kwargs or {}))
        return optimizer(pontuadas, scenario, policy=policy, **extras)

    tentativas: list[dict] = []

    def _registrar(readmitidos: Sequence[dict], resultado: dict) -> list[dict]:
        itens = resultado.get("items") or []
        tentativas.append({
            "readmitidos": _tickers(readmitidos),
            "assets": len(itens),
            "status": str(resultado.get("status") or ""),
        })
        return itens

    def _podar(
        readmitidos: Sequence[dict], resultado: dict,
        aceita: Callable[[dict], bool],
    ) -> tuple[Sequence[dict], dict]:
        """Devolve o menor conjunto readmitido que ainda serve.

        A varredura acha um PREFIXO viável da fila, e prefixo mínimo não é
        conjunto mínimo: o otimizador pode usar o 3º e o 9º readmitidos e
        ignorar os seis do meio, que teriam a proteção cedida sem necessidade
        nenhuma. Aqui tiramos do conjunto quem a carteira resultante não usa e
        re-rodamos; enquanto encolher e `aceita` continuar verdadeiro, o menor
        conjunto vence.

        `aceita` é parâmetro porque a poda também precisa valer no caminho em
        que NENHUMA tentativa alcança a cardinalidade cheia. Ali o critério não
        pode ser `_viavel` — ele é falso por definição — e sim "não perde
        ativo". Sem isso, o regime que mais precisa da concessão era justamente
        o que a publicava inteira, sem poda nenhuma.
        """
        atuais, atual = list(readmitidos), resultado
        while atuais:
            usados = {str(item.get("ticker") or "")
                      for item in (atual.get("items") or [])}
            menores = [row for row in atuais
                       if str(row.get("ticker") or "") in usados]
            if len(menores) == len(atuais):
                return atuais, atual
            candidato = _tentar(menores)
            _registrar(menores, candidato)
            if not aceita(candidato):
                return atuais, atual
            atuais, atual = menores, candidato
        return atuais, atual

    # Um prefixo cujo conjunto inteiro tem menos fundos que
    # `policy.max_assets` NAO pode ser viavel: `_viavel` cobra cardinalidade
    # cheia e o otimizador nao seleciona mais ativos do que existem
    # candidatos. E aritmetica, nao heuristica -- a varredura pode comecar no
    # primeiro prefixo que satisfaz a desigualdade sem trocar o vencedor,
    # porque o menor prefixo viavel continua sendo exatamente o mesmo.
    # Medido na tela com renda recorrente em 12%: 5 estritos e fila de 24
    # faziam 13 montagens completas em 435 s, e a primeira que podia dar certo
    # era a nona. Com o pulo sao 5 montagens em 167 s, mesma carteira.
    primeiro_possivel = max(0, int(policy.max_assets) - len(estritos))
    if primeiro_possivel > len(fila):
        # Nem a fila inteira alcanca a cardinalidade cheia. Nenhum prefixo e
        # viavel e a varredura serve so para escolher a melhor tentativa,
        # entao ela continua inteira -- e o regime das carteiras colapsadas, e
        # ali cada tentativa e barata porque ha poucos candidatos.
        ordem = list(range(len(fila) + 1))
    else:
        # A tentativa estrita continua sendo a PRIMEIRA, sempre. Ela e o
        # conjunto preferido, e o que a nota de viabilidade publica como ponto
        # de partida e e o que a pontuacao tem de ver sozinha; comprar uma
        # montagem para preservar isso e barato ao lado de pular as outras.
        # Os prefixos intermediarios pulados voltam DEPOIS, e so se nada for
        # viavel: ali eles nao disputam viabilidade, disputam o `melhor`, e
        # deixa-los de fora trocaria a carteira entregue no caso extremo.
        ordem = ([0] + list(range(max(1, primeiro_possivel), len(fila) + 1))
                 + list(range(1, primeiro_possivel)))

    melhor: tuple[dict, Sequence[dict], tuple[int, int]] | None = None
    for quantidade in ordem:
        readmitidos = fila[:quantidade]
        resultado = _tentar(readmitidos)
        itens = _registrar(readmitidos, resultado)
        if _viavel(resultado, policy):
            readmitidos, resultado = _podar(
                readmitidos, resultado, lambda r: _viavel(r, policy))
            return _anotar(resultado, readmitidos, fila, tentativas)
        # Mantém a tentativa com mais ativos: se nem a concessão inteira
        # viabilizar a cardinalidade cheia, a carteira ainda não pode voltar
        # vazia — e no empate vence quem readmitiu menos. O desempate é
        # explícito porque a ordem de visita deixou de ser crescente: confiar
        # nela para preferir o estrito era uma dependência invisível que a
        # reordenação acima quebraria em silêncio.
        chave = (len(itens), -quantidade)
        if melhor is None or chave > melhor[2]:
            melhor = (resultado, readmitidos, chave)
    resultado, readmitidos = (melhor[0], melhor[1]) if melhor else ({}, ())
    if readmitidos and (resultado.get("items") or []):
        # Nenhuma tentativa fechou a cardinalidade cheia. A carteira não volta
        # vazia, mas também não publica cessão inútil: poda contra "não perder
        # ativo", que é o que ainda dá para exigir aqui.
        alvo = len(resultado.get("items") or [])
        readmitidos, resultado = _podar(
            readmitidos, resultado,
            lambda r: len(r.get("items") or []) >= alvo,
        )
    return _anotar(resultado, readmitidos, fila, tentativas)


def _anotar(
    resultado: dict,
    readmitidos: Sequence[dict],
    fila: Sequence[dict],
    tentativas: list[dict],
) -> dict:
    """Torna a cessão visível: notas de viabilidade e detalhe por ticker."""
    resultado = dict(resultado)
    na_carteira = {str(item.get("ticker") or "")
                   for item in (resultado.get("items") or [])}
    detalhe = [{
        "ticker": str(row.get("ticker") or ""),
        "motivos": list(row.get("protecao_cedivel") or ()),
        "severidade": (row.get("severidade_da_concessao") or (0, 0, 0))[0],
        "na_carteira": str(row.get("ticker") or "") in na_carteira,
    } for row in readmitidos]
    notas = list(resultado.get("viability_notes") or [])
    if detalhe:
        notas.append(
            "proteção ao investidor cedida na elegibilidade para viabilizar a "
            f"carteira ({len(detalhe)} de {len(fila)} candidatos readmitidos, "
            "na ordem crescente de severidade): "
            + "; ".join(f"{item['ticker']} — {', '.join(item['motivos'])}"
                        for item in detalhe)
            + ". Proteção cedida não é ausência de risco."
        )
    resultado["viability_notes"] = list(dict.fromkeys(notas))
    resultado["protecao_cedida_na_elegibilidade"] = detalhe
    resultado["concessao_de_elegibilidade"] = {
        "usada": bool(detalhe),
        "disponiveis": len(fila),
        "readmitidos": [item["ticker"] for item in detalhe],
        "tentativas": tentativas,
    }
    return resultado
