"""Quantifica o viés de sobrevivência do painel histórico americano.

Avisar que o viés existe é o mínimo; o aviso genérico não diz ao usuário se ele
é pequeno ou se invalida a evidência. Este módulo mede o tamanho dele com o
número que o próprio painel entrega: quantas empresas ENTRARAM e quantas SAÍRAM
do universo ao longo das safras.

Num mercado real as duas contas existem -- empresas abrem capital e empresas
somem, por falência, fechamento de capital ou aquisição. Um painel com entradas
e nenhuma saída não é um painel com poucas saídas: é um painel construído a
partir de quem sobreviveu até hoje e projetado para trás.

Medido em 27/08/2026 no armazém local, sobre `market_us.score_vintages`:
16 safras de 2010-06-30 a 2025-06-30, 106 empresas na primeira e 2.798 na
última, **2.692 entradas e zero saídas**. Nenhuma das 106 empresas de 2010
deixou a amostra em quinze anos. As séries de preço confirmam: das 2.800
empresas do painel, nenhuma tem cotação que pare antes do fim da amostra.

O painel mora no armazém local; a base publicada só alcança
`company_snapshots` e `prices_monthly` (esta com 12 símbolos). Por isso a
medição é gravada em disco por quem tem o armazém e lida pela tela em
produção -- mesmo padrão do manifesto do RAG. O arquivo é resultado de medição
com data e procedência, não critério cravado: `medir_turnover` o recalcula, e
o dia em que houver deslistagem ingerida o número muda sozinho.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CAMINHO_MEDICAO = Path(__file__).resolve().parents[1] / "data" / "us_survivorship.json"

# CIK é o identificador decimal da SEC com no máximo dez dígitos. Centralizar a
# faixa evita que parser e classificação aceitem identidades diferentes.
CIK_MAX_SEC = 9_999_999_999

# Cobertura de identidade: quanto da coorte-base a SEC classificou por SIC.
#
# Exigir 100% seria um portão que nunca poderia abrir. Os CIKs sem SIC não são
# falha de apuração: são BDC e companhia de investimento fechada, que registram
# sob o Investment Company Act e cujo cadastro a SEC serve sem SIC -- 111 dos
# 8.955 da coorte doméstica de 2010, aferido em 29/08/2026. Nenhuma execução
# futura vai preenchê-los, e um critério inalcançável nunca é revisto enquanto
# o número errado segue na tela ([[gate-que-so-dava-false]]).
#
# O que substitui a exigência impossível não é tolerância cega. A dúvida sobre
# eles é só de PERTENCIMENTO -- o desfecho de cada um é observado no índice como
# o de qualquer outro. Então o efeito máximo do desconhecido é calculável: mede-
# se a mortalidade nos dois extremos (todos veículos / todos operacionais) e
# publica-se a banda. O portão passa a exigir que a banda seja estreita o
# bastante para não mudar a leitura, e que a coorte tenha sido inteiramente
# CONSULTADA -- essa parte continua sendo 100%, porque essa é alcançável.
COBERTURA_IDENTIDADE_MINIMA_PCT = 98.0
BANDA_MORTALIDADE_MAXIMA_PP = 1.0


def cik_sec_valido(cik: object) -> bool:
    """Retorna se ``cik`` é um identificador SEC canônico de até 10 dígitos."""
    return type(cik) is int and 1 <= cik <= CIK_MAX_SEC

# Uma serie que para bem antes do fim da amostra e sinal de que a empresa deixou
# de negociar, mesmo sem `delisted_date` preenchida. A folga de 120 dias evita
# contar atraso de ingestao como deslistagem.
FOLGA_FIM_DIAS = 120

# `medir_mortalidade` publica porcentagens arredondadas a duas casas. A UI só
# aceita a diferença máxima introduzida por esse arredondamento, nunca uma
# aproximação livre do payload gravado.
TOLERANCIA_ARREDONDAMENTO_MORTALIDADE_PCT = 0.005 + 1e-9


def medir_turnover(engine) -> dict[str, Any]:
    """Conta entradas e saídas de empresas entre safras consecutivas do painel.

    Uma saída é uma empresa presente na safra `t` e ausente na safra `t+1`. Se
    esse número for zero em toda a janela, o universo é 100% sobrevivente e o
    risco de perda permanente de capital não é observável na série.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        linhas = list(conn.execute(text(
            "SELECT as_of_date, company_id FROM market_us.score_vintages")))
    if not linhas:
        raise ValueError("score_vintages vazio")

    por_safra: dict[date, set[int]] = {}
    for d, cid in linhas:
        d = d.date() if hasattr(d, "date") else d
        por_safra.setdefault(d, set()).add(int(cid))
    safras = sorted(por_safra)

    saidas = entradas = 0
    for anterior, atual in zip(safras, safras[1:]):
        saidas += len(por_safra[anterior] - por_safra[atual])
        entradas += len(por_safra[atual] - por_safra[anterior])

    return {
        "medido_em": datetime.now(timezone.utc).date().isoformat(),
        "safras": len(safras),
        "primeira_safra": safras[0].isoformat(),
        "ultima_safra": safras[-1].isoformat(),
        "empresas_primeira": len(por_safra[safras[0]]),
        "empresas_ultima": len(por_safra[safras[-1]]),
        "entradas": entradas,
        "saidas": saidas,
    }


def medicao_turnover_verificada(medicao: object) -> bool:
    """Contrato completo do agregado produzido por :func:`medir_turnover`.

    Entradas e saídas são somadas entre safras, portanto não se exige igualdade
    líquida (uma empresa pode entrar e sair mais de uma vez). As desigualdades
    verificam somente o mínimo observável entre os dois extremos.
    """
    if not isinstance(medicao, dict):
        return False
    campos = ("safras", "empresas_primeira", "empresas_ultima", "entradas", "saidas")
    if not all(_inteiro_estrito(medicao.get(campo)) for campo in campos):
        return False
    try:
        medido_em = date.fromisoformat(medicao["medido_em"])
        primeira = date.fromisoformat(medicao["primeira_safra"])
        ultima = date.fromisoformat(medicao["ultima_safra"])
    except (KeyError, TypeError, ValueError):
        return False
    safras = medicao["safras"]
    inicial = medicao["empresas_primeira"]
    final = medicao["empresas_ultima"]
    entradas = medicao["entradas"]
    saidas = medicao["saidas"]
    hoje_utc = datetime.now(timezone.utc).date()
    return (safras >= 2 and inicial > 0 and final > 0 and entradas >= 0 and saidas >= 0
            and primeira < ultima <= medido_em <= hoje_utc
            and final <= inicial + entradas and inicial <= final + saidas)


def gravar_medicao(medicao: dict[str, Any],
                   caminho: Path | str = CAMINHO_MEDICAO) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(medicao, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return caminho


def carregar_medicao(caminho: Path | str = CAMINHO_MEDICAO) -> dict[str, Any] | None:
    """Devolve a última medição gravada, ou None se não houver nenhuma.

    Ausência não vira zero: sem medição, quem chama volta ao aviso qualitativo
    em vez de afirmar um número que ninguém apurou.
    """
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("medicao de sobrevivencia indisponivel: %s", type(exc).__name__)
        return None
    return dados if isinstance(dados, dict) and "saidas" in dados else None


def _mil(n: int) -> str:
    """Separador de milhar pt-BR.

    Trocar vírgula por ponto na frase inteira já comeu a vírgula da prosa e
    virou ponto final no meio de uma oração; a troca tem de ser no número.
    """
    return f"{int(n):,}".replace(",", ".")


def frase_turnover(medicao: dict[str, Any] | None = None) -> str | None:
    """Frase pronta com o tamanho do viés, ou None se não houver medição."""
    medicao = carregar_medicao() if medicao is None else medicao
    if not medicao:
        return None
    try:
        saidas = int(medicao["saidas"])
        entradas = int(medicao["entradas"])
        safras = int(medicao["safras"])
        ini, fim = medicao["primeira_safra"][:4], medicao["ultima_safra"][:4]
        n_ini = int(medicao["empresas_primeira"])
    except Exception:  # noqa: BLE001
        return None
    if saidas == 0:
        return (f"Medido: em {safras} safras de {ini} a {fim} o painel registrou "
                f"{_mil(entradas)} entradas e **nenhuma saída** -- as {n_ini} "
                f"empresas da primeira safra continuam todas na última. Num "
                f"mercado real isso não acontece; a amostra é 100% sobrevivente.")
    return (f"Medido: em {safras} safras de {ini} a {fim} o painel registrou "
            f"{_mil(entradas)} entradas e {_mil(saidas)} saídas de empresas.")


# ── Mortalidade da coorte: o viés medido FORA do painel (A-157) ──────────────
#
# `medir_turnover` responde "o painel tem saídas?" e a resposta foi zero. Isso
# diz que a amostra é sobrevivente, mas não diz de QUANTO -- e sem o tamanho o
# usuário não consegue descontar nada. O tamanho não está no painel, por
# construção: quem morreu nunca entrou nele.
#
# A fonte independente é o índice de arquivamentos da SEC (`full-index`), que é
# ponto-no-tempo de verdade: lista quem arquivou relatório anual naquele
# trimestre, vivo ou não. Uma empresa que arquivava em 2010 e não arquiva mais
# saiu do mercado por falência, fechamento de capital ou aquisição.
#
# Medido em 27/08/2026 (`scripts/medir_mortalidade_us.py`): 9.686 empresas
# arquivaram relatório anual em 2010 e 2.899 ainda arquivavam em 2025 --
# **70,1% desapareceram**; a queda é contínua (57,9% vivas em 2015, 39,0% em
# 2020). O painel tem 106 empresas na safra de 2010, 1,09% do universo real
# daquele ano, e nenhuma delas morreu. Não é um painel com poucas mortes: é um
# painel construído a partir dos 30% que sobreviveram, e por isso todo retorno
# histórico que sai dele é teto, não expectativa.
#
# As formas incluem `10-K405` e `10-KSB`, extintas depois de 2003 e 2008: quem
# só contasse `10-K` leria como morte a empresa que apenas trocou de formulário.
# A coorte ampla responde por todo arquivador anual da SEC e, portanto, inclui
# 20-F. A operacional/doméstica é outro contrato e deliberadamente não herda
# esse formulário estrangeiro (ver `ciks_com_relatorio_anual_operacional`).
FORMAS_RELATORIO_ANUAL_IDX = ("10-K", "10-K405", "10-KSB", "20-F")
FORMAS_RELATORIO_ANUAL_OPERACIONAL_IDX = ("10-K", "10-K405", "10-KSB")

_LINHA_IDX = re.compile(r"^(?P<forma>\S[^ ]*(?: [^ ]+)?)\s{2,}.*?edgar/data/(?P<cik>\d+)/")


def _ciks_com_formas_relatorio_anual(texto_idx: str,
                                     formas: tuple[str, ...]) -> set[int]:
    """CIKs de um contrato anual, lidos de um `form.idx` da SEC.

    O CIK sai do caminho do arquivo (`edgar/data/<cik>/`) e não da coluna de
    largura fixa: a coluna desalinha em nome societário longo, e a versão que
    lia por posição colheu um `CIK 0` de linha de cabeçalho.
    """
    achados: set[int] = set()
    for linha in str(texto_idx or "").splitlines():
        m = _LINHA_IDX.match(linha)
        if not m:
            continue
        if m.group("forma").strip().upper() not in formas:
            continue
        cik_texto = m.group("cik")
        # O regex aceita qualquer sequência de dígitos; recusar pelo tamanho
        # antes de int() evita conversão custosa ou ValueError em paths hostis.
        if len(cik_texto) > 10:
            continue
        cik = int(cik_texto)
        if cik_sec_valido(cik):
            achados.add(cik)
    return achados


def ciks_com_relatorio_anual(texto_idx: str) -> set[int]:
    """CIKs da coorte AMPLA: todo arquivador de relatório anual, inclusive 20-F."""
    return _ciks_com_formas_relatorio_anual(texto_idx, FORMAS_RELATORIO_ANUAL_IDX)


def ciks_com_relatorio_anual_operacional(texto_idx: str) -> set[int]:
    """CIKs da coorte operacional/doméstica com ao menos um 10-K doméstico.

    A evidência positiva de 10-K satisfaz o contrato mesmo que o mesmo CIK
    também tenha 20-F no ano; 20-F sem 10-K não entra. A exclusão acontece no
    índice, antes de formar o denominador, e não depende de ticker, bolsa ou
    atividade atual, campos que desaparecem com a morte.
    """
    return _ciks_com_formas_relatorio_anual(
        texto_idx, FORMAS_RELATORIO_ANUAL_OPERACIONAL_IDX)


def medir_mortalidade(por_ano: dict[int, set[int]],
                      painel_por_ano: dict[int, set[int]] | None = None
                      ) -> dict[str, Any]:
    """Curva de sobrevivência da coorte mais antiga, e o que o painel viu dela.

    `por_ano` é {ano: CIKs que arquivaram relatório anual}, e `painel_por_ano`
    é o mesmo recorte visto pelo nosso painel. A comparação entre os dois é o
    número que interessa: cobertura do universo real e mortes observadas.
    """
    anos = sorted(a for a, ciks in (por_ano or {}).items() if ciks)
    if len(anos) < 2:
        raise ValueError("mortalidade exige ao menos dois anos com filiais")
    base_ano, ultimo = anos[0], anos[-1]
    base = por_ano[base_ano]
    curva = {
        str(ano): {
            "vivas": len(base & por_ano[ano]),
            "universo_do_ano": len(por_ano[ano]),
            "sobrevivencia_pct": round(100.0 * len(base & por_ano[ano]) / len(base), 2),
        }
        for ano in anos
    }
    painel_base = (painel_por_ano or {}).get(base_ano, set())
    medicao: dict[str, Any] = {
        "medido_em": datetime.now(timezone.utc).date().isoformat(),
        "ano_base": base_ano,
        "ano_final": ultimo,
        "universo_base": len(base),
        "sobreviventes": len(base & por_ano[ultimo]),
        "mortalidade_pct": round(100.0 * (1 - len(base & por_ano[ultimo]) / len(base)), 2),
        "curva": curva,
    }
    if painel_por_ano is not None:
        medicao.update({
            "painel_no_ano_base": len(painel_base),
            "cobertura_pct": round(100.0 * len(painel_base & base) / len(base), 2),
            "mortes_no_painel": len(painel_base - por_ano[ultimo]),
        })
    return medicao


def restringir_a_operacionais(por_ano: dict[int, set[int]],
                              operacionais: set[int]) -> dict[int, set[int]]:
    """Recorta {ano: CIKs} à população de companhias operacionais.

    O recorte precisa valer para TODOS os anos, não só para o ano base. Filtrar
    apenas a coorte de partida e conferir a sobrevivência contra o universo
    inteiro faria uma empresa "morrer" e "ressuscitar" conforme o outro conjunto
    a contivesse ou não -- a conta tem de comparar populações iguais nos dois
    extremos.
    """
    return {ano: (ciks & operacionais) for ano, ciks in (por_ano or {}).items()}


def _inteiro_estrito(valor: object) -> bool:
    """`bool` e decimais nunca representam contagens auditáveis."""
    return type(valor) is int


def _numero_finito(valor: object) -> bool:
    """Números JSON válidos para métricas; rejeita bool, NaN e infinitos."""
    if type(valor) not in (int, float):
        return False
    try:
        return math.isfinite(float(valor))
    except (OverflowError, ValueError):
        return False


def _periodo_coorte_valido(base_ano: int, ano_final: int,
                            medido_em: date) -> bool:
    """Só aceita uma coorte encerrada e medida após o fechamento do ano final."""
    hoje_utc = datetime.now(timezone.utc).date()
    return (1900 <= base_ano < ano_final < hoje_utc.year
            and date(ano_final + 1, 1, 1) <= medido_em <= hoje_utc)


def _banda_mortalidade_valida(coorte: dict[str, Any], mortalidade: float) -> bool:
    """A incerteza residual tem de vir declarada, coerente e pequena.

    Sem CIK não classificado a banda é degenerada e pode ser omitida: não há
    dúvida a declarar. Havendo, os dois extremos são obrigatórios -- publicar o
    ponto sem a banda apresentaria como exato um número que não é, que é o
    defeito que este módulo inteiro existe para não repetir.
    """
    nao_classificados = coorte.get("nao_classificados")
    if not _inteiro_estrito(nao_classificados) or nao_classificados < 0:
        return False
    if nao_classificados == 0:
        # Se ninguém ficou sem classificar, a cobertura é 100% por definição.
        # Um payload que afirma as duas coisas ao mesmo tempo está descrevendo
        # uma medição que não aconteceu, e afrouxar o limiar não pode virar
        # porta para publicá-lo.
        return float(coorte.get("cobertura_identidade_pct", 0.0)) == 100.0
    minimo = coorte.get("mortalidade_pct_min")
    maximo = coorte.get("mortalidade_pct_max")
    if not (_numero_finito(minimo) and _numero_finito(maximo)):
        return False
    minimo, maximo = float(minimo), float(maximo)
    if not 0 <= minimo <= mortalidade <= maximo <= 100:
        return False
    return (maximo - minimo) <= BANDA_MORTALIDADE_MAXIMA_PP


def coorte_operacional_verificada(coorte: dict[str, Any]) -> bool:
    """Contrato compartilhado para publicar ou gravar a coorte operacional."""
    if not isinstance(coorte, dict) or coorte.get("populacao") != "operacional":
        return False
    try:
        medido_em = date.fromisoformat(coorte["medido_em"])
    except (KeyError, TypeError, ValueError):
        return False
    campos_inteiros = ("ano_base", "ano_final", "universo_base", "sobreviventes",
                       "sem_identidade_apurada", "nao_classificados")
    if not all(_inteiro_estrito(coorte.get(campo)) for campo in campos_inteiros):
        return False
    if not all(_numero_finito(coorte.get(campo))
               for campo in ("mortalidade_pct", "cobertura_identidade_pct")):
        return False

    base_ano = coorte["ano_base"]
    ano_final = coorte["ano_final"]
    universo = coorte["universo_base"]
    sobreviventes = coorte["sobreviventes"]
    mortalidade = float(coorte["mortalidade_pct"])
    cobertura = float(coorte["cobertura_identidade_pct"])
    if not _periodo_coorte_valido(base_ano, ano_final, medido_em) or universo <= 0:
        return False
    if not 0 <= sobreviventes <= universo:
        return False
    if not 0 <= mortalidade <= 100 or cobertura < COBERTURA_IDENTIDADE_MINIMA_PCT:
        return False
    # Consulta é exigência absoluta; classificação é exigência com banda. CIK
    # que ninguém perguntou à SEC é lacuna de execução e não tem tamanho
    # conhecido -- não dá para acotá-lo como se dá para acotar o sem SIC.
    if coorte["sem_identidade_apurada"] != 0:
        return False
    if not _banda_mortalidade_valida(coorte, mortalidade):
        return False
    esperada = 100.0 * (1 - sobreviventes / universo)
    return (abs(mortalidade - esperada) <= TOLERANCIA_ARREDONDAMENTO_MORTALIDADE_PCT
            and _curva_coorte_verificada(coorte.get("curva"), base_ano, ano_final,
                                          universo, sobreviventes))


def _curva_coorte_verificada(curva: object, base_ano: int, ano_final: int,
                             universo: int, sobreviventes: int) -> bool:
    """Valida a curva de qualquer coorte, que pode ser esparsa nos anos observados.

    Não exige todos os anos intermediários, mas exige chaves canônicas,
    ordem crescente e os extremos da janela para que cada ponto seja auditável.
    """
    if not isinstance(curva, dict):
        return False
    pontos: dict[int, object] = {}
    anterior: int | None = None
    for chave, ponto in curva.items():
        # JSON publica anos como string decimal sem zeros à esquerda. Validar
        # antes de int() impede "02010" e a colisão silenciosa 2010/"2010".
        if type(chave) is not str or not re.fullmatch(r"[1-9][0-9]{3,}", chave):
            return False
        ano = int(chave)
        if str(ano) != chave or ano in pontos or (anterior is not None and ano <= anterior):
            return False
        pontos[ano] = ponto
        anterior = ano
    if base_ano not in pontos or ano_final not in pontos:
        return False
    for ano, ponto in pontos.items():
        if not base_ano <= ano <= ano_final or not isinstance(ponto, dict):
            return False
        campos = ("vivas", "universo_do_ano")
        if not all(_inteiro_estrito(ponto.get(campo)) for campo in campos):
            return False
        if not _numero_finito(ponto.get("sobrevivencia_pct")):
            return False
        vivas = ponto["vivas"]
        universo_ano = ponto["universo_do_ano"]
        sobrevivencia = float(ponto["sobrevivencia_pct"])
        if (not 0 <= vivas <= universo or universo_ano <= 0
                or vivas > universo_ano or not 0 <= sobrevivencia <= 100):
            return False
        esperada = 100.0 * vivas / universo
        if abs(sobrevivencia - esperada) > TOLERANCIA_ARREDONDAMENTO_MORTALIDADE_PCT:
            return False
    return pontos[base_ano]["vivas"] == universo and pontos[ano_final]["vivas"] == sobreviventes


def coorte_ampla_verificada(coorte: dict[str, Any]) -> bool:
    """Contrato compartilhado para publicar ou gravar a coorte SEC ampla."""
    if not isinstance(coorte, dict):
        return False
    try:
        medido_em = date.fromisoformat(coorte["medido_em"])
    except (KeyError, TypeError, ValueError):
        return False
    campos_inteiros = ("ano_base", "ano_final", "universo_base", "sobreviventes")
    if not all(_inteiro_estrito(coorte.get(campo)) for campo in campos_inteiros):
        return False
    if not _numero_finito(coorte.get("mortalidade_pct")):
        return False
    base_ano = coorte["ano_base"]
    ano_final = coorte["ano_final"]
    universo = coorte["universo_base"]
    sobreviventes = coorte["sobreviventes"]
    mortalidade = float(coorte["mortalidade_pct"])
    if (not _periodo_coorte_valido(base_ano, ano_final, medido_em) or universo <= 0
            or not 0 <= sobreviventes <= universo or not 0 <= mortalidade <= 100):
        return False
    esperada = 100.0 * (1 - sobreviventes / universo)
    return (abs(mortalidade - esperada) <= TOLERANCIA_ARREDONDAMENTO_MORTALIDADE_PCT
            and _curva_coorte_verificada(coorte.get("curva"), base_ano, ano_final,
                                          universo, sobreviventes))


def selecionar_coorte_mortalidade(medicao: object) -> tuple[dict[str, Any] | None, str | None]:
    """Seleciona a única coorte publicável e informa invalidez sem fallback.

    A operacional válida tem prioridade. Se sua chave existe mas falha o
    contrato, o segundo retorno é ``"operacional"`` e a coorte ampla não pode
    substituir a lacuna. Sem chave operacional, a ampla só é devolvida após sua
    própria validação.
    """
    dados = medicao if isinstance(medicao, dict) else {}
    if "coorte_operacional" in dados:
        operacional = dados["coorte_operacional"]
        if coorte_operacional_verificada(operacional):
            return operacional, None
        return None, "operacional"
    if "coorte" in dados:
        ampla = dados["coorte"]
        if coorte_ampla_verificada(ampla):
            return ampla, None
        return None, "ampla"
    return None, None


def _contexto_painel_valido(coorte: dict[str, Any], universo: int) -> bool:
    """Campos opcionais do painel só aparecem quando formam um fato coerente."""
    cobertura = coorte.get("cobertura_pct")
    painel = coorte.get("painel_no_ano_base")
    return (_numero_finito(cobertura) and 0 <= float(cobertura) <= 100
            and _inteiro_estrito(painel) and 0 <= painel <= universo)


def _contexto_veiculos_valido(coorte: dict[str, Any]) -> bool:
    """Evita que um opcional malformado vire texto factual ou exceção na UI."""
    veiculos = coorte.get("veiculos_excluidos")
    nao_classificados = coorte.get("nao_classificados")
    return (_inteiro_estrito(veiculos) and veiculos >= 0
            and _inteiro_estrito(nao_classificados) and nao_classificados >= 0)


def _frase_operacional_nao_verificada(coorte: dict[str, Any]) -> str:
    """Expõe a lacuna sem converter percentual parcial em fato de UI."""
    def contagem(chave: str) -> str:
        valor = coorte.get(chave)
        return _mil(valor) if _inteiro_estrito(valor) and valor >= 0 else "não informado"

    desconhecidos = contagem("sem_identidade_apurada")
    sem_identidade = coorte.get("sem_identidade_apurada")
    nao_classificados = coorte.get("nao_classificados")
    if (_inteiro_estrito(sem_identidade) and sem_identidade >= 0
            and _inteiro_estrito(nao_classificados) and nao_classificados >= 0):
        desconhecidos_n = sem_identidade + nao_classificados
        desconhecidos = _mil(desconhecidos_n)
    return ("Mortalidade operacional **NÃO VERIFICADO**: identidade da coorte-base "
            "incompleta ou legado sem cobertura auditável; numerador "
            f"{contagem('sobreviventes')}, denominador {contagem('universo_base')}, "
            f"desconhecidos {desconhecidos}. O percentual operacional não é fato apurado.")


def _frase_ampla_nao_verificada(coorte: dict[str, Any]) -> str:
    """Não transforma payload amplo malformado em percentual factual."""
    def contagem(chave: str) -> str:
        valor = coorte.get(chave)
        return _mil(valor) if _inteiro_estrito(valor) and valor >= 0 else "não informado"

    return ("Mortalidade ampla **NÃO VERIFICADO**: legado sem contrato auditável; "
            f"numerador {contagem('sobreviventes')}, denominador "
            f"{contagem('universo_base')}. O percentual amplo não é fato apurado.")


def frase_mortalidade(medicao: dict[str, Any] | None = None) -> str | None:
    """Frase com o tamanho do viés medido fora do painel, ou None sem medição.

    Prefere a coorte OPERACIONAL quando ela existe. A coorte ampla mede todo
    arquivador de relatório anual -- trust de leasing, emissor de ABS,
    subsidiária de seguradora, fundo fechado -- e o painel analisa ação
    operacional. Veículo termina por desenho, não por fracasso: mantê-lo na
    conta infla a mortalidade que o usuário usa para descontar o retorno. Ver
    `core.us_universo_sec`.
    """
    medicao = carregar_medicao() if medicao is None else medicao
    dados = medicao if isinstance(medicao, dict) else {}
    coorte, invalida = selecionar_coorte_mortalidade(dados)
    if coorte is None and invalida == "operacional":
        operacional = dados.get("coorte_operacional")
        return _frase_operacional_nao_verificada(
            operacional if isinstance(operacional, dict) else {})
    if coorte is None and invalida == "ampla":
        ampla = dados.get("coorte")
        return _frase_ampla_nao_verificada(ampla if isinstance(ampla, dict) else {})
    if coorte is None:
        return None
    try:
        base_ano, final = int(coorte["ano_base"]), int(coorte["ano_final"])
        universo, mortalidade = int(coorte["universo_base"]), float(coorte["mortalidade_pct"])
    except Exception:  # noqa: BLE001
        return None
    populacao = ("companhias operacionais que publicaram relatório anual"
                 if coorte.get("populacao") == "operacional"
                 else "empresas que publicaram relatório anual")
    frase = (f"Tamanho do viés, medido no índice de arquivamentos da SEC: das "
             f"{_mil(universo)} {populacao} em "
             f"{base_ano}, {mortalidade:.0f}% não publicam mais em {final}.")
    cobertura = coorte.get("cobertura_pct")
    painel = coorte.get("painel_no_ano_base")
    if _contexto_painel_valido(coorte, universo):
        pct = f"{float(cobertura):.1f}".replace(".", ",")
        frase += (f" O painel cobre {pct}% daquele universo ({int(painel)} "
                  f"empresas) e nenhuma delas desapareceu. O retorno histórico "
                  f"exibido é teto, não expectativa.")
    # O que foi tirado da conta é dito, não subentendido: sem isso o leitor não
    # tem como saber se a diferença entre este número e o anterior veio de
    # rigor ou de recorte conveniente.
    fora = coorte.get("veiculos_excluidos")
    nao_class = coorte.get("nao_classificados")
    if _contexto_veiculos_valido(coorte):
        frase += (f" Ficaram fora {_mil(fora)} veículos (trust de leasing, "
                  f"ABS, fundo, REIT, SPAC), que encerram por desenho e não por "
                  f"fracasso")
        estrangeiros = coorte.get("estrangeiros_20f_excluidos")
        if _inteiro_estrito(estrangeiros) and estrangeiros >= 0:
            frase += (f", {_mil(estrangeiros)} emissores estrangeiros de 20-F")
        frase += (f" e {_mil(nao_class)} CIKs sem SIC informado, "
                  f"que não entram em nenhum dos dois lados.")
        # A banda é o tamanho honesto da dúvida. Omiti-la faria o ponto parecer
        # exato quando ele não é -- e ela é curta justamente porque o
        # desconhecido é pouco, o que só o leitor pode julgar se puder vê-la.
        minimo, maximo = coorte.get("mortalidade_pct_min"), coorte.get("mortalidade_pct_max")
        if nao_class and _numero_finito(minimo) and _numero_finito(maximo):
            frase += (f" Classificá-los de um jeito ou de outro deixa a "
                      f"mortalidade entre {float(minimo):.1f}% e "
                      f"{float(maximo):.1f}%.").replace(".", ",", 2)
    return frase


# ── O score protege contra perda permanente de capital? (A-158) ──────────────
#
# `frase_mortalidade` diz o tamanho do que ficou de fora, mas não diz se o
# ranking exibido teria evitado essas empresas. Corrigir o backtest exigiria o
# retorno futuro das mortas, que não existe em fonte nossa -- o yfinance não
# serve deslistada e chega a devolver a série de OUTRO papel que herdou o
# ticker. O que é observável sem cotação é o desfecho extremo: a empresa sumiu
# sem ninguém comprar.
#
# `scripts/testar_score_prediz_morte_us.py` calcula o score de produção sobre a
# coorte de 2012 com dados visíveis em 2013-06-30 e confere o desfecho em 2025.
# Aquisição é desfecho SEPARADO de desaparecimento: empresa boa é comprada com
# prêmio, e contar fusão como morte já inverteu a leitura uma vez. Quem sai sem
# deixar marca de falência nem de fusão fica FORA da conta, e o seu tamanho é
# publicado junto -- é a maior parte das saídas, e escondê-la faria o número
# parecer mais completo do que é.
CAMINHO_TESTE_MORTE = (Path(__file__).resolve().parents[1]
                       / "data" / "us_score_vs_morte.json")


def carregar_teste_morte(caminho: Path | str = CAMINHO_TESTE_MORTE
                         ) -> dict[str, Any] | None:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("teste de morte indisponivel: %s", type(exc).__name__)
        return None
    return dados if isinstance(dados, dict) and "apenas_exibiveis" in dados else None


def frase_score_vs_morte(resultado: dict[str, Any] | None = None) -> str | None:
    """Frase com o poder medido do score de separar quem sumiu, ou None.

    A frase muda de sentido conforme o número: AUC perto de 0,50 é confissão de
    que o ranking não protege, e tem de aparecer com a mesma clareza de um
    resultado bom. Frase que só sabe elogiar não é medição.
    """
    resultado = carregar_teste_morte() if resultado is None else resultado
    bloco = (resultado or {}).get("apenas_exibiveis") or {}
    if not bloco or bloco.get("insuficiente"):
        return None
    try:
        auc = float(bloco["auc_nao_sumiu"])
        n = int(bloco["empresas"])
        sumiu = int(bloco["sumiu"])
        indefinidos = int(bloco.get("indefinido") or 0)
        coorte = int(resultado["ano_coorte"])
        desfecho = int(resultado["ano_desfecho"])
    except Exception:  # noqa: BLE001
        return None
    pct = f"{100 * auc:.0f}".replace(".", ",")
    fora = (f" Outras {_mil(indefinidos)} saíram da bolsa sem deixar registro de "
            f"falência nem de fusão e ficaram fora da conta." if indefinidos
            else "")
    base = (f"Teste do ranking contra o desfecho pior de todos: o score "
            f"calculado com os dados de {coorte}, sobre {_mil(n)} empresas "
            f"dessa safra ({_mil(sumiu)} delas pediram falência ou recuperação judicial "
            f"até {desfecho}), acerta {pct}% dos pares ao apontar quem NÃO "
            f"iria quebrar.{fora}")
    if auc < 0.55:
        return base + (" Sorte pura seria 50%: **o ranking não protege contra "
                       "perda permanente de capital** e não deve ser lido como "
                       "se protegesse.")
    if auc < 0.65:
        return base + (" Sorte pura seria 50%: há sinal, mas fraco -- serve "
                       "para inclinar a carteira, não para dispensar análise "
                       "de solvência.")
    return base + (" Sorte pura seria 50%, de modo que o ranking carrega sinal "
                   "real sobre sobrevivência -- ainda assim é probabilidade "
                   "sobre um universo, não garantia sobre uma empresa.")
