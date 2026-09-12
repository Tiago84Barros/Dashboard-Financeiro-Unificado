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
        linhas = estritos + [
            {**row, "protecao_cedida": tuple(row.get("protecao_cedivel") or ())}
            for row in readmitidos
        ]
        pontuadas = list(score(linhas))
        extras = (optimizer_kwargs(pontuadas) if callable(optimizer_kwargs)
                  else dict(optimizer_kwargs or {}))
        return optimizer(pontuadas, scenario, policy=policy, **extras)

    tentativas: list[dict] = []
    melhor: tuple[dict, Sequence[dict]] | None = None
    for quantidade in range(len(fila) + 1):
        readmitidos = fila[:quantidade]
        resultado = _tentar(readmitidos)
        itens = resultado.get("items") or []
        tentativas.append({
            "readmitidos": _tickers(readmitidos),
            "assets": len(itens),
            "status": str(resultado.get("status") or ""),
        })
        if _viavel(resultado, policy):
            return _anotar(resultado, readmitidos, fila, tentativas)
        # Mantém a tentativa com mais ativos: se nem a concessão inteira
        # viabilizar a cardinalidade cheia, a carteira ainda não pode voltar
        # vazia — e a preferência pelo estrito sobrevive ao empate, porque só
        # trocamos quando o número de ativos cresce.
        if melhor is None or len(itens) > len(melhor[0].get("items") or []):
            melhor = (resultado, readmitidos)
    resultado, readmitidos = melhor if melhor else ({}, ())
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
