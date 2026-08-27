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
                f"{entradas:,} entradas e **nenhuma saída** -- as {n_ini} empresas "
                f"da primeira safra continuam todas na última. Num mercado real "
                f"isso não acontece; a amostra é 100% sobrevivente."
                ).replace(",", ".")
    return (f"Medido: em {safras} safras de {ini} a {fim} o painel registrou "
            f"{entradas:,} entradas e {saidas:,} saídas de empresas."
            ).replace(",", ".")
