"""Calibra o Motor Conjuntural contra os eventos datáveis do armazém local.

O que este script faz, e o que ele deliberadamente não faz
==========================================================
A instrução pede um conjunto de validação com 15 tipos de evento. Ele não
existe e não é obtível: não há corpus histórico de notícias neste projeto --
``noticias_itens`` nasce vazia e os provedores gratuitos não servem arquivo
retroativo. Inventar rótulo de notícia para o passado seria repetir
``memoria: declaracao-de-rigor-nao-verificada``: publicar rigor que ninguém
mediu.

Então a calibração é ancorada no que existe de verdade: eventos **datáveis com
carimbo de publicação** já ingeridos no armazém, listados em
:mod:`core.calibracao.catalogo`. Hoje isso cobre 3 dos 25 tipos da taxonomia, e
o relatório publica os 12% na primeira linha em vez de escondê-los no rodapé.

Ponto-no-tempo, em dois lugares
-------------------------------
Um: a estimativa do evento *i* só usa eventos anteriores a *i* -- e, mais que
isso, só eventos cuja **janela já fechou** antes de *i*. Um evento de dez dias
antes ainda não tem retorno de 20 pregões no dia de *i*; usá-lo é look-ahead que
sobrevive a qualquer revisão que só olhe datas de evento.

Dois: o limiar de movimento relevante sai da volatilidade dos 60 pregões
**anteriores** ao evento (``EventoMedido.volatilidade_pre``), por classe de
ativo. Um limiar único recriaria dentro da própria métrica o viés que
:mod:`core.calibracao.limiar` existe para remover.

Nada aqui grava
---------------
O script lê o armazém e escreve um JSON local. Promoção de pesos é decisão
separada, e os portões de :mod:`core.calibracao.pesos` são o que a autoriza --
com turnover e risco não simulados, eles devolvem "não medido", o conjunto não
é promovido, e o prior segue valendo. Esse é o desfecho esperado hoje.

Uso
---
    python scripts/calibrar_motor_conjuntural.py --limite 4000
    python scripts/calibrar_motor_conjuntural.py --mercado fii --horizonte 5
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, timedelta
from math import sqrt
from pathlib import Path
from statistics import median

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine  # noqa: E402

from core.calibracao import CALIBRACAO_VERSAO  # noqa: E402
from core.calibracao import catalogo as cat  # noqa: E402
from core.calibracao import limiar as lim  # noqa: E402
from core.calibracao import metricas as met  # noqa: E402
from core.calibracao import pesos as pes  # noqa: E402
from core.memoria_mercado import retornos as ret  # noqa: E402
from scripts.construir_memoria_mercado import (  # noqa: E402
    construir,
    verificar_fonte,
    warehouse_url,
)

logger = logging.getLogger("calibracao")

#: Pregões por dia corrido, para saber se a janela de um evento anterior já
#: fechou quando o evento atual acontece. É aproximação -- 252 pregões em 365
#: dias -- e por isso ela arredonda **para cima** o tempo de espera: errar para
#: o lado de descartar evidência é o único erro que não vira look-ahead.
DIAS_CORRIDOS_POR_PREGAO = 1.55

#: Retorno anormal acima do qual a observação é defeito de série, não evento.
#: Não é uma faixa de validação que apaga evidência
#: (``memoria: faixa-de-validacao-apaga-evidencia``): o descartado é **contado e
#: publicado** no relatório. O motivo é medido: em ``market.fii_b3_security_history``
#: há 581 retornos diários acima de 50% e o maior é +305.900% (BRCR12 em
#: 25/02/2013), porque a série mistura cotas e recibos da mesma família (BRCR11
#: com BRCR12, XPHT11 com XPHT14). Um desses eventos sozinho move o erro médio
#: mais que mil eventos honestos -- é ``memoria: preco-bilionario-e-retroajuste``
#: aparecendo do lado de quem lê a série.
TETO_PLAUSIBILIDADE_ANORMAL = 1.0

#: Mínimo de eventos anteriores para o walk-forward emitir estimativa. Abaixo
#: disso não se publica probabilidade -- publica-se ausência.
MINIMO_ANTERIORES = 20

#: De qual fonte do catálogo sai qual mercado de preços.
MERCADO_DE_PRECOS = {"b3": "b3", "us": "us", "fii": "fii"}

CLASSE_DO_MERCADO = {"b3": lim.CLASSE_ACAO_B3,
                     "us": lim.CLASSE_ACAO_US,
                     "fii": lim.CLASSE_FII}


# ─────────────────────────────────────────────────────────────────────────────
# Limiar por evento
# ─────────────────────────────────────────────────────────────────────────────
def limiar_do_evento(evento, mercado: str, horizonte: int) -> lim.Limiar:
    """Limiar do próprio ativo, estimado só com o que existia antes do evento.

    ``volatilidade_pre`` é anualizada sobre os 60 pregões anteriores; dividir
    por ``sqrt(252)`` devolve o desvio diário que :mod:`core.calibracao.limiar`
    espera. Quando ela não foi medida, o limiar cai no prior da classe e sai
    marcado como não estimado -- o que faz o evento entrar no relatório com a
    ressalva, e não sair dele em silêncio.
    """
    anual = getattr(evento, "volatilidade_pre", None)
    sigma = (anual / sqrt(252.0)) if anual else None
    return lim.calcular(
        classe=CLASSE_DO_MERCADO.get(mercado, lim.CLASSE_DESCONHECIDA),
        horizonte_pregoes=horizonte,
        sigma_diario=sigma,
        n_observacoes=(60 if sigma else 0),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward
# ─────────────────────────────────────────────────────────────────────────────
def _janela_fechada_em(evento, horizonte: int) -> date:
    base = evento.data_pregao_zero or evento.data_evento
    return base + timedelta(days=int(horizonte * DIAS_CORRIDOS_POR_PREGAO) + 1)


def walk_forward(medidos, *, mercado: str, horizonte: int) -> dict:
    """Estima cada evento com os anteriores e compara com o realizado.

    Devolve as listas cruas que :mod:`core.calibracao.metricas` consome. A
    separação é de propósito: quem calcula não decide, e quem decide vê os pares
    que produziram o número.
    """
    com_janela = [e for e in medidos
                  if e.janelas.get(horizonte) is not None
                  and e.janelas[horizonte].medida
                  and e.retorno_anormal(horizonte) is not None]
    com_janela.sort(key=lambda e: (e.data_evento, e.simbolo))

    por_tipo: dict[str, list] = defaultdict(list)
    for evento in com_janela:
        por_tipo[evento.tipo_evento].append(evento)

    deteccao: list[tuple] = []
    probabilidade: list[tuple] = []
    magnitude: list[tuple] = []
    referencia: list[tuple] = []
    faixa: list[tuple] = []
    direcao: list[tuple] = []
    por_ano: dict[str, list[float]] = defaultdict(list)

    sem_amostra = 0
    sem_limiar_estimado = 0
    defeito_de_serie = 0
    amostra_perdida_por_defeito = 0
    simbolos_com_defeito: set[str] = set()

    for tipo, eventos in por_tipo.items():
        for i, atual in enumerate(eventos):
            corte = atual.data_evento
            anteriores = [a for a in eventos[:i]
                          if _janela_fechada_em(a, horizonte) < corte]
            if len(anteriores) < MINIMO_ANTERIORES:
                sem_amostra += 1
                continue

            faixa_limiar = limiar_do_evento(atual, mercado, horizonte)
            if not faixa_limiar.estimado:
                sem_limiar_estimado += 1

            passados = [a.retorno_anormal(horizonte) for a in anteriores]
            passados = sorted(v for v in passados if v is not None
                              and abs(v) <= TETO_PLAUSIBILIDADE_ANORMAL)
            if len(passados) < MINIMO_ANTERIORES:
                # Separa os dois motivos: faltar evento anterior é história curta;
                # ter evento anterior e perdê-lo no teto é série contaminada, e
                # somar os dois num contador só esconderia a segunda causa.
                if len(anteriores) - len(passados) >= MINIMO_ANTERIORES:
                    amostra_perdida_por_defeito += 1
                    simbolos_com_defeito.add(atual.simbolo)
                else:
                    sem_amostra += 1
                continue

            # A estimativa: probabilidade de movimento relevante, faixa e centro,
            # tudo derivado apenas da distribuição anterior.
            relevantes = sum(1 for v in passados
                             if abs(v) >= faixa_limiar.valor)
            prob = relevantes / len(passados)
            p10 = passados[max(0, int(0.10 * len(passados)) - 1)]
            p90 = passados[min(len(passados) - 1, int(0.90 * len(passados)))]
            centro = median(passados)

            realizado = atual.retorno_anormal(horizonte)
            if abs(realizado) > TETO_PLAUSIBILIDADE_ANORMAL:
                defeito_de_serie += 1
                simbolos_com_defeito.add(atual.simbolo)
                continue
            ocorreu = abs(realizado) >= faixa_limiar.valor

            deteccao.append((prob >= 0.5, ocorreu))
            probabilidade.append((prob, ocorreu))
            magnitude.append((centro, realizado))
            referencia.append((0.0, realizado))    # referência ingênua: nada acontece
            faixa.append((p10, p90, realizado))
            direcao.append((centro, realizado))
            por_ano[str(atual.data_evento.year)].append(
                realizado if centro > 0 else -realizado)

    estabilidade = met.Estabilidade({
        ano: (sum(v) / len(v)) if v else None
        for ano, v in sorted(por_ano.items())})

    return {
        "n_avaliados": len(deteccao),
        "n_sem_amostra_anterior": sem_amostra,
        "n_sem_limiar_estimado": sem_limiar_estimado,
        "n_defeito_de_serie": defeito_de_serie,
        "n_amostra_perdida_por_defeito": amostra_perdida_por_defeito,
        "simbolos_com_defeito": sorted(simbolos_com_defeito)[:20],
        "confusao": met.avaliar_deteccao(deteccao),
        "calibracao": met.avaliar_probabilidade(probabilidade),
        "magnitude": met.avaliar_magnitude(magnitude, referencia=referencia),
        "faixa": met.avaliar_faixa(faixa),
        "direcao": met.avaliar_direcao(direcao),
        "estabilidade": estabilidade,
        "tipos": {t: len(e) for t, e in sorted(por_tipo.items())},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────
def rodar(engine, *, mercados, horizonte: int, limite: int | None,
          ate: date | None) -> dict:
    conjunto = cat.montar(engine, ate=ate, limite_por_fonte=limite)
    cobertura = conjunto["cobertura"]

    por_mercado_eventos: dict[str, list[dict]] = defaultdict(list)
    for evento in conjunto["eventos"]:
        por_mercado_eventos[evento["mercado"]].append(evento)

    resultados: dict[str, dict] = {}
    for mercado in mercados:
        eventos = por_mercado_eventos.get(mercado, [])
        if not eventos:
            logger.warning("mercado %s sem eventos no catalogo", mercado)
            continue
        verificar_fonte(engine, MERCADO_DE_PRECOS[mercado])
        construido = construir(engine, mercado=MERCADO_DE_PRECOS[mercado],
                               eventos=eventos)
        avaliacao = walk_forward(construido["medidos"], mercado=mercado,
                                 horizonte=horizonte)
        avaliacao["construcao"] = construido["relatorio"]
        resultados[mercado] = avaliacao

    # Portões: agregados sobre o mercado com mais eventos avaliados. Turnover e
    # risco ficam "não medido" -- nenhuma política foi simulada, e inventar uma
    # simulação aqui seria o peso arbitrário que esta entrega existe para não ter.
    principal = max(resultados, key=lambda k: resultados[k]["n_avaliados"],
                    default=None)
    base = resultados.get(principal, {})
    portoes = pes.avaliar_portoes(
        confusao=base.get("confusao"),
        calibracao=base.get("calibracao"),
        comparacao=None,
        variaveis={
            "retorno_anormal_do_evento": True,
            "volatilidade_pre_evento": True,
            "distribuicao_de_eventos_anteriores": True,
        },
        estabilidade=base.get("estabilidade"),
    )
    pode, impedimentos = pes.pode_promover(portoes)

    return {
        "calibracao_versao": CALIBRACAO_VERSAO,
        "horizonte_pregoes": horizonte,
        "cobertura_da_taxonomia": cobertura.resumo(),
        "tipos_com_fonte": list(cobertura.com_fonte),
        "tipos_sem_fonte": dict(cobertura.sem_fonte),
        "limitacoes": list(conjunto["limitacoes"]) + [
            "conjunto de validacao de 15 tipos de evento nao construido: nao "
            "existe corpus historico de noticias e nenhum provedor gratuito "
            "serve arquivo retroativo",
            "segmentacao por setor e por tamanho nao medida: o catalogo nao "
            "carrega essas dimensoes",
            f"retorno anormal acima de {TETO_PLAUSIBILIDADE_ANORMAL:.0%} tratado "
            "como defeito de serie de precos e contado a parte, nao medido como "
            "evento",
            "evento a mais de "
            f"{ret.TOLERANCIA_PREGAO_ZERO_DIAS} dias corridos do pregao mais "
            "proximo nao e medido: sem esse corte, evento anterior ao inicio da "
            "serie casava com a primeira linha existente",
            "hora do evento nao separada entre dentro e fora do pregao: as "
            "fontes do catalogo trazem data, nao horario",
            "fechamento de janela aproximado por dias corridos "
            f"({DIAS_CORRIDOS_POR_PREGAO} por pregao), arredondado para cima",
        ],
        "mercado_dos_portoes": principal,
        "resultados": {
            mercado: {
                "n_avaliados": r["n_avaliados"],
                "n_sem_amostra_anterior": r["n_sem_amostra_anterior"],
                "n_sem_limiar_estimado": r["n_sem_limiar_estimado"],
                "n_defeito_de_serie": r["n_defeito_de_serie"],
                "n_amostra_perdida_por_defeito":
                    r["n_amostra_perdida_por_defeito"],
                "simbolos_com_defeito": r["simbolos_com_defeito"],
                "tipos": r["tipos"],
                "confusao": r["confusao"].como_dict(),
                "calibracao": r["calibracao"].como_dict(),
                "magnitude": {
                    "n": r["magnitude"].n,
                    "mae": r["magnitude"].mae,
                    "mediana_erro": r["magnitude"].mediana_erro,
                    "vies": r["magnitude"].vies,
                    "mae_referencia": r["magnitude"].mae_referencia,
                    "ganho_sobre_referencia":
                        r["magnitude"].ganho_sobre_referencia,
                },
                "faixa": r["faixa"],
                "direcao": r["direcao"],
                "estabilidade": {
                    "por_segmento": r["estabilidade"].por_segmento,
                    "amplitude": r["estabilidade"].amplitude,
                    "concentrado": r["estabilidade"].concentrado,
                },
                "construcao": r["construcao"],
            }
            for mercado, r in resultados.items()
        },
        "portoes": [{"nome": p.nome, "ok": p.ok, "motivo": p.motivo}
                    for p in portoes],
        "pode_promover": pode,
        "impedimentos": list(impedimentos),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mercado", action="append",
                        choices=sorted(MERCADO_DE_PRECOS),
                        help="repetível; padrão: todos")
    parser.add_argument("--horizonte", type=int, default=5,
                        help="horizonte em pregões (padrão 5)")
    parser.add_argument("--limite", type=int, default=None,
                        help="limite de eventos por fonte")
    parser.add_argument("--ate", default=None,
                        help="corte AAAA-MM-DD; eventos posteriores ficam fora")
    parser.add_argument("--saida", default="calibracao_conjuntural.json")
    args = parser.parse_args()

    ate = date.fromisoformat(args.ate) if args.ate else None
    mercados = args.mercado or sorted(MERCADO_DE_PRECOS)

    engine = create_engine(warehouse_url(), pool_pre_ping=True)
    try:
        relatorio = rodar(engine, mercados=mercados, horizonte=args.horizonte,
                          limite=args.limite, ate=ate)
    finally:
        engine.dispose()

    destino = Path(args.saida)
    destino.write_text(json.dumps(relatorio, indent=2, ensure_ascii=False,
                                  default=str), encoding="utf-8")

    print(relatorio["cobertura_da_taxonomia"])
    for mercado, r in relatorio["resultados"].items():
        c = r["confusao"]
        print(f"{mercado}: {r['n_avaliados']} eventos avaliados | "
              f"precisao {c['precisao']} | falso alarme {c['taxa_falso_alarme']} "
              f"| brier {r['calibracao']['brier']}")
    for portao in relatorio["portoes"]:
        marca = {True: "PASSOU", False: "REPROVOU", None: "NAO MEDIDO"}[portao["ok"]]
        print(f"[{marca}] {portao['nome']}: {portao['motivo']}")
    print("promovivel:", relatorio["pode_promover"])
    print("relatorio em", destino)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
