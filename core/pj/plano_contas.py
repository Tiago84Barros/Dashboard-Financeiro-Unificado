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

#: Rótulo de "nenhuma categoria" em toda tela. É reservado: uma categoria
#: própria com esse nome capturava a opção "Sem categoria" da prévia e da
#: correção, e todo movimento pendente entrava na DRE por ela (achado do Codex
#: no PR #687).
SEM_CATEGORIA = "Sem categoria"


def nome_reservado(nome: str) -> bool:
    return normalizar(nome) == normalizar(SEM_CATEGORIA)


#: Código das categorias criadas pelo usuário. O prefixo impede colisão com
#: as fixas, inclusive com uma fixa que o app venha a ganhar depois.
PREFIXO_PROPRIA = "u_"


@dataclass(frozen=True)
class PlanoDeContas:
    """As categorias fixas mais as criadas pelo dono (``pj_categorias``).

    Toda tela que lista, valida ou rotula categoria parte daqui, e não de
    ``CATEGORIAS``: uma lista montada só com as fixas some com a categoria
    nova do seletor, da DRE ou do filtro, e o lançamento dela vira "Sem
    categoria" na tela que esqueceu.
    """
    proprias: tuple[Categoria, ...] = ()

    @property
    def todas(self) -> tuple[Categoria, ...]:
        """Na ordem da DRE; dentro de cada grupo, as fixas antes das próprias."""
        ordem = {g: i for i, (g, _) in enumerate(GRUPOS_DRE)}
        return tuple(sorted((*CATEGORIAS, *self.proprias),
                            key=lambda c: (ordem.get(c.grupo, 99),
                                           c.codigo.startswith(PREFIXO_PROPRIA))))

    @property
    def por_codigo(self) -> dict[str, Categoria]:
        return {c.codigo: c for c in self.todas}

    def nomes(self) -> list[str]:
        return [c.nome for c in self.todas]

    def codigo_por_nome(self) -> dict[str, str]:
        """Nome -> código. "Sem categoria" nunca vira código, nem se alguma
        categoria tiver esse nome: quem traduz a escolha da tela recebe None."""
        return {c.nome: c.codigo for c in self.todas if not nome_reservado(c.nome)}

    def rotulo(self, codigo: str | None) -> str:
        cat = self.por_codigo.get(codigo or "")
        return cat.nome if cat else SEM_CATEGORIA

    def do_grupo(self, grupo: str) -> list[Categoria]:
        return [c for c in self.todas if c.grupo == grupo]


PLANO_PADRAO = PlanoDeContas()


def codigo_para_propria(nome: str, existentes: set[str]) -> str:
    """Código estável a partir do nome: ``u_`` + nome normalizado, com sufixo
    numérico se já houver outro igual. Renomear não muda o código, então os
    lançamentos e as regras continuam apontando para a mesma categoria."""
    base = PREFIXO_PROPRIA + ("_".join(normalizar(nome).split()) or "categoria")[:40]
    codigo, n = base, 2
    while codigo in existentes or codigo in POR_CODIGO:
        codigo, n = f"{base}_{n}", n + 1
    return codigo

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

# Entrada que devolve dinheiro que a empresa pagou: estorno de tarifa, de
# compra, reembolso de fornecedor. NÃO é faturamento -- cair na regra genérica
# de "Pix recebido" punha o estorno na receita bruta e aumentava o DAS. A
# entrada com um destes termos é lida pelas regras de SAÍDA ("Estorno de
# tarifa" volta para Tarifas, abatendo a despesa) e, sem regra, fica pendente.
_TERMOS_ESTORNO = (
    "estorno", "estornado", "estornada", "extorno", "devolucao", "devolvido",
    "devolvida", "reembolso", "reembolsado", "ressarcimento", "cashback", "chargeback",
)


def normalizar(texto: object) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = base.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", base).split())


def _contem(texto_norm: str, termo: str) -> bool:
    return re.search(rf"\b{re.escape(termo)}\b", texto_norm) is not None


def sugerir_categoria(mov: dict, regras_aprendidas: dict[str, str] | None = None,
                      plano: PlanoDeContas = PLANO_PADRAO) -> str | None:
    """Código sugerido para um movimento, ou ``None`` (fica pendente).

    Regra aprendida: contraparte normalizada -> código, gravada quando o
    usuário classifica um movimento na importação. Ela vence a regra padrão,
    porque diz o que ESTE usuário faz com ESTA contraparte.

    A regra aprendida é por contraparte, sem sentido, e o mesmo cliente que
    paga também pode receber dinheiro de volta. Por isso o sentido do
    movimento decide entre receita e devolução (``categoria_no_sentido``).

    Estorno de fornecedor conhecido segue a regra dele (o reembolso da
    imobiliária entra em Aluguel e abate a despesa: é o tratamento certo).
    Estorno de quem não tem regra não vira receita (``_TERMOS_ESTORNO``).
    """
    contraparte = normalizar(mov.get("contraparte"))
    sentido = "entrada" if float(mov.get("valor") or 0) > 0 else "saida"
    if regras_aprendidas and contraparte and contraparte in regras_aprendidas:
        codigo = categoria_no_sentido(regras_aprendidas[contraparte], sentido, plano)
        if codigo in plano.por_codigo:
            return codigo
    texto = normalizar(f"{mov.get('descricao', '')} {mov.get('contraparte', '')}")
    if sentido == "entrada" and any(_contem(texto, t) for t in _TERMOS_ESTORNO):
        sentido = "saida"  # estorno desfaz uma saída: vale a regra dela, nunca a de receita
    for termos, codigo, so_para in _REGRAS_PADRAO:
        if so_para != "ambas" and so_para != sentido:
            continue
        if any(_contem(texto, t) for t in termos):
            return codigo
    return None


def categoria_no_sentido(codigo: str | None, sentido: str,
                         plano: PlanoDeContas = PLANO_PADRAO) -> str | None:
    """A categoria aprendida para a contraparte, ajustada ao sentido do dinheiro.

    - receita (grupo Receita bruta) numa SAÍDA vira devolução ao cliente: o
      Pix enviado a quem paga a empresa não é faturamento negativo, é dedução;
    - devolução numa ENTRADA não vale (``None``): dinheiro que chega do
      cliente é receita, e a regra padrão de crédito recebido decide qual.
    """
    cat = plano.por_codigo.get(codigo or "")
    if cat is None:
        return None
    if sentido == "saida" and cat.grupo == "receita_bruta":
        return "devolucoes"
    if sentido == "entrada" and cat.codigo == "devolucoes":
        return None
    return cat.codigo


def rotulo(codigo: str | None) -> str:
    return POR_CODIGO[codigo].nome if codigo in POR_CODIGO else SEM_CATEGORIA
