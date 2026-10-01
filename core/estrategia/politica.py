"""
core/estrategia/politica.py
A política de investimentos do usuário — schema, validação, progresso e status.

Puro: nada aqui toca banco, Streamlit ou LLM. O repositório grava, a
entrevista propõe, a tela mostra; quem decide o que é um valor válido, o que
falta e em que estado a política está é este módulo, e só ele.

Forma da política gravada (``policy_json``)::

    {campo: {"value": ..., "source": "entrevista" | "manual",
             "evidence": "o que o usuário disse", "at": "ISO-8601"}}

A procedência viaja com o valor. Um número que a pessoa não disse nunca entra
aqui: não existe valor padrão gravado como escolha
(``memoria: padrao-de-widget-vira-fato-do-usuario``).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

SCHEMA_VERSION = "1"
REVISAO_DIAS = 365
TOLERANCIA_SOMA = 0.5
MAX_TEXTO = 500

NOT_STARTED = "NOT_STARTED"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
NEEDS_REVIEW = "NEEDS_REVIEW"
ARCHIVED = "ARCHIVED"

ROTULO_STATUS = {
    NOT_STARTED: "Não iniciada",
    IN_PROGRESS: "Em andamento",
    COMPLETED: "Concluída",
    NEEDS_REVIEW: "Precisa de revisão",
}

FONTES = ("entrevista", "manual")

CLASSES: tuple[tuple[str, str], ...] = (
    ("renda_fixa", "Renda fixa"),
    ("acoes_br", "Ações Brasil"),
    ("fiis", "Fundos imobiliários"),
    ("exterior", "Internacional"),
)
_CHAVES_CLASSE = tuple(c for c, _ in CLASSES)

_OBJETIVOS = (
    ("crescimento_patrimonial", "Crescimento do patrimônio"),
    ("renda_passiva", "Renda passiva"),
    ("aposentadoria", "Aposentadoria"),
    ("preservacao_patrimonial", "Preservação do patrimônio"),
    ("objetivo_especifico", "Objetivo específico (imóvel, estudo, etc.)"),
)
_NIVEIS = (("baixa", "Baixa"), ("media", "Média"), ("alta", "Alta"))


@dataclass(frozen=True)
class Campo:
    chave: str
    rotulo: str
    tipo: str  # escolha | multi | numero | bool | texto | alocacao | limites
    grupo: str
    pergunta: str
    obrigatorio: bool = False
    opcoes: tuple[tuple[str, str], ...] = ()
    minimo: float | None = None
    maximo: float | None = None
    unidade: str = ""


CAMPOS: tuple[Campo, ...] = (
    # -- mínimos: sem eles a política não conclui --------------------------
    Campo("objective", "Objetivo principal", "escolha", "Objetivos",
          "Qual é o principal objetivo do seu patrimônio investido?",
          obrigatorio=True, opcoes=_OBJETIVOS),
    Campo("time_horizon", "Horizonte de investimento", "escolha", "Objetivos",
          "Em quanto tempo você pretende começar a usar esse dinheiro?",
          obrigatorio=True, opcoes=(
              ("curto", "Até 2 anos"), ("medio", "De 2 a 5 anos"),
              ("longo", "De 5 a 15 anos"), ("muito_longo", "Mais de 15 anos"))),
    Campo("risk_profile", "Perfil de risco", "escolha", "Risco",
          "Se sua carteira caísse 20% em poucos meses, o que você faria?",
          obrigatorio=True, opcoes=(
              ("conservador", "Conservador"), ("moderado", "Moderado"),
              ("arrojado", "Arrojado"))),
    Campo("liquidity_need", "Necessidade de liquidez", "escolha", "Liquidez",
          "Qual parte do patrimônio você pode precisar resgatar de uma hora "
          "para outra?", obrigatorio=True, opcoes=_NIVEIS),
    Campo("predominant_strategy", "Estratégia predominante", "escolha",
          "Estratégia",
          "O que pesa mais para você hoje: fazer o patrimônio crescer, "
          "receber dividendos, equilibrar os dois ou preservar o que já tem?",
          obrigatorio=True, opcoes=(
              ("crescimento", "Crescimento"), ("dividendos", "Dividendos"),
              ("equilibrada", "Equilibrada"), ("preservacao", "Preservação"))),
    Campo("asset_class_targets", "Alocação alvo por classe", "alocacao",
          "Alocação",
          "Como você gostaria de dividir a carteira entre renda fixa, ações "
          "no Brasil, fundos imobiliários e internacional (em %)?",
          obrigatorio=True, unidade="%"),
    # -- complementares -----------------------------------------------------
    Campo("secondary_objectives", "Objetivos secundários", "multi",
          "Objetivos",
          "Além do objetivo principal, há outros objetivos para o patrimônio?",
          opcoes=_OBJETIVOS),
    Campo("time_horizon_years", "Horizonte em anos", "numero", "Objetivos",
          "Quantos anos, aproximadamente, até precisar do dinheiro?",
          minimo=0, maximo=80, unidade="anos"),
    Campo("short_term_goals", "Objetivos de curto prazo", "texto", "Objetivos",
          "Há algum objetivo de curto prazo (até 2 anos) que dependa dos "
          "investimentos?"),
    Campo("medium_term_goals", "Objetivos de médio prazo", "texto", "Objetivos",
          "E de médio prazo (2 a 5 anos)?"),
    Campo("long_term_goals", "Objetivos de longo prazo", "texto", "Objetivos",
          "E de longo prazo (mais de 5 anos)?"),
    Campo("risk_capacity", "Capacidade de risco", "escolha", "Risco",
          "Se os investimentos perdessem parte do valor, isso afetaria suas "
          "contas do mês ou seus planos?", opcoes=_NIVEIS),
    Campo("max_drawdown_tolerance_pct", "Queda máxima aceitável", "numero",
          "Risco", "Qual queda temporária da carteira você aceitaria sem "
          "mudar de estratégia (em %)?", minimo=0, maximo=100, unidade="%"),
    Campo("income_needed_now", "Precisa de renda agora", "bool", "Renda",
          "Você precisa que a carteira gere renda para você já, hoje?"),
    Campo("income_monthly_target", "Renda mensal desejada", "numero", "Renda",
          "Quanto de renda mensal você gostaria que a carteira gerasse?",
          minimo=0, unidade="R$"),
    Campo("makes_contributions", "Faz aportes", "bool", "Aportes",
          "Você pretende fazer aportes regulares?"),
    Campo("monthly_contribution", "Aporte mensal", "numero", "Aportes",
          "Qual valor, aproximadamente, você consegue aportar por mês?",
          minimo=0, unidade="R$"),
    Campo("has_emergency_reserve", "Tem reserva de emergência", "bool",
          "Reservas", "Você já tem uma reserva de emergência separada?"),
    Campo("emergency_reserve_months", "Reserva de emergência (meses)",
          "numero", "Reservas",
          "Quantos meses de gastos essa reserva cobre (ou deveria cobrir)?",
          minimo=0, maximo=120, unidade="meses"),
    Campo("opportunity_reserve_pct", "Reserva de oportunidade", "numero",
          "Reservas", "Você quer manter uma parte em caixa para aproveitar "
          "oportunidades? Quanto, em % da carteira?",
          minimo=0, maximo=100, unidade="%"),
    Campo("strategy_priorities", "Prioridades da estratégia", "multi",
          "Estratégia",
          "Quais destes pontos importam para você: crescimento, dividendos, "
          "preservação, aposentadoria, proteção contra a inflação?",
          opcoes=(("crescimento", "Crescimento"), ("dividendos", "Dividendos"),
                  ("preservacao", "Preservação"),
                  ("aposentadoria", "Aposentadoria"),
                  ("protecao_inflacao", "Proteção contra a inflação"))),
    Campo("asset_class_limits", "Limite máximo por classe", "limites",
          "Limites", "Há alguma classe que não deve passar de um certo "
          "percentual da carteira?", unidade="%"),
    Campo("single_asset_limit_pct", "Limite por ativo", "numero", "Limites",
          "Qual o máximo que um único ativo pode pesar na carteira (em %)?",
          minimo=0, maximo=100, unidade="%"),
    Campo("sector_limit_pct", "Limite por setor", "numero", "Limites",
          "E um único setor, no máximo quanto (em %)?",
          minimo=0, maximo=100, unidade="%"),
    Campo("liquidity_constraints", "Restrições de liquidez", "texto",
          "Liquidez", "Há algum resgate já previsto, com data e valor?"),
    Campo("user_constraints", "Outras restrições", "texto", "Limites",
          "Há algo em que você não quer investir, ou alguma outra regra "
          "pessoal para a carteira?"),
)

POR_CHAVE: dict[str, Campo] = {c.chave: c for c in CAMPOS}
OBRIGATORIOS: tuple[str, ...] = tuple(c.chave for c in CAMPOS if c.obrigatorio)
COMPLEMENTARES: tuple[str, ...] = tuple(
    c.chave for c in CAMPOS if not c.obrigatorio)

# Complementares que só fazem sentido quando outro campo diz "sim". Sem isto
# a entrevista perguntaria o valor do aporte a quem acabou de dizer que não
# aporta, e o progresso contaria como faltando o que não se aplica.
CONDICIONAIS: dict[str, tuple[str, object]] = {
    "monthly_contribution": ("makes_contributions", True),
    "emergency_reserve_months": ("has_emergency_reserve", True),
}


# -- normalização -------------------------------------------------------------

def _num(bruto) -> float | None:
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, (int, float)):
        valor = float(bruto)
    elif isinstance(bruto, str):
        texto = bruto.strip().replace("%", "").replace("R$", "").strip()
        if not texto:
            return None
        # "1.500,50" (pt-BR) e "1500.5" (ponto decimal) — nessa ordem.
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        try:
            valor = float(texto)
        except ValueError:
            return None
    else:
        return None
    return valor if math.isfinite(valor) else None


def _opcao(campo: Campo, bruto) -> str | None:
    if not isinstance(bruto, str):
        return None
    alvo = bruto.strip().casefold()
    for chave, rotulo in campo.opcoes:
        if alvo in (chave.casefold(), rotulo.casefold()):
            return chave
    return None


def _classes(campo: Campo, bruto) -> tuple[dict | None, str | None]:
    if not isinstance(bruto, dict):
        return None, f"{campo.rotulo}: esperado um percentual por classe."
    desconhecidas = [k for k in bruto if k not in _CHAVES_CLASSE]
    if desconhecidas:
        return None, (f"{campo.rotulo}: classe desconhecida "
                      f"{', '.join(map(str, desconhecidas))}.")
    saida: dict[str, float] = {}
    for chave, valor in bruto.items():
        numero = _num(valor)
        if numero is None or not 0 <= numero <= 100:
            return None, f"{campo.rotulo}: percentual inválido em {chave}."
        saida[chave] = round(numero, 1)
    if not saida:
        return None, f"{campo.rotulo}: nenhuma classe informada."
    return saida, None


def normalizar(chave: str, bruto) -> tuple[object, str | None]:
    """Valor pronto para gravar, ou ``(None, motivo)``.

    Não completa nada: uma alocação com três classes informadas continua com
    três, e a soma é exigida — o que falta é da pessoa dizer, não daqui supor.
    """
    campo = POR_CHAVE.get(chave)
    if campo is None:
        return None, f"Campo desconhecido: {chave}."
    if bruto is None:
        return None, f"{campo.rotulo}: vazio."

    if campo.tipo == "escolha":
        valor = _opcao(campo, bruto)
        return (valor, None) if valor else (
            None, f"{campo.rotulo}: opção inválida ({bruto!r}).")

    if campo.tipo == "multi":
        itens = bruto if isinstance(bruto, (list, tuple)) else [bruto]
        valores = [_opcao(campo, i) for i in itens]
        if any(v is None for v in valores):
            return None, f"{campo.rotulo}: opção inválida em {bruto!r}."
        # Ordem do schema, sem repetição: a mesma escolha grava igual.
        escolhidos = set(valores)
        return [c for c, _ in campo.opcoes if c in escolhidos], None

    if campo.tipo == "numero":
        valor = _num(bruto)
        if valor is None:
            return None, f"{campo.rotulo}: número inválido ({bruto!r})."
        if campo.minimo is not None and valor < campo.minimo:
            return None, f"{campo.rotulo}: abaixo de {campo.minimo:g}."
        if campo.maximo is not None and valor > campo.maximo:
            return None, f"{campo.rotulo}: acima de {campo.maximo:g}."
        return round(valor, 2), None

    if campo.tipo == "bool":
        if isinstance(bruto, bool):
            return bruto, None
        texto = str(bruto).strip().casefold()
        if texto in ("sim", "s", "true", "yes"):
            return True, None
        if texto in ("não", "nao", "n", "false", "no"):
            return False, None
        return None, f"{campo.rotulo}: responda sim ou não."

    if campo.tipo == "texto":
        texto = str(bruto).strip()
        if not texto:
            return None, f"{campo.rotulo}: vazio."
        return texto[:MAX_TEXTO], None

    if campo.tipo == "alocacao":
        valor, erro = _classes(campo, bruto)
        if erro:
            return None, erro
        completo = {c: valor.get(c, 0.0) for c in _CHAVES_CLASSE}
        soma = sum(completo.values())
        if abs(soma - 100) > TOLERANCIA_SOMA:
            return None, (f"{campo.rotulo}: as classes somam {soma:g}%, "
                          "precisam somar 100%.")
        return completo, None

    if campo.tipo == "limites":
        return _classes(campo, bruto)

    return None, f"{campo.rotulo}: tipo sem regra ({campo.tipo})."


# -- leitura da política ------------------------------------------------------

def valor(politica: dict, chave: str):
    item = (politica or {}).get(chave)
    return item.get("value") if isinstance(item, dict) else None


def preenchido(politica: dict, chave: str) -> bool:
    """Preenchido = tem valor que AINDA passa na validação atual.

    Um valor gravado sob um schema antigo que hoje é inválido conta como
    faltando: é isso que faz a política concluída cair para NEEDS_REVIEW
    quando a regra muda.
    """
    bruto = valor(politica, chave)
    if bruto is None:
        return False
    _, erro = normalizar(chave, bruto)
    return erro is None


def aplicavel(politica: dict, chave: str) -> bool:
    cond = CONDICIONAIS.get(chave)
    if cond is None:
        return True
    return valor(politica, cond[0]) == cond[1]


def valores(politica: dict) -> dict:
    """Só os valores válidos, achatados, mais os derivados por classe.

    ``fixed_income_target``/``variable_income_target``/``international_target``
    não são perguntados: saem da alocação por classe. Perguntar duas vezes a
    mesma coisa abre a porta para as duas respostas divergirem.
    """
    saida = {c: valor(politica, c) for c in POR_CHAVE if preenchido(politica, c)}
    alvo = saida.get("asset_class_targets")
    if alvo:
        saida["fixed_income_target"] = alvo["renda_fixa"]
        saida["variable_income_target"] = round(
            alvo["acoes_br"] + alvo["fiis"], 1)
        saida["international_target"] = alvo["exterior"]
    return saida


@dataclass(frozen=True)
class Progresso:
    pct: float
    obrigatorios_ok: int
    obrigatorios_total: int
    faltantes: tuple[str, ...]
    complementares_ok: int
    complementares_total: int
    complementares_faltantes: tuple[str, ...] = field(default=())

    @property
    def minimos_completos(self) -> bool:
        return not self.faltantes


def progresso(politica: dict) -> Progresso:
    """Percentual pelos campos MÍNIMOS; complementares contados à parte.

    Misturar os dois faria "80% concluída" conviver com status Concluída (os
    mínimos todos lá, complementares não) — dois números contraditórios na
    mesma tela. Assim, 100% é exatamente "pode concluir".
    """
    faltantes = tuple(c for c in OBRIGATORIOS if not preenchido(politica, c))
    ok = len(OBRIGATORIOS) - len(faltantes)
    comp = [c for c in COMPLEMENTARES if aplicavel(politica, c)]
    comp_faltantes = tuple(c for c in comp if not preenchido(politica, c))
    return Progresso(
        pct=round(100.0 * ok / len(OBRIGATORIOS), 1),
        obrigatorios_ok=ok,
        obrigatorios_total=len(OBRIGATORIOS),
        faltantes=faltantes,
        complementares_ok=len(comp) - len(comp_faltantes),
        complementares_total=len(comp),
        complementares_faltantes=comp_faltantes,
    )


def erros_de_conclusao(politica: dict) -> list[str]:
    """O que impede concluir. Lista vazia = pode concluir."""
    erros = []
    for chave in OBRIGATORIOS:
        bruto = valor(politica, chave)
        if bruto is None:
            erros.append(f"Falta: {POR_CHAVE[chave].rotulo}.")
            continue
        _, erro = normalizar(chave, bruto)
        if erro:
            erros.append(erro)
    return erros


def alertas(politica: dict) -> list[str]:
    """Incoerências que NÃO bloqueiam: a pessoa pode ter motivo.

    Vão para a tela e para o contexto da LLM, que precisa saber que a própria
    política tem uma tensão antes de usá-la como régua.
    """
    v = valores(politica)
    saida = []
    alvo = v.get("asset_class_targets") or {}
    if v.get("time_horizon") == "curto" and v.get("risk_profile") == "arrojado":
        saida.append("Horizonte curto com perfil arrojado: uma queda perto da "
                     "data de uso não teria tempo de se recuperar.")
    if v.get("liquidity_need") == "alta" and alvo and alvo["renda_fixa"] < 20:
        saida.append("Liquidez alta com menos de 20% em renda fixa.")
    if (v.get("objective") == "renda_passiva"
            and v.get("predominant_strategy") == "crescimento"):
        saida.append("Objetivo de renda passiva com estratégia de crescimento.")
    if (v.get("risk_profile") == "arrojado"
            and v.get("risk_capacity") == "baixa"):
        saida.append("Tolerância arrojada, mas capacidade de absorver perdas "
                     "baixa: a capacidade deve prevalecer.")
    if v.get("has_emergency_reserve") is False:
        saida.append("Sem reserva de emergência: um imprevisto pode forçar "
                     "venda na hora errada.")
    if v.get("income_needed_now") and "income_monthly_target" not in v:
        saida.append("Precisa de renda agora, mas o valor mensal não foi "
                     "informado.")
    for classe, teto in (v.get("asset_class_limits") or {}).items():
        if alvo and alvo.get(classe, 0) > teto:
            saida.append(f"{_rotulo_classe(classe)}: alvo de "
                         f"{alvo[classe]:g}% acima do limite de {teto:g}%.")
    return saida


def status_efetivo(status_gravado: str | None, politica: dict | None, *,
                   schema_version: str | None = None,
                   completed_at: dt.datetime | None = None,
                   agora: dt.datetime | None = None) -> str:
    """Status recalculado na leitura, nunca só o que a coluna diz.

    Uma política concluída que hoje não passaria na validação, que foi
    validada com outro conjunto de campos ou cuja revisão venceu vira
    NEEDS_REVIEW — um COMPLETED gravado não sobrevive ao próprio motivo
    (``memoria: portao-que-sobrevive-ao-proprio-motivo``).
    """
    politica = politica or {}
    if status_gravado is None:
        return NOT_STARTED
    if status_gravado in (IN_PROGRESS, ARCHIVED):
        return IN_PROGRESS if status_gravado == IN_PROGRESS else ARCHIVED
    if erros_de_conclusao(politica):
        return NEEDS_REVIEW
    if schema_version is not None and str(schema_version) != SCHEMA_VERSION:
        return NEEDS_REVIEW
    if status_gravado == NEEDS_REVIEW:
        return NEEDS_REVIEW
    if completed_at is not None:
        agora = agora or dt.datetime.now(dt.timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=dt.timezone.utc)
        if (agora - completed_at).days > REVISAO_DIAS:
            return NEEDS_REVIEW
    return COMPLETED


# -- escrita (pura: devolve uma política nova) --------------------------------

def aplicar(politica: dict, atualizacoes: dict, *, fonte: str,
            evidencias: dict | None = None,
            agora: dt.datetime | None = None) -> tuple[dict, dict]:
    """Aplica o que é válido; devolve ``(nova_politica, {campo: erro})``.

    O inválido é descartado campo a campo, e o que já estava gravado naquele
    campo permanece. Uma resposta ruim não apaga uma boa.
    """
    if fonte not in FONTES:
        raise ValueError(f"fonte desconhecida: {fonte}")
    agora = agora or dt.datetime.now(dt.timezone.utc)
    evidencias = evidencias or {}
    nova = {k: dict(v) for k, v in (politica or {}).items()
            if isinstance(v, dict)}
    erros: dict[str, str] = {}
    for chave, bruto in (atualizacoes or {}).items():
        normal, erro = normalizar(chave, bruto)
        if erro:
            erros[chave] = erro
            continue
        item = {"value": normal, "source": fonte, "at": agora.isoformat()}
        evidencia = str(evidencias.get(chave) or "").strip()
        if evidencia:
            item["evidence"] = evidencia[:MAX_TEXTO]
        nova[chave] = item
    return nova, erros


def remover(politica: dict, chave: str) -> dict:
    return {k: dict(v) for k, v in (politica or {}).items() if k != chave}


# -- apresentação --------------------------------------------------------------

def _rotulo_classe(chave: str) -> str:
    return dict(CLASSES).get(chave, chave)


def formatar(chave: str, bruto) -> str:
    campo = POR_CHAVE.get(chave)
    if campo is None or bruto is None:
        return "—"
    if campo.tipo == "escolha":
        return dict(campo.opcoes).get(bruto, str(bruto))
    if campo.tipo == "multi":
        rotulos = dict(campo.opcoes)
        return ", ".join(rotulos.get(i, str(i)) for i in bruto) or "nenhum"
    if campo.tipo == "bool":
        return "Sim" if bruto else "Não"
    if campo.tipo in ("alocacao", "limites"):
        return " · ".join(f"{_rotulo_classe(k)} {v:g}%"
                          for k, v in bruto.items())
    if campo.tipo == "numero":
        if campo.unidade == "R$":
            texto = f"{bruto:,.2f}".replace(",", "X").replace(".", ",")
            return "R$ " + texto.replace("X", ".")
        return f"{bruto:g} {campo.unidade}".strip()
    return str(bruto)


def texto_da_politica(politica: dict, *, versao: int | None = None,
                      status: str | None = None) -> str:
    """A política como a LLM vai lê-la: rótulo, valor e procedência.

    Campo ausente aparece como "não informado" em vez de sumir: a LLM que não
    vê o campo não sabe se ele não existe ou se a pessoa não respondeu, e
    tende a supor. Aqui a lacuna tem nome.
    """
    linhas = ["=== POLÍTICA DE INVESTIMENTOS DO USUÁRIO ==="]
    if versao is not None:
        linhas.append(f"Versão: {versao} · Status: "
                      f"{ROTULO_STATUS.get(status or '', status or '—')}")
    grupo_atual = None
    for campo in CAMPOS:
        if not aplicavel(politica, campo.chave):
            continue
        if campo.grupo != grupo_atual:
            grupo_atual = campo.grupo
            linhas.append(f"\n[{grupo_atual}]")
        if preenchido(politica, campo.chave):
            item = politica[campo.chave]
            origem = ("dito na entrevista" if item.get("source") == "entrevista"
                      else "informado no formulário")
            linhas.append(f"- {campo.rotulo}: "
                          f"{formatar(campo.chave, item['value'])} ({origem})")
        else:
            linhas.append(f"- {campo.rotulo}: não informado")
    v = valores(politica)
    if "fixed_income_target" in v:
        linhas.append(
            f"\nDerivado da alocação: renda fixa {v['fixed_income_target']:g}%, "
            f"renda variável {v['variable_income_target']:g}%, "
            f"internacional {v['international_target']:g}%.")
    avisos = alertas(politica)
    if avisos:
        linhas.append("\n[Tensões internas da política]")
        linhas.extend(f"- {a}" for a in avisos)
    return "\n".join(linhas)
