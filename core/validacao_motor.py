# -*- coding: utf-8 -*-
"""A-152: qual evidência sustenta cada motor de score, dita na própria aba.

O App 4 tem **três** motores de score independentes sob uma casca visual única:
`core/fii_methodology.py`, `core/us_score.py` e o motor B3 em
`views/empresas_b3.py`. A casca comum faz as três notas parecerem igualmente
sustentadas. Não são, e até aqui só uma delas dizia isso ao usuário.

`design/componentes.aviso_escala_do_score` já declarava que a escala é local e
que as metodologias são independentes -- o que se lê como "diferentes porém
equivalentes". O que faltava era o segundo fato: **que evidência temporal
sustenta cada uma**. Medido em 27/08/2026, contra o Supabase de produção:

  FII  -- mostra "Validação PIT: Aprovada/Pendente" como KPI ao lado do score.
  B3   -- `core.b3_validation.validation_readiness` apura o estado e NOMEIA os
          bloqueadores ("PIT estrito sem published_at/revisões CVM"; "universo
          histórico de deslistadas incompleto"). Nenhuma tela consultava isso:
          o único chamador era o relatório de confiança. Motor de diagnóstico
          sem porta de entrada é decoração.
  EUA  -- declara, mas dentro de um expander colapsado da aba de Criação de
          Portfólio, e só sobre o painel de backtest. A seção de pontuação, que
          é onde a nota aparece, não dizia nada.

Este módulo não inventa nota nem grau: lê as fontes que já existem e devolve o
estado para a aba renderizar. Não valida nada -- só relata quem validou.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["DIMENSOES", "EstadoValidacao", "Portao", "comparacao_de_rigor",
           "validacao_b3", "validacao_fii", "validacao_us"]


# A-162: a lista de perguntas que os TRES motores respondem.
#
# Ate 28/08/2026 cada motor declarava os portoes que lhe convinham -- B3 dois,
# EUA dois, FII um -- e `fracao_aprovada` dividia pelo que cada um tinha
# declarado. O resultado: o FII marcava 100% em "metodologia validada" enquanto
# a B3 marcava 50% e os EUA, 0%. Nao porque o FII fosse mais rigoroso, e sim
# porque ele nunca havia sido perguntado sobre sobrevivencia nem sobre vantagem
# fora da amostra. Quem mede menos ganha nota maior e mede a propria omissao
# como virtude -- a mesma familia de defeito de `medicao-que-pune-a-evidencia`.
#
# Com a lista comum, cada motor responde as tres perguntas ou declara que nao
# apurou. Nao apurado continua fora do denominador, mas agora aparece nomeado
# em vez de simplesmente nao existir.
DIM_PIT = "Visibilidade point-in-time"
DIM_SAIDAS = "Universo com saidas"
DIM_VANTAGEM = "Vantagem fora da amostra"
DIMENSOES = (DIM_PIT, DIM_SAIDAS, DIM_VANTAGEM)


_SEM_VINTAGES = ("vitrine publicada sem score_vintages e preços mensais: "
                 "nenhum retorno histórico foi simulado")


@dataclass(frozen=True)
class Portao:
    """Uma condicao nomeada que a validacao temporal exige.

    Existe para que "metodologia validada" deixe de ser um sim/nao. O indice de
    confianca lia esse booleano e o publicava como `100.0 if ok else 50.0` -- e
    o 50 era uma constante, nao uma medicao. Na pratica o numero nao se mexia:
    marcava 50 antes de o PIT estrito ser liberado em producao e 50 depois,
    incapaz de registrar que um bloqueador real havia caido.

    Com os portoes declarados, a nota e a fracao vencida, e ela se move quando o
    trabalho avanca. `ok=None` e nao apurado, e nunca conta como vencido.
    """

    nome: str
    ok: bool | None
    detalhe: str = ""
    dimensao: str = ""


@dataclass(frozen=True)
class EstadoValidacao:
    """Estado da validação temporal de um motor de score.

    `aprovada` é tri-estado de propósito: `None` significa *não foi possível
    apurar*, e não pode virar "pendente" nem "aprovada". Apagar a diferença
    entre "medi e reprovou" e "não consegui medir" é o defeito que este módulo
    existe para não repetir.
    """
    classe: str
    versao: str
    aprovada: bool | None
    bloqueadores: tuple[str, ...] = ()
    detalhe: str = ""
    portoes: tuple[Portao, ...] = ()

    @property
    def fracao_aprovada(self) -> float | None:
        """Fracao dos portoes APURADOS que foram vencidos. ``None`` = nao apurado.

        Sem portoes declarados, cai no booleano -- 1.0 ou 0.0, nunca um meio
        termo inventado. Reprovar em portao unico vale zero: credito parcial so
        existe onde ha mais de uma condicao e uma delas foi de fato cumprida.
        """
        apurados = [p for p in self.portoes if p.ok is not None]
        if apurados:
            return sum(1.0 for p in apurados if p.ok) / len(apurados)
        if self.aprovada is None:
            return None
        return 1.0 if self.aprovada else 0.0

    @property
    def rotulo(self) -> str:
        if self.aprovada is None:
            return "Não apurada"
        return "Aprovada" if self.aprovada else "Pendente"

    @property
    def texto(self) -> str:
        """Frase única para caption, com os bloqueadores nomeados."""
        base = f"Validação temporal (PIT): {self.rotulo.lower()}"
        if self.aprovada:
            return f"{base}. Metodologia {self.versao}."
        if self.bloqueadores:
            return (f"{base} — {'; '.join(self.bloqueadores)}. "
                    f"Metodologia {self.versao}. A nota ordena o universo; "
                    f"ela ainda não foi verificada fora da amostra.")
        return (f"{base}. Metodologia {self.versao}. A nota ordena o universo; "
                f"ela ainda não foi verificada fora da amostra.")


def _detalhe(bloqueadores: tuple[str, ...], marca: str) -> str:
    for b in bloqueadores:
        if marca in b:
            return b[:90]
    return ""


def _falha(classe: str, versao: str, exc: Exception) -> EstadoValidacao:
    logger.warning("validacao_motor %s: %s", classe, exc)
    return EstadoValidacao(classe, versao, None,
                           detalhe=f"não apurado: {type(exc).__name__}")


def _vantagem_nao_apurada(motivo: str) -> Portao:
    """A pergunta existe para os tres motores; a resposta, so onde foi medida.

    Nao apurado nao vira reprovado nem aprovado: sai do denominador e continua
    escrito. O que nao pode acontecer e a pergunta desaparecer para o motor que
    nao a responde -- foi assim que o FII chegou a 100% de "metodologia
    validada" com uma pergunta a menos que os outros dois.
    """
    return Portao("Vantagem fora da amostra", None, motivo, dimensao=DIM_VANTAGEM)


def _vantagem_persistida(motor: str, versao: str) -> Portao:
    """Le a medicao gravada por `scripts/medir_vantagem_oos.py` (SCORE-05).

    B3 e EUA calculam a vantagem dentro da propria tela e a esquecem no fim do
    rerun; sem persistencia, a tela de Grau de Confianca -- que roda em outra
    sessao -- so podia dizer "nao apurado". A leitura e do arquivo, nunca um
    recalculo aqui: a medicao depende do armazem local, que a nuvem nao alcanca.
    """
    from core.vantagem_oos import avaliar
    veredito, detalhe = avaliar(motor, versao)
    if veredito is None:
        return _vantagem_nao_apurada(detalhe)
    return Portao("Vantagem fora da amostra", veredito, detalhe,
                  dimensao=DIM_VANTAGEM)


def _saidas_fii(engine=None) -> Portao:
    """O universo historico de FIIs observa fundos que deixaram de existir?

    Fundo imobiliario tambem acaba: liquida, incorpora, sai da negociacao. Se o
    historico so contem quem esta listado hoje, o backtest mede a carteira de
    quem sobreviveu -- e `core/fii_validation.py` chega a exigir cobertura de
    retornos alta, que e justamente o que um painel sem saidas entrega de graca.

    Medido em 28/08/2026 no Supabase: `market.fii_universe_history` tinha 1.029
    tickers, TODOS com `active_status = 'listed'`, em 2 datas de referencia.
    """
    from sqlalchemy import text
    try:
        from core.database import get_engine
        with (engine or get_engine()).connect() as conn:
            linha = conn.execute(text(
                "SELECT count(DISTINCT ticker) FILTER "
                "  (WHERE active_status NOT IN ('listed','active')), "
                "       count(DISTINCT ticker), count(DISTINCT reference_date) "
                "FROM market.fii_universe_history")).first()
    except Exception as exc:  # noqa: BLE001
        logger.info("saidas_fii sem fii_universe_history: %s", type(exc).__name__)
        return Portao("Universo com saidas", None,
                      "historico de universo nao alcancavel nesta base",
                      dimensao=DIM_SAIDAS)
    saidas, total, _datas = (int(v or 0) for v in linha)
    if saidas:
        return Portao("Universo com saidas", True,
                      f"{saidas} de {total} fundos ja sairam do universo",
                      dimensao=DIM_SAIDAS)
    return Portao("Universo com saidas", False,
                  f"nenhum dos {total} fundos consta como encerrado; "
                  + _porque_sem_saidas_fii(engine), dimensao=DIM_SAIDAS)


def _porque_sem_saidas_fii(engine=None) -> str:
    """Distingue "nao houve saida" de "nao havia como haver saida".

    A frase anterior -- "o historico so tem quem continua listado" -- sugeria
    que saidas foram procuradas e nao encontradas. Com uma unica foto completa
    do universo, ausencia nao e sequer observavel: o portao so podia dar False,
    e um criterio inalcancavel nunca e revisto. `core.fii_saidas` sabe qual dos
    dois casos e o atual e a frase passa a dizer.
    """
    try:
        from core.database import get_engine
        from core.fii_saidas import derivar_saidas, fotos_do_banco
        with (engine or get_engine()).connect() as conn:
            diag = derivar_saidas(fotos_do_banco(conn))
    except Exception as exc:  # noqa: BLE001
        logger.info("diagnostico de saidas FII indisponivel: %s", type(exc).__name__)
        return "historico de universo nao diagnosticavel nesta base"
    return diag.motivo or "sem diagnostico"


def _vantagem_fii(metrics: dict) -> Portao:
    """O excesso sobre o IFIX e distinguivel de zero?

    `validate_methodology` exige que o intervalo bootstrap EXISTA -- nunca que
    ele exclua o zero. Um certificado pode entao sair "passed" com o excesso
    medio dentro de um intervalo que atravessa o zero, e a tela mostra
    "Validacao PIT: Aprovada" em verde ao lado da nota. O usuario le isso como
    "bate o indice"; o portao nunca testou isso.

    Aqui a pergunta e feita explicitamente. O certificado continua valendo pelo
    que ele de fato atesta -- integridade do protocolo point-in-time --, e a
    vantagem economica vira um portao separado, com o intervalo no detalhe.
    """
    ci = ((metrics or {}).get("backtest") or {}).get("excess_bootstrap") or {}
    low, high = ci.get("lower"), ci.get("upper")
    try:
        low, high = float(low), float(high)
    except (TypeError, ValueError):
        return _vantagem_nao_apurada(
            "certificado sem intervalo bootstrap do excesso")
    if low != low or high != high:  # NaN
        return _vantagem_nao_apurada(
            "certificado sem intervalo bootstrap do excesso")
    faixa = f"IC 95% do excesso por periodo: {low:+.2%} a {high:+.2%}"
    if low > 0:
        return Portao("Vantagem fora da amostra", True, faixa,
                      dimensao=DIM_VANTAGEM)
    return Portao("Vantagem fora da amostra", False,
                  f"{faixa} -- atravessa o zero, entao a vantagem sobre o "
                  f"indice nao e distinguivel de acaso", dimensao=DIM_VANTAGEM)


def comparacao_de_rigor(estados: "tuple[EstadoValidacao, ...] | None" = None,
                        engine=None
                        ) -> dict[str, dict[str, Portao | None]]:
    """As tres notas lado a lado na MESMA lista de perguntas.

    Sem isto a comparacao entre os motores fica por conta do usuario, e a casca
    visual unica sugere que as tres notas se equivalem. Devolve
    ``{classe: {dimensao: Portao | None}}``; ``None`` significa que o motor nao
    declara aquela dimensao -- diferente de declarar e nao apurar.
    """
    if estados is None:
        estados = (validacao_b3(engine), validacao_fii(engine),
                   validacao_us(engine=engine))
    return {e.classe: {d: next((p for p in e.portoes if p.dimensao == d), None)
                       for d in DIMENSOES} for e in estados}


def validacao_b3(engine=None) -> EstadoValidacao:
    """Lê `core.b3_validation.validation_readiness` -- os bloqueadores são dele."""
    from core.b3_methodology import SCORE_VERSION
    try:
        from core.b3_validation import build_data_manifest, validation_readiness
        from core.database import get_engine
        pronto = validation_readiness(build_data_manifest(engine or get_engine()))
        bloq = tuple(str(b) for b in (pronto.get("blockers") or []))
        portoes = tuple(
            Portao(nome, not any(marca in b for b in bloq), _detalhe(bloq, marca),
                   dimensao=dim)
            for nome, marca, dim in (
                ("PIT estrito", "PIT estrito", DIM_PIT),
                ("Universo de deslistadas", "deslistadas", DIM_SAIDAS)))
        portoes += (_vantagem_persistida("b3", SCORE_VERSION),)
        return EstadoValidacao("Empresas B3", SCORE_VERSION,
                               bool(pronto.get("ready")), bloq, portoes=portoes)
    except Exception as exc:  # noqa: BLE001
        return _falha("Empresas B3", SCORE_VERSION, exc)


def validacao_fii(engine=None) -> EstadoValidacao:
    """Lê o certificado PIT persistido para a metodologia em uso."""
    from core.fii_methodology import METHODOLOGY_VERSION
    try:
        from core.market_read import load_fii_validation_status
        val = load_fii_validation_status(METHODOLOGY_VERSION, engine=engine) or {}
        bloq = tuple(str(b) for b in (val.get("blockers") or []))
        passou = str(val.get("status")) == "passed"
        # Portao unico: o certificado PIT ou existe e aprovou, ou nao. Aqui nao
        # cabe credito parcial -- e por isso que reprovar vale zero, e nao a
        # metade que a formula antiga concedia de graca.
        portoes = (Portao("Certificado PIT", passou,
                          "; ".join(bloq)[:90] if bloq else "",
                          dimensao=DIM_PIT),
                   _saidas_fii(engine),
                   _vantagem_fii(val.get("metrics") or {}))
        return EstadoValidacao("Seleção de FIIs", METHODOLOGY_VERSION,
                               passou, bloq, portoes=portoes)
    except Exception as exc:  # noqa: BLE001
        return _falha("Seleção de FIIs", METHODOLOGY_VERSION, exc)


def _deslistadas_us_pelo_painel() -> Portao:
    """Mesmo portao, medido pelo turnover do painel quando `assets` nao existe.

    Na base publicada o schema `market_us` so tem `company_snapshots` e
    `prices_monthly`, entao a contagem de `delisted_date` nao roda -- e o portao
    virava "nao apurado" justamente em producao, que e onde o usuario decide.
    Mas o painel ja responde a pergunta por outro caminho: se nenhuma empresa
    saiu do universo entre safras, o universo e 100% sobrevivente. A medicao
    vem de `core.us_survivorship`, gravada por quem alcanca o armazem.

    Sem medicao gravada, continua nao apurado. Ausencia nao vira zero.
    """
    from core.us_survivorship import (
        carregar_medicao,
        medicao_turnover_verificada,
        selecionar_coorte_mortalidade,
    )

    med = carregar_medicao()
    if not med:
        return Portao("Universo de deslistadas", None,
                      "fonte de deslistagem nao alcancavel nesta base",
                      dimensao=DIM_SAIDAS)
    if not medicao_turnover_verificada(med):
        return Portao(
            "Universo de deslistadas", None,
            "turnover do painel **NÃO VERIFICADO**: agregado sem "
            "contrato auditável", dimensao=DIM_SAIDAS)
    saidas = med["saidas"]
    safras = med["safras"]
    if saidas:
        return Portao("Universo de deslistadas", True,
                      f"{saidas} saidas de empresas em {safras} safras do painel",
                      dimensao=DIM_SAIDAS)
    # "zero saidas" reprova, mas não diz de quanto é o buraco. A seleção é o
    # mesmo contrato usado pela frase: operacional válida vence a ampla; uma
    # operacional inválida nunca cai para ampla.
    coorte, invalida = selecionar_coorte_mortalidade(med)
    if invalida:
        return Portao(
            "Universo de deslistadas", None,
            f"coorte {invalida} **NÃO VERIFICADO**: não há tamanho auditável "
            "do viés de sobrevivência", dimensao=DIM_SAIDAS)
    tamanho = ""
    if coorte is not None:
        populacao = ("companhias operacionais" if coorte.get("populacao") == "operacional"
                     else "empresas")
        tamanho = (f"; no mercado real {float(coorte['mortalidade_pct']):.0f}% das "
                   f"{int(coorte['universo_base'])} {populacao} de "
                   f"{int(coorte['ano_base'])} sumiram ate "
                   f"{int(coorte['ano_final'])}")
    return Portao(
        "Universo de deslistadas", False,
        f"nenhuma saida de empresa em {safras} safras: o painel so tem "
        f"sobreviventes{tamanho}", dimensao=DIM_SAIDAS)


def _tem_coluna(conn, tabela: str, coluna: str) -> bool:
    """A coluna existe em `market_us.<tabela>` neste banco?

    Warehouse local e vitrine nao andam no mesmo passo: a migration 058 pode
    ter rodado num e nao no outro. Perguntar antes custa uma consulta ao
    catalogo; nao perguntar aborta a transacao e derruba a contagem seguinte
    por arrasto, reportando a causa errada.
    """
    from sqlalchemy import text
    try:
        return bool(conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'market_us' AND table_name = :t "
            "  AND column_name = :c"), {"t": tabela, "c": coluna}).first())
    except Exception:  # noqa: BLE001
        return False


# A-159: quanto do registro de saidas o painel precisa consumir para que o
# portao possa dizer que o vies foi corrigido.
#
# O criterio anterior era `if no_painel:` -- qualquer numero acima de zero
# aprovava. Enquanto a tabela so existia no armazem local isso nunca aparecia,
# porque a resposta em producao era sempre None. Em 31/08/2026, no dia em que
# `publish_us_delistings --apply` levou as 12.107 linhas para a vitrine, o
# portao passou a aprovar com SETE saidas de 11.793 (0,06%) -- e a aprovacao
# significaria que o backtest americano deixou de medir sobreviventes. E o
# `gate-que-so-dava-false` pelo avesso: o criterio inalcancavel nunca foi
# revisto, e no dia em que a fonte chegou ele promoveu a base inteira.
#
# O piso e uma escolha declarada, nao uma medida: abaixo dele o portao reprova
# NOMEANDO a fracao, que e o numero de que o leitor precisa. Sete de 11.793 nao
# e um comeco de correcao -- 1.882 das 1.889 saidas com simbolo resolvido nao
# tem nenhuma linha em `score_vintages`, porque as safras sao construidas a
# partir do universo vivo. Registrar a saida nao a coloca no painel; e o painel
# que precisa ser reconstruido incluindo quem saiu.
_PISO_SAIDAS_NO_PAINEL = 0.10


def _registro_de_saidas_us(engine=None) -> tuple[int, int] | None:
    """(saidas registradas, quantas dessas o painel de backtest enxerga).

    As duas contagens andam juntas de proposito. Registrar a saida e o passo
    barato; o que corrige o vies e o backtest CONSUMIR a saida. Enquanto o
    painel nao enxergar nenhuma delas, o resultado medido continua sendo o de
    uma amostra sobrevivente, por maior que seja o registro -- e aprovar o
    portao pelo tamanho do registro seria declarar um rigor que a medicao nao
    tem, exatamente o defeito que a tela dos EUA ja cometeu uma vez.
    """
    from sqlalchemy import text
    try:
        from core.database import get_engine
        with (engine or get_engine()).connect() as conn:
            # A saida refutada -- relatorio anual arquivado em ano igual ou
            # posterior ao da ausencia -- nao conta como saida. Ela e artefato
            # de uma lista de formas que nao continha 40-F nem emenda, e somar
            # morte inventada ao registro inflaria justamente o numero que este
            # portao usa para julgar se o vies foi corrigido.
            # `refuted_by` cobre as duas portas: relatorio anual posterior
            # sob o mesmo CIK, e papel que seguiu negociando depois da
            # saida (sucessao de registrante -- BlackRock, Bunge, Noble,
            # Ferguson). A segunda e a que pega o caso comum: a primeira
            # sozinha derrubava 1 de 60 saidas ja nomeadas com cotacao.
            onde = ("WHERE refuted_by IS NULL"
                    if _tem_coluna(conn, "delistings", "refuted_by") else
                    "WHERE refuted_form IS NULL"
                    if _tem_coluna(conn, "delistings", "refuted_form") else "")
            total = int(conn.execute(text(
                f"SELECT count(*) FROM market_us.delistings {onde}")).scalar() or 0)
            # A juncao e por SIMBOLO, nao por `company_id`: a esmagadora maioria
            # das saidas nunca teve linha em `companies` (elas sairam antes de
            # o cadastro existir), e a `score_vintages` publicada na vitrine nem
            # carrega `company_id`. Juntar pela chave do cadastro so encontrava
            # quem sobreviveu o bastante para ser cadastrado.
            no_painel = int(conn.execute(text(
                "SELECT count(DISTINCT d.cik) FROM market_us.delistings d "
                "JOIN market_us.score_vintages v "
                "  ON upper(v.symbol) = upper(d.symbol) "
                f"{onde or 'WHERE 1=1'} AND d.symbol IS NOT NULL")).scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.info("registro de saidas US indisponivel: %s", type(exc).__name__)
        return None
    return total, no_painel


def _deslistadas_us(engine=None) -> Portao:
    """O universo historico americano observa empresas que pararam de negociar?

    Medido em 27/08/2026: `delisted_date` era NULL nos 7.654 registros de
    `market_us.assets`, e nenhuma empresa deslistada entrava em
    `score_vintages`. A coluna existe e o pipeline a le em
    `data_pipeline/us/scoring_history.py`; ninguem nunca a preencheu.

    Ate aqui o motor americano tinha UM portao -- "existe painel PIT" -- enquanto
    a B3 tinha dois, e o segundo da B3 era justamente sobrevivencia. O resultado
    era o defeito do A-153 repetido em outro eixo: a classe que MEDE a limitacao
    pontuava pior que a classe que nao a mede. O EUA nao estava melhor; estava
    sem regua.

    Devolve `ok=None` quando a fonte nao e alcancavel -- na producao publicada o
    schema so tem `company_snapshots` e `prices_monthly`. Nao apurado nao vira
    reprovado nem aprovado; some da fracao e declara que sumiu.
    """
    reg = _registro_de_saidas_us(engine)
    if reg is not None and reg[0]:
        total, no_painel = reg
        fracao = no_painel / total
        if fracao >= _PISO_SAIDAS_NO_PAINEL:
            return Portao(
                "Universo de deslistadas", True,
                f"{no_painel} das {total} saidas registradas ({fracao:.0%}) "
                f"entram no painel de backtest", dimensao=DIM_SAIDAS)
        if no_painel:
            return Portao(
                "Universo de deslistadas", False,
                f"so {no_painel} das {total} saidas registradas ({fracao:.1%}) "
                f"chegam ao painel: o backtest segue medindo, na pratica, um "
                f"universo sobrevivente", dimensao=DIM_SAIDAS)
        return Portao(
            "Universo de deslistadas", False,
            f"{total} saidas registradas em market_us.delistings, mas nenhuma "
            f"chega ao painel: o backtest continua medindo so sobreviventes",
            dimensao=DIM_SAIDAS)

    from sqlalchemy import text
    try:
        from core.database import get_engine
        with (engine or get_engine()).connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM market_us.assets "
                "WHERE delisted_date IS NOT NULL")).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.info("deslistadas_us sem market_us.assets: %s", type(exc).__name__)
        return _deslistadas_us_pelo_painel()
    n = int(n or 0)
    return Portao(
        "Universo de deslistadas", n > 0,
        f"{n} deslistagens no universo" if n
        else "nenhuma deslistagem ingerida: o historico so tem sobreviventes",
        dimensao=DIM_SAIDAS)


def validacao_us(history_available: object = None, engine=None) -> EstadoValidacao:
    """Estado do motor americano.

    Sem `score_vintages` e preços mensais na vitrine não há Rank-IC fora da
    amostra -- é a mesma condição que a aba de Criação de Portfólio já usa para
    decidir se roda o painel PIT. Aceita o valor já apurado pela tela para não
    repetir a consulta; sem ele, apura por conta própria.
    """
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION as v
    if history_available is not None:
        pronto = bool(history_available)
        return EstadoValidacao(
            "Empresas Americanas", v, pronto,
            () if pronto else (_SEM_VINTAGES,),
            portoes=(Portao("Painel PIT", pronto,
                            "" if pronto else _SEM_VINTAGES, dimensao=DIM_PIT),
                     _deslistadas_us(engine),
                     _vantagem_persistida("us", v)))
    try:
        import core.us_data as us
        painel = us.score_panel()
        pronto = painel is not None and not painel.empty
        return EstadoValidacao(
            "Empresas Americanas", v, pronto,
            () if pronto else (_SEM_VINTAGES,),
            portoes=(Portao("Painel PIT", pronto,
                            "" if pronto else _SEM_VINTAGES, dimensao=DIM_PIT),
                     _deslistadas_us(engine),
                     _vantagem_persistida("us", v)))
    except Exception as exc:  # noqa: BLE001
        return _falha("Empresas Americanas", v, exc)
