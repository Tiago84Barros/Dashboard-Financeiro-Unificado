"""
views/portfolio_b3_safras.py — relatorio de desempenho safra a safra.

Renderizacao apenas: toda a aritmetica de safra mora em core/b3_safras.py.
views/portfolio_b3.py ja tem 4.400+ linhas e nao recebe logica nova.

`_resumo_safras`, `_legenda_resumo`, `_tabela_para_exibicao`,
`_column_config_retorno`, `_grafico_barras` e `_expectativa` sao as pecas
de logica deste modulo, e sao deliberadamente
puras (sem streamlit, sem banco) para poder ser testadas direto — nesta
base, teste via Streamlit AppTest vaza atribuicao de modulo e falha só
dentro da suíte completa no CI, nunca isolado (nota de memória
`apptest-vaza-atribuicao-de-modulo`).
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from core.b3_pooled_evidence import MIN_ATIVOS_ANO
from core.b3_safras import MIN_SAFRAS_LOO, tabela_de_safras
from design.componentes import card_metrica
from views.empresas_b3 import _COR_ALT, _COR_NEU, _COR_POS, _plot_layout

_COLUNAS_RETORNO = ["Estratégia (%)", "Equal-weight (%)", "Selic (%)",
                    "Excesso s/ Selic (pp)"]

_CORES_SERIE = {
    "Estratégia (%)": _COR_POS,
    "Equal-weight (%)": _COR_NEU,
    "Selic (%)": _COR_ALT,
}


def _resumo_safras(tabela: pd.DataFrame) -> dict:
    """Deriva das colunas publicadas tudo que a tela afirma em texto.

    Pura (sem streamlit) para poder ser testada isoladamente. Regras
    fechadas por revisão:

    - a população das médias é `attrs["safras_completas"]`, nunca
      `tabela["Completa"]` sozinha — "Completa" só diz que a janela civil
      fechou, e uma safra pode ter janela fechada e zero pregão observado
      (core/b3_safras.py, rodada 4 da Task 4);
    - a frase sobre a safra vigente é derivada de `tabela["Completa"]`
      observada agora, nunca de uma suposição de calendário — de janeiro a
      março `safra_vigente_em` devolve o ano anterior, que ESTÁ dentro do
      range que `views/portfolio_b3.py` itera, e a safra vigente pode
      aparecer como linha parcial (rodada de correção 1, achado I-2);
    - uma safra pode ter `Completa=True` e ainda assim não estar em
      `completas` (janela fechada, mas zero pregão observado — o mesmo
      caso do I-1). Ela não é `parciais` (a janela FECHOU) nem `completas`
      (não foi medida): fica órfã das duas categorias, e por isso ganha a
      própria lista, `orfas`, para a legenda não deixá-la muda (rodada de
      correção 2, achado N-3).
    """
    safras_medidas = set(tabela.attrs.get("safras_completas", []))
    completas_bool = tabela["Safra"].isin(safras_medidas)
    completas = tabela[completas_bool]
    parciais = tabela[~tabela["Completa"]]
    orfas = tabela[tabela["Completa"] & ~completas_bool]

    n_medidas = len(completas)
    if n_medidas:
        media_excesso = float(completas["Excesso s/ Selic (pp)"].mean())
        venceu = int((completas["Excesso s/ Selic (pp)"] > 0).sum())
        peso_ausente_max = float(completas["Peso sem preço (%)"].max())
        safra_min = int(completas["Safra"].min())
        safra_max = int(completas["Safra"].max())
    else:
        media_excesso = None
        venceu = 0
        peso_ausente_max = 0.0
        safra_min = None
        safra_max = None

    return {
        "completas": completas,
        "parciais": parciais,
        "orfas": orfas,
        "n_medidas": n_medidas,
        "media_excesso": media_excesso,
        "venceu": venceu,
        "peso_ausente_max": peso_ausente_max,
        "safra_min": safra_min,
        "safra_max": safra_max,
    }


def _legenda_resumo(resumo: dict) -> str:
    """Texto do caption principal — sempre derivado de `resumo`, nunca do
    intervalo bruto de `tabela["Safra"]` (achado m-1: a legenda contava as
    safras medidas mas publicava o min..max da tabela inteira)."""
    if resumo["n_medidas"]:
        intervalo = f"de {resumo['safra_min']} a {resumo['safra_max']}"
        base = f"{resumo['n_medidas']} safra(s) já encerrada(s) e mensurável(is), {intervalo}."
    else:
        base = "Nenhuma safra encerrada e mensurável ainda."
    base += (" Cada safra é pontuada com dados até o ano anterior e vigora "
            "de abril a março.")

    parciais = resumo["parciais"]
    if parciais.empty:
        base += (" **A safra vigente não aparece nesta tabela** — a janela "
                "dela ainda está em curso; o desempenho em andamento está "
                "no gráfico logo acima, não aqui.")
    else:
        safras_parciais = ", ".join(str(int(s)) for s in sorted(parciais["Safra"]))
        base += (f" **A safra {safras_parciais} está nesta tabela com a "
                "janela em curso** (marcada \"Completa\" = Não) — o retorno "
                "dela é parcial e não entra nas médias acima.")

    orfas = resumo["orfas"]
    if not orfas.empty:
        # N-3: a linha existe na tabela (Completa=True) mas não em
        # `completas` nem em `parciais` — sem esta frase, ela aparece em
        # branco do lado de uma legenda que só fala de "safra vigente" e
        # "safra medida", sem cobrir o próprio caso.
        safras_orfas = ", ".join(str(int(s)) for s in sorted(orfas["Safra"]))
        base += (f" A safra {safras_orfas} tem a janela fechada, mas nenhum "
                "pregão foi observado nela — por isso aparece em branco "
                "nesta tabela e não entra em nenhuma média acima.")
    return base


def _tabela_para_exibicao(tabela: pd.DataFrame) -> pd.DataFrame:
    """Cópia de exibição — só uma cópia. Não formata os valores como texto.

    A rodada de correção 1 (achado m-2) tinha `NaN` virando o texto "—"
    aqui, via `.map(lambda v: "—" if pd.isna(v) else f"{v:.1f}")`. Isso
    tornava as 4 colunas de retorno `object`/string, e o `st.dataframe`
    passou a ordenar por clique de cabeçalho em ordem ALFABÉTICA, não
    numérica: "-3,0 → 10,0 → 100,0 → 9,0" (achado N-1, rodada de correção
    2). O dtype numérico tem que sobreviver até o `st.dataframe` para a
    ordenação funcionar — por isso esta função não toca nos valores, só
    copia; a formatação visual (1 casa) fica com `st.column_config` em
    `_column_config_retorno`, que não muda o dtype subjacente.

    `NaN` continua `NaN`: o motivo de uma célula estar vazia já está na
    coluna "Mensurável", ao lado — não precisa (e não deve) virar texto
    dentro da própria célula de novo."""
    return tabela.copy()


def _column_config_retorno() -> dict:
    """Config de exibição das 4 colunas de retorno — 1 casa decimal, sem
    alterar o dtype numérico da coluna (convenção já usada 3x em
    `views/portfolio_b3.py`, linhas 2370/2397/2422, via
    `st.column_config.NumberColumn`)."""
    return {
        col: st.column_config.NumberColumn(format="%.1f")
        for col in _COLUNAS_RETORNO
    }


def _grafico_barras(completas: pd.DataFrame):
    """Barras Estratégia/Equal-weight/Selic por safra, no mesmo tema
    transparente e nas mesmas cores dos demais gráficos da aba (achado
    I-3): sem isso este era o único gráfico com papel branco opaco e Selic
    trocando de cor em relação ao gráfico vizinho."""
    longo = completas.melt(
        id_vars="Safra",
        value_vars=["Estratégia (%)", "Equal-weight (%)", "Selic (%)"],
        var_name="Série", value_name="Retorno da safra (%)",
    )
    fig = px.bar(longo, x="Safra", y="Retorno da safra (%)",
                 color="Série", barmode="group",
                 color_discrete_map=_CORES_SERIE)
    fig.update_layout(**_plot_layout(360))
    return fig


def render_safras(resultados: list[dict], df_precos: pd.DataFrame, *,
                  selic_por_ano: dict[int, float],
                  taxa_selic_aa: float,
                  resultados_todos: list[dict] | None = None) -> None:
    """Bloco 1: a tabela de safras e a barra por safra contra Selic/EW.
    Ao fim, chama `render_expectativa` (Bloco 3).

    `resultados_todos` fica reservado para a Task 7 (medicao de vies de
    universo); esta task nao o consome.
    """
    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin-bottom:8px;">🗂️ Desempenho safra a safra</div>',
        unsafe_allow_html=True,
    )

    if not resultados or df_precos is None or df_precos.empty:
        st.caption("Rode a análise para reconstruir as safras.")
        return

    tabela = tabela_de_safras(resultados, df_precos,
                              selic_por_ano=selic_por_ano,
                              taxa_selic_aa=taxa_selic_aa)
    if tabela.empty:
        st.caption("Nenhuma safra com líderes reconstruídos.")
        return

    resumo = _resumo_safras(tabela)
    completas = resumo["completas"]

    st.caption(_legenda_resumo(resumo))

    cols = st.columns(3)
    if resumo["n_medidas"]:
        with cols[0]:
            card_metrica("Safras medidas", f"{resumo['n_medidas']}",
                         ajuda="Janela fechada e com pelo menos um pregão observado")
        with cols[1]:
            media = resumo["media_excesso"]
            card_metrica("Excesso médio s/ Selic", f"{media:+.1f} pp",
                         positivo=media > 0,
                         ajuda="Média simples das safras medidas")
        with cols[2]:
            card_metrica("Safras acima da Selic",
                         f"{resumo['venceu']} de {resumo['n_medidas']}",
                         ajuda="Contagem, não significância")
    else:
        with cols[0]:
            card_metrica("Safras medidas", "0",
                         ajuda="Nenhuma safra encerrada e mensurável ainda")

    st.dataframe(_tabela_para_exibicao(tabela), width="stretch", hide_index=True,
                column_config=_column_config_retorno())

    if not completas.empty:
        st.plotly_chart(_grafico_barras(completas), width="stretch",
                        config={"displayModeBar": False},
                        key="pb3_safras_barras")
        st.caption(
            "Barras, não curva acumulada: encadear os retornos produziria um "
            "número grande e único, que esconde quantas safras individuais "
            "ficaram atrás do benchmark."
        )

    render_expectativa(resultados, tabela)

    # Mesma população das outras agregações (as safras medidas): incluir as
    # não mensuráveis infla o aviso, porque nelas "Peso sem preço" vale
    # 100,0 justamente por não ter havido observação nenhuma (achado I-1).
    ausente = resumo["peso_ausente_max"]
    if ausente > 0:
        st.info(
            f"Em pelo menos uma safra medida, até {ausente:.1f}% do peso "
            "ficou sem preço na janela — deslistagem, incorporação ou "
            "buraco de dado. Essa fatia rende **zero** no cálculo: não "
            "inventamos a perda, mas ela também não rende o que os "
            "sobreviventes renderam."
        )


def _ics_por_ano(resultados: list[dict]) -> dict[int, float]:
    """Um Rank-IC por ANO sobre o universo agrupado (rodada 2, A-1).

    Usa `pooled_yearly_ics`, a MESMA redução que `_render_evidencia_universo`
    aplica no bloco logo acima desta tela. Ler `rank_ic_values` (a lista
    anual DE UM SEGMENTO) e concatenar entre segmentos faria `n` valer
    `segmentos × anos`, e o teste t trataria a mesma ordenação de mercado,
    recontada uma vez por segmento, como observações independentes.

    Sem `ic_pairs` devolve vazio: o bloco diz que não pôde medir. Cair no
    `rank_ic_values` concatenado seria trocar "não medi" por um número
    inflado.
    """
    from core.b3_pooled_evidence import pooled_yearly_ics

    pares = _pares_de_ic(resultados)
    return pooled_yearly_ics(pares) if pares else {}


def _pares_de_ic(resultados: list[dict]) -> list[tuple]:
    """Observações (ano, score, retorno) de TODOS os segmentos, sem reduzir.

    Existe separada porque a mensagem de limitação precisa distinguir duas
    ausências opostas que `ics_por_ano == {}` confunde: *não chegaram
    pares* e *chegaram pares, mas nenhum ano juntou ativos suficientes*.
    Discriminar por `rank_ic_values` respondia a outra pergunta — a lista
    por segmento pode existir com zero pares, e a tela culpava a ausência
    errada.
    """
    pares: list[tuple] = []
    for res in (resultados or []):
        pares.extend(res.get("ic_pairs") or [])
    return pares


def _texto_banda_p(loo: dict) -> str:
    """Margem medida do leave-one-out: a banda de p-valores das
    subamostras contra `alpha`. Contagem diz quantos viram; só a banda diz
    por quanto. Vazio quando não há banda calculável."""
    baixo, alto = loo.get("p_banda", (None, None))
    if baixo is None or alto is None:
        return ""
    return (f"Tirando um ano de cada vez, o p-valor fica entre {baixo:.3f} e "
            f"{alto:.3f} (α = {loo['alpha']:.2f}).")


def _ajuda_ordena(veredito) -> str:
    """Ajuda do card mais forte: amplitude e efeito mínimo detectável.

    A premissa de independência NÃO fica aqui — ela qualifica o selo mais
    forte da tela e saiu para `_nota_independencia`, que a view publica em
    texto visível. `ajuda` vira o atributo `title=` do card
    (`design/componentes.py`), isto é, tooltip de hover: invisível no
    toque e invisível para quem não passa o mouse. Ressalva que só aparece
    no hover é ressalva que não foi publicada.
    """
    partes = [f"{veredito.anos_medidos} ano(s) de Rank-IC"]
    mde = veredito.efeito_minimo_detectavel
    if mde is not None:
        partes.append(f"Efeito mínimo detectável: {mde:.3f}")
    return ". ".join(partes)


def _nota_independencia(anos: list[int]) -> str:
    """Premissa que o teste t assume e os dados não garantem — visível.

    Derivada da medição, nunca fixa (este projeto já publicou texto de
    limitação que envelheceu invertido e continuou soando como rigor):
    cita quantos anos entraram, quais, e o maior bloco consecutivo, que é
    a sobreposição de regime que o teste ignora.
    """
    if not anos:
        return ""
    corrida = maior = 1
    for anterior, atual in zip(anos, anos[1:]):
        corrida = corrida + 1 if atual == anterior + 1 else 1
        maior = max(maior, corrida)
    return (f"O teste trata os {len(anos)} anos ({anos[0]}–{anos[-1]}, até "
            f"{maior} consecutivos) como independentes. Anos vizinhos "
            "compartilham universo e regime de mercado, então o p-valor é "
            "otimista.")


def _frase(*partes: str) -> str:
    """Junta pedaços de texto já pontuados, sem deixar espaço órfão quando
    um deles é vazio (a banda não é calculável em toda amostra)."""
    return " ".join(p.strip() for p in partes if p and p.strip())


def _expectativa(resultados: list[dict], tabela: pd.DataFrame) -> dict:
    """Tudo que o Bloco 3 publica, derivado das leituras — sem streamlit.

    Três perguntas distintas, e é de propósito que elas não virem um
    número só: **ordenar não é superar**. Um Rank-IC positivo diz que o
    score discriminou retornos; ele não diz que a carteira bateu a Selic.
    Fundir os dois num "retorno esperado" leria como previsão um resultado
    que a amostra não sustenta.

    Regras fechadas nesta task:

    - a população da banda é `attrs["safras_completas"]`, nunca
      `tabela["Completa"]` — mesma população das médias do Bloco 1. Uma
      safra de janela fechada e zero pregão observado não é evidência;
    - a fragilidade NÃO é medida re-semeando o bootstrap (trocar a semente
      só troca ruído de Monte Carlo) e sim por leave-one-out sobre as
      safras. O veredito da B3 já passou a APROVADO por 0,004 e reprovava
      de novo ao tirar uma safra — quem enxerga isso é o LOO;
    - o veredito do card "Ordena?" sai de `veredito_do_rank_ic`, o MESMO
      critério que o leave-one-out aplica (rodada de correção 1, F-1).
      Dois critérios homônimos publicavam vereditos opostos lado a lado;
    - abaixo de `MIN_SAFRAS_LOO` o card de fragilidade sai "—" e sem cor
      (F-2): "0 safra(s)" em verde tem duas causas opostas, e só uma delas
      é robustez;
    - todo texto de limitação sai da medição. `limitacao_banda` cita a
      contagem observada de safras mensuráveis, para não virar uma frase
      fixa que envelhece invertida e continua soando como rigor;
    - a população do veredito é **um Rank-IC por ano** (rodada 2, A-1).
      Antes, `ic_values` concatenava `rank_ic_values` de todos os
      segmentos, então `n` era *segmento × ano* e o teste t tratava a
      mesma ordenação de mercado, recontada uma vez por segmento, como
      observações independentes. Medido: os MESMOS 8 ICs anuais
      replicados por 20 segmentos (zero informação nova) moviam a tela de
      "Inconclusivo / fragilidade 2 sem cor" para "Evidência a favor /
      fragilidade 0 em VERDE", com a ajuda dizendo "160 ano(s)" para 8
      anos de dado. A redução vem de `pooled_yearly_ics`, a MESMA função
      que o bloco "Evidência no universo" usa logo acima — dois critérios
      homônimos sobre o mesmo dado, com populações diferentes, é o defeito
      que o F-1 fechou um bloco abaixo;
    - o verde da fragilidade exige veredito CONCLUSIVO (rodada 2, A-2).
      "0 safra(s)" ao lado de "Inconclusivo" afirma robustez da
      *ignorância*, e isso acontecia em 50,8% dos casos inconclusivos.
    """
    from core.b3_safras import (
        bootstrap_excesso,
        fragilidade_leave_one_out,
        veredito_do_rank_ic,
    )

    pares = _pares_de_ic(resultados)
    ics_por_ano = _ics_por_ano(resultados)
    anos = sorted(ics_por_ano)
    ic_values = [ics_por_ano[ano] for ano in anos]

    medidas = set(tabela.attrs.get("safras_completas", []))
    completas = (tabela[tabela["Safra"].isin(medidas)]
                 if not tabela.empty else tabela)
    excessos = [float(v) / 100.0
                for v in (completas["Excesso s/ Selic (pp)"]
                          if not completas.empty else [])
                if pd.notna(v)]

    # F-1: o MESMO criterio que o leave-one-out usa. Antes o card chamava
    # `classify_evidence` sem p-valor e o LOO chamava com: a tela imprimia
    # "Inconclusivo" enquanto o motor interno concluia `evidencia_a_favor`,
    # e o card de fragilidade dava selo verde a um veredito que a tela nao
    # mostrava e que contradizia o card ao lado.
    veredito = veredito_do_rank_ic(ic_values)
    baixo, alto = bootstrap_excesso(excessos)
    loo = fragilidade_leave_one_out(ic_values)

    avisos: list[str] = []
    if baixo is not None and baixo <= 0 <= alto:
        avisos.append(
            f"O intervalo de 95% do excesso sobre a Selic vai de {baixo:+.1%} "
            f"a {alto:+.1%} por safra: ele **atravessa o zero**. Nesta "
            "amostra, a vantagem observada não é distinguível de acaso. "
            "Ordenar não é superar — o Rank-IC pode indicar que o motor "
            "discrimina retornos sem que isso vire vantagem líquida."
        )
    if loo["safras_que_viram"] > 0:
        avisos.append(
            f"O veredito muda se {loo['safras_que_viram']} dos "
            f"{loo['n_safras']} anos for removido. Uma conclusão que depende "
            "de um ano específico não é uma conclusão sobre a estratégia — é "
            "uma conclusão sobre aquele ano."
        )

    # N-3: a causa vem do que `_ics_por_ano` VIU (os pares), não de
    # `rank_ic_values`. As duas ausências são opostas e a tela culpava a
    # errada: com pares presentes e nenhum ano elegível ela dizia "os pares
    # não vieram", e a frase correta ficava inalcançável três linhas abaixo.
    if anos:
        limitacao_ev = ""
    elif not pares:
        limitacao_ev = (
            "Veredito não publicado: os pares (ano, score, retorno) não "
            "vieram nos resultados, e só eles permitem reduzir a um Rank-IC "
            "por ano. A lista `rank_ic_values` está disponível, mas ela é "
            "por segmento — usá-la multiplicaria a mesma evidência pelo "
            "número de segmentos e inflaria a significância por √k.")
    else:
        limitacao_ev = (
            f"Veredito não publicado: {len(pares)} observação(ões) (ano, "
            "score, retorno) chegaram, mas nenhum ano juntou os "
            f"{MIN_ATIVOS_ANO} ativos mínimos com score e retorno — sem isso "
            "o Rank-IC do ano não é calculável.")

    n_banda = len(excessos)
    if baixo is not None:
        limitacao = ""
    elif n_banda == 0:
        limitacao = ("Banda não publicada: nenhuma safra mensurável até "
                     "agora, e portanto nenhum excesso observado para "
                     "reamostrar.")
    else:
        limitacao = (f"Banda não publicada: {n_banda} safra(s) mensurável(is) "
                     "— abaixo de 2 não há dispersão entre safras para "
                     "reamostrar, e um ponto central sozinho seria um número "
                     "sem incerteza medida.")

    # F-2 + A-2: o zero da fragilidade tem TRÊS causas, e só uma é verde.
    # (a) amostra abaixo do piso -- não houve o que remover; (b) veredito
    # inconclusivo -- o zero afirma robustez da ignorância; (c) conclusão
    # medida que nenhuma remoção derruba. O texto do estado neutro carrega
    # a causa, derivada da própria medição.
    n_loo = loo["n_safras"]
    if not loo["medido"]:
        texto_frag, positivo_frag = "—", None
        ajuda_frag = (
            f"{n_loo} ano(s) com Rank-IC: abaixo de {MIN_SAFRAS_LOO} a taxa "
            "de virada do leave-one-out é a mesma para sinal nulo e para "
            "sinal forte (amplitude medida de 0,012 em n=3 contra 0,216 em "
            "n=4), então o número existiria sem carregar informação")
    else:
        texto_frag = f"{loo['safras_que_viram']} de {n_loo} anos"
        if loo["robusto"]:
            positivo_frag = True
            ajuda_frag = _frase(f"Nenhum dos {n_loo} anos derruba o veredito.",
                                _texto_banda_p(loo))
        elif loo["safras_que_viram"] > 0:
            positivo_frag = False
            ajuda_frag = _frase("Quantos anos precisam sair para o veredito "
                                "virar.", _texto_banda_p(loo))
        elif not loo["conclusivo"]:
            positivo_frag = None
            ajuda_frag = _frase(
                "Nesta amostra, remover um ano não é capaz de mudar o "
                "veredito — e o veredito é inconclusivo. Zero aqui é "
                "insensibilidade do teste sobre uma não-conclusão, não "
                "robustez.", _texto_banda_p(loo))
        else:
            # N-1: zero viradas sobre veredito CONCLUSIVO, sem selo verde.
            # O ramo anterior afirmava "o veredito é inconclusivo" ao lado
            # de um card dizendo "Evidência contra" — em [-0.1, -0.1, -0.1,
            # -0.5] as subamostras idênticas não têm dispersão, o p-valor
            # não existe e a margem some, mas a conclusão continua de pé.
            positivo_frag = None
            sem_p = sum(1 for p in loo["p_loo"] if p is None)
            causa = (
                f"{sem_p} das {n_loo} subamostras ficam sem dispersão entre "
                "os Rank-ICs e o p-valor nem existe nelas"
                if sem_p else
                f"a banda de p-valores atravessa α = {loo['alpha']:.2f}")
            ajuda_frag = _frase(
                f"Nenhum dos {n_loo} anos derruba o veredito "
                f"({veredito.rotulo}), mas a margem não sustenta selo: "
                f"{causa}. A contagem diz que nenhum ano virou; sem margem, "
                "ela não diz por quanto.", _texto_banda_p(loo))

    # A banda de p-valores é o "por quanto passou" — e é ela que impede o
    # selo verde de ser lido como salvaguarda testada. Vai em texto
    # VISÍVEL: no `ajuda` ela virava `title=` do card, tooltip de hover, e
    # o usuário via "0 de 8 anos" em verde e mais nada.
    nota_frag = _texto_banda_p(loo) if loo["medido"] else ""
    # N-5: as duas contagens da tela medem coisas diferentes e ficavam
    # lado a lado sem explicação — "Safras medidas: N" (janela fechada com
    # pregão) contra "0 de M anos" (anos com Rank-IC calculável).
    if loo["medido"] and n_banda and n_banda != n_loo:
        nota_frag = _frase(
            nota_frag,
            f"A fragilidade conta os {n_loo} ano(s) com Rank-IC calculável "
            f"(≥ {MIN_ATIVOS_ANO} ativos no universo), não as {n_banda} "
            "safra(s) mensurável(is) do bloco acima: são populações "
            "diferentes.")

    return {
        "ic_values": ic_values,
        "anos": anos,
        "pares": len(pares),
        "veredito": veredito,
        "ajuda_ordena": _ajuda_ordena(veredito),
        "nota_independencia": _nota_independencia(anos),
        "nota_fragilidade": nota_frag,
        "limitacao_evidencia": limitacao_ev,
        "texto_fragilidade": texto_frag,
        "positivo_fragilidade": positivo_frag,
        "ajuda_fragilidade": ajuda_frag,
        "banda": (baixo, alto),
        "n_safras_banda": n_banda,
        "texto_banda": (f"{baixo:+.1%} a {alto:+.1%}"
                        if baixo is not None else "—"),
        "loo": loo,
        "avisos": avisos,
        "limitacao_banda": limitacao,
    }


def render_expectativa(resultados: list[dict], tabela: pd.DataFrame) -> None:
    """Bloco 3: o motor ordena? supera? e quão frágil é a conclusão?

    Só renderiza — a medição inteira está em `_expectativa`, que é pura e
    testada direto (AppTest, nesta base, vaza atribuição de módulo e falha
    só dentro da suíte no CI)."""
    from core.b3_evidence import evidence_label

    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin:20px 0 8px;">🎯 O que esperar da safra vigente</div>',
        unsafe_allow_html=True,
    )

    exp = _expectativa(resultados, tabela)
    veredito = exp["veredito"]

    cols = st.columns(3)
    with cols[0]:
        card_metrica("Ordena?", evidence_label(veredito),
                     ajuda=exp["ajuda_ordena"])
    with cols[1]:
        baixo = exp["banda"][0]
        card_metrica("Supera? (excesso s/ Selic)", exp["texto_banda"],
                     positivo=(baixo is not None and baixo > 0),
                     ajuda=(f"Intervalo de 95% por reamostragem de "
                            f"{exp['n_safras_banda']} safra(s) mensurável(is)"))
    with cols[2]:
        card_metrica("Fragilidade", exp["texto_fragilidade"],
                     positivo=exp["positivo_fragilidade"],
                     ajuda=exp["ajuda_fragilidade"])

    for aviso in exp["avisos"]:
        st.warning(aviso)
    # Ressalva e margem em texto visível, não em tooltip: `ajuda` vira
    # `title=` do card e não existe no toque.
    for nota in (exp["nota_independencia"], exp["nota_fragilidade"],
                 exp["limitacao_evidencia"], exp["limitacao_banda"]):
        if nota:
            st.caption(nota)
