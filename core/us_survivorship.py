"""Quantifica o viés de sobrevivência do painel histórico americano.

Avisar que o viés existe é o mínimo; o aviso genérico não diz ao usuário se ele
é pequeno ou se invalida a evidência. Este módulo mede o tamanho dele com o
número que o próprio painel entrega: quantas empresas ENTRARAM e quantas SAÍRAM
do universo ao longo das safras.

Num mercado real as duas contas existem -- empresas abrem capital e empresas
somem, por falência, fechamento de capital ou aquisição. Um painel com entradas
e nenhuma saída não é um painel com poucas saídas: é um painel construído a
partir de quem sobreviveu até hoje e projetado para trás.

Medido em 27/08/2026 no armazém local, sobre `market_us.score_vintages`:
16 safras de 2010-06-30 a 2025-06-30, 106 empresas na primeira e 2.798 na
última, **2.692 entradas e zero saídas**. Nenhuma das 106 empresas de 2010
deixou a amostra em quinze anos. As séries de preço confirmam: das 2.800
empresas do painel, nenhuma tem cotação que pare antes do fim da amostra.

O painel mora no armazém local; a base publicada só alcança
`company_snapshots` e `prices_monthly` (esta com 12 símbolos). Por isso a
medição é gravada em disco por quem tem o armazém e lida pela tela em
produção -- mesmo padrão do manifesto do RAG. O arquivo é resultado de medição
com data e procedência, não critério cravado: `medir_turnover` o recalcula, e
o dia em que houver deslistagem ingerida o número muda sozinho.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CAMINHO_MEDICAO = Path(__file__).resolve().parents[1] / "data" / "us_survivorship.json"

# Uma serie que para bem antes do fim da amostra e sinal de que a empresa deixou
# de negociar, mesmo sem `delisted_date` preenchida. A folga de 120 dias evita
# contar atraso de ingestao como deslistagem.
FOLGA_FIM_DIAS = 120


def medir_turnover(engine) -> dict[str, Any]:
    """Conta entradas e saídas de empresas entre safras consecutivas do painel.

    Uma saída é uma empresa presente na safra `t` e ausente na safra `t+1`. Se
    esse número for zero em toda a janela, o universo é 100% sobrevivente e o
    risco de perda permanente de capital não é observável na série.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        linhas = list(conn.execute(text(
            "SELECT as_of_date, company_id FROM market_us.score_vintages")))
    if not linhas:
        raise ValueError("score_vintages vazio")

    por_safra: dict[date, set[int]] = {}
    for d, cid in linhas:
        d = d.date() if hasattr(d, "date") else d
        por_safra.setdefault(d, set()).add(int(cid))
    safras = sorted(por_safra)

    saidas = entradas = 0
    for anterior, atual in zip(safras, safras[1:]):
        saidas += len(por_safra[anterior] - por_safra[atual])
        entradas += len(por_safra[atual] - por_safra[anterior])

    return {
        "medido_em": datetime.now(timezone.utc).date().isoformat(),
        "safras": len(safras),
        "primeira_safra": safras[0].isoformat(),
        "ultima_safra": safras[-1].isoformat(),
        "empresas_primeira": len(por_safra[safras[0]]),
        "empresas_ultima": len(por_safra[safras[-1]]),
        "entradas": entradas,
        "saidas": saidas,
    }


def gravar_medicao(medicao: dict[str, Any],
                   caminho: Path | str = CAMINHO_MEDICAO) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(medicao, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return caminho


def carregar_medicao(caminho: Path | str = CAMINHO_MEDICAO) -> dict[str, Any] | None:
    """Devolve a última medição gravada, ou None se não houver nenhuma.

    Ausência não vira zero: sem medição, quem chama volta ao aviso qualitativo
    em vez de afirmar um número que ninguém apurou.
    """
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("medicao de sobrevivencia indisponivel: %s", type(exc).__name__)
        return None
    return dados if isinstance(dados, dict) and "saidas" in dados else None


def _mil(n: int) -> str:
    """Separador de milhar pt-BR.

    Trocar vírgula por ponto na frase inteira já comeu a vírgula da prosa e
    virou ponto final no meio de uma oração; a troca tem de ser no número.
    """
    return f"{int(n):,}".replace(",", ".")


def frase_turnover(medicao: dict[str, Any] | None = None) -> str | None:
    """Frase pronta com o tamanho do viés, ou None se não houver medição."""
    medicao = carregar_medicao() if medicao is None else medicao
    if not medicao:
        return None
    try:
        saidas = int(medicao["saidas"])
        entradas = int(medicao["entradas"])
        safras = int(medicao["safras"])
        ini, fim = medicao["primeira_safra"][:4], medicao["ultima_safra"][:4]
        n_ini = int(medicao["empresas_primeira"])
    except Exception:  # noqa: BLE001
        return None
    if saidas == 0:
        return (f"Medido: em {safras} safras de {ini} a {fim} o painel registrou "
                f"{_mil(entradas)} entradas e **nenhuma saída** -- as {n_ini} "
                f"empresas da primeira safra continuam todas na última. Num "
                f"mercado real isso não acontece; a amostra é 100% sobrevivente.")
    return (f"Medido: em {safras} safras de {ini} a {fim} o painel registrou "
            f"{_mil(entradas)} entradas e {_mil(saidas)} saídas de empresas.")


# ── Mortalidade da coorte: o viés medido FORA do painel (A-157) ──────────────
#
# `medir_turnover` responde "o painel tem saídas?" e a resposta foi zero. Isso
# diz que a amostra é sobrevivente, mas não diz de QUANTO -- e sem o tamanho o
# usuário não consegue descontar nada. O tamanho não está no painel, por
# construção: quem morreu nunca entrou nele.
#
# A fonte independente é o índice de arquivamentos da SEC (`full-index`), que é
# ponto-no-tempo de verdade: lista quem arquivou relatório anual naquele
# trimestre, vivo ou não. Uma empresa que arquivava em 2010 e não arquiva mais
# saiu do mercado por falência, fechamento de capital ou aquisição.
#
# Medido em 27/08/2026 (`scripts/medir_mortalidade_us.py`): 9.686 empresas
# arquivaram relatório anual em 2010 e 2.899 ainda arquivavam em 2025 --
# **70,1% desapareceram**; a queda é contínua (57,9% vivas em 2015, 39,0% em
# 2020). O painel tem 106 empresas na safra de 2010, 1,09% do universo real
# daquele ano, e nenhuma delas morreu. Não é um painel com poucas mortes: é um
# painel construído a partir dos 30% que sobreviveram, e por isso todo retorno
# histórico que sai dele é teto, não expectativa.
#
# As formas incluem `10-K405` e `10-KSB`, extintas depois de 2003 e 2008: quem
# só contasse `10-K` leria como morte a empresa que apenas trocou de formulário.
FORMAS_RELATORIO_ANUAL_IDX = ("10-K", "10-K405", "10-KSB", "20-F")

_LINHA_IDX = re.compile(r"^(?P<forma>\S[^ ]*(?: [^ ]+)?)\s{2,}.*?edgar/data/(?P<cik>\d+)/")


def ciks_com_relatorio_anual(texto_idx: str) -> set[int]:
    """CIKs que arquivaram relatório anual, lidos de um `form.idx` da SEC.

    O CIK sai do caminho do arquivo (`edgar/data/<cik>/`) e não da coluna de
    largura fixa: a coluna desalinha em nome societário longo, e a versão que
    lia por posição colheu um `CIK 0` de linha de cabeçalho.
    """
    achados: set[int] = set()
    for linha in str(texto_idx or "").splitlines():
        m = _LINHA_IDX.match(linha)
        if not m:
            continue
        if m.group("forma").strip().upper() not in FORMAS_RELATORIO_ANUAL_IDX:
            continue
        cik = int(m.group("cik"))
        if cik:
            achados.add(cik)
    return achados


def medir_mortalidade(por_ano: dict[int, set[int]],
                      painel_por_ano: dict[int, set[int]] | None = None
                      ) -> dict[str, Any]:
    """Curva de sobrevivência da coorte mais antiga, e o que o painel viu dela.

    `por_ano` é {ano: CIKs que arquivaram relatório anual}, e `painel_por_ano`
    é o mesmo recorte visto pelo nosso painel. A comparação entre os dois é o
    número que interessa: cobertura do universo real e mortes observadas.
    """
    anos = sorted(a for a, ciks in (por_ano or {}).items() if ciks)
    if len(anos) < 2:
        raise ValueError("mortalidade exige ao menos dois anos com filiais")
    base_ano, ultimo = anos[0], anos[-1]
    base = por_ano[base_ano]
    curva = {
        str(ano): {
            "vivas": len(base & por_ano[ano]),
            "universo_do_ano": len(por_ano[ano]),
            "sobrevivencia_pct": round(100.0 * len(base & por_ano[ano]) / len(base), 2),
        }
        for ano in anos
    }
    painel_base = (painel_por_ano or {}).get(base_ano, set())
    medicao: dict[str, Any] = {
        "medido_em": datetime.now(timezone.utc).date().isoformat(),
        "ano_base": base_ano,
        "ano_final": ultimo,
        "universo_base": len(base),
        "sobreviventes": len(base & por_ano[ultimo]),
        "mortalidade_pct": round(100.0 * (1 - len(base & por_ano[ultimo]) / len(base)), 2),
        "curva": curva,
    }
    if painel_por_ano is not None:
        medicao.update({
            "painel_no_ano_base": len(painel_base),
            "cobertura_pct": round(100.0 * len(painel_base & base) / len(base), 2),
            "mortes_no_painel": len(painel_base - por_ano[ultimo]),
        })
    return medicao


def frase_mortalidade(medicao: dict[str, Any] | None = None) -> str | None:
    """Frase com o tamanho do viés medido fora do painel, ou None sem medição."""
    medicao = carregar_medicao() if medicao is None else medicao
    coorte = (medicao or {}).get("coorte")
    if not coorte:
        return None
    try:
        base_ano, final = int(coorte["ano_base"]), int(coorte["ano_final"])
        universo, mortalidade = int(coorte["universo_base"]), float(coorte["mortalidade_pct"])
    except Exception:  # noqa: BLE001
        return None
    frase = (f"Tamanho do viés, medido no índice de arquivamentos da SEC: das "
             f"{_mil(universo)} empresas que publicaram relatório anual em "
             f"{base_ano}, {mortalidade:.0f}% não publicam mais em {final}.")
    cobertura = coorte.get("cobertura_pct")
    painel = coorte.get("painel_no_ano_base")
    if cobertura is not None and painel is not None:
        pct = f"{float(cobertura):.1f}".replace(".", ",")
        frase += (f" O painel cobre {pct}% daquele universo ({int(painel)} "
                  f"empresas) e nenhuma delas desapareceu. O retorno histórico "
                  f"exibido é teto, não expectativa.")
    return frase


# ── O score protege contra perda permanente de capital? (A-158) ──────────────
#
# `frase_mortalidade` diz o tamanho do que ficou de fora, mas não diz se o
# ranking exibido teria evitado essas empresas. Corrigir o backtest exigiria o
# retorno futuro das mortas, que não existe em fonte nossa -- o yfinance não
# serve deslistada e chega a devolver a série de OUTRO papel que herdou o
# ticker. O que é observável sem cotação é o desfecho extremo: a empresa sumiu
# sem ninguém comprar.
#
# `scripts/testar_score_prediz_morte_us.py` calcula o score de produção sobre a
# coorte de 2012 com dados visíveis em 2013-06-30 e confere o desfecho em 2025.
# Aquisição é desfecho SEPARADO de desaparecimento: empresa boa é comprada com
# prêmio, e contar fusão como morte já inverteu a leitura uma vez. Quem sai sem
# deixar marca de falência nem de fusão fica FORA da conta, e o seu tamanho é
# publicado junto -- é a maior parte das saídas, e escondê-la faria o número
# parecer mais completo do que é.
CAMINHO_TESTE_MORTE = (Path(__file__).resolve().parents[1]
                       / "data" / "us_score_vs_morte.json")


def carregar_teste_morte(caminho: Path | str = CAMINHO_TESTE_MORTE
                         ) -> dict[str, Any] | None:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("teste de morte indisponivel: %s", type(exc).__name__)
        return None
    return dados if isinstance(dados, dict) and "apenas_exibiveis" in dados else None


def frase_score_vs_morte(resultado: dict[str, Any] | None = None) -> str | None:
    """Frase com o poder medido do score de separar quem sumiu, ou None.

    A frase muda de sentido conforme o número: AUC perto de 0,50 é confissão de
    que o ranking não protege, e tem de aparecer com a mesma clareza de um
    resultado bom. Frase que só sabe elogiar não é medição.
    """
    resultado = carregar_teste_morte() if resultado is None else resultado
    bloco = (resultado or {}).get("apenas_exibiveis") or {}
    if not bloco or bloco.get("insuficiente"):
        return None
    try:
        auc = float(bloco["auc_nao_sumiu"])
        n = int(bloco["empresas"])
        sumiu = int(bloco["sumiu"])
        indefinidos = int(bloco.get("indefinido") or 0)
        coorte = int(resultado["ano_coorte"])
        desfecho = int(resultado["ano_desfecho"])
    except Exception:  # noqa: BLE001
        return None
    pct = f"{100 * auc:.0f}".replace(".", ",")
    fora = (f" Outras {indefinidos} saíram da bolsa sem deixar registro de "
            f"falência nem de fusão e ficaram fora da conta." if indefinidos
            else "")
    base = (f"Teste do ranking contra o desfecho pior de todos: o score "
            f"calculado com os dados de {coorte}, sobre {n} empresas dessa "
            f"safra ({sumiu} delas pediram falência ou recuperação judicial "
            f"até {desfecho}), acerta {pct}% dos pares ao apontar quem NÃO "
            f"iria quebrar.{fora}")
    if auc < 0.55:
        return base + (" Sorte pura seria 50%: **o ranking não protege contra "
                       "perda permanente de capital** e não deve ser lido como "
                       "se protegesse.")
    if auc < 0.65:
        return base + (" Sorte pura seria 50%: há sinal, mas fraco -- serve "
                       "para inclinar a carteira, não para dispensar análise "
                       "de solvência.")
    return base + (" Sorte pura seria 50%, de modo que o ranking carrega sinal "
                   "real sobre sobrevivência -- ainda assim é probabilidade "
                   "sobre um universo, não garantia sobre uma empresa.")
