"""Plano de contas da empresa e regras de classificação do extrato.

A categoria de empresa não é a do financeiro pessoal: "Pró-labore" e
"Distribuição de lucros" são saídas da empresa e entradas da pessoa, e o DAS é
dedução da receita, não despesa. Por isso a lista é própria, e cada categoria
diz em que linha da DRE cai.

DRE gerencial em regime de CAIXA (o extrato só sabe quando o dinheiro passou):

    Receita bruta
    (-) Deduções (DAS, impostos sobre a receita e devoluções a clientes)
    = Receita líquida
    (-) Custos
    = Lucro bruto
    (-) Despesas operacionais
    (-) Pessoal
    = Resultado operacional
    (+/-) Resultado financeiro
    = Resultado do período

``fora_do_resultado`` (aporte, distribuição, transferência, empréstimo,
aplicação) mexe no caixa e não no lucro: aparece no fluxo, nunca na DRE.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Categoria:
    codigo: str
    nome: str
    grupo: str


GRUPOS_DRE: tuple[tuple[str, str], ...] = (
    ("receita_bruta", "Receita bruta"),
    ("deducoes", "Deduções da receita"),
    ("custos", "Custos"),
    ("despesas_operacionais", "Despesas operacionais"),
    ("pessoal", "Pessoal"),
    ("financeiro", "Resultado financeiro"),
    ("fora_do_resultado", "Fora do resultado"),
)
NOME_GRUPO = dict(GRUPOS_DRE)

CATEGORIAS: tuple[Categoria, ...] = (
    Categoria("receita_servicos", "Receita de serviços", "receita_bruta"),
    Categoria("receita_vendas", "Receita de vendas", "receita_bruta"),
    Categoria("outras_receitas", "Outras receitas operacionais", "receita_bruta"),
    Categoria("das", "DAS / Simples Nacional", "deducoes"),
    # Dinheiro devolvido a cliente: estorno de serviço, venda cancelada. É
    # dedução da receita (não despesa) e sai da receita bruta do Simples (LC
    # 123/2006, art. 3º, § 1º: "não incluídas as vendas canceladas").
    Categoria("devolucoes", "Devoluções e cancelamentos a clientes", "deducoes"),
    Categoria("outros_impostos", "Outros impostos sobre a receita", "deducoes"),
    Categoria("custo_servicos", "Custo de serviços (terceiros)", "custos"),
    Categoria("custo_mercadorias", "Custo de mercadorias", "custos"),
    Categoria("aluguel", "Aluguel e condomínio", "despesas_operacionais"),
    Categoria("software", "Software e assinaturas", "despesas_operacionais"),
    Categoria("contabilidade", "Contabilidade", "despesas_operacionais"),
    Categoria("marketing", "Marketing e publicidade", "despesas_operacionais"),
    Categoria("tarifas", "Tarifas bancárias", "despesas_operacionais"),
    Categoria("telefone_internet", "Telefone e internet", "despesas_operacionais"),
    Categoria("viagens", "Viagens e deslocamento", "despesas_operacionais"),
    Categoria("material", "Material e escritório", "despesas_operacionais"),
    Categoria("outras_despesas", "Outras despesas operacionais", "despesas_operacionais"),
    Categoria("pro_labore", "Pró-labore", "pessoal"),
    Categoria("salarios", "Salários", "pessoal"),
    Categoria("encargos", "Encargos (INSS, FGTS)", "pessoal"),
    Categoria("beneficios", "Benefícios", "pessoal"),
    Categoria("receitas_financeiras", "Receitas financeiras (rendimentos)", "financeiro"),
    Categoria("despesas_financeiras", "Despesas financeiras (juros, IOF)", "financeiro"),
    Categoria("distribuicao_lucros", "Distribuição de lucros", "fora_do_resultado"),
    Categoria("aporte_socio", "Aporte de sócio", "fora_do_resultado"),
    Categoria("transferencia", "Transferência entre contas", "fora_do_resultado"),
    Categoria("emprestimo", "Empréstimo (entrada ou pagamento)", "fora_do_resultado"),
    Categoria("aplicacao", "Aplicação / resgate", "fora_do_resultado"),
)
POR_CODIGO = {c.codigo: c for c in CATEGORIAS}

#: Folha para o Fator R do Simples: pró-labore, salários e encargos (INSS
#: patronal e FGTS). Benefício não entra.
CODIGOS_FOLHA = ("pro_labore", "salarios", "encargos")

# (palavras, código, só para saída/entrada/ambas). A primeira que casar vence;
# regra aprendida do usuário vem antes de todas estas.
_REGRAS_PADRAO: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("simples nacional", "pgdas", "documento de arrecadacao do simples", "das simples"),
     "das", "saida"),
    (("pro labore", "prolabore"), "pro_labore", "saida"),
    (("fgts", "inss", "gps", "esocial", "dae"), "encargos", "saida"),
    (("tarifa", "mensalidade do pacote", "cesta de servicos"), "tarifas", "saida"),
    (("contabil", "contabilidade", "contador"), "contabilidade", "saida"),
    (("iof", "juros"), "despesas_financeiras", "saida"),
    (("rendimento", "remuneracao da conta", "rend pago"), "receitas_financeiras", "entrada"),
    (("aplicacao", "resgate"), "aplicacao", "ambas"),
    # Por último, e só como SUGESTÃO na prévia: numa empresa de serviços quase
    # todo crédito recebido é faturamento. Aporte do sócio também chega como
    # Pix recebido -- por isso a regra perde para qualquer outra e o usuário
    # vê e troca na prévia; a escolha dele para a contraparte vira regra.
    (("pix recebido", "ted recebida", "transferencia recebida", "recebimento",
      "credito recebido", "boleto recebido"), "receita_servicos", "entrada"),
)


def normalizar(texto: object) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = base.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", base).split())


def _contem(texto_norm: str, termo: str) -> bool:
    return re.search(rf"\b{re.escape(termo)}\b", texto_norm) is not None


def sugerir_categoria(mov: dict, regras_aprendidas: dict[str, str] | None = None) -> str | None:
    """Código sugerido para um movimento, ou ``None`` (fica pendente).

    Regra aprendida: contraparte normalizada -> código, gravada quando o
    usuário classifica um movimento na importação. Ela vence a regra padrão,
    porque diz o que ESTE usuário faz com ESTA contraparte.

    A regra aprendida é por contraparte, sem sentido, e o mesmo cliente que
    paga também pode receber dinheiro de volta. Por isso o sentido do
    movimento decide entre receita e devolução (``categoria_no_sentido``).
    """
    contraparte = normalizar(mov.get("contraparte"))
    sentido = "entrada" if float(mov.get("valor") or 0) > 0 else "saida"
    if regras_aprendidas and contraparte and contraparte in regras_aprendidas:
        codigo = categoria_no_sentido(regras_aprendidas[contraparte], sentido)
        if codigo in POR_CODIGO:
            return codigo
    texto = normalizar(f"{mov.get('descricao', '')} {mov.get('contraparte', '')}")
    for termos, codigo, so_para in _REGRAS_PADRAO:
        if so_para != "ambas" and so_para != sentido:
            continue
        if any(_contem(texto, t) for t in termos):
            return codigo
    return None


def categoria_no_sentido(codigo: str | None, sentido: str) -> str | None:
    """A categoria aprendida para a contraparte, ajustada ao sentido do dinheiro.

    - receita (grupo Receita bruta) numa SAÍDA vira devolução ao cliente: o
      Pix enviado a quem paga a empresa não é faturamento negativo, é dedução;
    - devolução numa ENTRADA não vale (``None``): dinheiro que chega do
      cliente é receita, e a regra padrão de crédito recebido decide qual.
    """
    cat = POR_CODIGO.get(codigo or "")
    if cat is None:
        return None
    if sentido == "saida" and cat.grupo == "receita_bruta":
        return "devolucoes"
    if sentido == "entrada" and cat.codigo == "devolucoes":
        return None
    return cat.codigo


def rotulo(codigo: str | None) -> str:
    return POR_CODIGO[codigo].nome if codigo in POR_CODIGO else "Sem categoria"
