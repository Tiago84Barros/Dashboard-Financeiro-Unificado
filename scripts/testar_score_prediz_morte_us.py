# -*- coding: utf-8 -*-
"""O score americano, calculado em 2013, previa quem morreria ate 2025? (A-158)

Por que este teste e nao a correcao do painel: corrigir o backtest exigiria o
retorno futuro das empresas mortas, e ele nao existe nas nossas fontes -- o
yfinance nao serve deslistada e, pior, devolve serie de OUTRO papel que herdou
o ticker (SHLD hoje e um ETF de 2023, nao a Sears). Enxertar isso seria
inventar evidencia.

Mas ha um desfecho observavel sem cotacao nenhuma, e e o pior desfecho possivel
para o investidor: a empresa deixar de existir sem ninguem comprar. Se o score
de 2013 nao separa quem seguiu de quem sumiu, o ranking nao protege contra
perda permanente de capital -- e e exatamente isso que um backtest 100%
sobrevivente nao consegue dizer.

O desfecho tem TRES estados, e reduzi-los a dois inverte a conclusao. A
primeira versao deste teste chamou de "morte" todo mundo que parou de arquivar
e concluiu que as mortas pontuavam MAIS que as sobreviventes (56,3 x 46,8).
Nao era sinal invertido: metade das saidas tem assinatura de fusao, e ser
comprada e bom desfecho -- empresa boa e comprada com premio. Contar aquisicao
como morte mede o oposto do que interessa.

Desenho:
  coorte   = quem arquivou relatorio anual em 2012 (indice full-index da SEC);
  as_of    = 2013-06-30, so com fatos ja arquivados nessa data (PIT de verdade);
  desfecho = sobreviveu (arquivava em 2025) | adquirida (proxy de fusao,
             SC 14D9, ou 8-K item 2.01 no fim da historia) | sumiu (8-K item
             1.03: falencia ou recuperacao judicial) | indefinido (parou de
             arquivar sem deixar nenhuma dessas marcas -- FORA da comparacao).

`S-4` fica de fora do sinal de fusao de proposito: quem emite S-4 costuma ser o
COMPRADOR, nao o comprado.

O indefinido nao e detalhe: numa sondagem de 60 saidas, so 3 tinham 8-K de
falencia. Se a amostra classificada de "sumiu" nao alcancar MINIMO_SUMIU, o
bloco sai marcado `insuficiente` e nada e afirmado -- e o desenlace mais
provavel, e e um resultado, nao uma falha.

Amostra pareada por sorteio entre mortas e sobreviventes; o score sai do MESMO
`score_cross_section` da producao, sobre o cross-section conjunto -- calculado
so entre sobreviventes, ele reproduziria o vies que se quer medir.

    python scripts/testar_score_prediz_morte_us.py [--n 600] [--cache DIR]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AS_OF = date(2013, 6, 30)
ANO_COORTE, ANO_DESFECHO = 2012, 2025
AGENTE = "Dashboard Financeiro Unificado tsbcorporation84@gmail.com"
URL_IDX = "https://www.sec.gov/Archives/edgar/full-index/{ano}/QTR{q}/form.idx"
URL_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
URL_SUB = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Proxy de fusao/fechamento de capital arquivado pela PROPRIA empresa comprada.
FORMAS_FUSAO = ("DEFM14A", "PREM14A", "DEFM14C", "PREM14C", "SC 13E3")
# Recomendacao de resposta a oferta hostil: quem arquiva e o ALVO.
FORMAS_ALVO = ("SC 14D9",)
ITEM_FALENCIA = "1.03"       # 8-K: pedido de falencia ou concordata
ITEM_AQUISICAO = "2.01"      # 8-K: conclusao de aquisicao ou alienacao de ativos
# Janela final da historia de arquivamento em que um 2.01 fala da PROPRIA
# empresa sendo comprada, e nao dela comprando algo no curso normal.
DIAS_FIM = 365

# Abaixo disto a AUC e ruido: a falencia comprovada e evento raro (3 em 60
# saidas amostradas), e uma amostra pequena de casos raros produz numero
# convincente e errado.
MINIMO_SUMIU = 30

SOBREVIVEU, ADQUIRIDA, SUMIU = "sobreviveu", "adquirida", "sumiu"
# Saida sem evidencia de qual foi: fica FORA da comparacao. Empurra-la para o
# grupo ruim foi o erro que inverteu a primeira medicao -- 34 de 60 saidas
# amostradas carregam 8-K 2.01 e so 3 carregam 1.03.
INDEFINIDO = "indefinido"


def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _idx(ano: int, cache: Path) -> set[int]:
    from core.us_survivorship import ciks_com_relatorio_anual
    achados: set[int] = set()
    for q in (1, 2, 3, 4):
        alvo = cache / "{}Q{}.idx".format(ano, q)
        if not alvo.exists():
            try:
                alvo.write_bytes(_get(URL_IDX.format(ano=ano, q=q), timeout=180))
            except Exception as exc:  # noqa: BLE001
                print("   {}Q{} indisponivel ({})".format(ano, q, type(exc).__name__))
                continue
        achados |= ciks_com_relatorio_anual(
            alvo.read_text(encoding="latin-1", errors="ignore"))
    return achados


def _compactar(fatos: dict) -> dict:
    """Reduz o companyfacts as linhas anuais, com a data de cada campo.

    Guardar o blob cru estourou a memoria: ha companyfacts de dezenas de MB e o
    processo retem os 1.400. Aqui ficam so as linhas anuais -- duas ordens de
    grandeza menores -- e com `filed_at` por campo, sem o qual nao da para
    comparar as duas regras de visibilidade.
    """
    from scripts._pit_visibilidade import linhas_anuais

    return linhas_anuais(fatos)


def _baixar_empresa(cik: int, cache: Path, itens: bool = False) -> dict | None:
    """Registro compacto (fundamentos visiveis + SIC + formas), em cache no disco.

    None quando nao ha XBRL: ausencia tambem e resultado, e fica gravada para
    nao ser rebaixada de novo na proxima rodada.
    """
    alvo = cache / "linhas" / "{}.json".format(cik)
    if alvo.exists():
        try:
            pacote = json.loads(alvo.read_text(encoding="utf-8")) or None
        except Exception:  # noqa: BLE001
            return None
        # Cache antigo guardava so o tipo de formulario. Classificar saida por
        # tipo de formulario nao funciona (34 de 60 aquisicoes escapavam), entao
        # quem vai ser classificado precisa dos itens de 8-K -- e so ele.
        if pacote and itens and "itens_todos" not in pacote:
            _completar_submissions(pacote, cik)
            alvo.write_text(json.dumps(pacote), encoding="utf-8")
        return pacote
    alvo.parent.mkdir(parents=True, exist_ok=True)
    legado = cache / "empresas" / "{}.json".format(cik)
    try:
        if legado.exists():
            bruto = json.loads(legado.read_text(encoding="utf-8"))
            pacote = _compactar(bruto.get("facts") or {})
            for k in ("sic", "sic_desc", "nome", "formas"):
                pacote[k] = bruto.get(k) if k != "formas" else (bruto.get(k) or [])
            del bruto
            alvo.write_text(json.dumps(pacote), encoding="utf-8")
            return pacote
    except Exception:  # noqa: BLE001
        pass
    try:
        fatos = json.loads(_get(URL_FACTS.format(cik=cik)))
    except Exception:  # noqa: BLE001
        alvo.write_text("{}", encoding="utf-8")
        return None
    try:
        pacote = _compactar(fatos)
    except Exception as exc:  # noqa: BLE001
        print("   CIK {} ilegivel ({})".format(cik, type(exc).__name__))
        alvo.write_text("{}", encoding="utf-8")
        return None
    finally:
        del fatos
    _completar_submissions(pacote, cik)
    alvo.write_text(json.dumps(pacote), encoding="utf-8")
    return pacote


def _completar_submissions(pacote: dict, cik: int) -> None:
    try:
        sub = json.loads(_get(URL_SUB.format(cik=cik)))
        pacote["sic"] = str(sub.get("sic") or "")
        pacote["sic_desc"] = str(sub.get("sicDescription") or "")
        pacote["nome"] = str(sub.get("name") or "")
        recentes = (sub.get("filings") or {}).get("recent") or {}
        pacote["formas"] = sorted({str(f) for f in (recentes.get("form") or [])})
        pacote.update(_itens(recentes))
    except Exception:  # noqa: BLE001
        pacote.setdefault("sic", "")
        pacote.setdefault("sic_desc", "")
        pacote.setdefault("nome", "")
        pacote["formas"] = []
        pacote["itens_finais"] = []
        pacote["itens_todos"] = []


def _itens(recentes: dict) -> dict:
    """Itens de 8-K, separando os do fim da historia dos do curso normal.

    O item e o que discrimina: o tipo de formulario `8-K` cobre desde troca de
    auditor ate falencia. `2.01` no meio da vida costuma ser a empresa comprando
    algo; `2.01` nos ultimos meses de arquivamento e ela sendo comprada.
    """
    datas = [str(d) for d in (recentes.get("filingDate") or [])]
    itens = [str(i or "") for i in (recentes.get("items") or [])]
    if not datas:
        return {"itens_finais": [], "itens_todos": []}
    itens += [""] * (len(datas) - len(itens))
    fim = max(datas)
    try:
        corte = (date.fromisoformat(fim) - timedelta(days=DIAS_FIM)).isoformat()
    except ValueError:
        corte = fim
    todos: set[str] = set()
    finais: set[str] = set()
    for data, item in zip(datas, itens):
        codigos = {c.strip() for c in item.split(",") if c.strip()}
        todos |= codigos
        if data >= corte:
            finais |= codigos
    return {"itens_finais": sorted(finais), "itens_todos": sorted(todos),
            "ultimo_arquivamento": fim}


def _metricas(pacote: dict, cik: int, regra: str = "linha") -> dict | None:
    from core.us_metrics import compute_company_metrics
    from scripts._pit_visibilidade import aplicar

    inc, bal, cash = (aplicar(pacote.get("inc") or [], AS_OF, regra, "inc"),
                      aplicar(pacote.get("bal") or [], AS_OF, regra, "bal"),
                      aplicar(pacote.get("cash") or [], AS_OF, regra, "cash"))
    if not (inc or bal):
        return None
    m = compute_company_metrics(inc, bal, cash)
    sic = (pacote.get("sic") or "")[:2] or "00"
    m.update({"symbol": "CIK{}".format(cik), "industry": sic,
              "sector": pacote.get("sic_desc") or "desconhecido"})
    return m


def classificar_saida(pacote: dict) -> str:
    """Adquirida, quebrou, ou -- o caso mais comum -- nao da para saber.

    A primeira versao chamava de morte toda saida sem proxy de fusao. Medido na
    coorte de 2012 isso estava errado na maioria dos casos, e a conclusao saiu
    invertida. Aqui `SUMIU` exige EVIDENCIA de falencia (8-K item 1.03), e o que
    nao se consegue classificar vira `INDEFINIDO`, que sai da comparacao.

    Excluir e melhor que chutar para o lado conservador: o indefinido nao e uma
    minoria residual, e o grupo majoritario -- deixa-lo no grupo ruim nao
    subestima o score, apaga o que a medicao tenta medir.
    """
    formas = pacote.get("formas") or []
    finais = set(pacote.get("itens_finais") or [])
    todos = set(pacote.get("itens_todos") or [])
    # Falencia vem antes: venda de ativos DENTRO da recuperacao judicial tambem
    # arquiva 2.01, e chamar isso de aquisicao esconderia justamente o caso que
    # o investidor precisa ver.
    if ITEM_FALENCIA in todos:
        return SUMIU
    if any(f.startswith(FORMAS_FUSAO) or f.startswith(FORMAS_ALVO) for f in formas):
        return ADQUIRIDA
    if ITEM_AQUISICAO in finais:
        return ADQUIRIDA
    return INDEFINIDO


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600, help="empresas por grupo")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--visibilidade", choices=("linha", "campo"),
                    default="linha", help="regra point-in-time")
    ap.add_argument("--saida", default=str(ROOT / "data" / "us_score_vs_morte.json"))
    args = ap.parse_args(argv)

    cache = Path(args.cache) if args.cache else ROOT / ".cache" / "sec_full_index"
    (cache / "empresas").mkdir(parents=True, exist_ok=True)

    coorte, vivas = _idx(ANO_COORTE, cache), _idx(ANO_DESFECHO, cache)
    if not coorte or not vivas:
        print("indice indisponivel; nada a medir")
        return 1
    saidas = sorted(coorte - vivas)
    sobreviventes = sorted(coorte & vivas)
    print("coorte {}: {} empresas -> {} ainda arquivavam em {}, {} sairam".format(
        ANO_COORTE, len(coorte), len(sobreviventes), ANO_DESFECHO, len(saidas)))

    rnd = random.Random(20260828)
    n = min(args.n, len(saidas), len(sobreviventes))
    alvo = ([(c, False) for c in rnd.sample(saidas, n)]
            + [(c, True) for c in rnd.sample(sobreviventes, n)])

    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        pacotes = list(ex.map(
            lambda cv: (cv[1], cv[0],
                        _baixar_empresa(cv[0], cache, itens=not cv[1])), alvo))

    linhas, sem_xbrl = [], 0
    for viva, cik, pacote in pacotes:
        met = _metricas(pacote, cik, args.visibilidade) if pacote else None
        if met is None:
            sem_xbrl += 1
            continue
        met["desfecho"] = SOBREVIVEU if viva else classificar_saida(pacote)
        linhas.append(met)
    print("com fundamentos utilizaveis em {}: {} ({} sem XBRL visivel na data)"
          .format(AS_OF, len(linhas), sem_xbrl))
    if len(linhas) < 30:
        print("amostra pequena demais para concluir qualquer coisa")
        return 1

    import pandas as pd

    from core.us_score import score_cross_section
    marcado = pd.DataFrame(linhas)
    df = juntar_desfecho(score_cross_section(marcado), marcado)
    resultado = _resumir(df)
    resultado["visibilidade"] = args.visibilidade
    _imprimir(resultado)
    Path(args.saida).write_text(
        json.dumps(resultado, indent=2, ensure_ascii=False) + chr(10),
        encoding="utf-8")
    print("gravado em", args.saida)
    return 0


def juntar_desfecho(scored, marcado):
    """Cola o desfecho pelo SIMBOLO, nunca pela posicao.

    `score_cross_section` termina em `sort_values("score").reset_index(drop=True)`:
    o quadro que volta esta ORDENADO POR NOTA, nao na ordem de entrada. Colar
    `marcado["desfecho"].values` nele parecia funcionar -- mesmo tamanho, sem
    erro, sem NaN -- e etiquetava cada empresa com o desfecho de outra.

    Como a amostra lista as saidas primeiro, o efeito nao foi ruido: as saidas
    caiam nas linhas de maior nota. Dai vinham as "mortas pontuando mais que as
    sobreviventes" e o penhasco impossivel de zero falencias nos seis primeiros
    decis. Nenhuma das hipoteses economicas testadas antes disso tinha chance.
    """
    mapa = dict(zip(marcado["symbol"], marcado["desfecho"]))
    out = scored.copy()
    out["desfecho"] = out["symbol"].map(mapa)
    faltando = int(out["desfecho"].isna().sum())
    if faltando:
        raise ValueError("{} linhas sem desfecho apos a juncao".format(faltando))
    return out


def _auc(d, coluna_bom: str) -> float | None:
    """P(score de quem terminou bem > score de quem sumiu), por Mann-Whitney."""
    r = d["score"].rank()
    n1 = int(d[coluna_bom].sum())
    n0 = int(len(d) - n1)
    if not n1 or not n0:
        return None
    return round(float((r[d[coluna_bom]].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 4)


def _bloco(d) -> dict:
    """AUC calculada so sobre desfecho conhecido; o indefinido e contado a parte.

    Ele nao e ruido a descartar em silencio: e a maior parte das saidas, e o seu
    tamanho e o que diz quanto vale a AUC ao lado.
    """
    import pandas as pd
    indefinidos = int((d["desfecho"] == INDEFINIDO).sum()) if len(d) else 0
    d = d[d["desfecho"] != INDEFINIDO]
    poucas_mortes = int((d["desfecho"] == SUMIU).sum()) < MINIMO_SUMIU
    if len(d) < 20 or poucas_mortes:
        return {"empresas": int(len(d)), "indefinido": indefinidos,
                "sumiu": int((d["desfecho"] == SUMIU).sum()),
                "insuficiente": True}
    d = d.copy()
    d["nao_sumiu"] = d["desfecho"] != SUMIU
    decis = pd.qcut(d["score"].rank(method="first"), 10, labels=False,
                    duplicates="drop") + 1
    por = (d.assign(decil=decis).groupby("decil")["desfecho"]
           .agg(n="size", sumiu=lambda s: float((s == SUMIU).mean()))
           .reset_index().to_dict("records"))
    return {
        "empresas": int(len(d)),
        "indefinido": indefinidos,
        "sobreviveu": int((d.desfecho == SOBREVIVEU).sum()),
        "adquirida": int((d.desfecho == ADQUIRIDA).sum()),
        "sumiu": int((d.desfecho == SUMIU).sum()),
        "auc_nao_sumiu": _auc(d, "nao_sumiu"),
        "score_medio": {k: round(float(v), 2) for k, v in
                        d.groupby("desfecho")["score"].mean().items()},
        "taxa_sumiu_por_decil": [
            {"decil": int(r["decil"]), "empresas": int(r["n"]),
             "sumiu_pct": round(100 * float(r["sumiu"]), 1)} for r in por],
    }


def _resumir(df) -> dict:
    """Duas leituras: a coorte inteira e o recorte que a app de fato exibe.

    A coorte inteira e dominada por arquivadores minusculos sem XBRL, que a
    app nunca mostraria; medir so nela responde uma pergunta que o usuario nao
    faz. O recorte `research_grade`/`decision_grade` responde a dele.
    """
    d = df.dropna(subset=["score"]).copy()
    exibiveis = d[d["score_status"].isin(("research_grade", "decision_grade"))]
    return {
        "medido_em": date.today().isoformat(),
        "ano_coorte": ANO_COORTE, "ano_desfecho": ANO_DESFECHO,
        "as_of": AS_OF.isoformat(),
        "coorte_inteira": _bloco(d),
        "apenas_exibiveis": _bloco(exibiveis),
    }


def _imprimir(res: dict) -> None:
    for rotulo in ("coorte_inteira", "apenas_exibiveis"):
        b = res[rotulo]
        print()
        print("== {} ==".format(rotulo))
        if b.get("insuficiente"):
            print("   {} empresas: amostra insuficiente".format(b["empresas"]))
            continue
        print("   {} empresas: {} seguiram, {} adquiridas, {} sumiram".format(
            b["empresas"], b["sobreviveu"], b["adquirida"], b["sumiu"]))
        print("   AUC (nao sumiu vs sumiu) = {}   [0,50 = score nao separa]"
              .format(b["auc_nao_sumiu"]))
        print("   score medio por desfecho:", b["score_medio"])
        for x in b["taxa_sumiu_por_decil"]:
            print("     decil {:2d} ({:3d}): {:5.1f}% sumiram".format(
                x["decil"], x["empresas"], x["sumiu_pct"]))


if __name__ == "__main__":
    raise SystemExit(main())
