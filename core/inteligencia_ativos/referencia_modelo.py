"""
core/inteligencia_ativos/referencia_modelo.py
A carteira recomendada do Portfólio Global como REFERÊNCIA COMPARATIVA na
Inteligência dos Ativos. Puro, sem I/O: quem lê o banco é ``carregar``.

Por que só referência: o Portfólio Global já usa o veredito da Inteligência
(``core/inteligencia_ativos/veredito.py``) para montar as recomendações. Se a
Inteligência passasse a usar a carteira recomendada para decidir, um ativo
ficaria favorecido por estar no modelo, e estaria no modelo por ter sido
favorecido -- confirmação circular disfarçada de duas fontes. Por isso nada
daqui entra em ``analise``, ``adequacao``, ``fit_por_regras``, ``veredito``
nem na "Ação a considerar". A tela mostra a comparação à parte e a LLM do
Portfolio Fit a recebe em ``app_model_reference`` com ``REGRA_REFERENCIA``,
que proíbe usá-la como argumento.

Pesos, sempre em % e na mesma base dos dois lados:

* **modelo** = alvo global da classe × peso do ativo dentro do modelo da
  classe (``aggregate.montar_posicoes`` renormaliza; ``carteira_real.
  alvos_globais`` põe a fatia de renda fixa);
* **real** = valor de mercado do ativo ÷ valor de mercado da base.

A base é o patrimônio inteiro quando o Portfólio Global tem fatia de renda
fixa definida. Sem ela, os alvos do modelo dividem só a parcela de risco, e a
base real passa a ser a carteira sem renda fixa -- comparar o modelo com o
patrimônio inteiro inventaria um desvio que é só renda fixa.

Coberto por tests/test_inteligencia_ativos_referencia_modelo.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.global_portfolio import carteira_real
from core.global_portfolio.aggregate import classes_sem_posicao, montar_posicoes

log = logging.getLogger(__name__)

BASE_PATRIMONIO = "patrimonio"
BASE_SEM_RENDA_FIXA = "sem_renda_fixa"
ROTULO_BASE = {
    BASE_PATRIMONIO: "patrimônio inteiro",
    BASE_SEM_RENDA_FIXA: "carteira sem renda fixa (o Portfólio Global não "
                         "tem fatia de renda fixa definida)",
}

NOS_DOIS = "nos_dois"
SO_NA_CARTEIRA = "so_na_carteira"
SO_NO_MODELO = "so_no_modelo"
ROTULO_SITUACAO = {
    NOS_DOIS: "está no modelo e na sua carteira",
    SO_NA_CARTEIRA: "está na sua carteira, fora do modelo",
    SO_NO_MODELO: "está no modelo, fora da sua carteira",
}

# Ativos do modelo fora da carteira que vão para a tela e para a LLM.
N_FORA_DA_CARTEIRA = 10

AVISO = ("Referência comparativa: a carteira recomendada do Portfólio Global "
         "não entra na \"Ação a considerar\", no Portfolio Fit por regras nem "
         "no veredito. Ela é montada em parte com o próprio veredito desta "
         "aba, então não é evidência independente.")

REGRA_REFERENCIA = (
    "REFERÊNCIA DO MODELO DO APP: \"app_model_reference\" traz a carteira "
    "recomendada do Portfólio Global (peso do modelo, peso real e diferença, "
    "calculados em código). É só comparação. Ela é montada em parte com o "
    "veredito desta própria análise, então NÃO é evidência independente: não "
    "use estar ou não estar no modelo como argumento a favor ou contra o "
    "ativo, nem para escolher \"action_to_consider\" ou o nível de "
    "portfolio_fit. Pode citá-la em \"portfolio_impact\" como comparação "
    "(ex.: \"o peso real está X pp acima do modelo do app\"), dizendo que é o "
    "modelo do app. Estado indisponível não é sinal de nada.")


def normalizar(ticker) -> str:
    """Ticker comparável entre a carteira e os snapshots: maiúsculo, sem
    espaço e sem o ``.SA`` que algumas fontes da B3 acrescentam."""
    tk = str(ticker or "").strip().upper()
    return tk[:-3] if tk.endswith(".SA") else tk


@dataclass(frozen=True)
class LinhaAtivo:
    ticker: str
    nome: str
    classe_modelo: str | None      # b3 | fii | us; None = fora do modelo
    peso_modelo: float | None      # % da base; None = fora do modelo
    peso_real: float | None        # % da base; None = fora da carteira
    situacao: str

    @property
    def diferenca(self) -> float | None:
        """Real menos modelo, em pp. Ausente de um lado conta como 0%."""
        if self.peso_modelo is None and self.peso_real is None:
            return None
        return (self.peso_real or 0.0) - (self.peso_modelo or 0.0)

    def como_dict(self) -> dict:
        return {"ticker": self.ticker, "nome": self.nome,
                "classe_modelo": self.classe_modelo,
                "peso_modelo_pct": _r(self.peso_modelo),
                "peso_real_pct": _r(self.peso_real),
                "diferenca_pp": _r(self.diferenca),
                "situacao": ROTULO_SITUACAO[self.situacao]}


@dataclass(frozen=True)
class LinhaClasse:
    classe: str
    rotulo: str
    alvo: float | None             # % da base; None = sem alvo no modelo
    real: float                    # % da base

    @property
    def diferenca(self) -> float:
        return self.real - (self.alvo or 0.0)

    def como_dict(self) -> dict:
        return {"classe": self.rotulo, "alvo_modelo_pct": _r(self.alvo),
                "real_pct": _r(self.real), "diferenca_pp": _r(self.diferenca)}


@dataclass(frozen=True)
class ReferenciaModelo:
    disponivel: bool
    motivo: str = ""
    base: str = BASE_PATRIMONIO
    classes: tuple[LinhaClasse, ...] = ()
    ativos: dict[str, LinhaAtivo] = field(default_factory=dict)
    avisos: tuple[str, ...] = ()
    # Indisponível porque a leitura do banco falhou, e não por falta de
    # modelo ou de alocação: a tela não lembra esse resultado.
    falha_de_leitura: bool = False

    def linha(self, ticker) -> LinhaAtivo | None:
        return self.ativos.get(normalizar(ticker))

    def fora_da_carteira(self, n: int = N_FORA_DA_CARTEIRA) -> list[LinhaAtivo]:
        """Ativos do modelo que você não tem, do maior peso para o menor."""
        so = [x for x in self.ativos.values() if x.situacao == SO_NO_MODELO]
        so.sort(key=lambda x: (-(x.peso_modelo or 0.0), x.ticker))
        return so[:n]

    def para_llm(self, ticker) -> dict:
        """O que vai em ``app_model_reference`` no contexto do Portfolio Fit."""
        if not self.disponivel:
            return {"estado": "indisponível", "motivo": self.motivo}
        linha = self.linha(ticker)
        return {
            "estado": "disponível",
            "natureza": "referência comparativa, não evidência independente",
            "base_dos_pesos": ROTULO_BASE[self.base],
            "ativo": (linha.como_dict() if linha is not None else
                      {"ticker": normalizar(ticker),
                       "situacao": "não identificado na carteira nem no modelo"}),
            "classes": [c.como_dict() for c in self.classes],
            "do_modelo_fora_da_carteira": [
                {"ticker": x.ticker, "peso_modelo_pct": _r(x.peso_modelo)}
                for x in self.fora_da_carteira()],
            "avisos": list(self.avisos),
        }


def indisponivel(motivo: str, *,
                 falha_de_leitura: bool = False) -> ReferenciaModelo:
    return ReferenciaModelo(disponivel=False, motivo=motivo,
                            falha_de_leitura=falha_de_leitura)


def _r(x: float | None, casas: int = 2) -> float | None:
    return None if x is None else round(float(x), casas)


def _pct_br(x: float) -> str:
    return f"{x:.1f}%".replace(".", ",")


def _num(x) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def montar(snapshots: dict[str, dict[str, dict]], alocacao: dict,
           posicoes: list[dict] | None) -> ReferenciaModelo:
    """Compara a carteira recomendada (``snapshots`` + ``alocacao``, como o
    Portfólio Global os lê) com as ``posicoes`` de ``get_carteira()``."""
    alvos_modelo = dict(alocacao.get("targets") or {})
    renda_fixa = alocacao.get("renda_fixa")
    if not any(snapshots.get(c) for c in snapshots):
        return indisponivel("O Portfólio Global não tem carteira-modelo ativa "
                            "em nenhuma classe.")
    if not any(_num(v) > 0 for v in alvos_modelo.values()):
        return indisponivel("O Portfólio Global não tem alocação-alvo entre "
                            "as classes salva.")

    alvos = carteira_real.alvos_globais(alvos_modelo, renda_fixa)
    df = montar_posicoes(snapshots, alvos_modelo)

    modelo: dict[str, tuple[str, str, float]] = {}
    for linha in df.to_dict("records"):
        tk = normalizar(linha["symbol"])
        peso = alvos.get(linha["asset_class"], 0.0) * linha["weight_class"] * 100
        classe, nome = linha["asset_class"], str(linha.get("name") or tk)
        if tk in modelo:   # mesmo ticker em duas classes: soma, não duplica
            classe, nome, anterior = modelo[tk]
            peso += anterior
        modelo[tk] = (classe, nome, peso)

    base = BASE_PATRIMONIO if renda_fixa is not None else BASE_SEM_RENDA_FIXA
    valores: dict[str, float] = {}
    nomes: dict[str, str] = {}
    por_classe: dict[str, float] = {}
    for p in posicoes or []:
        vm = _num(p.get("valor_mercado"))
        if vm <= 0:
            continue
        classe = carteira_real.classe_global(p)
        if base == BASE_SEM_RENDA_FIXA and classe == "renda_fixa":
            continue
        tk = normalizar(p.get("ticker"))
        if not tk:
            continue
        valores[tk] = valores.get(tk, 0.0) + vm
        nomes.setdefault(tk, str(p.get("nome") or tk))
        por_classe[classe] = por_classe.get(classe, 0.0) + vm
    total = sum(valores.values())
    if total <= 0:
        return indisponivel("A carteira não tem valor de mercado positivo "
                            "para comparar com o modelo.")

    ativos: dict[str, LinhaAtivo] = {}
    for tk in sorted(set(modelo) | set(valores)):
        no_modelo, na_carteira = tk in modelo, tk in valores
        situacao = (NOS_DOIS if no_modelo and na_carteira
                    else SO_NO_MODELO if no_modelo else SO_NA_CARTEIRA)
        classe, nome, peso = modelo.get(tk, (None, nomes.get(tk, tk), None))
        ativos[tk] = LinhaAtivo(
            ticker=tk, nome=nomes.get(tk, nome), classe_modelo=classe,
            peso_modelo=peso,
            peso_real=(valores[tk] / total * 100) if na_carteira else None,
            situacao=situacao)

    classes = tuple(
        LinhaClasse(classe=c, rotulo=carteira_real.ROTULOS[c],
                    alvo=(alvos[c] * 100) if c in alvos else None,
                    real=por_classe.get(c, 0.0) / total * 100)
        for c in carteira_real.CLASSES_REAIS
        if c in alvos or por_classe.get(c, 0.0) > 0)

    avisos = [f"{carteira_real.ROTULOS.get(c, c)} tem alvo de "
              f"{_pct_br(_num(a) * 100)} no Portfólio Global, mas nenhuma "
              "carteira-modelo ativa: os ativos dessa classe ficam fora da "
              "comparação."
              for c, a in classes_sem_posicao(snapshots, alvos_modelo)]
    return ReferenciaModelo(disponivel=True, base=base, classes=classes,
                            ativos=ativos, avisos=tuple(avisos))


def _ler(engine=None, owner_id=None) -> tuple[dict, dict]:
    """(snapshots por classe, alocação-alvo): o mesmo que o Portfólio Global
    lê em ``views/portfolio_global.py::render``. Único ponto com banco."""
    from core.portfolio.registry import asset_classes
    from core.portfolio.repository import load_active_snapshots, load_allocation_targets
    snapshots = {c: load_active_snapshots(c, engine=engine, owner_id=owner_id)
                 for c in asset_classes()}
    return snapshots, load_allocation_targets(engine=engine, owner_id=owner_id)


def carregar(posicoes: list[dict] | None, *, engine=None,
             owner_id=None) -> ReferenciaModelo:
    """Lê a carteira recomendada e chama ``montar``. Falha de leitura vira
    referência indisponível, com o motivo nomeado."""
    try:
        snapshots, alocacao = _ler(engine, owner_id)
    except Exception as exc:  # noqa: BLE001 — a análise segue sem a referência
        log.warning("referência do modelo indisponível: %s", exc)
        return indisponivel("Falha ao ler a carteira recomendada do Portfólio "
                            f"Global ({type(exc).__name__}).",
                            falha_de_leitura=True)
    return montar(snapshots, alocacao, posicoes)
