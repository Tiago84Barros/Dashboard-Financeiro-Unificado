"""O bloco de mercado que faltava em todo chat: macro, notícias e conjuntura.

O defeito que este módulo corrige
---------------------------------
Perguntado sobre a carteira, o app respondia: *"O contexto fornecido contém
estritamente dados quantitativos e operacionais da sua carteira, não contendo
notícias recentes, dados macroeconômicos ou fatos relevantes do cenário
brasileiro atual."* A frase estava correta sobre o prompt e errada sobre o
banco. ``core.noticias`` coleta, classifica e publica noticiário; ``public.macro``
guarda a série anual de Selic, IPCA, câmbio, PIB e dívida; ``core.conjuntura``
junta os dois com procedência e corte point-in-time. Nada disso chegava a três
dos sete construtores de contexto — carteira, portfólio global e financeiro.

Não é um motor novo. :func:`core.conjuntura.bloco_para_prompt` já monta o bloco
por ativo, e quatro telas já o chamavam. O que este módulo acrescenta são as
duas peças que faltavam para as outras três:

1. **Roteamento de classe.** A Análise do Portfólio fala em ``acoes``, ``fiis``,
   ``exterior`` e ``tesouro``; a conjuntura fala em ``b3``, ``fii`` e ``us``.
   Sem a tradução, cada tela inventaria a sua — e uma delas inventaria errado.
2. **Macro de país.** Impacto macro por ativo não responde "como está o país".
   Selic, IPCA e câmbio estão em ``public.macro``, no Supabase, alcançáveis de
   produção, e são o único bloco macro que faz sentido no chat de Controle
   Financeiro, onde não há ativo nenhum para carregar impacto setorial.

Por que a ausência continua sendo escrita
-----------------------------------------
Um bloco que some quando a fonte falha ensina a LLM a supor. Aqui toda ausência
vira frase — "sem noticiário publicado para estes ativos", "macro de país
indisponível" — porque "não medimos" e "medimos e deu neutro" autorizam
conclusões opostas, e só o texto separa as duas.

Grão anual, e o que isso proíbe
--------------------------------
``public.macro`` é anual. Ela responde "em que regime de juro e inflação este
ano se passou" e **não** responde "o que o Copom fez no mês passado". A
limitação viaja junto no texto em vez de ficar implícita, para que a LLM não
leia a Selic de 2026 como a taxa de ontem.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Tradução entre a classe da Análise do Portfólio e a classe da conjuntura.
#: ``tesouro`` não aparece de propósito: título público não tem setor, não tem
#: noticiário por emissor e não tem impacto macro setorial — o que responde por
#: ele é :func:`bloco_macro_pais`, e forçá-lo em uma das três classes de renda
#: variável produziria cobertura fantasma.
ASSET_CLASS_POR_CLASSE = {
    "acoes": "b3",
    "acao": "b3",
    "fiis": "fii",
    "fii": "fii",
    "exterior": "us",
    "us": "us",
}

#: Campos de ``public.macro`` que entram no bloco, na ordem de leitura, com o
#: rótulo e como cada um deve ser formatado. ``pct`` marca os que estão em
#: percentual (gravados ora como 4,5 ora como 0,045 — ver ``_pct_macro``).
_CAMPOS_MACRO = (
    ("selic", "Selic", "pct"),
    ("ipca", "IPCA", "pct"),
    ("juros_real_ex_ante", "Juro real ex-ante", "pct"),
    ("cambio", "USD/BRL", "num"),
    ("pib", "PIB", "pct"),
    ("divida_publica", "Dívida pública", "pct"),
    ("balanca_comercial", "Balança comercial", "num"),
    ("icc", "Índice de confiança do consumidor", "num"),
)


def _num(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero and abs(numero) != float("inf") else None


def _pct_macro(valor) -> str:
    """Percentual tolerante à dupla convenção de ``public.macro``.

    A tabela grava Selic ora como ``0.1075`` ora como ``10.75``, e
    ``core.b3_db.load_macro_history`` só normaliza a Selic. Aplicar aqui a mesma
    regra (``|x| <= 1`` é fração) mantém as colunas comparáveis entre si. Onde a
    heurística pode errar — inflação de 0,8% ao ano, por exemplo — quem lê tem a
    ressalva de unidade escrita no fim do bloco.
    """
    numero = _num(valor)
    if numero is None:
        return "ausente"
    return f"{numero * 100:.2f}%" if abs(numero) <= 1 else f"{numero:.2f}%"


def _fmt(valor, forma: str) -> str:
    if forma == "pct":
        return _pct_macro(valor)
    numero = _num(valor)
    return "ausente" if numero is None else f"{numero:,.2f}".replace(",", "@") \
        .replace(".", ",").replace("@", ".")


def bloco_macro_pais(historico=None, *, anos: int = 3) -> str:
    """Série anual de ``public.macro`` como texto, com variação ano a ano.

    ``historico`` permite injetar o dicionário já carregado pela tela em vez de
    consultar de novo — a mesma disciplina do resto dos construtores de
    contexto: quem já leu, passa; quem não leu, esta função lê.

    Devolve sempre texto. Banco fora do ar vira frase de indisponibilidade, não
    bloco vazio: um prompt sem o bloco é indistinguível de um país sem dados.
    """
    if historico is None:
        try:
            from core.b3_db import load_macro_history

            historico = load_macro_history()
        except Exception as exc:  # noqa: BLE001 - ausência declarada
            logger.warning("macro de país indisponível: %s", exc)
            return ("MACRO DO PAÍS: indisponível nesta sessão "
                    f"({str(exc).splitlines()[0].strip()}). Não trate isso como "
                    "cenário estável nem como ausência de risco macro.")
    if not historico:
        return ("MACRO DO PAÍS: nenhuma linha em public.macro. Não trate isso "
                "como cenário estável.")

    ordenados = sorted(historico.keys(), reverse=True)[:max(anos, 1)]
    linhas = ["MACRO DO PAÍS (public.macro, série anual):"]
    for ano in sorted(ordenados):
        dados = historico.get(ano) or {}
        partes = [f"{rotulo}={_fmt(dados.get(campo), forma)}"
                  for campo, rotulo, forma in _CAMPOS_MACRO
                  if dados.get(campo) is not None]
        if partes:
            linhas.append(f"- {ano}: " + ", ".join(partes))
    if len(linhas) == 1:
        return ("MACRO DO PAÍS: linhas existem em public.macro mas nenhum "
                "indicador veio preenchido nos últimos anos.")

    recente, anterior = (ordenados[0], ordenados[1] if len(ordenados) > 1 else None)
    if anterior is not None:
        deltas = []
        for campo, rotulo, forma in _CAMPOS_MACRO:
            novo = _num((historico.get(recente) or {}).get(campo))
            velho = _num((historico.get(anterior) or {}).get(campo))
            if novo is None or velho is None:
                continue
            direcao = "subiu" if novo > velho else ("caiu" if novo < velho else "estável")
            deltas.append(f"{rotulo} {direcao} ({_fmt(velho, forma)} → "
                          f"{_fmt(novo, forma)})")
        if deltas:
            linhas.append(f"- Variação {anterior}→{recente}: " + "; ".join(deltas))

    linhas.append(
        "- GRÃO: anual. Esta série descreve o regime do ano, não a decisão de "
        "política monetária mais recente. Não a apresente como a taxa de hoje.")
    linhas.append(
        "- UNIDADES conforme gravadas em public.macro; confira a escala antes "
        "de comparar com taxa contratada de um título específico.")
    return "\n".join(linhas)


def bloco_conjuntura(*, classe=None, asset_class=None, ativos=None,
                     estruturais=None, max_itens: int = 12) -> str:
    """Noticiário e impacto macro por ativo, roteando a classe da tela.

    ``classe`` aceita o vocabulário da Análise do Portfólio (``acoes``,
    ``fiis``, ``exterior``, ``tesouro``); ``asset_class`` aceita o da conjuntura
    (``b3``, ``fii``, ``us``). Passar os dois é redundante, e ``asset_class``
    ganha.

    ``ativos`` é ``{símbolo: setor}``. Setor vazio é aceito de propósito: sem
    ele o impacto macro setorial não sai, mas o noticiário por símbolo sai — e
    meio bloco declarado vale mais que bloco nenhum.
    """
    destino = asset_class or ASSET_CLASS_POR_CLASSE.get(str(classe or "").lower())
    if destino is None:
        return ""
    ativos = {str(k).strip().upper(): str(v or "")
              for k, v in (ativos or {}).items() if str(k).strip()}
    if not ativos:
        return ""
    try:
        from core.conjuntura import bloco_para_prompt

        texto = bloco_para_prompt(asset_class=destino, ativos=ativos,
                                  estruturais=estruturais, max_itens=max_itens)
    except Exception as exc:  # noqa: BLE001 - montar prompt não derruba a tela
        logger.exception("bloco conjuntural indisponível")
        return ("CONTEXTO CONJUNTURAL: não foi possível montá-lo "
                f"({str(exc).splitlines()[0].strip()}). Não trate isso como "
                "ausência de notícias nem como conjuntura neutra.")
    return texto or ("CONTEXTO CONJUNTURAL: sem noticiário nem impacto macro "
                     "publicado para estes ativos. Ausência de notícia não é "
                     "notícia neutra.")


def bloco_mercado(*, classe=None, asset_class=None, ativos=None,
                  historico_macro=None, estruturais=None,
                  com_macro_pais: bool = True, max_itens: int = 12) -> str:
    """Conjuntura por ativo + macro de país, na ordem em que se lê um cenário.

    O macro de país vem depois porque é pano de fundo: quem lê começa pelo que
    aconteceu com os ativos que possui e sobe para o regime em que aquilo
    aconteceu, não o contrário.
    """
    partes = [bloco_conjuntura(classe=classe, asset_class=asset_class,
                               ativos=ativos, estruturais=estruturais,
                               max_itens=max_itens)]
    if com_macro_pais:
        partes.append(bloco_macro_pais(historico_macro))
    return "\n\n".join(parte for parte in partes if parte)
