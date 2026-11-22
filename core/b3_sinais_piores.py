"""
core/b3_sinais_piores.py — algum sinal da DATA DA DECISÃO apontava os piores
nomes da carteira B3?

Contexto (medição oos-carteira-2.6.0, perfil Equilibrado): trocar os pesos do
motor por pesos iguais nos mesmos nomes muda +0,5 pp por safra, IC
[-0,2; +1,2]; vetar o pior nome de cada safra -- o oráculo, que conhece o
futuro -- vale +13,4 pp, IC [+4,4; +25,0]. O valor está em QUAIS nomes entram,
não em quanto pesa cada um. A pergunta deste módulo: havia, em 31/03 da safra,
algum sinal observável que separasse o nome que ia afundar?

Regras da medição:

- **Só o que existia na decisão.** Preço até a véspera de 1º/04 da safra (o
  fechamento de 31/03 é o preço de compra), balanço do retrato PIT cortado em
  N-1 pela regra de vintage (`core.b3_retrato_pit`), score do ranking da
  safra. Nada da vigência entra num sinal.
- **Direção registrada antes de medir.** Cada sinal diz, em ``SINAIS``, que
  lado é o suspeito (momento baixo, volatilidade alta...). A medição não
  escolhe o lado que deu certo: isso seria ajustar ao resultado.
- **A régua é vetar ao acaso, não vetar nada.** Vetar troca o nome pelo
  próximo do ranking do segmento; se os substitutos rendem, em média, mais
  que os titulares, qualquer veto "ganha". O sinal só tem valor se o nome que
  ele aponta for pior que um nome sorteado da mesma carteira.
- **Mesma mecânica da banda do portão.** O veto passa por
  `vetar_na_carteira` -> `montar_carteira` -> `rendimento_contado` (caixa a
  Selic quando a carteira deixa de ser salvável, regra D1) -- os mesmos
  caminhos de `escolher_veto`. O rendimento é BRUTO de custos, como na banda.
- **Muitas comparações.** Com nove sinais, a chance de ao menos um IC de 95%
  excluir o zero por acaso é de cerca de 37%. Um sinal "vencedor" isolado,
  num perfil só, é hipótese para a próxima safra, não regra para a tela.

Puro: sem streamlit, sem banco. Coberto por tests/test_b3_sinais_piores.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.b3_oos_carteira import (
    Exclui,
    Segmento,
    carteira_salvavel,
    escolher_veto,
    leitura_honesta,
    montar_carteira,
    rendimento_contado,
    resumir,
    substituto_no_segmento,
    vetar_na_carteira,
)
from core.b3_safras import TOLERANCIA_DIAS
from core.b3_vigencia import janela_de_vigencia

VERSAO_SINAIS = "sinais-piores-1.0.0"


@dataclass(frozen=True)
class Sinal:
    nome: str
    #: +1: valor ALTO é o suspeito; -1: valor BAIXO é o suspeito.
    direcao: int
    fonte: str
    descricao: str


#: Registrados antes de medir. Mudar uma direção depois de ver o resultado
#: invalida a medição.
SINAIS: tuple[Sinal, ...] = (
    Sinal("momento_12m", -1, "preco", "retorno dos 12 meses até 31/03 (baixo = suspeito)"),
    Sinal("momento_6m", -1, "preco", "retorno dos 6 meses até 31/03 (baixo = suspeito)"),
    Sinal("volatilidade_12m", +1, "preco",
          "desvio-padrão dos retornos dos 12 meses até 31/03 (alto = suspeito)"),
    Sinal("queda_do_topo_12m", -1, "preco",
          "preço de 31/03 sobre o topo dos 12 meses, menos 1 (fundo = suspeito)"),
    Sinal("roe", -1, "balanco", "ROE do último exercício aceito até N-1 (baixo = suspeito)"),
    Sinal("endividamento", +1, "balanco",
          "Endividamento_Total do retrato PIT (alto = suspeito)"),
    Sinal("p_vp", +1, "balanco", "P/VP do retrato PIT (caro = suspeito)"),
    Sinal("alertas_balanco", +1, "balanco",
          "soma de Patrimonio_Negativo, Endividamento_Fora_De_Faixa e FCO_Negativo"),
    Sinal("folga_do_score", -1, "ranking",
          "score do nome menos o do substituto no segmento (folga pequena = suspeito)"),
)

NOMES_SINAIS = tuple(s.nome for s in SINAIS)

#: Mínimo de nomes com sinal e retorno para calcular o IC de posto da safra.
MIN_NOMES_IC = 4
#: Mínimo de retornos mensais para a volatilidade.
MIN_RETORNOS_VOL = 8

AVISO_COMPARACOES = (
    f"{len(SINAIS)} sinais medidos: a chance de ao menos um IC de 95% excluir o "
    f"zero por acaso é de ~{1 - 0.95 ** len(SINAIS):.0%}. Sinal que só aparece "
    "num perfil, ou que não supera o veto ao acaso, não é evidência.")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _chave(tk) -> str:
    return str(tk).upper().replace(".SA", "")


# ── sinais ──────────────────────────────────────────────────────────────────

def sinais_de_preco(serie: pd.Series | None, decisao_em: pd.Timestamp) -> dict:
    """Sinais de preço com o que existia ANTES de ``decisao_em`` (1º/04).

    A última cotação tem de estar a até ``TOLERANCIA_DIAS`` da decisão (a
    mesma tolerância das pontas de `core.b3_safras`); sem ela, todo sinal de
    preço do papel é ``None``. A ponta de 12 (ou 6) meses atrás segue a mesma
    tolerância."""
    vazio = {"momento_12m": None, "momento_6m": None,
             "volatilidade_12m": None, "queda_do_topo_12m": None}
    if serie is None:
        return vazio
    s = pd.to_numeric(serie, errors="coerce").dropna()
    s = s[(s > 0) & (s.index < decisao_em)].sort_index()
    tol = pd.Timedelta(days=TOLERANCIA_DIAS)
    if s.empty or (decisao_em - s.index[-1]) > tol:
        return vazio
    d_fim, p_fim = s.index[-1], float(s.iloc[-1])

    def _momento(meses: int) -> float | None:
        alvo = d_fim - pd.DateOffset(months=meses)
        antes = s[s.index <= alvo]
        if antes.empty or (alvo - antes.index[-1]) > tol:
            return None
        return p_fim / float(antes.iloc[-1]) - 1.0

    janela = s[s.index > d_fim - pd.DateOffset(months=12)]
    rets = janela.pct_change().dropna()
    vol = float(rets.std(ddof=1)) if len(rets) >= MIN_RETORNOS_VOL else None
    topo = None
    if _momento(12) is not None:
        topo = p_fim / float(s[s.index >= d_fim - pd.DateOffset(months=12)].max()) - 1.0
    return {"momento_12m": _momento(12), "momento_6m": _momento(6),
            "volatilidade_12m": vol, "queda_do_topo_12m": topo}


def sinais_de_balanco(linha: dict | None) -> dict:
    """Sinais do retrato PIT (`core.b3_retrato_pit.quadro_decisao_pit`).
    Ausência fica ``None``: nunca vira zero, que seria um balanço inventado."""
    linha = linha or {}
    alertas = [_num(linha.get(c)) for c in
               ("Patrimonio_Negativo", "Endividamento_Fora_De_Faixa", "FCO_Negativo")]
    alertas = [a for a in alertas if a is not None]
    return {"roe": _num(linha.get("ROE")),
            "endividamento": _num(linha.get("Endividamento_Total")),
            "p_vp": _num(linha.get("P/VP")),
            "alertas_balanco": float(sum(alertas)) if alertas else None}


def folga_do_score(tk: str, segmentos: list[Segmento], exclui: Exclui = None) -> float | None:
    """Score do nome menos o do substituto que herdaria a vaga dele (o mesmo
    de `substituto_no_segmento`). Nome em mais de um segmento: a menor folga.
    Sem substituto, ou sem score, ``None``."""
    folgas = []
    for _setor, sel, _p, ranking in segmentos:
        if tk not in sel:
            continue
        scores = {str(c): _num(sc) for c, sc in ranking}
        sub = substituto_no_segmento(sel, ranking, exclui)
        if sub is None or scores.get(tk) is None or scores.get(sub) is None:
            continue
        folgas.append(scores[tk] - scores[sub])
    return min(folgas) if folgas else None


def sinais_da_carteira(nomes, segmentos: list[Segmento], precos: pd.DataFrame,
                       df_pit: pd.DataFrame | None, safra: int,
                       exclui: Exclui = None) -> dict[str, dict]:
    """Os sinais de ``SINAIS`` de cada nome, todos medidos na decisão da safra."""
    decisao_em, _fim = janela_de_vigencia(int(safra))
    retrato: dict[str, dict] = {}
    if df_pit is not None and not df_pit.empty and "Ticker" in df_pit.columns:
        for linha in df_pit.to_dict("records"):
            retrato[_chave(linha["Ticker"])] = linha
    saida: dict[str, dict] = {}
    for tk in nomes:
        tk = str(tk)
        serie = precos[tk] if tk in precos.columns else None
        saida[tk] = {**sinais_de_preco(serie, decisao_em),
                     **sinais_de_balanco(retrato.get(_chave(tk))),
                     "folga_do_score": folga_do_score(tk, segmentos, exclui)}
    return saida


# ── medição ─────────────────────────────────────────────────────────────────

def _sinal(nome: str) -> Sinal:
    for s in SINAIS:
        if s.nome == nome:
            return s
    raise KeyError(nome)


def mais_suspeito(nomes, sinais: dict[str, dict], nome_sinal: str) -> str | None:
    """O nome que o sinal aponta: o valor mais do lado suspeito. Nome sem o
    sinal não é apontado; ninguém com sinal, ``None`` (não veta).

    Empate no extremo também não veta: o sinal não distingue os empatados, e
    desempatar pela ordem alfabética mediria o alfabeto. É o caso comum de
    ``alertas_balanco`` -- carteira em que ninguém tem alerta não tem quem
    vetar."""
    d = _sinal(nome_sinal).direcao
    valores = sorted((-d * v, str(tk)) for tk in nomes
                     if (v := _num((sinais.get(str(tk)) or {}).get(nome_sinal))) is not None)
    if not valores or (len(valores) > 1 and abs(valores[0][0] - valores[1][0]) < 1e-12):
        return None
    return valores[0][1]


def ic_de_posto(nomes, sinais: dict[str, dict], retornos: dict, nome_sinal: str) -> float | None:
    """Correlação de Spearman, dentro da carteira, entre a SUSPEITA (o sinal
    orientado pela direção registrada) e o retorno da vigência. Negativa =
    o sinal aponta quem rendeu menos. Menos de ``MIN_NOMES_IC`` pares, ou
    sem variação, ``None``."""
    d = _sinal(nome_sinal).direcao
    pares = [(d * v, float(r)) for tk in nomes
             if (v := _num((sinais.get(str(tk)) or {}).get(nome_sinal))) is not None
             and (r := _num(retornos.get(str(tk)))) is not None]
    if len(pares) < MIN_NOMES_IC:
        return None
    df = pd.DataFrame(pares, columns=["s", "r"])
    if df["s"].nunique() < 2 or df["r"].nunique() < 2:
        return None
    return _num(df["s"].rank().corr(df["r"].rank()))


def medir_safra(safra: int, segmentos: list[Segmento], retornos: dict,
                sinais: dict[str, dict], *, cap: float, teto_setor: float,
                teto_ciclico: float, exclui: Exclui = None,
                caixa: float | None = None) -> dict | None:
    """Uma safra: o que cada sinal teria vetado e quanto isso mudaria.

    ``retornos`` cobre os nomes da banda (`tickers_da_banda`): titulares e
    substitutos. Safra cuja carteira sem portão não é salvável fica de fora
    (``None``): ali o investidor estava em caixa e não há nome a vetar."""
    tetos = {"cap": cap, "teto_setor": teto_setor, "teto_ciclico": teto_ciclico}

    def _contado(vetado: str | None) -> tuple[float, dict]:
        itens, _ = vetar_na_carteira(segmentos, vetado, exclui)
        cart = montar_carteira(itens, **tetos, exclui=exclui)
        bruto = sum(w * float(retornos.get(tk) or 0.0) for tk, w in cart["pesos"].items())
        return rendimento_contado(cart, bruto, caixa), cart

    base, cart = _contado(None)
    if not carteira_salvavel(cart["pesos"], inviavel=bool(cart["inviavel"]),
                             exige_revisao=bool(cart["exige_revisao"])):
        return None
    nomes = sorted(cart["pesos"])
    com_ret = [(float(retornos[tk]), tk) for tk in nomes if _num(retornos.get(tk)) is not None]
    pior = min(com_ret)[1] if com_ret else None
    excesso_de = {tk: _contado(tk)[0] - base for tk in nomes}
    acaso = float(np.mean(list(excesso_de.values())))
    oraculo = {lado: escolher_veto(segmentos, retornos, veta=lado, **tetos,
                                   exclui=exclui, caixa=caixa)["efetivo"] - base
               for lado in ("pior", "melhor")}

    por_sinal = {}
    for s in SINAIS:
        vetado = mais_suspeito(nomes, sinais, s.nome)
        excesso = excesso_de[vetado] if vetado is not None else 0.0
        por_sinal[s.nome] = {
            "vetado": vetado,
            "excesso": excesso,
            "sobre_acaso": excesso - acaso,
            "acertou_o_pior": (vetado is not None and vetado == pior),
            "ic": ic_de_posto(nomes, sinais, retornos, s.nome),
            "cobertura": sum(1 for tk in nomes
                             if _num((sinais.get(tk) or {}).get(s.nome)) is not None),
        }
    return {
        "safra": int(safra),
        "n_nomes": len(nomes),
        "pior_realizado": pior,
        "retorno_do_pior": _num(retornos.get(pior)) if pior else None,
        "rendimento_sem_portao": base,
        "veto_ao_acaso": acaso,
        "veto_do_pior_realizado": excesso_de[pior] if pior else None,
        "oraculo_pior": oraculo["pior"],
        "oraculo_melhor": oraculo["melhor"],
        "sinais": por_sinal,
        "valores": {tk: sinais.get(tk, {}) for tk in nomes},
        "retornos": {tk: _num(retornos.get(tk)) for tk in nomes},
    }


def resumir_sinais(linhas: list[dict]) -> dict:
    """O placar de cada sinal ao longo das safras medidas.

    - ``veto``: excesso de vetar o nome apontado sobre não vetar;
    - ``sobre_acaso``: o mesmo, menos o veto de um nome sorteado da carteira
      (a régua que importa; é dela a ``leitura``);
    - ``ic``: Spearman médio entre suspeita e retorno (negativo = bom);
    - ``acertos``: safras em que o apontado foi o pior realizado, contra o
      esperado ao acaso (soma de 1/n);
    - ``fracao_do_oraculo``: média do veto sobre a média do oráculo.
    """
    linhas = [r for r in linhas if r]

    def _col(chave, f):
        return [{"safra": r["safra"], chave: f(r)} for r in linhas]

    oraculo = resumir(_col("x", lambda r: r["oraculo_pior"]), "x")
    sinais = {}
    for s in SINAIS:
        veto = resumir(_col("x", lambda r, n=s.nome: r["sinais"][n]["excesso"]), "x")
        sobre = resumir(_col("x", lambda r, n=s.nome: r["sinais"][n]["sobre_acaso"]), "x")
        ic = resumir(_col("x", lambda r, n=s.nome: r["sinais"][n]["ic"]), "x")
        fracao = (veto["media"] / oraculo["media"]
                  if veto["media"] is not None and oraculo["media"] else None)
        sinais[s.nome] = {
            "direcao": s.direcao, "fonte": s.fonte, "descricao": s.descricao,
            "veto": veto,
            "sobre_acaso": sobre,
            "ic": ic,
            "acertos": sum(1 for r in linhas if r["sinais"][s.nome]["acertou_o_pior"]),
            "acertos_ao_acaso": float(sum(1.0 / r["n_nomes"] for r in linhas if r["n_nomes"])),
            "fracao_do_oraculo": fracao,
            "leitura": leitura_honesta(sobre, "vetar um nome ao acaso",
                                       alternativa="vetar um nome ao acaso"),
        }
    return {
        "versao": VERSAO_SINAIS,
        "n_safras": len(linhas),
        "oraculo_pior": oraculo,
        "oraculo_melhor": resumir(_col("x", lambda r: r["oraculo_melhor"]), "x"),
        "veto_do_pior_realizado": resumir(_col("x", lambda r: r["veto_do_pior_realizado"]), "x"),
        "veto_ao_acaso": resumir(_col("x", lambda r: r["veto_ao_acaso"]), "x"),
        "sinais": sinais,
        "aviso": AVISO_COMPARACOES,
        "regua": ("rendimento BRUTO de custos da carteira com um veto, na mecânica "
                  "da banda do portão (substituto do ranking, cap, tetos, caixa D1)"),
        "safras": linhas,
    }
