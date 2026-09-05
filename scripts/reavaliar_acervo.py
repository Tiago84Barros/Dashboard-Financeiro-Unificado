"""Reconstroi a camada de avaliacao do acervo sob a versao corrente da metodologia.

Por que este script existe
--------------------------
``noticias_avaliacoes`` e carimbada com ``VERSAO_METODOLOGIA``, e ``ler_recentes``
faz ``JOIN`` por essa versao. A escolha e deliberada -- avaliacao de regua antiga
nao entra na lista sem nota, ela simplesmente nao entra. O preco e que **subir a
versao esvazia a tela** ate alguem reconstruir a safra.

Este projeto ja pagou esse preco no outro sentido: subir ``*_VERSION`` sem
reconstruir a safra correspondente e desligar um painel em silencio
(``memoria: versao-de-metodologia-sem-safra``). Aqui o esvaziamento e visivel, o
que e melhor -- mas visivel nao e resolvido. O que resolve e isto.

Em 05/09/2026 a versao subiu para 1.1.0 porque o indice de relevancia ganhou
**teto de evidencia** (A-146): a nota de uma noticia sem corroboracao externa
deixou de poder ser compensada pelos componentes que a propria noticia declara.

O que ele NAO faz, e por que
-----------------------------
**Nao re-coleta.** A camada do fato observado nao e tocada: o que a fonte
publicou nao muda porque a metodologia mudou, e reescrever a evidencia contra a
qual a metodologia esta sendo conferida seria circular. Alem disso, re-coletar
gastaria cota de provedor para reobter o que ja esta no disco.

**Nao reagrupa eventos.** ``n_fontes_independentes``, ``confirmado_por_primaria``
e ``estado_verificacao`` sao reaproveitados da avaliacao de origem. Sao
observacoes sobre o evento, nao conclusoes da formula -- e reagrupar sobre a
janela que sobrou no acervo mediria a janela, nao o evento: uma materia cujas
irmas ja sairam do recorte perderia confirmacao que ela teve de verdade, e
gravaria isso como se fosse medicao (``memoria: foto-truncada-vira-evidencia``).

``primeiro_em`` e a excecao, e e derivavel sem risco: sai do ``MIN(publicado_em)``
das materias que compartilham ``evento_id`` dentro do proprio acervo. Materia sem
evento agrupado usa a propria data, que e o que um evento de uma materia so
significa.

Uso, no padrao do projeto (simulacao por omissao):

    python scripts/reavaliar_acervo.py
    python scripts/reavaliar_acervo.py --apply
    python scripts/reavaliar_acervo.py --de 1.0.0 --para 1.1.0 --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger("reavaliar_acervo")

#: A avaliacao de origem entra por causa dos tres campos de evidencia do evento,
#: que a camada do fato nao guarda. ``LEFT JOIN`` porque item sem avaliacao
#: nenhuma tambem precisa ganhar uma -- ele existiria de fora da tela para
#: sempre, e um acervo que so cresce e nunca aparece e o pior dos dois mundos.
_LER = text("""
    SELECT i.id_dedup, i.hash_conteudo, i.simhash, i.titulo, i.resumo, i.url,
           i.url_canonica, i.dominio, i.veiculo, i.autor, i.publicado_em,
           i.coletado_em, i.provedor, i.idioma, i.pais, i.entidades,
           i.tipo_evento, i.evento_id, i.sentimento_api, i.sentimento_app4,
           i.rotulo_sentimento, i.metodo_sentimento,
           a.n_fontes_independentes, a.confirmado_por_primaria,
           a.estado_verificacao, a.nota AS nota_antiga
      FROM noticias_itens i
      LEFT JOIN noticias_avaliacoes a
        ON a.id_dedup = i.id_dedup AND a.versao_metodologia = :de
     ORDER BY i.id_dedup
""")

_PRIMEIRO_DO_EVENTO = text("""
    SELECT evento_id, MIN(publicado_em) AS primeiro_em
      FROM noticias_itens
     WHERE evento_id IS NOT NULL AND publicado_em IS NOT NULL
     GROUP BY evento_id
""")


def _entidades(bruto):
    from core.noticias.modelos import Entidades

    dados = bruto if isinstance(bruto, dict) else json.loads(bruto or "{}")
    return Entidades(
        tickers=tuple(dados.get("tickers") or ()),
        empresas=tuple(dados.get("empresas") or ()),
        setores=tuple(dados.get("setores") or ()),
        paises=tuple(dados.get("paises") or ()),
        moedas=tuple(dados.get("moedas") or ()),
        ativos=tuple(dados.get("ativos") or ()),
    )


def _utc(valor):
    """Instante gravado de volta como UTC aware.

    O driver devolve ``naive`` quando a coluna e ``timestamp`` sem fuso, e
    comparar naive com aware levanta -- num script que roda uma vez, o erro
    apareceria so na linha em que a data existe.
    """
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def _noticia(linha):
    from core.noticias import fontes
    from core.noticias.modelos import Noticia, Sentimento

    return Noticia(
        id_dedup=linha["id_dedup"],
        hash_conteudo=linha["hash_conteudo"] or "",
        simhash=linha["simhash"],
        titulo=linha["titulo"] or "",
        resumo=linha["resumo"],
        url=linha["url"] or "",
        url_canonica=linha["url_canonica"] or "",
        # Reclassificada a partir do dominio, e nao lida de
        # ``confiabilidade_fonte``: se o catalogo de fontes passou a conhecer um
        # veiculo que antes era desconhecido, a reavaliacao tem de enxergar
        # isso. Congelar o numero gravado deixaria o teto de evidencia preso na
        # ignorancia da coleta antiga.
        fonte=fontes.classificar(linha["url_canonica"] or linha["url"],
                                 linha["veiculo"]),
        autor=linha["autor"],
        publicado_em=_utc(linha["publicado_em"]),
        coletado_em=_utc(linha["coletado_em"]),
        provedor=linha["provedor"] or "",
        idioma=linha["idioma"],
        pais=linha["pais"],
        entidades=_entidades(linha["entidades"]),
        tipo_evento=linha["tipo_evento"],
        evento_id=linha["evento_id"],
        sentimento=Sentimento(
            valor_api=linha["sentimento_api"],
            valor_app4=linha["sentimento_app4"],
            rotulo_api=linha["rotulo_sentimento"],
            metodo_app4=linha["metodo_sentimento"],
        ),
    )


def reavaliar(conn, *, de: str, agora=None):
    """Recalcula relevancia, impacto e portoes de todo o acervo.

    Devolve ``(avaliadas, vereditos, mudancas)``. Nao grava: quem grava e o
    ``--apply``, e ver a lista antes e o que torna a mudanca conferivel.
    """
    from core.noticias import bases_historicas as bases_mod
    from core.noticias import impacto as imp_mod
    from core.noticias import perfil_carteira as perfil_mod
    from core.noticias import portoes as pt_mod
    from core.noticias import relevancia as rel_mod
    from core.noticias.coleta import confirmacao_quantitativa, exposicao_de_carteira
    from core.noticias.modelos import NoticiaAvaliada

    perfil, lim_perfil = perfil_mod.carregar()
    bases, lim_bases = bases_mod.carregar()
    for texto in list(lim_perfil) + list(lim_bases):
        logger.info("limitacao: %s", texto)

    primeiro_de = {r["evento_id"]: _utc(r["primeiro_em"])
                   for r in conn.execute(_PRIMEIRO_DO_EVENTO).mappings()}

    avaliadas = []
    vereditos = {}
    mudancas = []
    for linha in conn.execute(_LER, {"de": de}).mappings():
        noticia = _noticia(linha)
        base = (bases or {}).get(noticia.tipo_evento)
        n_fontes = int(linha["n_fontes_independentes"] or 1)
        primaria = bool(linha["confirmado_por_primaria"])
        estado = linha["estado_verificacao"] or "nao_verificada"

        # O instante de referencia e o da COLETA, nunca "agora". A novidade
        # decai com a idade, entao reavaliar hoje uma materia de tres dias
        # atras derrubaria a nota dela sem que a metodologia tivesse dito nada
        # sobre isso -- e o diff mostraria como efeito da correcao algo que e
        # so a passagem do tempo. Medido: com ``agora``, as 48 linhas do acervo
        # "mudavam", 40 delas com o teto acima da nota, isto e, sem o teto ter
        # encostado. Reavaliar e trocar a regua sobre a mesma foto.
        referencia = (agora or _utc(linha["coletado_em"])
                      or _utc(linha["publicado_em"])
                      or datetime.now(timezone.utc))

        exposicao = exposicao_de_carteira(noticia, perfil)

        rel = rel_mod.calcular(
            noticia,
            agora=referencia,
            n_fontes_independentes=n_fontes,
            confirmado_por_primaria=primaria,
            primeiro_em=primeiro_de.get(noticia.evento_id),
            tickers_alvo=perfil.tickers,
            exposicao_carteira=exposicao,
        )
        imp = imp_mod.estimar(
            tipo_evento=noticia.tipo_evento,
            sentimento=noticia.sentimento,
            confiabilidade_fonte=(noticia.fonte.confiabilidade
                                  if noticia.fonte else None),
            estado_verificacao=estado,
            cobertura_relevancia=rel.cobertura,
            base=base,
        )
        avaliada = NoticiaAvaliada(
            noticia=noticia, relevancia=rel, impacto=imp,
            estado_verificacao=estado, n_fontes_independentes=n_fontes,
            confirmado_por_primaria=primaria,
        )
        avaliadas.append(avaliada)
        vereditos[noticia.id_dedup] = pt_mod.avaliar(
            avaliada, perfil=perfil,
            confirmacao_quantitativa=confirmacao_quantitativa(base))

        antiga = linha["nota_antiga"]
        if antiga is None or abs(float(antiga) - rel.nota) >= 0.05:
            mudancas.append({
                "id_dedup": noticia.id_dedup,
                "titulo": (noticia.titulo or "")[:70],
                "dominio": linha["dominio"],
                "antes": None if antiga is None else float(antiga),
                "depois": rel.nota,
                "bruta": rel.nota_bruta,
                "teto": rel.teto_evidencia,
            })
    return avaliadas, vereditos, mudancas


def main(argv: list[str] | None = None) -> int:
    from core.noticias.armazenamento import VERSAO_METODOLOGIA, gravar_avaliacoes
    from core.noticias.destino import engine_acervo

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--de", default="1.0.0",
                        help="versao de origem, de onde vem a evidencia do "
                             "evento (padrao: 1.0.0)")
    parser.add_argument("--para", default=VERSAO_METODOLOGIA,
                        help=f"versao de destino (padrao: {VERSAO_METODOLOGIA})")
    parser.add_argument("--apply", action="store_true",
                        help="grava; sem isto so simula e imprime o diff")
    parser.add_argument("--limite-diff", type=int, default=25)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    motor = engine_acervo()
    if motor is None:
        print("Sem NOTICIAS_LOCAL_DB_URL nem MACRO_LOCAL_DB_URL: o acervo mora "
              "no armazem local e nao ha onde ler.")
        return 1

    with motor.connect() as conn:
        avaliadas, vereditos, mudancas = reavaliar(conn, de=args.de)

    # A atribuicao importa mais que a contagem. Uma nota pode mudar por duas
    # razoes independentes, e ler as duas como uma so foi o primeiro resultado
    # deste script: o teto e a causa quando ``depois < bruta``; quando as duas
    # sao iguais e a nota mesmo assim caiu, quem mudou foi a entrada -- perfil
    # de carteira que passou a existir, fonte que saiu de "desconhecida" no
    # catalogo. Sem separar, a correcao levaria credito por efeito alheio.
    pelo_teto = [m for m in mudancas if m["teto"] is not None
                 and m["depois"] < m["bruta"] - 1e-9]
    rebaixadas = [m for m in mudancas
                  if m["antes"] is not None and m["depois"] < m["antes"]]
    print(f"acervo: {len(avaliadas)} itens | {args.de} -> {args.para}")
    print(f"notas que mudam: {len(mudancas)} (menores que antes: "
          f"{len(rebaixadas)}; limitadas pelo teto de evidencia: "
          f"{len(pelo_teto)})")
    for m in mudancas[:args.limite_diff]:
        antes = "sem avaliacao" if m["antes"] is None else f"{m['antes']:5.1f}"
        teto = "-" if m["teto"] is None else f"{m['teto']:.0f}"
        causa = "TETO " if m["depois"] < m["bruta"] - 1e-9 else "     "
        print(f"  {causa}{antes} -> {m['depois']:5.1f} (bruta {m['bruta']:5.1f}"
              f", teto {teto:>5s})  {m['dominio']}  {m['titulo']}")
    if len(mudancas) > args.limite_diff:
        print(f"  ... e mais {len(mudancas) - args.limite_diff}")

    if not args.apply:
        print("\nsimulacao: nada gravado. Repita com --apply.")
        return 0

    resumo = gravar_avaliacoes(avaliadas, engine=motor, vereditos=vereditos,
                               versao=args.para)
    print(f"\ngravado: {resumo}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
