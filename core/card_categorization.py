"""
core/card_categorization.py
Classificação de categorias das transações de cartão de crédito — LÓGICA PURA.

Fonte única da taxonomia e das regras, usada tanto pelo importador de faturas
(core.controle.parse_fatura_cartao_csv) quanto pelo script de recategorização
(scripts/recategorizar_cartao.py). Sem Streamlit, sem banco.

Ordem de decisão em classify():
  1. Estrutural (independe do estabelecimento):
       pagamento da fatura  -> "Pagamento de Cartão"
       estorno/crédito      -> "Créditos e Estornos"
       anuidade/tarifa      -> "Tarifas & Anuidade"
  2. Regras do USUÁRIO (aprendidas na tela) — palavra-chave no estabelecimento.
  3. Regras internas por ESTABELECIMENTO (MERCHANT_RULES).
  4. Fallback pela categoria da operadora (CATEGORY_MAP).
  5. Nada casou -> REVIEW_SENTINEL ("A revisar"): a UI pede a categoria ao usuário.

Os nomes da taxonomia são os MESMOS das categorias de sistema que o extrato
bancário e o lançamento manual usam (``Saúde``, ``Lazer``, ``Combustível``...).
Até 31/10/2026 a fatura tinha nomes próprios (``Saúde & Bem-estar``,
``Transporte & Combustível``...), criados pelo importador na conta de cada
usuário, e o mesmo gasto caía em duas categorias conforme fosse pago no débito
ou no cartão. ``NOMES_LEGADOS`` traduz os nomes antigos — que continuam
gravados em regras aprendidas e em lançamentos ainda não migrados por
``scripts/unificar_categorias_cartao.py``.
"""
from __future__ import annotations

import re
import unicodedata

# ── Sentinela para itens não catalogados (revisão manual) ─────────────────────
REVIEW_SENTINEL = "A revisar"

# ── Taxonomia-alvo (14) + tipo de cada categoria ─────────────────────────────
# 'Pagamento de Cartão' e 'Créditos e Estornos' são preservados: o primeiro é
# hardcoded em SQL do app (_SQL_GASTOS_CARTAO_MENSAL) e ambos representam transfer.
# 'Cuidados pessoais', 'Casa & Construção' e 'Tarifas & Anuidade' não têm par
# entre as categorias de sistema e continuam só do cartão.
CATEGORY_TYPE = {
    "Alimentação": "expense",
    "Mercado": "expense",
    "Compras": "expense",
    "Assinaturas": "expense",
    "Saúde": "expense",
    "Cuidados pessoais": "expense",
    "Casa & Construção": "expense",
    "Transporte": "expense",
    "Combustível": "expense",
    "Lazer": "expense",
    "Educação": "expense",
    "Tarifas & Anuidade": "expense",
    "Pagamento de Cartão": "transfer",
    "Créditos e Estornos": "transfer",
    REVIEW_SENTINEL: "expense",
}


def categorias_disponiveis(incluir_estruturais: bool = True) -> list[str]:
    """Lista de categorias-alvo para popular seletores na UI (sem o sentinela)."""
    estruturais = {"Pagamento de Cartão", "Créditos e Estornos"}
    out = []
    for nome in CATEGORY_TYPE:
        if nome == REVIEW_SENTINEL:
            continue
        if not incluir_estruturais and nome in estruturais:
            continue
        out.append(nome)
    return out


# ── Regras por ESTABELECIMENTO (prioridade alta -> baixa) ────────────────────
MERCHANT_RULES = [
    ("SCARDINO", "Casa & Construção"),        # toldo p/ reforma (informado pelo usuário)
    ("WELLHUB", "Saúde"),
    ("GYMPASS", "Saúde"),
    ("CLAUDE", "Assinaturas"),
    ("ANTHROPIC", "Assinaturas"),
    ("OPENAI", "Assinaturas"),
    ("CHATGPT", "Assinaturas"),
    ("SUPABASE", "Assinaturas"),
    ("BRAPI", "Assinaturas"),
    # 123Comprou (MERCADO*123COMPROU) é MARKETPLACE, não assinatura → varejo.
    ("123COMPROU", "Compras"),
    ("LIVELO", "Assinaturas"),
    ("SMILES", "Assinaturas"),
    ("IFOOD CLUB", "Assinaturas"),
    ("JUSBRASIL", "Assinaturas"),
    ("GOOGLE", "Assinaturas"),
    ("IFOOD", "Alimentação"),
    ("TUCUPI", "Alimentação"),
    ("PESCADO", "Alimentação"),
    ("ATACADAO", "Mercado"),
    ("ASSAI", "Mercado"),
    ("ATACADISTA", "Mercado"),
    ("TIP HOME", "Casa & Construção"),
    ("HOME CENTER", "Casa & Construção"),
    ("CASAS BAHIA", "Compras"),
    ("TEMU", "Compras"),
    ("MERCADOLIVRE", "Compras"),
    ("MERCADO LIVRE", "Compras"),
    ("KALUNGA", "Compras"),
    ("QUADROSDECORA", "Compras"),
    ("POSTO", "Combustível"),
    ("PBADMINISTRADORA", "Combustível"),
    # Nomes de posto sem a palavra "posto" ("SHELL BOX", "XYZ COMBUSTIVEIS
    # LTDA"). Também decidem a divisão do nome antigo "Transporte & Combustível".
    ("COMBUSTIV", "Combustível"),
    ("SHELL", "Combustível"),
    ("PETROBRAS", "Combustível"),
    ("BARBEARIA", "Cuidados pessoais"),
    ("CABELO", "Cuidados pessoais"),
    ("NAUTINST", "Educação"),
    ("CENTRO DE TR", "Educação"),
    ("PLANET PARK", "Lazer"),
    ("ANANIN PARK", "Lazer"),
    ("CINESYSTEM", "Lazer"),
    ("CINEMARK", "Lazer"),
    ("KINOPLEX", "Lazer"),
]

# ── Fallback pela categoria da operadora (MCC) -> categoria-alvo ──────────────
CATEGORY_MAP = {
    "Restaurante / Lanchonete / Bar": "Alimentação",
    "Supermercados / Mercearia / Padarias / Lojas de Conveniência": "Mercado",
    "Entretenimento": "Lazer",
    "Recreativo": "Lazer",
    "Arte / Artesanato / Passatempo": "Lazer",
    "Vestuário / Roupas": "Compras",
    "Departamento / Desconto": "Compras",
    "Especialidade varejo": "Compras",
    "Serviços Profissionais": "Compras",
    "Empresa para empresa": "Compras",
    "Construção": "Casa & Construção",
    "Casa / Escritório Mobiliário": "Casa & Construção",
    "Materiais de construção para casa": "Casa & Construção",
    "Transporte": "Transporte",
    "Relacionados a Automotivo": "Transporte",
    # Telecom é serviço genuinamente recorrente → assinatura.
    "Serviços de telecomunicações": "Assinaturas",
    # MCC "grab-bag": misturam SaaS real (pego pelas MERCHANT_RULES) com
    # marketplaces e compras avulsas. NÃO devem cair automaticamente em
    # "Assinaturas": o que não casar uma marca conhecida vai para revisão do
    # usuário, evitando classificar compra como assinatura.
    "Empresa serviços": REVIEW_SENTINEL,
    "Marketing Direto": REVIEW_SENTINEL,
    "Elétrico": REVIEW_SENTINEL,
    "Serviços pessoais": "Cuidados pessoais",
    "Educacional": "Educação",
    "Associação": "Educação",
    "Anuidade": "Tarifas & Anuidade",
    "T&E": "Alimentação",
    # Preservadas (mapeiam para si mesmas):
    "Pagamento de Cartão": "Pagamento de Cartão",
    "Créditos e Estornos": "Créditos e Estornos",
}

# ── Nomes antigos da fatura -> nomes de sistema ──────────────────────────────
# A fatura usava nomes próprios até 31/10/2026. Eles seguem gravados em regras
# aprendidas (``card_category_rules``) e em lançamentos ainda não migrados, e
# por isso continuam reconhecidos. ``None`` marca o único nome que se divide:
# ``Transporte & Combustível`` vira ``Combustível`` quando a descrição é de
# posto e ``Transporte`` no resto (ver ``nome_unificado``).
NOMES_LEGADOS: dict[str, str | None] = {
    "Compras / Varejo": "Compras",
    "Assinaturas & Serviços digitais": "Assinaturas",
    "Saúde & Bem-estar": "Saúde",
    "Lazer & Entretenimento": "Lazer",
    "Educação & Profissional": "Educação",
    "Transporte & Combustível": None,
}

#: Estabelecimentos de combustível, tirados das próprias MERCHANT_RULES: a
#: divisão do nome antigo segue exatamente a regra que classifica o novo.
_TERMOS_COMBUSTIVEL = tuple(kw for kw, cat in MERCHANT_RULES if cat == "Combustível")


def nome_unificado(nome: object, descricao: object = "") -> str:
    """Nome de sistema para um nome de categoria, antigo ou atual.

    ``descricao`` só importa para ``Transporte & Combustível``: o lançamento de
    posto vai para ``Combustível``, o resto para ``Transporte``. Nome que não é
    legado volta como veio (sem espaços nas pontas).
    """
    texto = str(nome or "").strip()
    if texto not in NOMES_LEGADOS:
        return texto
    destino = NOMES_LEGADOS[texto]
    if destino is not None:
        return destino
    desc_up = _norm_up(descricao)
    if any(_norm_up(kw) in desc_up for kw in _TERMOS_COMBUSTIVEL):
        return "Combustível"
    return "Transporte"


# ── Termos estruturais (detecção por descrição) ──────────────────────────────
_PAYMENT_TERMS = (
    "pag fatura", "pagamento fatura", "inclusao de pagamento",
    "debito em conta", "pagto debito",
)
_REFUND_TERMS = ("estorno", "reembolso")
_ANUIDADE_TERMS = ("anuidade",)


def _norm_up(value: object) -> str:
    """Normaliza para MAIÚSCULO sem acento (usado no casamento de merchant)."""
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).upper().strip()


def _norm_low(value: object) -> str:
    """Normaliza para minúsculo sem acento (usado nos termos estruturais)."""
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).lower().strip()


def classify(
    raw_category: object,
    description: object,
    value_brl: float,
    *,
    user_rules: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """
    Retorna (categoria_nova, regra_que_decidiu).

    user_rules: lista de (palavra_chave, categoria) aprendidas pelo usuário; têm
    prioridade sobre as regras internas por estabelecimento.
    """
    desc_low = _norm_low(description)
    desc_up = _norm_up(description)

    # 1) Estrutural
    if any(t in desc_low for t in _PAYMENT_TERMS):
        return "Pagamento de Cartão", "estrutural:pagamento"
    if float(value_brl or 0) < 0 or any(t in desc_low for t in _REFUND_TERMS):
        return "Créditos e Estornos", "estrutural:estorno"
    if any(t in desc_low for t in _ANUIDADE_TERMS):
        return "Tarifas & Anuidade", "estrutural:anuidade"

    # 2) Regras do usuário (aprendidas). A regra gravada antes da unificação
    # guarda o nome antigo; a palavra-chave decide a divisão de Transporte.
    for palavra, categoria in (user_rules or []):
        pk = _norm_up(palavra)
        categoria = nome_unificado(categoria, palavra)
        if pk and pk in desc_up and categoria in CATEGORY_TYPE:
            return categoria, f"usuario:{palavra}"

    # 3) Regras internas por estabelecimento
    for kw, nc in MERCHANT_RULES:
        if _norm_up(kw) in desc_up:
            return nc, f"merchant:{kw}"

    # 4) Fallback pela categoria da operadora
    raw = str(raw_category or "").strip()
    if raw in CATEGORY_MAP:
        return CATEGORY_MAP[raw], "categoria"

    # 4.5) Idempotência: se já vier numa categoria-alvo válida, mantém — e o
    # nome antigo da fatura vira o de sistema.
    if raw in NOMES_LEGADOS:
        return nome_unificado(raw, description), "nome-legado"
    if raw in CATEGORY_TYPE and raw != REVIEW_SENTINEL:
        return raw, "ja-catalogada"

    # 5) Não catalogado -> revisão manual
    return REVIEW_SENTINEL, "sem-regra"


# ── Sugestão pelo Jev (modelo de decisão) — PILOTO, nunca decide sozinho ─────
# O Jev escolhe uma opção de uma lista fechada e devolve a probabilidade. Aqui
# só se montam a pergunta e a leitura da resposta; a chamada HTTP mora em
# core.jev. As categorias estruturais ficam de fora: pagamento, estorno e
# anuidade já são decididos pela descrição em classify(), sem ambiguidade.
DESCRICAO_CATEGORIA_JEV = {
    "Alimentação": "Restaurante, lanchonete, bar, padaria para consumo no local, delivery de comida",
    "Mercado": "Supermercado, atacadista, hortifruti, mercearia: compra de mantimentos para casa",
    "Compras": "Loja física ou online, marketplace, roupas, eletrônicos, presentes, compra avulsa de produto",
    "Assinaturas": "Cobrança recorrente de software, streaming, nuvem, telefonia, programa de pontos",
    "Saúde": "Farmácia, médico, exame, plano de saúde, academia",
    "Cuidados pessoais": "Barbearia, salão de beleza, estética, cosméticos",
    "Casa & Construção": "Material de construção, reforma, móveis, decoração, utilidades para casa",
    "Transporte": "Pedágio, estacionamento, aplicativo de transporte, oficina, peças do carro",
    "Combustível": "Posto de combustível, abastecimento",
    "Lazer": "Cinema, parque, show, viagem, hotel, jogos, passeio",
    "Educação": "Curso, escola, livro técnico, conselho ou associação profissional",
}

_INSTRUCAO_JEV = ("Em qual categoria de gasto pessoal se encaixa esta compra no cartão de "
                  "crédito, julgando pelo nome do estabelecimento em `estabelecimento`?")


def pergunta_jev_categoria(description: object, value_brl: float) -> tuple[dict, dict]:
    """Devolve (state, questions) para o Jev escolher a categoria de um lançamento."""
    state = {
        "estabelecimento": " ".join(str(description or "").split()),
        "valor_brl": round(abs(float(value_brl or 0.0)), 2),
    }
    questions = {"categoria": {
        "type": "choice",
        "instructions": _INSTRUCAO_JEV,
        "criteria": dict(DESCRICAO_CATEGORIA_JEV),
    }}
    return state, questions


def ler_resposta_jev_categoria(answers: dict) -> tuple[str, float | None]:
    """Extrai (categoria, probabilidade) da resposta do Jev.

    Categoria fora da taxonomia vira REVIEW_SENTINEL. A probabilidade é a da
    opção escolhida; sem ela, cai na `confidence`; sem nenhuma, None — ausência
    de medida não é confiança zero nem confiança total.
    """
    ans = (answers or {}).get("categoria")
    if not isinstance(ans, dict):
        return REVIEW_SENTINEL, None
    cat = ans.get("choice")
    if cat not in DESCRICAO_CATEGORIA_JEV:
        return REVIEW_SENTINEL, None
    prob = None
    probs = ans.get("probabilities")
    if isinstance(probs, dict) and isinstance(probs.get(cat), (int, float)):
        prob = float(probs[cat])
    elif isinstance(ans.get("confidence"), (int, float)):
        prob = float(ans["confidence"])
    return cat, prob
