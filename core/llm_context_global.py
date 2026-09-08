"""Contexto determinístico e auditável para o chat do Portfólio Global.

Este módulo NÃO decide nada e NÃO busca nada: recebe exatamente o que a tela
já calculou (`views/portfolio_global.render`) e devolve texto. É a regra
"medir a fonte que a decisão lê" aplicada ao chat — se o painel de risco na
tela mostra vol anual de 18,4%, o LLM precisa ler 18,4%, não um número
recalculado por outro caminho que pode divergir em silêncio.

As agregações que o builder faz por conta própria (`concentration.resumo`,
`metrics.*`, `correlation.*`, `risk.*`) são as MESMAS funções puras que os
painéis chamam, com os MESMOS argumentos (`df`, `retornos`, `pesos`) — mesma
entrada, mesma saída, sem lógica duplicada aqui. O que depende de I/O
(papéis, recomendações do motor) é passado pronto pelo chamador, justamente
para não existir uma segunda versão da consulta.

Ausência é sempre escrita como "ausente", nunca como 0 — o LLM não pode
confundir "não sabemos" com "vale zero".

Coberto por tests/test_llm_context_global.py.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import pandas as pd

from core.global_portfolio import concentration, correlation, metrics, risk, roles
from core.global_portfolio.fields import valor as campo_valor
from core.portfolio.registry import get_spec

# Teto de linhas por bloco: o contexto vai inteiro no prompt e um patrimônio
# grande estouraria a janela. Posições e papéis são o que mais cresce.
_MAX_POSICOES = 80
_MAX_PARES = 15
_MAX_PAPEIS = 60
_MAX_ACOES = 60


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _f(value: Any, casas: int = 2) -> str:
    """Número em formato brasileiro (milhar com ponto, decimal com vírgula)."""
    number = _num(value)
    if number is None:
        return "ausente"
    texto = f"{number:,.{casas}f}"
    return texto.replace(",", "@").replace(".", ",").replace("@", ".")


def _pct(value: Any, casas: int = 2) -> str:
    """Fração (0-1) formatada como percentual, decimal com vírgula."""
    number = _num(value)
    if number is None:
        return "ausente"
    return f"{number * 100:.{casas}f}".replace(".", ",") + "%"


def _classe(codigo: Any) -> str:
    texto = str(codigo or "").strip()
    if not texto:
        return "sem classe"
    try:
        return get_spec(texto).label
    except Exception:  # noqa: BLE001 - classe fora do registry nao pode derrubar o chat
        return texto


def _bloco_patrimonio(df: pd.DataFrame, alvos: dict | None, total_brl: Any) -> list[str]:
    linhas = ["=== PATRIMONIO CONSOLIDADO ==="]
    total = _num(total_brl)
    linhas.append(f"Patrimônio total informado: R$ {_f(total)}" if total is not None
                  else "Patrimônio total informado: ausente")
    linhas.append(f"Ativos com posição: {len(df)}")

    if df.empty:
        return linhas

    real = df.groupby("asset_class")["weight_global"].sum().to_dict()
    alvos = alvos or {}
    classes = sorted({str(c) for c in real} | {str(c) for c in alvos})
    linhas.append("Alvo x real por classe (peso do patrimônio):")
    for classe in classes:
        alvo = _num(alvos.get(classe))
        atual = _num(real.get(classe)) or 0.0
        desvio = "ausente" if alvo is None else _pct(atual - alvo)
        linhas.append(
            f"- {_classe(classe)}: alvo {'ausente' if alvo is None else _pct(alvo)} | "
            f"real {_pct(atual)} | desvio {desvio}"
        )
    return linhas


def _bloco_posicoes(df: pd.DataFrame) -> list[str]:
    linhas = ["", "=== POSICOES (ordenadas por peso) ==="]
    if df.empty:
        linhas.append("Nenhuma posição.")
        return linhas

    ordenado = df.sort_values("weight_global", ascending=False)
    if len(ordenado) > _MAX_POSICOES:
        linhas.append(
            f"Mostrando as {_MAX_POSICOES} maiores de {len(ordenado)} posições "
            "(as demais existem, mas ficaram fora por limite de contexto)."
        )
        ordenado = ordenado.head(_MAX_POSICOES)

    for registro in ordenado.to_dict(orient="records"):
        payload = registro.get("payload") or {}
        classe = registro.get("asset_class")
        fundamentos = []
        for campo, rotulo, pct in (("pe", "P/L", False), ("pvp", "P/VP", False),
                                   ("dy", "DY", True), ("roe", "ROE", True)):
            bruto = campo_valor(payload, classe, campo)
            if bruto is None:
                continue
            fundamentos.append(f"{rotulo} {_pct(bruto) if pct else _f(bruto)}")
        marcadores = payload.get("metrics") or {}
        nota = next((f"{k} {_f(v, 1)}" for k, v in marcadores.items()
                     if str(k).endswith("score") and _num(v) is not None), None)
        detalhe = " | ".join(fundamentos + ([nota] if nota else [])) or "sem fundamentos no snapshot"
        linhas.append(
            f"- {registro.get('symbol')} ({registro.get('name') or 'sem nome'}) | "
            f"{_classe(classe)} | setor {registro.get('sector') or 'ausente'} | "
            f"{registro.get('country') or '?'}/{registro.get('currency') or '?'} | "
            f"peso global {_pct(registro.get('weight_global'))} | "
            f"peso na classe {_pct(registro.get('weight_class'))} | "
            f"R$ {_f(registro.get('valor_brl'))} | {detalhe}"
        )
    return linhas


def _bloco_concentracao(df: pd.DataFrame) -> list[str]:
    linhas = ["", "=== CONCENTRACAO ==="]
    if df.empty:
        linhas.append("Sem posições.")
        return linhas
    for dimensao, dados in concentration.resumo(df).items():
        maior = dados.get("maior_nome")
        # Na dimensao de classe o "nome" e o codigo interno (b3/us/fii); o
        # rotulo e o que o usuario ve na tela.
        nome = _classe(maior) if (dimensao == "asset_class" and maior) else (maior or "ausente")
        linhas.append(
            f"- {dimensao}: HHI {_f(dados.get('hhi'), 4)} | "
            f"número efetivo {_f(dados.get('numero_efetivo'), 1)} | "
            f"maior = {nome} ({_pct(dados.get('maior_peso'))})"
        )
    return linhas


def _bloco_metricas(df: pd.DataFrame) -> list[str]:
    linhas = ["", "=== METRICAS AGREGADAS ==="]
    if df.empty:
        linhas.append("Sem posições.")
        return linhas

    pe = metrics.valuation_agregado(df, "pe")
    pvp = metrics.valuation_agregado(df, "pvp")
    dy = metrics.dy_consolidado(df)
    for rotulo, metrica, pct in (("P/L agregado", pe, False),
                                 ("P/VP agregado", pvp, False),
                                 ("Dividend yield", dy, True)):
        valor = _pct(metrica.valor) if pct else _f(metrica.valor)
        linhas.append(
            f"- {rotulo}: {valor} | cobertura {_pct(metrica.cobertura)} do peso | "
            f"{metrica.n_ativos} ativo(s) | "
            f"{'confiável' if metrica.confiavel else 'cobertura abaixo do mínimo'}"
        )
    linhas.append(
        "P/L e P/VP usam earnings yield ponderado, invertido no fim; média "
        "aritmética de múltiplos seria incorreta."
    )

    por_classe = metrics.qualidade_por_classe(df)
    if por_classe:
        linhas.append("Qualidade média por classe (escalas NÃO comparáveis entre classes):")
        for classe in sorted(por_classe):
            m = por_classe[classe]
            linhas.append(
                f"- {_classe(classe)}: {_f(m.valor, 1)} | cobertura {_pct(m.cobertura)} | "
                f"{m.n_ativos} ativo(s)"
            )
    return linhas


def _bloco_cobertura(cobertura: Any) -> list[str]:
    linhas = ["", "=== SERIE MENSAL DE RETORNOS (base das estatisticas) ==="]
    if cobertura is None:
        linhas.append("Cobertura da série não informada.")
        return linhas

    periodo = getattr(cobertura, "periodo_comum", None)
    janela = f"{periodo[0]} a {periodo[1]}" if periodo else "ausente"
    linhas.append(
        f"Meses na janela comum: {getattr(cobertura, 'meses', 0)} | "
        f"período {janela} | "
        f"peso coberto {_pct(getattr(cobertura, 'peso_coberto', None))} | "
        f"moeda base {getattr(cobertura, 'base_currency', 'ausente')}"
    )
    sem_serie = tuple(getattr(cobertura, "simbolos_sem_serie", ()) or ())
    if sem_serie:
        linhas.append(
            "Ativos SEM série de preço (fora de toda estatística abaixo): "
            + ", ".join(sem_serie)
        )
    sem_fx = tuple(getattr(cobertura, "simbolos_sem_fx", ()) or ())
    if sem_fx:
        linhas.append("Ativos sem câmbio: " + ", ".join(sem_fx))
    stale = tuple(getattr(cobertura, "simbolos_fx_stale", ()) or ())
    if stale:
        linhas.append("Ativos com câmbio desatualizado: " + ", ".join(stale))
    return linhas


def _bloco_risco(retornos: pd.DataFrame | None, pesos: dict | None) -> list[str]:
    linhas = ["", "=== RISCO E CORRELACAO ==="]
    if retornos is None or retornos.empty or not pesos:
        linhas.append("Sem série mensal suficiente para risco e correlação.")
        return linhas

    r = risk.metricas_de_risco(retornos, pesos)
    if r is None:
        linhas.append("Métricas de risco indisponíveis para esta janela.")
    else:
        linhas.append(
            f"Volatilidade mensal {_pct(r.vol_mensal)} | anual {_pct(r.vol_anual)} | "
            f"VaR 95% {_pct(r.var_95)} | CVaR 95% {_pct(r.cvar_95)} | "
            f"drawdown máximo {_pct(r.drawdown_max)} | {r.n_obs} observações "
            f"({r.n_cauda} na cauda)"
        )

    linhas.append(
        f"Correlação média entre ativos: {_f(correlation.correlacao_media(retornos), 3)} | "
        f"razão de diversificação: {_f(correlation.razao_diversificacao(retornos, pesos), 3)} | "
        f"apostas efetivas: {_f(correlation.apostas_efetivas(retornos, pesos), 2)}"
    )

    pares = correlation.pares_redundantes(retornos)
    if pares:
        linhas.append("Pares redundantes (correlação alta):")
        for par in list(pares)[:_MAX_PARES]:
            try:
                a, b, valor = par
            except (TypeError, ValueError):
                linhas.append(f"- {par}")
                continue
            linhas.append(f"- {a} x {b}: {_f(valor, 3)}")

    contribuicoes = risk.contribuicao_marginal(retornos, pesos)
    if contribuicoes:
        # `contribuicao` sai em unidade de volatilidade (a soma fecha em
        # sigma_p, Euler). Dividida por sigma_p vira FATIA da volatilidade,
        # que e a unica leitura comparavel com o peso do ativo — mandar a
        # unidade crua faria o modelo ler "1,64%" contra "peso 45%" e
        # concluir o oposto do que o numero diz.
        sigma_p = sum(_num(getattr(c, "contribuicao", None)) or 0.0
                      for c in contribuicoes.values())
        linhas.append(
            "Fatia da volatilidade do portfólio explicada por cada ativo "
            "(soma 100%; comparar com o peso):"
        )
        ordenado = sorted(contribuicoes.items(),
                          key=lambda item: _num(getattr(item[1], "contribuicao", None)) or 0.0,
                          reverse=True)
        for symbol, c in ordenado:
            bruto = _num(getattr(c, "contribuicao", None))
            fatia = (bruto / sigma_p) if (bruto is not None and sigma_p > 0) else None
            linhas.append(
                f"- {symbol}: {_pct(fatia)} do risco | peso {_pct(pesos.get(symbol))}"
            )
        ausentes = sorted(set(pesos) - set(contribuicoes))
        if ausentes:
            linhas.append(
                "Ativos fora da decomposição de risco (série curta ou sem "
                "sobreposição suficiente): " + ", ".join(ausentes)
            )
    return linhas


def _bloco_papeis(papeis: Sequence[Any]) -> list[str]:
    linhas = ["", "=== PAPEL ESTRATEGICO POR ATIVO ==="]
    if not papeis:
        linhas.append("Nenhum papel classificado (sem série mensal ou sem posições).")
        return linhas

    rotulos = getattr(roles, "ROTULOS_PAPEL", {})
    for entrada in list(papeis)[:_MAX_PAPEIS]:
        nomes = [rotulos.get(p, p) for p in getattr(entrada, "papeis", ()) or ()]
        indeterminados = getattr(entrada, "indeterminados", ()) or ()
        linhas.append(
            f"- {getattr(entrada, 'symbol', '?')}: "
            f"{', '.join(nomes) if nomes else 'NENHUM papel com evidência suficiente'}"
            + (f" | indeterminado: {', '.join(indeterminados)}" if indeterminados else "")
        )
        # A causa da lacuna, quando conhecida, entra junto: sem ela o modelo
        # tende a ler "indeterminado" como "o ativo nao cumpre".
        for papel, motivo in getattr(entrada, "motivos_indeterminado", ()) or ():
            linhas.append(f"    motivo ({rotulos.get(papel, papel)}): {motivo}")
    return linhas


def _bloco_recomendacoes(acoes: Sequence[Any]) -> list[str]:
    linhas = ["", "=== RECOMENDACOES DO MOTOR DE MOVIMENTACAO ==="]
    if not acoes:
        linhas.append(
            "O motor não produziu recomendações nesta sessão (série mensal "
            "insuficiente ou falha isolada do motor)."
        )
        return linhas

    linhas.append(
        "'indeterminado' significa que NENHUM sinal existiu para o ativo — não é "
        "o mesmo que 'manter', que é uma decisão tomada com evidência."
    )
    for acao in list(acoes)[:_MAX_ACOES]:
        # `custo_estimado` esta em REAIS (advisor._resolver_custo multiplica o
        # delta de peso pelo patrimonio), nao em fracao — e `math.nan` quando
        # a classe nao tem parametros calibrados. Mesma leitura de
        # `views/portfolio_global.texto_de_custo`.
        if getattr(acao, "acao", "") == "indeterminado":
            custo = "não aplicável (sem sinal para este ativo)"
        elif not getattr(acao, "custo_calibrado", True):
            custo = "não calibrado (classe sem parâmetros; o motor se recusa a mexer)"
        else:
            custo = f"R$ {_f(getattr(acao, 'custo_estimado', None))}"
        score = getattr(acao, "score", None)
        analisadores = sorted(getattr(acao, "analisadores", ()) or ())
        linhas.append(
            f"- {getattr(acao, 'symbol', '?')}: {getattr(acao, 'acao', '?')} | "
            f"peso {_pct(getattr(acao, 'peso_atual', None))} -> "
            f"{_pct(getattr(acao, 'peso_sugerido', None))} | "
            f"score {'ausente' if score is None else _f(score, 3)} | custo {custo} | "
            f"analisadores: {', '.join(analisadores) if analisadores else 'nenhum'}"
        )
    return linhas


def build_global_portfolio_context(
    df: pd.DataFrame | None,
    *,
    alvos: dict | None = None,
    total_brl: Any = None,
    retornos: pd.DataFrame | None = None,
    cobertura: Any = None,
    pesos: dict | None = None,
    papeis: Iterable[Any] = (),
    acoes: Iterable[Any] = (),
) -> str:
    """Texto do contexto que vai ao LLM no chat do Portfólio Global.

    `df` é o quadro de `montar_posicoes`; `retornos`/`cobertura` vêm de
    `retornos_mensais`; `papeis` de `roles.classificar`; `acoes` do motor de
    movimentação. Tudo opcional: sem série mensal o contexto ainda descreve
    composição, concentração e métricas, dizendo explicitamente o que falta.
    """
    if df is None:
        df = pd.DataFrame()

    partes: list[str] = [
        "Dados reais do Portfólio Global do usuário, lidos dos snapshots "
        "persistidos das três carteiras-modelo (ações B3, FIIs e ações "
        "americanas) consolidados como um único patrimônio.",
        "",
    ]
    partes += _bloco_patrimonio(df, alvos, total_brl)
    partes += _bloco_posicoes(df)
    partes += _bloco_concentracao(df)
    partes += _bloco_metricas(df)
    partes += _bloco_cobertura(cobertura)
    partes += _bloco_risco(retornos, pesos)
    partes += _bloco_papeis(list(papeis))
    partes += _bloco_recomendacoes(list(acoes))
    return "\n".join(partes)
