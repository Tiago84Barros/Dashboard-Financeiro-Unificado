"""Alocação Black-Litterman do Portfólio Global (achado GLB-01 da auditoria app4).

O motor de movimentação (`advisor.recomendar`) inclina o peso de cada ativo por
um score médio de sinais -- `peso × (1 + 0,5·score)` -- e nunca pergunta quanto
cada score vale em retorno, nem quanto confiar no motor que o produziu. Um
score da B3, cujo Rank-IC fora da amostra foi medido em 0,075 com intervalo
acima de zero, pesava o mesmo que um score de FII, que não tem IC medido. Este
módulo é a alternativa com essas duas perguntas respondidas:

1. **Prior de equilíbrio** ``π = δ·Σ·w_meta`` (He & Litterman 1999): o retorno
   em excesso que torna a META de alocação ótima. Sem view nenhuma, o
   otimizador devolve a própria meta -- o modelo só sai dela quando um motor
   com evidência medida empurra.
2. **Views dos três motores** (B3, FII, EUA), uma view absoluta por ativo com
   score: ``Q_i = π_i + α_i``, com ``α_i = IC · σ_i · z_i`` (Grinold & Kahn,
   "alpha = IC × volatilidade × score padronizado"). ``z`` é o posto do score
   dentro da própria classe levado à normal padrão; ``σ`` sai da diagonal de
   Σ. Exemplo com os números gravados em ``data/vantagem_oos.json`` (B3 em
   24/09/2026): IC 0,075, σ 30% a.a. e o melhor de 20 ativos
   (z = Φ⁻¹(19,5/20) ≈ 1,96) dão α ≈ 0,075 × 0,30 × 1,96 ≈ +4,4% a.a.
3. **Confiança proporcional ao IC medido** (Ω de Idzorek 2005, já em
   `core.black_litterman`): ``c = 0,5 × min(IC / 0,10, 1)`` quando o IC é
   estatisticamente distinto de zero, metade disso se o portão de excesso
   reprovou, e ``c = 0,01`` -- Ω = 99·τ·pΣp, a view anda 1% do caminho --
   sem medição, com medição de outra versão do motor ou com IC não
   significativo. Valores hoje: B3 0,375; EUA 0,25; FII 0,01.
4. **Σ com encolhimento Ledoit-Wolf** (`core.markowitz.ledoit_wolf_shrinkage`)
   sobre a janela comum que `returns.retornos_mensais` já publica (todo mês
   com retorno de todos os ativos), cortada nos últimos 60 meses.
5. **Otimização média-variância com as restrições da política**
   (`investment_policies`): teto por ativo, por setor e por classe, com a
   alocação entre classes travada na meta (a divisão B3/FII/EUA é decisão
   do usuário, não do modelo). Restrição impossível é relaxada em degraus
   nomeados, nunca em silêncio.

Duas convenções que mudam o número e não são óbvias:

* O otimizador usa Σ (não o Σ posterior): com Σ, "sem view devolve a meta" é
  identidade exata, e a diferença para Σ_BL ≈ (1+τ)·Σ é de 2,5% na escala.
* Ativo sem série mensal não tem linha em Σ: fica FIXADO no peso da meta e
  sai do problema, com o nome na lista ``fixados``. Ele continua ocupando o
  teto do setor e da classe dele.

Camada pura: sem SQL, sem Streamlit, sem I/O além de ler o artefato local de
`core.vantagem_oos` (injetável). Coberto por tests/test_global_black_litterman.py.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd

from core.black_litterman import BLView, posterior_returns
from core.markowitz import ledoit_wolf_shrinkage

# Aversão ao risco do mercado. 2,5 é o valor de He & Litterman (1999) e da
# maior parte da literatura; com a meta como "mercado", δ só escala π e o
# tamanho do desvio que uma view produz (Δw ≈ Σ⁻¹·Δμ / δ) -- sem view, a
# meta sai igual para qualquer δ.
DELTA_PADRAO = 2.5
# Incerteza do prior. Com Ω de Idzorek a fração do caminho que uma view anda
# depende só de c, não de τ: τ fica no valor clássico e não é parâmetro de
# tela.
TAU_PADRAO = 0.025
# Janela da covariância. 60 meses é a mesma janela comum que
# `memoria: correlacao-janelas-heterogeneas` fixou para a correlação.
MAX_MESES_PADRAO = 60
MESES_POR_ANO = 12

# Mapeamento IC -> confiança de Idzorek. IC 0,10 é o que Grinold & Kahn
# chamam de "muito bom"; acima dele a confiança não cresce mais. O teto 0,5
# existe porque o IC foi medido sobre o UNIVERSO de cada motor (117 ações por
# ano na B3, 1176 pares), e aqui as views só comparam os ativos já
# selecionados -- uma faixa truncada de score, onde a dispersão é menor e o
# IC efetivo provavelmente também. Nenhum motor anda mais da metade do
# caminho até a própria view.
IC_REFERENCIA = 0.10
CONFIANCA_MAXIMA = 0.5
# Ω = (1/0,01 - 1)·τ·pΣp = 99·τ·pΣp: a view existe, aparece na tela, e move o
# retorno esperado 1% do caminho. Zero não serve: Idzorek divide por c.
CONFIANCA_MINIMA = 0.01
# Excesso médio reprovado (intervalo atravessa zero) com IC significativo é o
# caso dos EUA: ordena bem (Rank-IC 0,107, t = 3,96) mas a carteira montada não
# bateu o universo. Metade da confiança, não zero: o IC é medido.
PENALIDADE_EXCESSO_REPROVADO = 0.5
# t mínimo para um IC sem intervalo gravado contar como distinto de zero.
T_MINIMO = 1.96
# IC usado só para DESENHAR a view de um motor sem medição. Como a confiança
# dele é 0,01, o número não move nada -- existe para a tela mostrar qual
# seria a view e quanto ela foi ignorada, em vez de esconder o motor.
IC_SEM_MEDICAO = 0.05

# Teto padrão quando a política não fala: os mesmos de `signals.py`
# (LIMITE_ATIVO_DEFAULT, LIMITE_SETOR_DEFAULT).
CAP_ATIVO_PADRAO = 0.10
CAP_SETOR_PADRAO = 0.30
# Setores que não são setor: o balde "Outros" junta o que não tem mapa, e
# tetá-lo trataria como concentração o que é só falta de rótulo.
_SETORES_SEM_TETO = frozenset({"", "outros", "none", "nan"})

# Classe do Portfólio Global -> chave de `asset_class_limits` da política.
CLASSE_DA_POLITICA: dict[str, str] = {"b3": "acoes_br", "fii": "fiis", "us": "exterior"}
MOTORES = ("b3", "fii", "us")
ROTULO_MOTOR = {"b3": "Empresas B3", "fii": "FIIs", "us": "EUA"}

_TOL = 1e-6


# ---------------------------------------------------------------------------
# Confiança por motor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfiancaMotor:
    motor: str
    ic: float | None          # IC usado para desenhar a view (None = não medido)
    confianca: float          # c de Idzorek, em (0, 1]
    fonte: str                # de onde saiu o número, com data e versão

    @property
    def omega_multiplo(self) -> float:
        """Ω em múltiplos de τ·pΣp: (1/c − 1)."""
        return 1.0 / self.confianca - 1.0

    @property
    def efetiva(self) -> bool:
        return self.confianca > CONFIANCA_MINIMA + 1e-12


def _num(valor) -> float | None:
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _confianca_do_ic(ic: float) -> float:
    return CONFIANCA_MAXIMA * min(max(ic / IC_REFERENCIA, 0.0), 1.0)


def confianca_do_motor(motor: str, medicao: Mapping | None,
                       versao_atual: str | None) -> ConfiancaMotor:
    """Traduz a medição gravada em `data/vantagem_oos.json` na confiança da view.

    Regras na ordem em que decidem:
    1. sem medição, ou medição de outra versão do motor -> mínima
       (`memoria: versao-de-metodologia-sem-safra`: resultado de outra versão
       não atesta esta);
    2. IC em coeficiente com intervalo acima de zero (B3) -> proporcional;
    3. IC de postos nos extras com t >= 1,96 (EUA) -> proporcional, e metade
       se o intervalo do excesso atravessa zero;
    4. qualquer outro caso (IC <= 0, não significativo) -> mínima.
    """
    minima = lambda fonte: ConfiancaMotor(motor, None, CONFIANCA_MINIMA, fonte)  # noqa: E731
    if not medicao:
        return minima("sem medição de IC fora da amostra gravada")
    versao = str(medicao.get("versao_metodologia") or "")
    if versao and versao_atual and versao != str(versao_atual):
        return minima(f"medição da versão {versao}, o motor roda {versao_atual}")
    quando = str(medicao.get("medido_em") or "?")
    low, high = _num(medicao.get("ic_low")), _num(medicao.get("ic_high"))

    if str(medicao.get("formato")) == "coeficiente":
        ic = _num(medicao.get("media"))
        if ic is not None and ic > 0 and low is not None and low > 0:
            return ConfiancaMotor(
                motor, ic, _confianca_do_ic(ic),
                f"Rank-IC {ic:.4f}, IC 95% [{low:.4f}; {high:.4f}], "
                f"versão {versao}, medido em {quando}")
        return minima(f"Rank-IC sem intervalo acima de zero (medido em {quando})")

    extras = medicao.get("extras") or {}
    ic = _num(extras.get("rank_ic_medio"))
    t = _num(extras.get("rank_ic_t"))
    if ic is not None and ic > 0 and t is not None and t >= T_MINIMO:
        c = _confianca_do_ic(ic)
        texto = f"Rank-IC {ic:.4f} (t = {t:.2f}), versão {versao}, medido em {quando}"
        if low is not None and low <= 0:
            c *= PENALIDADE_EXCESSO_REPROVADO
            texto += (f"; excesso da carteira reprovado (IC 95% {low:+.2%} a "
                      f"{high:+.2%}), confiança pela metade")
        return ConfiancaMotor(motor, ic, c, texto)
    return minima(f"sem IC de postos significativo (medido em {quando})")


def confianca_dos_motores(*, carregar=None, versao=None) -> dict[str, ConfiancaMotor]:
    """As três confianças, lidas do artefato local (`core.vantagem_oos`).

    `carregar(motor)` e `versao(motor)` existem para o teste; produção lê o
    JSON versionado no repositório -- nenhum acesso a banco.
    """
    if carregar is None or versao is None:
        from core import vantagem_oos
        carregar = carregar or vantagem_oos.carregar_medicao
        versao = versao or vantagem_oos.versao_corrente
    saida = {}
    for motor in MOTORES:
        try:
            med = carregar(motor)
        except Exception as exc:  # noqa: BLE001 - artefato ilegível vira "sem medição"
            saida[motor] = ConfiancaMotor(motor, None, CONFIANCA_MINIMA,
                                          f"medição ilegível ({type(exc).__name__})")
            continue
        try:
            atual = versao(motor)
        except Exception:  # noqa: BLE001 - sem versão, a checagem fica de fora
            atual = None
        saida[motor] = confianca_do_motor(motor, med, atual)
    return saida


# ---------------------------------------------------------------------------
# Restrições da política
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Restricoes:
    """Tetos na base da parcela de risco (soma 1 entre B3, FII e EUA)."""

    cap_ativo: float = CAP_ATIVO_PADRAO
    cap_setor: float | None = CAP_SETOR_PADRAO
    limites_classe: dict[str, float] = field(default_factory=dict)
    fonte: str = "tetos padrão do Portfólio Global (10% por ativo, 30% por setor)"


def restricoes_da_politica(valores: Mapping | None,
                           renda_fixa: float | None) -> Restricoes:
    """Converte os tetos de `investment_policies` (em % da carteira inteira)
    para a base da parcela de risco, que é onde a meta e o otimizador vivem.

    Com a fração de renda fixa conhecida, teto de 10% do patrimônio vira
    10% / (1 − rf) da parcela de risco (com rf = 30%: 14,3%). Sem ela, o teto
    é aplicado como está -- mais apertado do que a política pede, nunca mais
    frouxo.
    """
    if not valores:
        return Restricoes()
    escala = 1.0
    if renda_fixa is not None and 0.0 <= float(renda_fixa) < 1.0:
        escala = 1.0 / (1.0 - float(renda_fixa))

    def conv(pct) -> float | None:
        x = _num(pct)
        if x is None or x <= 0:
            return None
        return min(x / 100.0 * escala, 1.0)

    cap_ativo = conv(valores.get("single_asset_limit_pct"))
    cap_setor = conv(valores.get("sector_limit_pct"))
    limites = {}
    for classe, chave in CLASSE_DA_POLITICA.items():
        teto = conv((valores.get("asset_class_limits") or {}).get(chave))
        if teto is not None:
            limites[classe] = teto
    partes = []
    if cap_ativo is not None:
        partes.append(f"ativo {valores['single_asset_limit_pct']:g}%")
    if cap_setor is not None:
        partes.append(f"setor {valores['sector_limit_pct']:g}%")
    if limites:
        partes.append("classes " + ", ".join(
            f"{ROTULO_MOTOR[c]} {(valores['asset_class_limits'][CLASSE_DA_POLITICA[c]]):g}%"
            for c in sorted(limites)))
    base = ("convertidos para a parcela de risco" if escala != 1.0
            else "aplicados à parcela de risco sem conversão (renda fixa sem alvo)")
    fonte = (f"política de investimento ({'; '.join(partes)}), {base}"
             if partes else Restricoes().fonte)
    return Restricoes(
        cap_ativo=cap_ativo if cap_ativo is not None else CAP_ATIVO_PADRAO,
        cap_setor=cap_setor if cap_setor is not None else CAP_SETOR_PADRAO,
        limites_classe=limites, fonte=fonte)


# ---------------------------------------------------------------------------
# Covariância e views
# ---------------------------------------------------------------------------

def covariancia_lw(ret: pd.DataFrame, max_meses: int = MAX_MESES_PADRAO
                   ) -> tuple[pd.DataFrame, float, int]:
    """(Σ anual Ledoit-Wolf, intensidade α do encolhimento, meses usados).

    Só meses sem buraco: `retornos_mensais` já publica a janela comum, e o
    `dropna` aqui é a garantia de que Σ é PSD mesmo se alguém chamar com
    outro quadro.
    """
    limpo = ret.dropna(how="any").sort_index().tail(max_meses)
    if limpo.shape[0] < 2 or limpo.shape[1] < 2:
        return pd.DataFrame(), 1.0, int(limpo.shape[0])
    sigma, alpha = ledoit_wolf_shrinkage(limpo.to_numpy(dtype=float), target="diagonal")
    anual = pd.DataFrame(sigma * MESES_POR_ANO, index=limpo.columns, columns=limpo.columns)
    return anual, float(alpha), int(limpo.shape[0])


def z_por_classe(scores: Mapping[str, float], classes: Mapping[str, str]
                 ) -> dict[str, float]:
    """Posto do score dentro da classe levado à normal: z = Φ⁻¹((posto − ½)/n).

    Postos, não z-score cru: os três motores têm escalas próprias (score B3,
    entry_score EUA, score FII) e caudas diferentes; o posto põe todos na
    mesma régua e um outlier não vira uma view de 10 desvios. Classe com um
    ativo só não tem com quem comparar: z = 0.
    """
    nd = NormalDist()
    saida: dict[str, float] = {}
    por_classe: dict[str, list[str]] = {}
    for s in scores:
        por_classe.setdefault(classes.get(s, ""), []).append(s)
    for membros in por_classe.values():
        n = len(membros)
        if n < 2:
            saida.update({s: 0.0 for s in membros})
            continue
        postos = pd.Series({s: scores[s] for s in membros}).rank(method="average")
        for s in membros:
            saida[s] = float(nd.inv_cdf((float(postos[s]) - 0.5) / n))
    return saida


@dataclass(frozen=True)
class ViewAtivo:
    symbol: str
    classe: str
    score: float
    z: float
    sigma_anual: float
    alpha: float      # α = IC·σ·z, a.a.
    pi: float         # prior, excesso a.a.
    q: float          # view = π + α
    confianca: float


def montar_views(simbolos: list[str], pi: np.ndarray, sigma: pd.DataFrame,
                 scores: Mapping[str, float], classes: Mapping[str, str],
                 motores: Mapping[str, ConfiancaMotor]) -> list[ViewAtivo]:
    idx = {s: i for i, s in enumerate(simbolos)}
    cobertos = {s: v for s, v in scores.items() if s in idx and classes.get(s) in motores}
    z = z_por_classe(cobertos, classes)
    views = []
    for s in sorted(cobertos):
        motor = motores[classes[s]]
        ic = motor.ic if motor.ic is not None else IC_SEM_MEDICAO
        vol = math.sqrt(max(float(sigma.loc[s, s]), 0.0))
        alpha = ic * vol * z[s]
        p = float(pi[idx[s]])
        views.append(ViewAtivo(symbol=s, classe=classes[s], score=float(cobertos[s]),
                               z=z[s], sigma_anual=vol, alpha=alpha, pi=p,
                               q=p + alpha, confianca=motor.confianca))
    return views


# ---------------------------------------------------------------------------
# Otimização
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Problema:
    """Restrições em u (pesos dos ativos cobertos, soma 1)."""

    lb: np.ndarray
    ub: np.ndarray
    eq: tuple[tuple[np.ndarray, float], ...]      # a·u = b
    ineq: tuple[tuple[np.ndarray, float], ...]    # a·u <= b
    degrau: str

    def viavel(self, u: np.ndarray, tol: float = _TOL) -> bool:
        if abs(u.sum() - 1.0) > tol or (u < self.lb - tol).any() or (u > self.ub + tol).any():
            return False
        if any(abs(a @ u - b) > tol for a, b in self.eq):
            return False
        return all(a @ u <= b + tol for a, b in self.ineq)


def _resolver(problema: _Problema, objetivo, gradiente, u0: np.ndarray) -> np.ndarray | None:
    from scipy.optimize import minimize

    # Igualdades linearmente dependentes travam o SLSQP ("Singular matrix C in
    # LSQ subproblem"): com as três classes fixadas na meta, soma(u) = 1 já é a
    # soma das três igualdades de classe. Medido no teste da fronteira: com a
    # linha redundante, toda varredura de retorno-alvo falhava. Fica só o
    # subconjunto de posto cheio, na ordem (soma 1 primeiro).
    linhas: list[tuple[np.ndarray, float]] = []
    for a, b in ((np.ones(len(u0)), 1.0), *problema.eq):
        a = np.asarray(a, dtype=float)
        candidata = np.vstack([x for x, _ in linhas] + [a])
        if np.linalg.matrix_rank(candidata, tol=1e-9) == len(linhas) + 1:
            linhas.append((a, float(b)))
    # Viabilidade primeiro, por programação linear: o SLSQP num degrau
    # inviável roda até o maxiter antes de desistir. Medido na carteira real
    # de 04/10/2026 (30 ativos, teto de setor incompatível com a meta): 3,4 s
    # nos dois degraus, quase tudo no primeiro, que não tinha solução. O
    # HiGHS responde "inviável" em milissegundos.
    from scipy.optimize import linprog

    lp = linprog(np.zeros(len(u0)),
                 A_ub=(np.vstack([a for a, _ in problema.ineq]) if problema.ineq else None),
                 b_ub=(np.array([b for _, b in problema.ineq]) if problema.ineq else None),
                 A_eq=np.vstack([a for a, _ in linhas]), b_eq=np.array([b for _, b in linhas]),
                 bounds=list(zip(problema.lb, problema.ub)), method="highs")
    if lp.status == 2:      # 2 = inviável
        return None
    cons = []
    for a, b in linhas:
        cons.append({"type": "eq", "fun": (lambda u, a=a, b=b: a @ u - b),
                     "jac": (lambda u, a=a: a)})
    for a, b in problema.ineq:
        cons.append({"type": "ineq", "fun": (lambda u, a=a, b=b: b - a @ u),
                     "jac": (lambda u, a=a: -a)})
    inicio = np.clip(u0, problema.lb, problema.ub)
    if inicio.sum() > 0:
        inicio = inicio / inicio.sum()
    res = minimize(objetivo, inicio, jac=gradiente, method="SLSQP",
                   bounds=list(zip(problema.lb, problema.ub)), constraints=cons,
                   options={"ftol": 1e-12, "maxiter": 1000})
    u = np.clip(np.asarray(res.x, dtype=float), 0.0, None)
    if u.sum() <= 0:
        return None
    u = u / u.sum()
    return u if problema.viavel(u, tol=1e-5) else None


def _problemas(simbolos: list[str], classes: Mapping[str, str], setores: Mapping[str, str],
               s: float, meta_classe_coberta: Mapping[str, float],
               fixado_classe: Mapping[str, float], fixado_setor: Mapping[str, float],
               r: Restricoes) -> list[_Problema]:
    """Os degraus de relaxamento, do mais fiel à política ao mínimo aceitável.

    Tudo em u (soma 1 sobre os cobertos): um teto global T vira (T − fixado)/s.
    """
    n = len(simbolos)
    lb = np.zeros(n)
    ub = np.full(n, min(r.cap_ativo / s, 1.0))

    def mascara(pred) -> np.ndarray:
        return np.array([1.0 if pred(x) else 0.0 for x in simbolos])

    teto_classe, igual_classe, conflito = [], [], False
    for c in sorted(set(classes[x] for x in simbolos)):
        a = mascara(lambda x, c=c: classes[x] == c)
        meta_c = meta_classe_coberta.get(c, 0.0) / s
        limite = r.limites_classe.get(c)
        if limite is not None:
            hi = (limite - fixado_classe.get(c, 0.0)) / s
            teto_classe.append((a, max(hi, 0.0)))
            if meta_c > hi + _TOL:
                conflito = True
        igual_classe.append((a, meta_c))

    teto_setor = []
    if r.cap_setor is not None:
        for setor in sorted(set(setores.get(x, "") for x in simbolos)):
            if str(setor).strip().lower() in _SETORES_SEM_TETO:
                continue
            a = mascara(lambda x, setor=setor: setores.get(x, "") == setor)
            teto_setor.append((a, max((r.cap_setor - fixado_setor.get(setor, 0.0)) / s, 0.0)))

    degraus = []
    if not conflito:
        degraus.append(_Problema(lb, ub, tuple(igual_classe), tuple(teto_setor),
                                 "completo"))
        degraus.append(_Problema(lb, ub, tuple(igual_classe), (), "sem_teto_setor"))
    degraus.append(_Problema(lb, ub, (), tuple(teto_classe) + tuple(teto_setor),
                             "classes_livres"))
    degraus.append(_Problema(lb, ub, (), tuple(teto_classe), "classes_livres_sem_teto_setor"))
    return degraus


_TEXTO_DEGRAU = {
    "sem_teto_setor": "o teto por setor era incompatível com a meta das classes e "
                      "ficou de fora",
    "classes_livres": "a meta entre classes era incompatível com os tetos (por ativo "
                      "ou da política) e ficou livre dentro dos tetos de classe",
    "classes_livres_sem_teto_setor": "a meta entre classes e o teto por setor eram "
                                     "incompatíveis com os demais tetos e ficaram de fora",
}


def otimizar(mu: np.ndarray, sigma: np.ndarray, delta: float, problema: _Problema,
             u_meta: np.ndarray) -> np.ndarray | None:
    """argmax u'μ − (δ/2)·u'Σu sob as restrições do degrau."""
    def f(u):
        return -(u @ mu - 0.5 * delta * (u @ sigma @ u))

    def g(u):
        return -(mu - delta * (sigma @ u))

    return _resolver(problema, f, g, u_meta)


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultadoBL:
    disponivel: bool
    motivo: str = ""
    simbolos: tuple[str, ...] = ()          # cobertos (linhas de Σ)
    classes: dict[str, str] = field(default_factory=dict)
    setores: dict[str, str] = field(default_factory=dict)
    pesos_meta: dict[str, float] = field(default_factory=dict)   # todos, soma 1
    pesos_bl: dict[str, float] = field(default_factory=dict)     # todos, soma 1
    pi: dict[str, float] = field(default_factory=dict)           # excesso a.a.
    mu_bl: dict[str, float] = field(default_factory=dict)        # excesso a.a.
    sigma: pd.DataFrame = field(default_factory=pd.DataFrame)    # anual
    alpha_lw: float = float("nan")
    meses: int = 0
    periodo: str = ""
    delta: float = DELTA_PADRAO
    tau: float = TAU_PADRAO
    motores: dict[str, ConfiancaMotor] = field(default_factory=dict)
    views: tuple[ViewAtivo, ...] = ()
    fixados: tuple[str, ...] = ()
    restricoes: Restricoes = field(default_factory=Restricoes)
    degrau: str = ""
    avisos: tuple[str, ...] = ()

    @property
    def peso_coberto(self) -> float:
        return float(sum(self.pesos_meta.get(s, 0.0) for s in self.simbolos))

    def vetor(self, pesos: Mapping[str, float]) -> np.ndarray:
        """Pesos em u (cobertos, soma 1) -- a base de Σ e de μ."""
        w = np.array([float(pesos.get(s, 0.0)) for s in self.simbolos])
        return w / w.sum() if w.sum() > 0 else w

    def risco_retorno(self, pesos: Mapping[str, float]) -> tuple[float, float] | None:
        """(volatilidade a.a., retorno esperado BL em excesso a.a.) da parte coberta."""
        if not self.disponivel:
            return None
        u = self.vetor(pesos)
        if u.sum() <= 0:
            return None
        sig = self.sigma.loc[list(self.simbolos), list(self.simbolos)].to_numpy()
        mu = np.array([self.mu_bl[s] for s in self.simbolos])
        return float(math.sqrt(max(u @ sig @ u, 0.0))), float(u @ mu)

    def pesos_por_classe(self, pesos: Mapping[str, float]) -> dict[str, float]:
        saida: dict[str, float] = {}
        for s, w in pesos.items():
            c = self.classes.get(s, "")
            saida[c] = saida.get(c, 0.0) + float(w)
        return saida

    def para_llm(self) -> str:
        if not self.disponivel:
            return f"Alocação Black-Litterman indisponível nesta consulta: {self.motivo}"
        linhas = [
            "Alocação Black-Litterman (TEÓRICA, não é ordem): prior de equilíbrio "
            f"π = δΣw com w = meta, δ = {self.delta:g}, τ = {self.tau:g}; Σ Ledoit-Wolf "
            f"(α = {self.alpha_lw:.2f}) sobre {self.meses} meses ({self.periodo}).",
            "Confiança por motor (fração do caminho até a view): " + "; ".join(
                f"{ROTULO_MOTOR[m]} c = {c.confianca:.3f} ({c.fonte})"
                for m, c in sorted(self.motores.items())),
            f"Restrições: {self.restricoes.fonte}.",
            "Pesos (% da parcela de risco) meta -> BL:",
        ]
        mudancas = sorted(self.pesos_meta, key=lambda s: -abs(
            self.pesos_bl.get(s, 0.0) - self.pesos_meta.get(s, 0.0)))
        for s in mudancas[:15]:
            linhas.append(f"  {s}: {self.pesos_meta.get(s, 0.0):.2%} -> "
                          f"{self.pesos_bl.get(s, 0.0):.2%}")
        linhas.extend(f"  aviso: {a}" for a in self.avisos)
        return "\n".join(linhas)


def indisponivel(motivo: str, **extra) -> ResultadoBL:
    return ResultadoBL(disponivel=False, motivo=motivo, **extra)


def black_litterman_global(
    df_posicoes: pd.DataFrame,
    ret: pd.DataFrame,
    *,
    motores: Mapping[str, ConfiancaMotor] | None = None,
    restricoes: Restricoes | None = None,
    scores: Mapping[str, float] | None = None,
    delta: float = DELTA_PADRAO,
    tau: float = TAU_PADRAO,
    max_meses: int = MAX_MESES_PADRAO,
) -> ResultadoBL:
    """Pesos Black-Litterman do Portfólio Global, na base da parcela de risco.

    `df_posicoes` é o quadro de `aggregate.montar_posicoes` (a meta está em
    `weight_global`); `ret`, o de `returns.retornos_mensais`. `scores` só
    existe para o teste; produção lê o score bruto de cada motor do payload do
    snapshot (`signals._scores_qualidade_brutos`).
    """
    restricoes = restricoes or Restricoes()
    if df_posicoes is None or df_posicoes.empty:
        return indisponivel("sem posições na carteira-modelo")
    meta_bruta = (df_posicoes.groupby("symbol")["weight_global"].sum()
                  .astype(float).clip(lower=0.0))
    if meta_bruta.sum() <= 0:
        return indisponivel("a meta não tem peso positivo")
    meta = (meta_bruta / meta_bruta.sum()).to_dict()
    linhas = df_posicoes.drop_duplicates("symbol").set_index("symbol")
    classes = {s: str(linhas.loc[s, "asset_class"] or "").strip().lower() for s in meta}
    setores = {s: str(linhas.loc[s].get("sector") or "") for s in meta}

    if ret is None or not isinstance(ret, pd.DataFrame) or ret.empty:
        return indisponivel("sem série mensal para estimar a covariância",
                            pesos_meta=meta, classes=classes)
    colunas = [c for c in ret.columns if c in meta and meta[c] > 0]
    sigma_df, alpha_lw, meses = covariancia_lw(ret[colunas], max_meses=max_meses)
    if sigma_df.empty:
        return indisponivel("menos de dois ativos com série mensal na janela comum",
                            pesos_meta=meta, classes=classes)
    simbolos = list(sigma_df.columns)
    janela = ret[simbolos].dropna(how="any").sort_index().tail(max_meses)
    periodo = (f"{janela.index[0]:%m/%Y} a {janela.index[-1]:%m/%Y}"
               if len(janela) else "")

    s = float(sum(meta[x] for x in simbolos))
    fixados = tuple(sorted(x for x in meta if x not in simbolos and meta[x] > 0))
    u_meta = np.array([meta[x] for x in simbolos]) / s
    sigma = sigma_df.to_numpy()
    pi = delta * sigma @ u_meta

    if motores is None:
        motores = confianca_dos_motores()
    if scores is None:
        from core.global_portfolio.signals import _scores_qualidade_brutos
        scores, _cls = _scores_qualidade_brutos(df_posicoes)
    views = montar_views(simbolos, pi, sigma_df, scores, classes, motores)
    bl_views = [BLView("absolute", [v.symbol], [1.0], v.q, v.confianca) for v in views]
    mu = posterior_returns(pi, sigma, bl_views, simbolos, tau=tau)

    meta_classe_coberta: dict[str, float] = {}
    for x in simbolos:
        meta_classe_coberta[classes[x]] = meta_classe_coberta.get(classes[x], 0.0) + meta[x]
    fixado_classe: dict[str, float] = {}
    fixado_setor: dict[str, float] = {}
    for x in fixados:
        fixado_classe[classes[x]] = fixado_classe.get(classes[x], 0.0) + meta[x]
        fixado_setor[setores[x]] = fixado_setor.get(setores[x], 0.0) + meta[x]

    avisos: list[str] = []
    acima = [x for x in meta if meta[x] > restricoes.cap_ativo + _TOL]
    if acima:
        avisos.append(f"A meta já passa do teto por ativo ({restricoes.cap_ativo:.1%} da "
                      f"parcela de risco) em {len(acima)} ativo(s): {', '.join(sorted(acima))}.")
    if fixados:
        avisos.append(f"{len(fixados)} ativo(s) sem série mensal ficaram fixados no peso "
                      f"da meta ({1 - s:.1%} da parcela de risco): {', '.join(fixados)}.")

    u_bl, degrau = None, ""
    for problema in _problemas(simbolos, classes, setores, s, meta_classe_coberta,
                               fixado_classe, fixado_setor, restricoes):
        # Sem view que mova μ e com a meta dentro das restrições, a meta é o
        # ótimo GLOBAL (π foi construído para isso) e portanto o restrito:
        # devolvê-la exata evita que a tolerância do SLSQP invente um desvio
        # de 1e-7 que a tela imprimiria como recomendação.
        if np.allclose(mu, pi, rtol=0, atol=1e-12) and problema.viavel(u_meta):
            u_bl, degrau = u_meta.copy(), problema.degrau
            break
        u_bl = otimizar(mu, sigma, delta, problema, u_meta)
        if u_bl is not None:
            degrau = problema.degrau
            break
    if u_bl is None:
        return indisponivel(
            "nenhuma carteira cabe nos tetos: "
            f"{len(simbolos)} ativos com série × teto de {restricoes.cap_ativo:.1%} "
            f"por ativo não cobrem {s:.1%} da parcela de risco",
            pesos_meta=meta, classes=classes)
    if degrau in _TEXTO_DEGRAU:
        avisos.append(f"Restrição relaxada: {_TEXTO_DEGRAU[degrau]}.")

    pesos_bl = {x: float(meta[x]) for x in fixados}
    pesos_bl.update({x: float(u_bl[i] * s) for i, x in enumerate(simbolos)})
    for x in meta:
        pesos_bl.setdefault(x, 0.0)
    return ResultadoBL(
        disponivel=True, simbolos=tuple(simbolos), classes=classes, setores=setores,
        pesos_meta=meta, pesos_bl=pesos_bl,
        pi=dict(zip(simbolos, map(float, pi))), mu_bl=dict(zip(simbolos, map(float, mu))),
        sigma=sigma_df, alpha_lw=alpha_lw, meses=meses, periodo=periodo,
        delta=delta, tau=tau, motores=dict(motores), views=tuple(views),
        fixados=fixados, restricoes=restricoes, degrau=degrau, avisos=tuple(avisos))


# ---------------------------------------------------------------------------
# Fronteira eficiente
# ---------------------------------------------------------------------------

def fronteira_eficiente(resultado: ResultadoBL, n_pontos: int = 20
                        ) -> list[tuple[float, float]]:
    """Pontos (volatilidade a.a., retorno BL em excesso a.a.) da fronteira.

    Mesmas restrições do degrau que produziu os pesos BL: a fronteira é a das
    carteiras que a própria alocação BL podia escolher, então o ponto BL cai
    sobre ela e a meta, abaixo ou sobre ela. Teórica: μ é o retorno ESPERADO
    pelo modelo, não um retorno observado.
    """
    if not resultado.disponivel:
        return []
    simbolos = list(resultado.simbolos)
    sigma = resultado.sigma.loc[simbolos, simbolos].to_numpy()
    mu = np.array([resultado.mu_bl[x] for x in simbolos])
    meta, classes, setores = resultado.pesos_meta, resultado.classes, resultado.setores
    meta_classe: dict[str, float] = {}
    fix_classe: dict[str, float] = {}
    fix_setor: dict[str, float] = {}
    for x in simbolos:
        meta_classe[classes[x]] = meta_classe.get(classes[x], 0.0) + meta[x]
    for x in resultado.fixados:
        fix_classe[classes[x]] = fix_classe.get(classes[x], 0.0) + meta[x]
        fix_setor[setores.get(x, "")] = fix_setor.get(setores.get(x, ""), 0.0) + meta[x]
    problemas = {p.degrau: p for p in _problemas(
        simbolos, classes, setores, resultado.peso_coberto, meta_classe,
        fix_classe, fix_setor, resultado.restricoes)}
    problema = problemas.get(resultado.degrau)
    if problema is None:
        return []
    u0 = resultado.vetor(resultado.pesos_bl)

    u_min = _resolver(problema, lambda u: u @ sigma @ u, lambda u: 2 * sigma @ u, u0)
    u_max = _resolver(problema, lambda u: -(u @ mu), lambda u: -mu, u0)
    if u_min is None or u_max is None:
        return []
    r_min, r_max = float(u_min @ mu), float(u_max @ mu)
    pontos = []
    for alvo in np.linspace(r_min, r_max, max(n_pontos, 2)):
        p = _Problema(problema.lb, problema.ub,
                      problema.eq + ((mu, float(alvo)),), problema.ineq, problema.degrau)
        u = _resolver(p, lambda u: u @ sigma @ u, lambda u: 2 * sigma @ u, u0)
        if u is not None:
            pontos.append((float(math.sqrt(max(u @ sigma @ u, 0.0))), float(u @ mu)))
    return pontos


def pesos_reais_cobertos(resultado: ResultadoBL, valores_reais: Mapping[str, float]
                         ) -> tuple[dict[str, float], float]:
    """(pesos reais sobre os ativos com série, fração do valor real coberta).

    A carteira real tem ativos fora do modelo e sem série; o ponto dela na
    fronteira só vale para a parte que tem linha em Σ, e a fração coberta vai
    junto para a tela dizer quanto da carteira o ponto representa.
    """
    total = float(sum(v for v in valores_reais.values() if v > 0))
    if total <= 0 or not resultado.disponivel:
        return {}, 0.0
    cobertos = {x: float(valores_reais.get(x, 0.0)) for x in resultado.simbolos
                if valores_reais.get(x, 0.0) > 0}
    soma = sum(cobertos.values())
    if soma <= 0:
        return {}, 0.0
    return {x: v / soma for x, v in cobertos.items()}, soma / total
