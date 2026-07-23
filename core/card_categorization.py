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
"""
from __future__ import annotations

import re
import unicodedata

# ── Sentinela para itens não catalogados (revisão manual) ─────────────────────
REVIEW_SENTINEL = "A revisar"

# ── Taxonomia-alvo (13) + tipo de cada categoria ─────────────────────────────
# 'Pagamento de Cartão' e 'Créditos e Estornos' são preservados: o primeiro é
# hardcoded em SQL do app (_SQL_GASTOS_CARTAO_MENSAL) e ambos representam transfer.
CATEGORY_TYPE = {
    "Alimentação": "expense",
    "Mercado": "expense",
    "Compras / Varejo": "expense",
    "Assinaturas & Serviços digitais": "expense",
    "Saúde & Bem-estar": "expense",
    "Cuidados pessoais": "expense",
    "Casa & Construção": "expense",
    "Transporte & Combustível": "expense",
    "Lazer & Entretenimento": "expense",
    "Educação & Profissional": "expense",
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
    ("WELLHUB", "Saúde & Bem-estar"),
    ("GYMPASS", "Saúde & Bem-estar"),
    ("CLAUDE", "Assinaturas & Serviços digitais"),
    ("ANTHROPIC", "Assinaturas & Serviços digitais"),
    ("OPENAI", "Assinaturas & Serviços digitais"),
    ("CHATGPT", "Assinaturas & Serviços digitais"),
    ("SUPABASE", "Assinaturas & Serviços digitais"),
    ("BRAPI", "Assinaturas & Serviços digitais"),
    # 123Comprou (MERCADO*123COMPROU) é MARKETPLACE, não assinatura → varejo.
    ("123COMPROU", "Compras / Varejo"),
    ("LIVELO", "Assinaturas & Serviços digitais"),
    ("SMILES", "Assinaturas & Serviços digitais"),
    ("IFOOD CLUB", "Assinaturas & Serviços digitais"),
    ("JUSBRASIL", "Assinaturas & Serviços digitais"),
    ("GOOGLE", "Assinaturas & Serviços digitais"),
    ("IFOOD", "Alimentação"),
    ("TUCUPI", "Alimentação"),
    ("PESCADO", "Alimentação"),
    ("ATACADAO", "Mercado"),
    ("ASSAI", "Mercado"),
    ("ATACADISTA", "Mercado"),
    ("TIP HOME", "Casa & Construção"),
    ("HOME CENTER", "Casa & Construção"),
    ("CASAS BAHIA", "Compras / Varejo"),
    ("TEMU", "Compras / Varejo"),
    ("MERCADOLIVRE", "Compras / Varejo"),
    ("MERCADO LIVRE", "Compras / Varejo"),
    ("KALUNGA", "Compras / Varejo"),
    ("QUADROSDECORA", "Compras / Varejo"),
    ("POSTO", "Transporte & Combustível"),
    ("PBADMINISTRADORA", "Transporte & Combustível"),
    ("BARBEARIA", "Cuidados pessoais"),
    ("CABELO", "Cuidados pessoais"),
    ("NAUTINST", "Educação & Profissional"),
    ("CENTRO DE TR", "Educação & Profissional"),
    ("PLANET PARK", "Lazer & Entretenimento"),
    ("ANANIN PARK", "Lazer & Entretenimento"),
    ("CINESYSTEM", "Lazer & Entretenimento"),
    ("CINEMARK", "Lazer & Entretenimento"),
    ("KINOPLEX", "Lazer & Entretenimento"),
]

# ── Fallback pela categoria da operadora (MCC) -> categoria-alvo ──────────────
CATEGORY_MAP = {
    "Restaurante / Lanchonete / Bar": "Alimentação",
    "Supermercados / Mercearia / Padarias / Lojas de Conveniência": "Mercado",
    "Entretenimento": "Lazer & Entretenimento",
    "Recreativo": "Lazer & Entretenimento",
    "Arte / Artesanato / Passatempo": "Lazer & Entretenimento",
    "Vestuário / Roupas": "Compras / Varejo",
    "Departamento / Desconto": "Compras / Varejo",
    "Especialidade varejo": "Compras / Varejo",
    "Serviços Profissionais": "Compras / Varejo",
    "Empresa para empresa": "Compras / Varejo",
    "Construção": "Casa & Construção",
    "Casa / Escritório Mobiliário": "Casa & Construção",
    "Materiais de construção para casa": "Casa & Construção",
    "Transporte": "Transporte & Combustível",
    "Relacionados a Automotivo": "Transporte & Combustível",
    # Telecom é serviço genuinamente recorrente → assinatura.
    "Serviços de telecomunicações": "Assinaturas & Serviços digitais",
    # MCC "grab-bag": misturam SaaS real (pego pelas MERCHANT_RULES) com
    # marketplaces e compras avulsas. NÃO devem cair automaticamente em
    # "Assinaturas": o que não casar uma marca conhecida vai para revisão do
    # usuário, evitando classificar compra como assinatura.
    "Empresa serviços": REVIEW_SENTINEL,
    "Marketing Direto": REVIEW_SENTINEL,
    "Elétrico": REVIEW_SENTINEL,
    "Serviços pessoais": "Cuidados pessoais",
    "Educacional": "Educação & Profissional",
    "Associação": "Educação & Profissional",
    "Anuidade": "Tarifas & Anuidade",
    "T&E": "Alimentação",
    # Preservadas (mapeiam para si mesmas):
    "Pagamento de Cartão": "Pagamento de Cartão",
    "Créditos e Estornos": "Créditos e Estornos",
}

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

    # 2) Regras do usuário (aprendidas)
    for palavra, categoria in (user_rules or []):
        pk = _norm_up(palavra)
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

    # 4.5) Idempotência: se já vier numa categoria-alvo válida, mantém.
    if raw in CATEGORY_TYPE and raw != REVIEW_SENTINEL:
        return raw, "ja-catalogada"

    # 5) Não catalogado -> revisão manual
    return REVIEW_SENTINEL, "sem-regra"
