"""
core/cenario/modelo.py
Cenário de Investimentos: itens, validação, versão e texto para a LLM. Puro.

Cada item guarda ``current_value``, ``expected_direction``, ``confidence``,
``source`` e ``last_updated``. O ``last_updated`` não é digitado: muda sozinho
quando o conteúdo do item muda, e só então. Assim a data diz quando a premissa
foi revista de fato, não quando alguém apertou "salvar".

Regra central: o cenário só muda por edição manual ou por pedido explícito do
usuário de atualizar a partir dos dados publicados. ``ORIGENS`` lista as duas;
não existe origem "llm", e ``revisar`` recusa qualquer outra. A LLM lê o
cenário como premissa e, se os fatos o contradisserem, escreve
``FRASE_REVISAO``. Nunca o altera.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

CHAVE_PREFERENCIA = "investment_scenario"
ESQUEMA = "cenario.v1"

# chave → (rótulo, o que registrar)
CAMPOS: tuple[tuple[str, str, str], ...] = (
    ("interest_rate", "Taxa de juros",
     "Nível atual dos juros básicos (Selic)."),
    ("interest_rate_outlook", "Expectativa de juros",
     "Para onde você espera que os juros andem."),
    ("inflation", "Inflação", "Inflação corrente (IPCA acumulado)."),
    ("inflation_outlook", "Expectativa de inflação",
     "Inflação esperada para os próximos meses."),
    ("economic_activity", "Atividade econômica",
     "Crescimento, emprego, confiança."),
    ("fx", "Câmbio", "Real contra o dólar."),
    ("fiscal_policy", "Política fiscal",
     "Resultado primário, dívida, credibilidade do arcabouço."),
    ("credit", "Crédito", "Oferta, custo e inadimplência do crédito."),
    ("commodities", "Commodities",
     "Petróleo, minério, agrícolas — o que pesa na sua carteira."),
    ("global_economy", "Economia internacional",
     "Juros e crescimento nos EUA, Europa e China."),
    ("geopolitical_risk", "Riscos geopolíticos",
     "Conflitos, eleições, sanções."),
    ("capital_markets", "Mercado de capitais",
     "Fluxo, emissões, apetite a risco na bolsa e no crédito privado."),
)
CHAVES = tuple(c for c, _, _ in CAMPOS)
ROTULO = {c: r for c, r, _ in CAMPOS}
AJUDA = {c: a for c, _, a in CAMPOS}

DIRECOES: tuple[tuple[str, str], ...] = (
    ("alta", "Alta"), ("estavel", "Estável"), ("queda", "Queda"),
    ("incerta", "Incerta"))
CONFIANCAS: tuple[tuple[str, str], ...] = (
    ("baixa", "Baixa"), ("media", "Média"), ("alta", "Alta"))
ROTULO_DIRECAO = dict(DIRECOES)
ROTULO_CONFIANCA = dict(CONFIANCAS)

MANUAL = "manual"
ATUALIZACAO_SOLICITADA = "atualizacao_solicitada"
ORIGENS = {MANUAL: "Edição manual",
           ATUALIZACAO_SOLICITADA: "Atualização pedida pelo usuário"}

MAX_VALOR = 300
MAX_FONTE = 200
MAX_HISTORICO = 20
ENVELHECE_DIAS = 90

FRASE_REVISAO = ("Existem mudanças relevantes que podem justificar revisão "
                 "do cenário.")

REGRA_CENARIO = (
    "CENÁRIO DE INVESTIMENTOS (premissa do usuário):\n"
    "1. O bloco CENÁRIO DE INVESTIMENTOS é a leitura do usuário sobre o "
    "ambiente econômico. Use-o como premissa adicional, ao lado da "
    "estratégia. Ele NÃO substitui os fundamentos do ativo nem a estratégia "
    "do usuário: com os três, a estratégia define o objetivo, os fundamentos "
    "descrevem o ativo e o cenário descreve o ambiente.\n"
    "2. Você NÃO altera o cenário. Não proponha valores novos como se fossem "
    "o cenário, não o reescreva e não o trate como desatualizado por conta "
    "própria: ele vale até o usuário mudá-lo.\n"
    "3. Se fatos do contexto (dados de mercado, notícias ou os sinais de "
    "revisão calculados pelo código) contradisserem o cenário, escreva "
    f"exatamente: \"{FRASE_REVISAO}\" e diga qual fato contradiz qual item. "
    "Nada além disso.\n"
    "4. Item sem valor é premissa ausente, não premissa neutra."
)


@dataclass(frozen=True)
class Item:
    current_value: str = ""
    expected_direction: str = ""
    confidence: str = ""
    source: str = ""
    last_updated: str | None = None      # AAAA-MM-DD

    @property
    def preenchido(self) -> bool:
        return bool(self.current_value)

    def conteudo(self) -> tuple[str, str, str, str]:
        return (self.current_value, self.expected_direction, self.confidence,
                self.source)

    def como_dict(self) -> dict:
        return {"current_value": self.current_value,
                "expected_direction": self.expected_direction,
                "confidence": self.confidence, "source": self.source,
                "last_updated": self.last_updated}

    @classmethod
    def de_dict(cls, d) -> Item:
        if not isinstance(d, dict):
            return cls()
        return cls(*(str(d.get(k) or "").strip() for k in (
            "current_value", "expected_direction", "confidence", "source")),
            last_updated=(str(d["last_updated"]) if d.get("last_updated")
                          else None))

    def idade_dias(self, hoje: dt.date) -> int | None:
        data = data_iso(self.last_updated)
        return None if data is None else (hoje - data).days


@dataclass(frozen=True)
class Cenario:
    itens: dict[str, Item] = field(default_factory=dict)
    versao: int = 0
    salvo_em: str | None = None           # instante ISO da última gravação
    origem: str | None = None
    historico: tuple[dict, ...] = ()

    def item(self, chave: str) -> Item:
        return self.itens.get(chave) or Item()

    @property
    def preenchidos(self) -> list[str]:
        return [c for c in CHAVES if self.item(c).preenchido]

    @property
    def vazio(self) -> bool:
        return not self.preenchidos

    def envelhecidos(self, hoje: dt.date) -> list[str]:
        return [c for c in self.preenchidos
                if (self.item(c).idade_dias(hoje) or 0) > ENVELHECE_DIAS]

    def como_dict(self) -> dict:
        return {"schema": ESQUEMA, "version": self.versao,
                "saved_at": self.salvo_em, "origin": self.origem,
                "items": {c: self.item(c).como_dict() for c in CHAVES},
                "history": list(self.historico)}

    @classmethod
    def de_dict(cls, d) -> Cenario:
        if not isinstance(d, dict):
            return cls()
        itens = d.get("items") if isinstance(d.get("items"), dict) else {}
        try:
            versao = int(d.get("version") or 0)
        except (TypeError, ValueError):
            versao = 0
        hist = d.get("history") if isinstance(d.get("history"), list) else []
        return cls(itens={c: Item.de_dict(itens.get(c)) for c in CHAVES},
                   versao=versao, salvo_em=d.get("saved_at"),
                   origem=d.get("origin"),
                   historico=tuple(h for h in hist if isinstance(h, dict)))


def data_iso(valor) -> dt.date | None:
    if not valor:
        return None
    try:
        return dt.date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


# -- edição -----------------------------------------------------------------------

class ConflitoDeVersao(Exception):
    """O cenário gravado mudou desde que a tela o leu."""


def normalizar_item(bruto) -> Item:
    return Item.de_dict(bruto if isinstance(bruto, dict) else {})


def erros_item(chave: str, it: Item) -> list[str]:
    rot = ROTULO.get(chave, chave)
    erros = []
    if len(it.current_value) > MAX_VALOR:
        erros.append(f"{rot}: valor com mais de {MAX_VALOR} caracteres.")
    if len(it.source) > MAX_FONTE:
        erros.append(f"{rot}: fonte com mais de {MAX_FONTE} caracteres.")
    if it.expected_direction and it.expected_direction not in ROTULO_DIRECAO:
        erros.append(f"{rot}: direção esperada inválida.")
    if it.confidence and it.confidence not in ROTULO_CONFIANCA:
        erros.append(f"{rot}: confiança inválida.")
    if it.preenchido:
        # Premissa sem procedência vira "fato" que ninguém sabe de onde veio.
        faltam = [n for n, v in (("direção esperada", it.expected_direction),
                                 ("confiança", it.confidence),
                                 ("fonte", it.source)) if not v]
        if faltam:
            erros.append(f"{rot}: preencha {', '.join(faltam)}.")
    elif any(it.conteudo()[1:]):
        erros.append(f"{rot}: direção, confiança ou fonte sem valor atual.")
    return erros


def revisar(anterior: Cenario, entradas: dict, *, origem: str,
            hoje: dt.date, agora: dt.datetime
            ) -> tuple[Cenario, list[str], list[str]]:
    """(novo cenário, itens alterados, erros). Puro.

    Sem alteração nenhuma, devolve o anterior intacto: salvar sem mudar não
    cria versão nem mexe em ``last_updated``. Só itens cujo conteúdo mudou
    ganham a data de hoje. Chave fora de ``CHAVES`` é ignorada.
    """
    if origem not in ORIGENS:
        raise ValueError(f"origem de alteração não permitida: {origem!r}")
    erros: list[str] = []
    itens: dict[str, Item] = {}
    alterados: list[str] = []
    for c in CHAVES:
        velho = anterior.item(c)
        if c not in entradas:
            itens[c] = velho
            continue
        novo = normalizar_item(entradas[c])
        erros += erros_item(c, novo)
        if novo.conteudo() == velho.conteudo():
            itens[c] = velho
            continue
        alterados.append(c)
        itens[c] = replace(novo, last_updated=(hoje.isoformat()
                                               if novo.preenchido else None))
    if erros or not alterados:
        return anterior, ([] if erros else alterados), erros
    registro = {"version": anterior.versao + 1, "saved_at": agora.isoformat(),
                "origin": origem, "changed": alterados}
    return (Cenario(itens=itens, versao=anterior.versao + 1,
                    salvo_em=agora.isoformat(), origem=origem,
                    historico=((registro,) + anterior.historico)[:MAX_HISTORICO]),
            alterados, [])


def gravar_em(extra: dict, novo: Cenario, *, versao_esperada: int) -> dict:
    """``extra_settings`` com o cenário novo. Puro.

    Recusa se o gravado não estiver na versão que a tela leu: duas abas
    abertas não se sobrescrevem em silêncio.
    """
    atual = Cenario.de_dict((extra or {}).get(CHAVE_PREFERENCIA))
    if atual.versao != versao_esperada:
        raise ConflitoDeVersao(
            f"O cenário foi salvo em outra sessão (versão {atual.versao}; "
            f"esta tela leu a {versao_esperada}). Recarregue antes de editar.")
    if novo.versao != versao_esperada + 1:
        raise ValueError("versão nova inconsistente com a esperada")
    saida = dict(extra or {})
    saida[CHAVE_PREFERENCIA] = novo.como_dict()
    return saida


# -- leitura pela análise ---------------------------------------------------------

# Itens que mais pesam em cada classe da política. Os demais continuam no
# contexto; isto só ordena a leitura e o resumo da seção.
RELEVANCIA: dict[str, tuple[str, ...]] = {
    "renda_fixa": ("interest_rate", "interest_rate_outlook", "inflation",
                   "inflation_outlook", "fiscal_policy", "credit"),
    "acoes_br": ("interest_rate_outlook", "economic_activity", "fx",
                 "fiscal_policy", "commodities", "capital_markets"),
    "fiis": ("interest_rate", "interest_rate_outlook", "inflation",
             "credit", "economic_activity", "capital_markets"),
    "exterior": ("fx", "global_economy", "geopolitical_risk",
                 "capital_markets"),
}


def relevantes(classe_politica: str | None) -> tuple[str, ...]:
    return RELEVANCIA.get(classe_politica or "", CHAVES)


def linha_item(chave: str, it: Item) -> str:
    if not it.preenchido:
        return f"- {ROTULO[chave]}: sem premissa cadastrada."
    return (f"- {ROTULO[chave]}: {it.current_value} · direção esperada "
            f"{ROTULO_DIRECAO.get(it.expected_direction, '—')} · confiança "
            f"{ROTULO_CONFIANCA.get(it.confidence, '—')} · fonte {it.source}"
            f" · revisto em {it.last_updated or '—'}")


def texto_para_llm(c: Cenario | None, *, hoje: dt.date,
                   classe_politica: str | None = None,
                   sinais: tuple = ()) -> str:
    """O bloco que a LLM lê. Sem cenário, diz isso em vez de omitir."""
    if c is None or c.vazio:
        return ("=== CENÁRIO DE INVESTIMENTOS ===\nO usuário não cadastrou "
                "cenário. Não presuma um; use só os dados de mercado do "
                "contexto.")
    ordem = list(relevantes(classe_politica))
    ordem += [k for k in CHAVES if k not in ordem]
    linhas = [f"=== CENÁRIO DE INVESTIMENTOS (premissa do usuário, versão "
              f"{c.versao}, salvo em {(c.salvo_em or '—')[:10]}) ===",
              "Não altere. Não substitui fundamentos nem estratégia."]
    linhas += [linha_item(k, c.item(k)) for k in ordem]
    velhos = c.envelhecidos(hoje)
    if velhos:
        linhas.append(f"Itens revistos há mais de {ENVELHECE_DIAS} dias: "
                      + ", ".join(ROTULO[k] for k in velhos) + ".")
    for s in sinais:
        linhas.append(f"Sinal de revisão (calculado pelo código): {s.texto}")
    return "\n".join(linhas)


def para_contexto(c: Cenario | None, *, hoje: dt.date,
                  classe_politica: str | None = None,
                  sinais: tuple = ()) -> dict | None:
    """O cenário como objeto do contexto estruturado. ``None`` sem cenário."""
    if c is None or c.vazio:
        return None
    return {
        "natureza": "premissa do usuário; não alterar; não substitui "
                    "fundamentos nem estratégia",
        "versao": c.versao,
        "salvo_em": (c.salvo_em or "")[:10] or None,
        "itens": {ROTULO[k]: c.item(k).como_dict() for k in c.preenchidos},
        "sem_premissa": [ROTULO[k] for k in CHAVES
                         if not c.item(k).preenchido],
        "mais_relevantes_para_a_classe": [ROTULO[k] for k in
                                          relevantes(classe_politica)],
        "dias_para_revisao_antiga": ENVELHECE_DIAS,
        "revisao_antiga": [ROTULO[k] for k in c.envelhecidos(hoje)],
        "sinais_de_revisao": [s.texto for s in sinais],
    }
