"""Publica o desempenho diário da Carteira 20 -- ``data/public/carteira20_desempenho.json``.

Roda localmente; o app publicado só lê o arquivo. As curvas saem de
``core/carteira20_desempenho.py`` e as fontes são:

- ações: ``dados/b3_retornos_diarios.parquet`` da pesquisa (retorno total com
  proventos, a mesma série que treinou a receita). Só avança quando o pipeline
  da pesquisa roda de novo;
- BOVA11: ``market.historical_prices`` do armazém local (Docker
  ``dfu_warehouse``). Linha gravada antes das 18 h do próprio pregão é cotação
  intradiária e fica de fora;
- CDI: ``data/public/cdi_diario.json.gz`` e, depois dele, o BCB.

    python scripts/carteira20_desempenho.py [--seco] [--execucao AAAA-MM-DD]

``--seco`` mostra o resultado sem gravar. ``--execucao`` troca a data de
execução da revisão vigente, só para ensaiar a conta com pregões que já
existem (exige ``--seco``: o artefato nunca leva uma data que o registro não tem).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core import carteira20 as c20  # noqa: E402
from core import carteira20_desempenho as cd  # noqa: E402

ARTEFATO = ROOT / "data" / "public" / "carteira20_desempenho.json"
FECHAMENTO_CONFIRMADO = time(18, 0)


def retornos_da_pesquisa(ids: set[str], desde: date) -> pd.DataFrame:
    from scripts.carteira20_revisao import pasta_pesquisa

    caminho = pasta_pesquisa() / "dados" / "b3_retornos_diarios.parquet"
    df = pd.read_parquet(caminho, columns=["id_ativo", "data", "ret_total", "ret_suspeito"],
                         filters=[("data", ">", pd.Timestamp(desde))])
    df = df[df["id_ativo"].isin(ids)]
    return df.rename(columns={"id_ativo": "id", "ret_total": "ret", "ret_suspeito": "suspeito"})


def bova11_do_armazem(desde: date) -> pd.Series:
    import sqlalchemy as sa

    from scripts.publish_fii_selection_from_local import _warehouse_url

    with sa.create_engine(_warehouse_url()).connect() as con:
        linhas = con.execute(sa.text(
            "SELECT date, close, updated_at FROM market.historical_prices "
            "WHERE ticker = 'BOVA11' AND date >= :d ORDER BY date"), {"d": desde}).fetchall()
    serie = {}
    for dia, fechamento, gravado in linhas:
        local = gravado.astimezone(c20.FUSO_B3) if gravado else None
        if local is None or local.date() > dia or local.time() >= FECHAMENTO_CONFIRMADO:
            serie[dia] = float(fechamento)
    return pd.Series(serie, dtype=float)


def cdi_diario(desde: date, ate: date) -> tuple[dict[date, float], str]:
    from core.rentabilidade import obter_cdi

    r = obter_cdi(desde, ate)
    return r["serie"], ", ".join(filter(None, [r.get("fonte"), r.get("motivo")]))


def montar(registros: list[dict], execucao: date | None = None, *,
           retornos=retornos_da_pesquisa, bova=bova11_do_armazem, cdi=cdi_diario,
           agora: datetime | None = None) -> dict | None:
    """Artefato da revisão vigente, ou None quando ainda não há pregão depois dela."""
    atual = c20.vigente(registros)
    if not atual:
        return None
    corpo = atual["corpo"]
    execucao = execucao or date.fromisoformat(corpo["pregao_execucao"])
    carteira = [{"id": c["id"], "ticker": c["ticker"]} for c in corpo["carteira"]]
    universo = [{"id": u["id"], "ticker": u["ticker"]}
                for u in (corpo.get("universo") or {}).get("pontuacoes") or []]
    ids = {x["id"] for x in carteira + universo}
    agora = agora or datetime.now(c20.FUSO_B3)
    ret = retornos(ids, execucao)
    serie_bova = bova(execucao)
    serie_cdi, fonte_cdi = cdi(execucao, agora.date())
    calc = cd.calcular(execucao, ret, serie_bova, serie_cdi, carteira, universo)
    if not calc["pregoes"]:
        return None
    return {
        "schema": 1,
        "gerado_em": agora.isoformat(timespec="seconds"),
        "revisao": {"seq": atual["registro"]["seq"], "hash": atual["registro"]["hash"],
                    "corpo_sha256": cd.impressao_corpo(corpo),
                    "data_formacao": corpo["data_formacao"], "pregao_execucao": execucao.isoformat()},
        "dados_ate": calc["datas"][-1],
        "regras": {"capital": cd.CAPITAL, "custo_por_lado": cd.CUSTO_LADO,
                   "aliquota_ir": cd.ALIQUOTA_IR, "isencao_mensal_acoes": cd.ISENCAO_MENSAL_ACOES},
        "fontes": {"acoes": "pesquisa: dados/b3_retornos_diarios.parquet (retorno total)",
                   "bova11": "armazém local: market.historical_prices (fechamento)",
                   "cdi": f"BCB/SGS 12 ({fonte_cdi})"},
        **calc,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seco", action="store_true", help="mostra sem gravar")
    ap.add_argument("--execucao", type=date.fromisoformat,
                    help="ensaio com outra data de execução (só com --seco)")
    a = ap.parse_args(argv)
    if a.execucao and not a.seco:
        ap.error("--execucao só vale com --seco")
    registros = c20.ler()
    status = c20.verificar_cadeia(registros)
    if not status.integra:
        raise SystemExit("cadeia quebrada: " + "; ".join(status.erros))
    art = montar(registros, a.execucao)
    if art is None:
        print("nenhum pregão depois da execução ainda; nada a publicar")
        return 0
    texto = json.dumps(art, ensure_ascii=False, indent=1) + "\n"
    finais = {k: v[-1] if v else None for k, v in art["curvas"].items()}
    print(f"{art['pregoes']} pregão(ões), dados até {art['dados_ate']}: {finais}")
    for aviso in art["avisos"]:
        print(f"aviso: {aviso}")
    if a.seco:
        print(texto[:2000])
        return 0
    ARTEFATO.write_text(texto, encoding="utf-8", newline="\n")
    print(f"gravado {ARTEFATO.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
