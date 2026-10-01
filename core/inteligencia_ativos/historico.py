"""
core/inteligencia_ativos/historico.py
Histórico e auditoria das análises dos ativos.

Um ``Snapshot`` é a foto compacta de uma análise: o que ela concluiu (peso,
tese, ação, riscos, métricas de valuation e de fundamentos) e a ``Auditoria``
de como chegou lá (quando, com que dados, qual cenário, qual estratégia,
quais fontes e, se houve leitura por LLM, qual modelo).

Salvar "quando fizer sentido" (``motivo_para_salvar``): a primeira análise
de um ativo, uma mudança material (tese, ação, peso ≥ 1 pp, sinais de risco,
estratégia ou cenário), uma leitura por LLM, ou a última foto com mais de
30 dias. Oscilação diária de preço não gera foto.

``comparar`` escreve as frases "Na análise anterior..." / "Desde a última
análise...". São comparações de números e estados, sem juízo de valor: uma
métrica que caiu é descrita como caiu, nunca como "ficou barata".

Puro: o armazenamento está em ``historico_repo.py``.

Coberto por tests/test_inteligencia_ativos_historico.py.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

from core.inteligencia_ativos import fundamentos as fund
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import painel
from core.inteligencia_ativos import valuation as val

ESQUEMA = "historico_analises.v1"
CHAVE_PREFERENCIA = "asset_analysis_history"
CARTEIRA = "_carteira"          # chave da foto da carteira no histórico
MAX_POR_CHAVE = 8               # fotos guardadas por ativo (e da carteira)
LIMIAR_PESO_PP = 1.0
IDADE_MAXIMA_DIAS = 30
MAX_METRICAS = 6
# Uma variação menor que isto (relativa) é tratada como estável.
TOLERANCIA_RELATIVA = 0.02

PRIMEIRA = "primeira análise do ativo"
LEITURA_LLM = "leitura por LLM gerada"
ANTIGA = f"última foto com mais de {IDADE_MAXIMA_DIAS} dias"
MANUAL = "salva manualmente"

# Chaves de data que dizem "até quando o dado vai"; datas de eventos futuros
# (``data``) ficam de fora de propósito.
_CHAVES_DATA = ("referencia", "reference_date", "base_ate", "retrieved_at",
                "fim")


@dataclass(frozen=True)
class Auditoria:
    analysis_timestamp: str
    investment_policy_version: int
    scenario_version: int | None = None
    data_timestamp: str | None = None
    model_used: str | None = None
    sources_used: tuple[str, ...] = ()

    def como_dict(self) -> dict:
        return {"analysis_timestamp": self.analysis_timestamp,
                "model_used": self.model_used,
                "data_timestamp": self.data_timestamp,
                "scenario_version": self.scenario_version,
                "investment_policy_version": self.investment_policy_version,
                "sources_used": list(self.sources_used)}

    @classmethod
    def de_dict(cls, d: dict | None) -> Auditoria:
        d = d or {}
        return cls(analysis_timestamp=str(d.get("analysis_timestamp") or ""),
                   investment_policy_version=int(
                       d.get("investment_policy_version") or 0),
                   scenario_version=d.get("scenario_version"),
                   data_timestamp=d.get("data_timestamp"),
                   model_used=d.get("model_used"),
                   sources_used=tuple(d.get("sources_used") or ()))


@dataclass(frozen=True)
class Metrica:
    rotulo: str
    valor: float
    unidade: str


@dataclass(frozen=True)
class Snapshot:
    ticker: str
    auditoria: Auditoria
    motivo: str = ""
    peso: float | None = None
    valor: float | None = None
    moeda: str = "BRL"
    tese: str = painel.TESE_SEM_VEREDITO
    acao: str | None = None
    papel: str | None = None
    riscos: tuple[str, ...] = ()
    valuation: dict[str, Metrica] = field(default_factory=dict)
    fundamentos: dict[str, Metrica] = field(default_factory=dict)
    # só na foto da carteira
    rentabilidade_pct: float | None = None
    alocacao: dict[str, float] = field(default_factory=dict)
    n_alertas: int | None = None

    @property
    def data(self) -> dt.date | None:
        try:
            return dt.datetime.fromisoformat(
                self.auditoria.analysis_timestamp).date()
        except ValueError:
            return None

    def como_dict(self) -> dict:
        def _ms(d):
            return {k: [x.rotulo, x.valor, x.unidade] for k, x in d.items()}
        saida = {"ticker": self.ticker, "audit": self.auditoria.como_dict(),
                 "reason": self.motivo, "weight": self.peso,
                 "value": self.valor, "currency": self.moeda,
                 "thesis": self.tese, "action": self.acao, "role": self.papel,
                 "risks": list(self.riscos), "valuation": _ms(self.valuation),
                 "fundamentals": _ms(self.fundamentos)}
        if self.ticker == CARTEIRA:
            saida.update(return_pct=self.rentabilidade_pct,
                         allocation=dict(self.alocacao),
                         n_alerts=self.n_alertas)
        return saida

    @classmethod
    def de_dict(cls, d: dict) -> Snapshot:
        def _ms(x) -> dict[str, Metrica]:
            saida = {}
            for k, v in (x or {}).items():
                try:
                    saida[k] = Metrica(str(v[0]), float(v[1]), str(v[2]))
                except (TypeError, ValueError, IndexError):
                    continue
            return saida
        return cls(ticker=str(d.get("ticker") or ""),
                   auditoria=Auditoria.de_dict(d.get("audit")),
                   motivo=str(d.get("reason") or ""),
                   peso=d.get("weight"), valor=d.get("value"),
                   moeda=d.get("currency") or "BRL",
                   tese=d.get("thesis") or painel.TESE_SEM_VEREDITO,
                   acao=d.get("action"), papel=d.get("role"),
                   riscos=tuple(d.get("risks") or ()),
                   valuation=_ms(d.get("valuation")),
                   fundamentos=_ms(d.get("fundamentals")),
                   rentabilidade_pct=d.get("return_pct"),
                   alocacao=dict(d.get("allocation") or {}),
                   n_alertas=d.get("n_alerts"))


# -- auditoria -------------------------------------------------------------------

def _datas(obj, saida: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _CHAVES_DATA and isinstance(v, str) and len(v) >= 4:
                saida.append(v)
            else:
                _datas(v, saida)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _datas(v, saida)


def data_dos_dados(analise: m.AnaliseAtivo, hoje: dt.date) -> str | None:
    """A data mais recente dos dados usados, nunca no futuro. Referências só
    com ano ("2025") contam como o próprio ano."""
    datas = []
    for s in analise.secoes_externas:
        _datas(s.dados, datas)
    validas = [d[:10] for d in datas
               if d[:4].isdigit() and d[:10] <= hoje.isoformat()]
    return max(validas) if validas else None


def fontes(analise: m.AnaliseAtivo) -> tuple[str, ...]:
    saida = [s.fonte for s in analise.secoes_externas
             if s.fonte and s.estado == m.DISPONIVEL]
    if analise.fundamentos.dados:
        saida.extend(fund.Fundamentos.de_dict(analise.fundamentos.dados).fontes)
    # uma seção pode citar várias fontes num texto só ("A, B"): separa para
    # a mesma fonte não aparecer duas vezes, sozinha e dentro da composta
    return tuple(sorted({p.strip() for f in saida if f
                         for p in str(f).split(", ") if p.strip()}))


def auditoria(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor, *,
              agora: dt.datetime, modelo: str | None = None) -> Auditoria:
    return Auditoria(
        analysis_timestamp=agora.isoformat(timespec="seconds"),
        investment_policy_version=analise.versao_politica,
        scenario_version=(ctx.cenario.versao
                          if ctx.cenario is not None and not ctx.cenario.vazio
                          else None),
        data_timestamp=data_dos_dados(analise, agora.date()),
        model_used=modelo,
        sources_used=fontes(analise))


# -- captura ---------------------------------------------------------------------

def _metricas_valuation(a: m.AnaliseAtivo) -> dict[str, Metrica]:
    if not a.valuation.dados:
        return {}
    v = val.Valuation.de_dict(a.valuation.dados)
    return {ln.chave: Metrica(ln.rotulo, float(ln.atual), ln.unidade)
            for ln in v.com_dado[:MAX_METRICAS]}


def _metricas_fundamentos(a: m.AnaliseAtivo) -> dict[str, Metrica]:
    if not a.fundamentos.dados:
        return {}
    f = fund.Fundamentos.de_dict(a.fundamentos.dados)
    saida = {}
    for i in f.disponiveis:
        try:
            saida[i.chave] = Metrica(i.rotulo, float(i.valor), i.unidade)
        except (TypeError, ValueError):
            continue
        if len(saida) >= MAX_METRICAS:
            break
    return saida


def capturar(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor, *,
             agora: dt.datetime, modelo: str | None = None) -> Snapshot:
    i = analise.ativo
    return Snapshot(
        ticker=i.ticker,
        auditoria=auditoria(analise, ctx, agora=agora, modelo=modelo),
        peso=round(i.peso_atual, 2),
        valor=None if i.valor_mercado is None else round(i.valor_mercado, 2),
        moeda=i.moeda,
        tese=painel.status_tese(analise.tese),
        acao=analise.acao.estado,
        papel=analise.papel_principal.codigo if analise.papel_principal else None,
        riscos=painel.riscos(analise, ctx),
        valuation=_metricas_valuation(analise),
        fundamentos=_metricas_fundamentos(analise))


def capturar_carteira(resumo: painel.ResumoCarteira, ctx: m.ContextoInvestidor,
                      *, agora: dt.datetime) -> Snapshot:
    return Snapshot(
        ticker=CARTEIRA,
        auditoria=Auditoria(
            analysis_timestamp=agora.isoformat(timespec="seconds"),
            investment_policy_version=ctx.versao_politica,
            scenario_version=(ctx.cenario.versao if ctx.cenario is not None
                              and not ctx.cenario.vazio else None)),
        valor=round(resumo.patrimonio, 2),
        rentabilidade_pct=resumo.rentabilidade_pct,
        alocacao={ln.rotulo: round(ln.atual, 2) for ln in resumo.alocacao},
        n_alertas=len(resumo.alertas))


# -- quando salvar ------------------------------------------------------------------

def motivo_para_salvar(novo: Snapshot, anterior: Snapshot | None) -> str | None:
    """Por que esta foto merece ficar, ou None se ela repete a anterior."""
    if anterior is None:
        return PRIMEIRA if novo.ticker != CARTEIRA else "primeira foto da carteira"
    if novo.auditoria.model_used:
        return LEITURA_LLM
    a, n = anterior, novo
    if n.auditoria.investment_policy_version != a.auditoria.investment_policy_version:
        return "a estratégia mudou"
    if n.auditoria.scenario_version != a.auditoria.scenario_version:
        return "o cenário mudou"
    if n.tese != a.tese:
        return "o status da tese mudou"
    if n.acao != a.acao:
        return "a ação a considerar mudou"
    if len(n.riscos) != len(a.riscos):
        return "os sinais de risco mudaram"
    if (n.peso is not None and a.peso is not None
            and abs(n.peso - a.peso) >= LIMIAR_PESO_PP):
        return f"o peso variou {LIMIAR_PESO_PP:.0f} pp ou mais"
    if n.ticker == CARTEIRA:
        for k, v in n.alocacao.items():
            if abs(v - a.alocacao.get(k, 0.0)) >= LIMIAR_PESO_PP:
                return "a alocação por classe mudou"
        if n.n_alertas != a.n_alertas:
            return "os alertas da carteira mudaram"
    dn, da = n.data, a.data
    if dn and da and (dn - da).days > IDADE_MAXIMA_DIAS:
        return ANTIGA
    return None


# -- histórico em extra_settings ---------------------------------------------------------

def ler(extra: dict | None) -> dict[str, list[Snapshot]]:
    bruto = (extra or {}).get(CHAVE_PREFERENCIA) or {}
    if not isinstance(bruto, dict) or bruto.get("schema") != ESQUEMA:
        return {}
    saida = {}
    for chave, lista in (bruto.get("items") or {}).items():
        if isinstance(lista, list):
            saida[chave] = [Snapshot.de_dict(x) for x in lista
                            if isinstance(x, dict)]
    return saida


def anexar(historico: dict[str, list[Snapshot]], fotos, *,
           forcar: str | None = None) -> tuple[dict[str, list[Snapshot]], list[str]]:
    """(histórico novo, chaves gravadas). Mais antigas primeiro; guarda as
    ``MAX_POR_CHAVE`` últimas de cada chave. ``forcar`` grava mesmo sem
    mudança material, com esse motivo."""
    novo = {k: list(v) for k, v in historico.items()}
    gravadas = []
    for f in fotos:
        lista = novo.setdefault(f.ticker, [])
        motivo = forcar or motivo_para_salvar(f, lista[-1] if lista else None)
        if motivo is None:
            continue
        lista.append(replace(f, motivo=motivo))
        del lista[:-MAX_POR_CHAVE]
        gravadas.append(f.ticker)
    return novo, gravadas


def gravar_em(extra: dict | None, historico: dict[str, list[Snapshot]]) -> dict:
    novo = dict(extra or {})
    novo[CHAVE_PREFERENCIA] = {
        "schema": ESQUEMA,
        "items": {k: [s.como_dict() for s in v] for k, v in historico.items()
                  if v}}
    return novo


# -- comparação ------------------------------------------------------------------------

def _br(x: float, casas: int = 1) -> str:
    return f"{x:.{casas}f}".replace(".", ",")


def _brl(x: float) -> str:
    """R$ 391.815,14: milhar com ponto, centavos com vírgula."""
    return "R$ " + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt(x: Metrica) -> str:
    return fund.formatar(x.valor, x.unidade, "BRL")


def _variou(a: float, b: float) -> str | None:
    base = max(abs(a), abs(b), 1e-9)
    if abs(b - a) / base < TOLERANCIA_RELATIVA:
        return None
    return "aumentou" if b > a else "diminuiu"


def _data_br(d: dt.date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "data desconhecida"


_TESE_TXT = {painel.TESE_VALIDA: "válida",
             painel.TESE_EM_RISCO: "com sinal contra",
             painel.TESE_SEM_VEREDITO: "sem veredito"}


def comparar(anterior: Snapshot | None, atual: Snapshot) -> list[str]:
    """Frases de comparação com a foto anterior. Vazia se não há anterior."""
    if anterior is None:
        return []
    a, n = anterior, atual
    quando = f"Na análise anterior ({_data_br(a.data)})"
    frases = []
    if a.peso is not None and n.peso is not None:
        if abs(n.peso - a.peso) >= 0.1:
            frases.append(f"{quando}, o peso era {_br(a.peso)}%; agora é "
                          f"{_br(n.peso)}%.")
        else:
            frases.append(f"Desde a última análise, o peso ficou estável em "
                          f"{_br(n.peso)}%.")
    if n.ticker == CARTEIRA:
        if a.valor and n.valor:
            frases.append(f"{quando}, o patrimônio era "
                          f"{_brl(a.valor)}; agora é {_brl(n.valor)}.")
        for k, v in n.alocacao.items():
            antes = a.alocacao.get(k)
            if antes is not None and abs(v - antes) >= LIMIAR_PESO_PP:
                frases.append(f"{k} passou de {_br(antes)}% para {_br(v)}% "
                              "da carteira.")
        if a.n_alertas is not None and n.n_alertas != a.n_alertas:
            frases.append(f"Os alertas relevantes passaram de {a.n_alertas} "
                          f"para {n.n_alertas}.")
    if a.tese == n.tese:
        if n.ticker != CARTEIRA and n.tese == painel.TESE_VALIDA:
            frases.append("Desde a última análise, a tese permaneceu válida.")
    else:
        frases.append(f"A tese passou de {_TESE_TXT[a.tese]} para "
                      f"{_TESE_TXT[n.tese]}.")
    if a.acao and n.acao and a.acao != n.acao:
        frases.append(f"A ação a considerar mudou de "
                      f"\"{m.ROTULO_ACAO.get(a.acao, a.acao)}\" para "
                      f"\"{m.ROTULO_ACAO.get(n.acao, n.acao)}\".")
    if len(n.riscos) > len(a.riscos):
        frases.append(f"O risco aumentou: os sinais objetivos passaram de "
                      f"{len(a.riscos)} para {len(n.riscos)}.")
    elif len(n.riscos) < len(a.riscos):
        frases.append(f"O risco diminuiu: os sinais objetivos passaram de "
                      f"{len(a.riscos)} para {len(n.riscos)}.")
    for grupo, nome in ((n.fundamentos, "fundamentos"),
                        (n.valuation, "valuation")):
        antes = a.fundamentos if nome == "fundamentos" else a.valuation
        for chave, x in grupo.items():
            y = antes.get(chave)
            if y is None:
                continue
            direcao = _variou(y.valor, x.valor)
            if direcao:
                frases.append(f"{x.rotulo} {direcao} de {_fmt(y)} para "
                              f"{_fmt(x)} ({nome}).")
    va, vn = a.auditoria, n.auditoria
    if va.investment_policy_version != vn.investment_policy_version:
        frases.append(f"A estratégia mudou da versão "
                      f"{va.investment_policy_version} para a "
                      f"{vn.investment_policy_version}.")
    if va.scenario_version != vn.scenario_version:
        frases.append(f"O cenário mudou da versão "
                      f"{va.scenario_version or '—'} para a "
                      f"{vn.scenario_version or '—'}.")
    return frases
