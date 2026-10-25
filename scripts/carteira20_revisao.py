"""Registro da Carteira 20 B3 -- protocolo, revisões e verificação da cadeia.

Roda localmente. O app publicado só lê ``data/public/carteira20_registro.jsonl``.

Subcomandos:

    python scripts/carteira20_revisao.py protocolo   # registro 0, uma vez só
    python scripts/carteira20_revisao.py verificar   # confere a cadeia inteira
    python scripts/carteira20_revisao.py validar     # refaz 2026 do teste final
    python scripts/carteira20_revisao.py revisar --formacao AAAA-MM-DD [--seco]

``validar`` e ``revisar`` treinam os modelos: rodam no ambiente da pesquisa
(lightgbm e torch), não no do app.

O protocolo congela, ANTES da primeira revisão, tudo o que a medição vai
usar: a receita, o calendário, a operação entre revisões, a medição, a
expectativa da pesquisa e o critério de leitura. Leva também o SHA-256 dos
arquivos da pesquisa de onde a receita saiu, porque a pasta da pesquisa não
está sob git e o hash é a única prova de qual código valia nesta data.

A pasta da pesquisa sai de ``CARTEIRA20_PESQUISA`` ou do caminho padrão.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import carteira20 as c20  # noqa: E402

PESQUISA_PADRAO = (Path.home() / "OneDrive" / "Área de Trabalho" / "Meus Arquivos"
                   / "Projetos" / "Redes Neurais Aplicadas No Mercado Finaceiro"
                   / "PESQUISA 2026 - PREVISAO")

VERSAO_RECEITA = "c2_sem_dy/v1"

#: Arquivos da pesquisa que definem a receita. Caminhos relativos à pasta da
#: pesquisa, com barra normal para o hash não depender do sistema.
ARQUIVOS_DA_RECEITA = (
    "PROTOCOLO.md",
    "relatorio/RELATORIO_FINAL.md",
    "codigo/b3_exp/carregar.py",
    "codigo/b3_exp/ranking.py",
    "codigo/b3_exp/teste_final_sem_dy.py",
    "codigo/nucleo/modelos.py",
    "codigo/nucleo/walkforward.py",
    "codigo/b3/config.py",
    "codigo/b3/pipeline.py",
    "codigo/b3/painel.py",
    "codigo/pratico/simular.py",
    "codigo/impostos.py",
)


def pasta_pesquisa() -> Path:
    return Path(os.environ.get("CARTEIRA20_PESQUISA") or PESQUISA_PADRAO)


def hashes_da_pesquisa(pasta: Path) -> dict[str, str]:
    faltando = [rel for rel in ARQUIVOS_DA_RECEITA if not (pasta / rel).is_file()]
    if faltando:
        raise SystemExit(f"arquivos da pesquisa ausentes em {pasta}: {faltando}")
    return {rel: c20.sha256_arquivo(pasta / rel) for rel in ARQUIVOS_DA_RECEITA}


def corpo_protocolo(hashes: dict[str, str]) -> dict:
    return {
        "nome": "Carteira 20 B3",
        "versao_receita": VERSAO_RECEITA,
        "origem": {
            "pesquisa": "PESQUISA 2026 - PREVISAO",
            "secoes": "relatorio/RELATORIO_FINAL.md, seções 6, 7 e 11",
            "sha256": hashes,
        },
        "receita": {
            "sinal": ("média dos postos na data das previsões de quatro modelos "
                      "(Huber, PLS, LightGBM e NN1) e do posto do momento 12-2: "
                      "o ensemble C2 sem dy_12m"),
            "modelos": ["ols_huber", "pls", "lgbm", "nn1"],
            "momento": "mom_12_2 do painel, em posto na data, sem normalização",
            "alvo": ("yr_6: posto na data do excesso de retorno sobre o CDI em "
                     "6 meses, em [-1, 1]"),
            "entradas": ("todas_sem_dy: as características numéricas do painel "
                         "menos dy_12m, em posto transversal [-1, 1], ausente = 0"),
            "universo": ("no_universo_mercado na data de formação: líquida "
                         "(mediana de 3 meses >= R$ 500 mil, negociada em >= 80% "
                         "dos dias, preço >= R$ 1) e classe principal da companhia"),
            "hiperparametros": ("as grades de codigo/nucleo/modelos.py, escolhidas "
                                "na validação a cada revisão"),
            "treino": ("re-treino a cada revisão com todo alvo realizado até a "
                       "formação. Validação: linhas dos 2 últimos anos cujo alvo "
                       "termina antes da formação. Treino: alvos que terminam "
                       "antes do início da validação"),
            "carteira": f"as {c20.N_ACOES} maiores pontuações, pesos iguais",
            "empate": "pontuação com 6 casas; empate resolvido pelo ticker, em ordem alfabética",
            "postos": ("calculados entre as ações do universo na data de formação. "
                       "Posto ausente (ação sem mom_12_2) fica fora da média dela, "
                       "como na pesquisa"),
        },
        "validacao_do_codigo": ("antes deste registro, scripts/carteira20_revisao.py "
                                "validar refez o ano de 2026 do teste final (corte em "
                                "2026-01-01, formações de jan. a mar.): 551 linhas, "
                                "diferença máxima 0 nas quatro famílias e no ensemble, "
                                "top 20 idêntico nos três meses"),
        "calendario": {
            "regra": ("formação no último pregão de abril e de outubro. O registro "
                      "é feito depois do fechamento da formação e antes da abertura "
                      "do pregão seguinte. A execução é no fechamento desse pregão "
                      "(leilão de fechamento)"),
            "inaugural": {
                "data_formacao": "2026-09-30",
                "pregao_execucao": "2026-10-26",
                "desvio": ("a formação usa o painel da pesquisa, que fecha em "
                           "30/09/2026 (dados até 22/10/2026). O sinal chega com ~4 "
                           "semanas de defasagem. Esperar o fim de outubro daria um "
                           "sinal mais novo, mas a revisão de abril/2027 já estaria "
                           "a 6 meses dela; a inaugural fica fora do calendário uma "
                           "vez só"),
            },
            "planejado": c20.calendario_planejado(2027, 2031),
            "atraso": ("revisão registrada depois do prazo mantém a data de "
                       "formação e passa a valer no fechamento do primeiro pregão "
                       "depois do registro. O atraso fica no registro"),
        },
        "operacao": {
            "na_revisao": ("vende o que saiu da lista e iguala os pesos das 20 no "
                           "fechamento do pregão de execução"),
            "entre_revisoes": ("aportes vão para as ações mais abaixo do peso-alvo, "
                               "sem venda (core/aporte.py::plano_de_aporte)"),
            "custo_por_lado": 0.003,
            "lote": "fracionário: 1 ação",
            "proventos": ("na curva teórica, reinvestidos na própria ação (retorno "
                          "total); na curva por aportes, creditados em caixa e "
                          "somados ao aporte seguinte"),
        },
        "medicao": {
            "frequencia": ("mensal, no fechamento do último pregão do mês. O "
                           "primeiro mês fecha em 30/11/2026"),
            "curvas": {
                "teorica": ("R$ 1.000 no pregão de execução da inaugural, pesos "
                            "iguais, sem aporte, rebalanceada só nas revisões"),
                "aportes": ("R$ 1.000 no pregão de execução da inaugural e R$ 500 "
                            "no fechamento do último pregão de cada mês, como no "
                            "pequeno investidor da pesquisa"),
            },
            "comparacoes": {
                "etf": "BOVA11 com os mesmos aportes, retorno total, 15% de IR no resgate",
                "cdi": "100% do CDI com os mesmos aportes, 15% de IR no resgate",
                "pesos_iguais": ("todas as ações do universo na formação, pesos "
                                 "iguais, mesmas datas e custos: é contra ela que a "
                                 "expectativa da pesquisa foi medida"),
            },
            "ir": ("15% sobre o ganho líquido mensal com ações, por preço médio e "
                   "com compensação de prejuízo; isento no mês em que as vendas "
                   "somam até R$ 20 mil (não vale para ETF); dividendos isentos; "
                   "JCP já vem líquido"),
            "real": "valores também em R$ do início, deflacionados pelo IPCA",
            "ic": ("rank-IC de cada revisão: correlação de Spearman entre a "
                   "pontuação registrada e o excesso sobre o CDI realizado nos 6 "
                   "meses seguintes, em todo o universo registrado"),
        },
        "expectativa": {
            "resumo": ("+2 a +4% a.a. sobre uma carteira de pesos iguais, com o "
                       "mesmo risco de queda do mercado e anos de perda forte"),
            "fora_da_amostra": [
                {"periodo": "1996-2009", "modelo": "congelado em 2010-2022",
                 "ic": 0.090, "excesso_aa": 0.024, "t": 0.8,
                 "anos_positivos": "9 de 14", "pior_ano": "2009: -17 p.p.",
                 "queda_maxima": -0.67},
                {"periodo": "2023-2026", "ic": 0.203, "excesso_aa": 0.081,
                 "sharpe_deflacionado": 0.734,
                 "leitura": "período favorável; não é a expectativa"},
            ],
            "contra_o_indice": ("em 2023-2026 o C2 antes do IR empatou com o "
                                "índice; a vantagem depois do IR veio da isenção "
                                "de R$ 20 mil, que o ETF não tem"),
        },
        "criterio": {
            "minimo": ("nenhuma conclusão antes de 12 meses de registro (2 "
                       "revisões com o IC realizado)"),
            "veredito": ("com 24 meses (4 revisões): consistente com a pesquisa "
                         "se o IC médio das revisões for > 0 e pelo menos 3 das 4 "
                         "forem > 0; refutado se o IC médio for <= 0. O excesso "
                         "sobre pesos iguais e sobre o BOVA11 é relatado com "
                         "intervalo, mas 24 meses não têm poder para provar "
                         "+3% a.a."),
            "mudanca": ("nenhuma mudança de receita durante o acompanhamento. "
                        "Uma receita nova é outro protocolo, em outro registro"),
        },
    }


# -- Pontuação (só no ambiente da pesquisa: lightgbm e torch) ------------------

FAMILIAS = ("ols_huber", "pls", "lgbm", "nn1")
HORIZONTE = 6
ALVO = f"yr_{HORIZONTE}"
ANOS_VALIDACAO = 2


def _modulos_da_pesquisa(pasta: Path):
    codigo = str(pasta / "codigo")
    if codigo not in sys.path:
        sys.path.insert(0, codigo)
    from b3_exp import carregar as cg
    from b3_exp.ranking import conjunto_de_entradas
    from nucleo import modelos
    from nucleo import walkforward as wf
    return cg, conjunto_de_entradas, modelos, wf


def preparar(pasta: Path, painel: Path | None = None):
    """Painel no formato do teste final: só o universo, yr_h e entradas normalizadas.

    Repete ``teste_final_sem_dy.main`` linha a linha até ``normalizar_por_data``.
    """
    cg, conjunto_de_entradas, _, wf = _modulos_da_pesquisa(pasta)
    df, features = cg.carregar(painel)
    df = df[df["liquido"].fillna(False)].copy()
    for h in cg.HORIZONTES:
        r = df.groupby("data")[f"yx_{h}"].rank(pct=True)
        n = df.groupby("data")[f"yx_{h}"].transform("count")
        df[f"yr_{h}"] = 2 * (r - 0.5 / n) - 1
    feats = conjunto_de_entradas("todas_sem_dy", features)
    norm = wf.normalizar_por_data(df, feats)
    return df, norm, feats


def ajustar_e_prever(norm, feats, modelo: str, ini_teste, datas_teste, pasta: Path, *,
                     so_com_alvo: bool):
    """Uma iteração de ``walkforward.rodar`` com o corte em ``ini_teste``.

    O treino e a validação são exatamente os do walk-forward: validação nas
    linhas dos ``ANOS_VALIDACAO`` anos anteriores cujo alvo termina antes de
    ``ini_teste``; treino nas linhas cujo alvo termina antes do início da
    validação. A diferença está no teste. Ao vivo (``so_com_alvo=False``), as
    linhas da formação não têm alvo, e ``rodar`` as descartaria.
    """
    import numpy as np
    import pandas as pd
    _, _, modelos, _ = _modulos_da_pesquisa(pasta)
    ini_teste = pd.Timestamp(ini_teste)
    ini_val = ini_teste - pd.DateOffset(years=ANOS_VALIDACAO)
    d = norm.dropna(subset=[ALVO])
    d = d[np.isfinite(d[ALVO])]
    fim_alvo = d["data"] + pd.offsets.MonthEnd(HORIZONTE)
    m_val = (d["data"] >= ini_val) & (fim_alvo < ini_teste)
    m_tr = fim_alvo < ini_val
    if m_tr.sum() < 200 or m_val.sum() < 50:
        raise SystemExit(f"{modelo}: treino ({m_tr.sum()}) ou validação ({m_val.sum()}) pequenos demais")
    tr, va = d[m_tr], d[m_val]
    te = (d if so_com_alvo else norm)
    te = te[te["data"].isin(pd.to_datetime(list(datas_teste)))]
    if te.empty:
        raise SystemExit(f"nenhuma linha de teste em {list(datas_teste)}")
    mdl = modelos.ajustar(modelo, tr[feats].values, tr[ALVO].values,
                          va[feats].values, va[ALVO].values, 0,
                          gtr=tr["data"].values, gva=va["data"].values)
    saida = pd.DataFrame({"id": te["id"].values, "data": te["data"].values,
                          "prev": mdl.predict(te[feats].values)})
    info = {"linhas_treino": int(m_tr.sum()), "linhas_validacao": int(m_val.sum()),
            "inicio_validacao": ini_val.date().isoformat(),
            "ultima_data_treino": pd.Timestamp(tr["data"].max()).date().isoformat(),
            "ultima_data_validacao": pd.Timestamp(va["data"].max()).date().isoformat()}
    if modelo == "lgbm":
        info["hiperparametros"] = {"num_leaves": mdl.num_leaves,
                                   "learning_rate": mdl.learning_rate,
                                   "arvores": int(mdl.best_iteration_ or mdl.n_estimators)}
    return saida, info


def ensemble(df, previsoes: dict, *, so_com_alvo: bool):
    """Média dos postos na data das quatro famílias e do momento 12-2."""
    import pandas as pd
    partes = {}
    for modelo, p in previsoes.items():
        q = p[["id", "data", "prev"]].copy()
        q["prev"] = q.groupby("data")["prev"].rank(pct=True)
        partes[modelo] = q.set_index(["id", "data"])["prev"]
    datas = sorted({d for p in previsoes.values() for d in p["data"]})
    mom = df[df["data"].isin(datas)][["id", "data", "mom_12_2", ALVO]]
    if so_com_alvo:
        mom = mom.dropna(subset=[ALVO])
    mom = mom.copy()
    mom["prev"] = mom.groupby("data")["mom_12_2"].rank(pct=True)
    partes["momento"] = mom.set_index(["id", "data"])["prev"]
    quadro = pd.concat(partes, axis=1)
    quadro["pontuacao"] = quadro.mean(axis=1)
    return quadro.reset_index()


def pontuar(pasta: Path, ini_teste, datas_teste, *, so_com_alvo: bool, painel=None):
    df, norm, feats = preparar(pasta, painel)
    previsoes, treinos = {}, {}
    for modelo in FAMILIAS:
        previsoes[modelo], treinos[modelo] = ajustar_e_prever(
            norm, feats, modelo, ini_teste, datas_teste, pasta, so_com_alvo=so_com_alvo)
        print(f"  {modelo}: {treinos[modelo]}", flush=True)
    quadro = ensemble(df, previsoes, so_com_alvo=so_com_alvo)
    quadro = quadro.merge(df[["id", "data", "ticker"]], on=["id", "data"], how="left")
    return quadro, previsoes, treinos, feats


def cmd_validar(args) -> int:
    """Refaz o ano de 2026 do teste final e compara com os parquets publicados.

    Mesmo corte (``ini_teste`` = 2026-01-01), mesmas linhas, mesma semente.
    Se o código ao vivo é a receita, as previsões saem iguais.
    """
    import numpy as np
    import pandas as pd
    pasta = pasta_pesquisa()
    saida = pasta / "resultados" / "teste_final"
    datas = ["2026-01-31", "2026-02-28", "2026-03-31"]
    quadro, previsoes, _, _ = pontuar(pasta, "2026-01-01", datas, so_com_alvo=True)
    falhou = False
    for modelo, p in previsoes.items():
        ref = pd.read_parquet(saida / f"b3_C2_sem_dy_{modelo}.parquet")
        ref = ref[ref["ano"] == 2026][["id", "data", "prev"]]
        m = p.merge(ref, on=["id", "data"], how="outer", suffixes=("", "_ref"), indicator=True)
        so_um = int((m["_merge"] != "both").sum())
        dif = float(np.nanmax(np.abs(m["prev"] - m["prev_ref"])))
        print(f"{modelo}: {len(p)} linhas, {so_um} sem par, diferença máxima {dif:.2e}")
        falhou |= so_um > 0 or dif > args.tolerancia
    ref = pd.read_parquet(saida / "b3_C2_sem_dy_ensemble.parquet")
    ref = ref[ref["data"].isin(pd.to_datetime(datas))]
    m = quadro.merge(ref, on=["id", "data"], how="outer", indicator=True)
    dif = float(np.nanmax(np.abs(m["pontuacao"] - m["prev"])))
    so_um = int((m["_merge"] != "both").sum())
    print(f"ensemble: {len(quadro)} linhas, {so_um} sem par, diferença máxima {dif:.2e}")
    for d, g in m.groupby("data"):
        top = set(g.nlargest(c20.N_ACOES, "pontuacao")["id"])
        top_ref = set(g.nlargest(c20.N_ACOES, "prev")["id"])
        print(f"  {pd.Timestamp(d).date()}: top {c20.N_ACOES} igual em {len(top & top_ref)}")
    falhou |= so_um > 0 or dif > args.tolerancia
    print("VALIDADO" if not falhou else "NÃO VALIDADO")
    return 1 if falhou else 0


def _sem_nan(x):
    import math
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 6)


def execucao_devida(proto: dict, formacao: str, agora) -> tuple[str, bool]:
    """Pregão de execução de uma formação do protocolo e se ela está atrasada.

    No prazo, vale o pregão do calendário. Depois da abertura dele, vale o
    primeiro pregão que abre depois de ``agora`` (regra de atraso do protocolo).
    """
    from datetime import date
    cal = proto["calendario"]
    previstas = {c["data_formacao"]: c["pregao_execucao"]
                 for c in [cal["inaugural"], *cal["planejado"]]}
    if formacao not in previstas:
        raise SystemExit(f"{formacao} não é data de formação do protocolo")
    execucao = previstas[formacao]
    if agora < c20.abertura_do_pregao(execucao):
        return execucao, False
    dia = agora.date()
    if not (c20.e_pregao(dia) and agora < c20.abertura_do_pregao(dia.isoformat())):
        dia = c20.proximo_pregao(dia)
    return date.isoformat(dia), True


def _versoes() -> dict:
    import platform
    from importlib import metadata
    saida = {"python": platform.python_version()}
    for pacote in ("numpy", "pandas", "scikit-learn", "lightgbm", "torch", "pyarrow"):
        try:
            saida[pacote] = metadata.version(pacote)
        except metadata.PackageNotFoundError:
            saida[pacote] = None
    return saida


def corpo_revisao(formacao: str, execucao: str, atrasada: bool, registros: list[dict],
                  painel: Path | None = None) -> dict:
    import pandas as pd
    pasta = pasta_pesquisa()
    proto = c20.protocolo(registros)
    hashes = hashes_da_pesquisa(pasta)
    mudaram = sorted(k for k, v in proto["origem"]["sha256"].items() if hashes.get(k) != v)
    if mudaram:
        raise SystemExit(f"a receita mudou desde o protocolo ({mudaram}); uma receita "
                         "nova é outro protocolo, não uma revisão")
    painel = painel or pasta / "dados" / "b3_painel_mensal.parquet"
    ini_teste = (pd.Timestamp(formacao) + pd.Timedelta(days=1)).date().isoformat()
    quadro, _, treinos, feats = pontuar(pasta, ini_teste, [formacao],
                                        so_com_alvo=False, painel=painel)
    dia = quadro[quadro["data"] == pd.Timestamp(formacao)].copy()
    if dia["ticker"].isna().any() or dia["ticker"].duplicated().any():
        raise SystemExit("ticker ausente ou repetido no universo da formação")
    dia["pontuacao"] = dia["pontuacao"].round(6)
    dia = dia.sort_values(["pontuacao", "ticker"], ascending=[False, True])
    escolhidas = dia.head(c20.N_ACOES)
    peso = round(1 / c20.N_ACOES, 6)
    componentes = [*FAMILIAS, "momento"]
    return {
        "versao_receita": VERSAO_RECEITA,
        "protocolo_hash": registros[0]["hash"],
        "data_formacao": formacao,
        "pregao_execucao": execucao,
        "atrasada": atrasada,
        "carteira": [{"ticker": r.ticker, "id": r.id, "pontuacao": _sem_nan(r.pontuacao),
                      "peso_alvo": peso} for r in escolhidas.itertuples()],
        "universo": {
            "n": len(dia),
            "componentes": componentes,
            "pontuacoes": [{"ticker": r["ticker"], "id": r["id"],
                            "pontuacao": _sem_nan(r["pontuacao"]),
                            "postos": [_sem_nan(r[c]) for c in componentes]}
                           for _, r in dia.iterrows()],
        },
        "treino": {"corte": ini_teste, "entradas": len(feats), "modelos": treinos},
        "entradas": {"painel": painel.name, "painel_sha256": c20.sha256_arquivo(painel)},
        "codigo": {"pesquisa_sha256": hashes,
                   "scripts/carteira20_revisao.py": c20.sha256_arquivo(Path(__file__)),
                   "core/carteira20.py": c20.sha256_arquivo(ROOT / "core" / "carteira20.py")},
        "ambiente": _versoes(),
    }


def cmd_revisar(args) -> int:
    from datetime import datetime
    registros = c20.ler(args.registro)
    status = c20.verificar_cadeia(registros)
    if not status.integra or not c20.protocolo(registros):
        raise SystemExit("cadeia quebrada ou sem protocolo: " + "; ".join(status.erros))
    feitas = {r["corpo"]["data_formacao"] for r in c20.revisoes(registros)}
    if args.formacao in feitas:
        raise SystemExit(f"a formação {args.formacao} já foi registrada; mudança é correção")
    agora = datetime.now(c20.FUSO_B3).replace(microsecond=0)
    execucao, atrasada = execucao_devida(c20.protocolo(registros), args.formacao, agora)
    corpo = corpo_revisao(args.formacao, execucao, atrasada, registros, args.painel)
    print(f"formação {args.formacao}, execução {execucao}"
          f"{' (ATRASADA)' if atrasada else ''}, universo {corpo['universo']['n']}")
    for i, c in enumerate(corpo["carteira"], start=1):
        print(f"  {i:2d}. {c['ticker']:<7} {c['pontuacao']:.6f}")
    if args.seco:
        print("--seco: nada foi gravado")
        return 0
    reg = c20.anexar("revisao", corpo, caminho=args.registro)
    print(f"revisão gravada: seq {reg['seq']}, hash {reg['hash']}, em {reg['registrado_em']}")
    return 0


def cmd_protocolo(args) -> int:
    registros = c20.ler(args.registro)
    if registros:
        raise SystemExit("o registro já tem protocolo; uma mudança é uma correção, não um protocolo novo")
    reg = c20.anexar("protocolo", corpo_protocolo(hashes_da_pesquisa(pasta_pesquisa())),
                     caminho=args.registro)
    print(f"protocolo gravado: seq {reg['seq']}, hash {reg['hash']}")
    return 0


def cmd_verificar(args) -> int:
    status = c20.verificar_cadeia(c20.ler(args.registro))
    if status.integra:
        print(f"cadeia íntegra: {status.n} registros, último hash {status.ultimo_hash}")
        return 0
    print(f"cadeia QUEBRADA ({status.n} registros):")
    for erro in status.erros:
        print(f"  - {erro}")
    return 1


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registro", type=Path, default=c20.REGISTRO)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("protocolo", help="grava o registro 0").set_defaults(func=cmd_protocolo)
    sub.add_parser("verificar", help="confere a cadeia").set_defaults(func=cmd_verificar)
    v = sub.add_parser("validar", help="refaz 2026 do teste final e compara")
    v.add_argument("--tolerancia", type=float, default=1e-6)
    v.set_defaults(func=cmd_validar)
    r = sub.add_parser("revisar", help="pontua o universo e grava a revisão")
    r.add_argument("--formacao", required=True, help="data de formação do protocolo (AAAA-MM-DD)")
    r.add_argument("--painel", type=Path, default=None)
    r.add_argument("--seco", action="store_true", help="mostra a carteira sem gravar")
    r.set_defaults(func=cmd_revisar)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
