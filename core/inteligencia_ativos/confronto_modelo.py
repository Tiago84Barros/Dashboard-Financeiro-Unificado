"""
core/inteligencia_ativos/confronto_modelo.py
Confronto da carteira do investidor com a carteira recomendada do Portfólio
Global, na Inteligência dos Ativos (pedido do usuário de 08/11/2026). Puro,
sem I/O: quem lê o banco é a tela (``views/inteligencia_ativos.py``).

O que ele responde, sempre que o Portfólio Global tem modelo e alocação:

1. **Qualidade**: a nota média ponderada da carteira contra a do modelo, no
   total e por classe, mais o peso com alerta eliminatório e com qualidade
   frágil de cada lado.
2. **Pesos por classe**: real × modelo × a sua estratégia, e o que fazer
   quando a divergência passa de ``LIMIAR_CLASSE_PP``.
3. **Pesos por ativo**: toda divergência de ``LIMIAR_ATIVO_PP`` ou mais
   ganha uma conclusão (mudar ou manter a escolha do investidor) e os motivos.
4. **Inclusões**: os ativos do modelo fora da carteira, cada um com a
   conclusão (candidato, não incluir agora, sem avaliação) e o porquê.

Por que isto não reabre a confirmação circular que ``referencia_modelo``
evita (o modelo é montado em parte com o veredito desta aba):

* A qualidade dos DOIS lados é medida pela MESMA régua, a ``Avaliacao`` da
  Inteligência (``veredito.avaliar_ativo``), e nunca por "estar no modelo".
* Para ativo da carteira, a conclusão nunca contradiz a decisão da
  Inteligência (``veredito.Veredito``): se ela barra o aporte, o confronto não
  manda aportar porque o modelo pesa mais; se ela não pede venda, ele não
  manda vender porque o modelo pesa menos.
* Ativo do modelo só vira candidato quando a avaliação dele mesmo deixa
  (limite livre, qualidade forte ou adequada, sem perder para a média dos
  seus ativos da classe) e a sua estratégia prevê a classe.
* Nada daqui entra em ``analise``, ``adequacao``, ``veredito`` nem na "Ação a
  considerar" (``tests/test_inteligencia_ativos_referencia_modelo.py``).

Coberto por tests/test_inteligencia_ativos_confronto_modelo.py.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import referencia_modelo as refm
from core.inteligencia_ativos import resumida as rs
from core.inteligencia_ativos import veredito as vd

# Divergência acentuada: abaixo disso é ruído de arredondamento e de preço.
LIMIAR_ATIVO_PP = 3.0
LIMIAR_CLASSE_PP = 5.0
# Ativos do modelo fora da carteira avaliados pela régua da Inteligência, do
# maior peso para o menor. Cada avaliação lê fundamentos, valuation, pares e
# notícias; o resto fica "sem avaliação", com a contagem na tela.
MAX_AVALIADOS_MODELO = 25
# Candidatos que vão para a LLM do Portfolio Fit.
N_CANDIDATOS_LLM = 5

CLASSES_ATIVO = ("b3", "fii", "us")   # as que o modelo monta ativo a ativo
# Classe do Portfólio Global → classe da sua estratégia.
CLASSE_POLITICA = {"b3": "acoes_br", "fii": "fiis", "us": "exterior",
                   "renda_fixa": "renda_fixa"}

NOTA_QUALIDADE = {av_.FORTE: 3.0, av_.ADEQUADA: 2.0, av_.FRAGIL: 1.0}

MUDAR, PERMANECER, INCLUIR, NAO_INCLUIR, SEM_AVALIACAO = (
    "mudar", "permanecer", "incluir", "nao_incluir", "sem_avaliacao")
ROTULO_CONCLUSAO = {
    MUDAR: "Mudança sugerida",
    PERMANECER: "Manter a sua escolha",
    INCLUIR: "Candidato a inclusão",
    NAO_INCLUIR: "Não incluir agora",
    SEM_AVALIACAO: "Sem avaliação",
}

AVISO = ("Confronto calculado em código. A qualidade dos dois lados usa a "
         "mesma régua da Inteligência; estar ou não estar no modelo nunca "
         "conta como argumento, e nenhuma conclusão contradiz a decisão da "
         "Inteligência para o ativo.")


def rotulo_nota(nota: float | None) -> str:
    if nota is None:
        return "sem leitura"
    if nota >= 2.5:
        return "Forte"
    if nota >= 1.5:
        return "Adequada"
    return "Frágil"


def _fmt_nota(nota: float | None) -> str:
    if nota is None:
        return "sem leitura"
    return f"{rotulo_nota(nota)} ({nota:.1f})".replace(".", ",")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def _pp(x: float) -> str:
    return f"{abs(x):.1f} pp".replace(".", ",")


def _r(x: float | None, casas: int = 2) -> float | None:
    return None if x is None else round(float(x), casas)


# -- qualidade ------------------------------------------------------------------

@dataclass(frozen=True)
class QualidadeLado:
    nota: float | None       # média ponderada pelo peso, de 1 (frágil) a 3
    cobertura: float         # % do peso do lado com leitura de qualidade
    peso_com_alerta: float   # % do peso do lado com alerta eliminatório
    peso_fragil: float       # % do peso do lado com qualidade frágil
    n_ativos: int
    n_avaliados: int

    @property
    def rotulo(self) -> str:
        return rotulo_nota(self.nota)

    def como_dict(self) -> dict:
        return {"qualidade_media": _fmt_nota(self.nota),
                "nota_1_a_3": _r(self.nota),
                "cobertura_pct": _r(self.cobertura, 1),
                "peso_com_alerta_eliminatorio_pct": _r(self.peso_com_alerta, 1),
                "peso_qualidade_fragil_pct": _r(self.peso_fragil, 1),
                "ativos": self.n_ativos, "avaliados": self.n_avaliados}


def qualidade(itens) -> QualidadeLado:
    """``itens`` = [(peso, Avaliacao | None)]. Peso sem avaliação, ou com
    dado insuficiente, sai da média e reduz a cobertura: não vira nota zero."""
    itens = [(float(p or 0.0), a) for p, a in itens if (p or 0.0) > 0]
    total = sum(p for p, _ in itens)
    if total <= 0:
        return QualidadeLado(None, 0.0, 0.0, 0.0, 0, 0)
    com_nota = [(p, NOTA_QUALIDADE[a.qualidade]) for p, a in itens
                if a is not None and a.qualidade in NOTA_QUALIDADE]
    peso_nota = sum(p for p, _ in com_nota)
    nota = (sum(p * n for p, n in com_nota) / peso_nota) if peso_nota else None
    alerta = sum(p for p, a in itens if a is not None and a.criticos)
    fragil = sum(p for p, a in itens if a is not None
                 and a.qualidade == av_.FRAGIL)
    return QualidadeLado(nota=nota, cobertura=peso_nota / total * 100,
                         peso_com_alerta=alerta / total * 100,
                         peso_fragil=fragil / total * 100,
                         n_ativos=len(itens),
                         n_avaliados=sum(1 for _, a in itens if a is not None))


@dataclass(frozen=True)
class QualidadeClasse:
    classe: str
    rotulo: str
    carteira: QualidadeLado
    modelo: QualidadeLado

    def como_dict(self) -> dict:
        return {"classe": self.rotulo, "carteira": self.carteira.como_dict(),
                "modelo": self.modelo.como_dict()}


# -- divergências -----------------------------------------------------------------

@dataclass(frozen=True)
class Divergencia:
    ticker: str
    nome: str
    classe: str | None
    situacao: str
    peso_modelo: float | None
    peso_real: float | None
    conclusao: str
    texto: str
    motivos: tuple[str, ...] = ()
    decisao: str | None = None        # rótulo da decisão da Inteligência
    qualidade: str | None = None      # leitura de qualidade da avaliação
    nota: float | None = None

    @property
    def diferenca(self) -> float:
        return (self.peso_real or 0.0) - (self.peso_modelo or 0.0)

    @property
    def rotulo_conclusao(self) -> str:
        return ROTULO_CONCLUSAO[self.conclusao]

    def como_dict(self) -> dict:
        return {"ticker": self.ticker,
                "situacao": refm.ROTULO_SITUACAO[self.situacao],
                "peso_modelo_pct": _r(self.peso_modelo),
                "peso_real_pct": _r(self.peso_real),
                "diferenca_pp": _r(self.diferenca),
                "conclusao": self.rotulo_conclusao, "explicacao": self.texto,
                "motivos": list(self.motivos),
                "decisao_inteligencia": self.decisao,
                "qualidade": self.qualidade}


@dataclass(frozen=True)
class DivergenciaClasse:
    classe: str
    rotulo: str
    alvo_modelo: float | None
    real: float
    alvo_estrategia: float | None     # na mesma base dos pesos
    conclusao: str
    texto: str

    @property
    def diferenca(self) -> float:
        return self.real - (self.alvo_modelo or 0.0)

    @property
    def acentuada(self) -> bool:
        return (self.alvo_modelo is not None
                and abs(self.diferenca) >= LIMIAR_CLASSE_PP)

    def como_dict(self) -> dict:
        return {"classe": self.rotulo,
                "alvo_modelo_pct": _r(self.alvo_modelo),
                "real_pct": _r(self.real),
                "alvo_sua_estrategia_pct": _r(self.alvo_estrategia),
                "diferenca_para_modelo_pp": _r(self.diferenca),
                "conclusao": ROTULO_CONCLUSAO[self.conclusao],
                "explicacao": self.texto}


@dataclass(frozen=True)
class Confronto:
    disponivel: bool
    motivo: str = ""
    base: str = refm.BASE_PATRIMONIO
    carteira: QualidadeLado | None = None
    modelo: QualidadeLado | None = None
    por_classe: tuple[QualidadeClasse, ...] = ()
    classes: tuple[DivergenciaClasse, ...] = ()
    divergencias: tuple[Divergencia, ...] = ()
    candidatos: tuple[Divergencia, ...] = ()
    nao_avaliados: int = 0            # do modelo, fora da carteira, sem avaliação
    aderencia: float | None = None    # Σ min(real, modelo), em % da base
    avisos: tuple[str, ...] = ()

    def da_carteira(self, ticker) -> Divergencia | None:
        tk = refm.normalizar(ticker)
        return next((d for d in self.divergencias if d.ticker == tk), None)

    def por_conclusao(self, conclusao: str) -> tuple[Divergencia, ...]:
        return tuple(d for d in self.candidatos if d.conclusao == conclusao)

    @property
    def classes_acentuadas(self) -> tuple[DivergenciaClasse, ...]:
        return tuple(c for c in self.classes if c.acentuada)

    def para_llm(self, ticker=None) -> dict:
        """O confronto em ``app_model_reference["confronto"]``."""
        if not self.disponivel:
            return {"estado": "indisponível", "motivo": self.motivo}
        saida = {
            "estado": "disponível",
            "natureza": AVISO,
            "base_dos_pesos": refm.ROTULO_BASE[self.base],
            "aderencia_ao_modelo_pct": _r(self.aderencia, 1),
            "qualidade_carteira": self.carteira.como_dict(),
            "qualidade_modelo": self.modelo.como_dict(),
            "qualidade_por_classe": [q.como_dict() for q in self.por_classe],
            "classes_com_divergencia_acentuada": [
                c.como_dict() for c in self.classes_acentuadas],
            "candidatos_a_inclusao": [
                d.como_dict() for d in self.por_conclusao(INCLUIR)
            ][:N_CANDIDATOS_LLM],
        }
        if ticker is not None:
            d = self.da_carteira(ticker)
            saida["ativo"] = (d.como_dict() if d is not None else
                              {"ticker": refm.normalizar(ticker),
                               "divergencia": "nenhuma divergência acentuada "
                               f"(abaixo de {LIMIAR_ATIVO_PP:.0f} pp)"})
        return saida


def indisponivel(motivo: str) -> Confronto:
    return Confronto(disponivel=False, motivo=motivo)


def a_avaliar(ref: refm.ReferenciaModelo,
              n: int = MAX_AVALIADOS_MODELO) -> list[refm.LinhaAtivo]:
    """Os ativos do modelo fora da carteira que a tela deve avaliar."""
    if not ref.disponivel:
        return []
    return ref.fora_da_carteira(n)


# -- regras de conclusão ------------------------------------------------------------

def _rotulo_classe(classe: str | None) -> str:
    from core.global_portfolio import carteira_real
    return carteira_real.ROTULOS.get(classe or "", classe or "classe")


def _conclusao_da_carteira(linha: refm.LinhaAtivo,
                           v: vd.Veredito | None) -> tuple[str, str, list[str]]:
    """Ativo que o investidor tem, com divergência acentuada. A conclusão é a
    decisão da Inteligência lida contra o modelo, nunca contra ela."""
    dif = linha.diferenca or 0.0
    fora = linha.situacao == refm.SO_NA_CARTEIRA
    onde = ("não está no modelo" if fora else
            f"pesa {_pp(dif)} {'acima' if dif > 0 else 'abaixo'} do modelo")
    if v is None:
        return (SEM_AVALIACAO,
                f"{linha.ticker} {onde}, mas não tem decisão da Inteligência "
                "nesta carteira: a divergência fica registrada, sem "
                "recomendação.", [])
    d = v.recomendacao
    codigo = getattr(d, "codigo", None)
    motivos = [f"Decisão da Inteligência: {v.decisao} — {v.detalhe}"]
    if v.avaliacao is not None:
        motivos.append(f"Avaliação do ativo: {v.avaliacao.resumo()}")
    porque = v.motivo or v.detalhe

    if codigo in (rs.VENDER, rs.SUBSTITUIR):
        sub = getattr(d, "substituto", None)
        if sub is not None:
            motivos.append(f"Substituto apontado pela Inteligência: "
                           f"{sub.resumo}.")
        return (MUDAR,
                f"{linha.ticker} {onde}. A Inteligência indica "
                f"{v.decisao.lower()} pela avaliação do próprio ativo "
                f"({porque}), e não por causa do modelo.", motivos)

    if dif > 0:
        if codigo == rs.REDUZIR_PARCIAL:
            prop = getattr(d, "proporcao", None)
            quanto = f" (cerca de {prop * 100:.0f}% da posição)" if prop else ""
            return (MUDAR,
                    f"{linha.ticker} {onde}. Reduzir parte{quanto}, como a "
                    f"Inteligência já indica ({porque}).", motivos)
        if fora:
            motivos.append("O modelo do Portfólio Global escolhe poucos nomes "
                           "por classe; ficar fora dele não é defeito do "
                           "ativo.")
        motivos.append("Vender só para copiar o modelo gera custo e imposto "
                       "sem melhorar a qualidade medida.")
        if codigo == rs.COMPRAR:
            motivos.append("Pela sua estratégia ainda há espaço para o ativo; "
                           "a premissa desta aba é a sua estratégia, não o "
                           "modelo.")
        else:
            motivos.append("Para se aproximar do modelo sem vender, deixe de "
                           "reforçar a posição e direcione os próximos "
                           "aportes a outros ativos.")
        if v.avaliacao is not None and v.avaliacao.qualidade in (av_.FORTE,
                                                                  av_.ADEQUADA):
            qual = av_.ROTULO_LEITURA[v.avaliacao.qualidade].lower()
            motivos.insert(1, f"Qualidade {qual}: a permanência se sustenta "
                              "pelo próprio ativo.")
        return (PERMANECER,
                f"{linha.ticker} {onde}, mas a Inteligência não pede venda "
                f"({v.decisao}).", motivos)

    if v.limite == vd.LIVRE:
        return (MUDAR,
                f"{linha.ticker} {onde} e a Inteligência libera compra "
                f"({v.detalhe}). Os aportes podem ir para cá, até a % devida "
                "da sua estratégia.", motivos)
    return (PERMANECER,
            f"{linha.ticker} {onde}, mas a Inteligência não libera aporte "
            f"agora ({v.decisao}: {porque}).", motivos)


def _conclusao_do_modelo(linha: refm.LinhaAtivo, av: av_.Avaliacao | None, *,
                         avaliado: bool, nota_classe: float | None,
                         tem_classe: bool, alvo_estrategia: float | None,
                         real_classe: float, limite_por_ativo: float | None
                         ) -> tuple[str, str, list[str]]:
    """Ativo do modelo que o investidor não tem: incluir ou não."""
    rot = _rotulo_classe(linha.classe)
    if av is None:
        texto = ("Não foi avaliado pela régua da Inteligência "
                 + (f"(só os {MAX_AVALIADOS_MODELO} maiores pesos do modelo "
                    "são avaliados)" if not avaliado else
                    "(a leitura dos dados falhou)")
                 + ": sem avaliação, não dá para recomendar a inclusão.")
        return SEM_AVALIACAO, texto, []
    motivos = [f"Avaliação do ativo: {av.resumo()}"]
    if not av.avaliou_algo or av.qualidade not in NOTA_QUALIDADE:
        return (SEM_AVALIACAO, "Dados insuficientes para medir a qualidade: "
                "não incluir sem análise.", motivos)
    lim, motivo = vd.limite(av)
    if lim != vd.LIVRE:
        return (NAO_INCLUIR, f"Não incluir agora: {motivo}. Estar no modelo "
                "não compensa isso.", motivos)
    if alvo_estrategia is not None and alvo_estrategia <= 0:
        return (NAO_INCLUIR, f"A sua estratégia não prevê {rot} (alvo 0%): "
                "incluir pede rever a estratégia primeiro.", motivos)
    nota = NOTA_QUALIDADE[av.qualidade]
    qual = av_.ROTULO_LEITURA[av.qualidade].lower()
    if not tem_classe:
        motivos.append(f"Você não tem posição em {rot}: o ativo cobre uma "
                       "classe que o modelo usa.")
    elif nota_classe is not None and nota < nota_classe:
        return (NAO_INCLUIR,
                f"Qualidade {qual}, abaixo da média dos seus ativos de {rot} "
                f"({_fmt_nota(nota_classe)}): incluir só diversificaria, sem "
                "ganho de qualidade.", motivos)
    elif nota_classe is not None and nota > nota_classe:
        motivos.append(f"Qualidade acima da média dos seus ativos de {rot} "
                       f"({_fmt_nota(nota_classe)}).")
    elif nota_classe is not None:
        motivos.append(f"Qualidade igual à média dos seus ativos de {rot} "
                       f"({_fmt_nota(nota_classe)}): ganho em diversificação.")
    if alvo_estrategia is not None and real_classe < alvo_estrategia - max(
            1.0, 0.2 * alvo_estrategia):
        motivos.append(f"{rot} está {_pp(alvo_estrategia - real_classe)} "
                       f"abaixo do alvo da sua estratégia "
                       f"({_pct(alvo_estrategia)}): há espaço para aportar.")
    if av.preco == av_.CARO:
        motivos.append("Preço acima do normal: se incluir, entre aos poucos.")
    # Peso de referência: o do modelo, sem passar do limite por ativo nem
    # do que falta na classe até o alvo da sua estratégia.
    peso = linha.peso_modelo or 0.0
    tetos = [(peso, "o do modelo")]
    if limite_por_ativo is not None:
        tetos.append((float(limite_por_ativo),
                      "o limite por ativo da sua estratégia"))
    espaco = None if alvo_estrategia is None else alvo_estrategia - real_classe
    if espaco is not None and espaco > 0:
        tetos.append((espaco, f"o que falta em {rot} até o alvo da sua "
                              "estratégia"))
    if espaco is not None and espaco <= 0:
        motivos.append(f"{rot} já está no alvo da sua estratégia ou acima: "
                       "incluir pede trocar por outro ativo da classe, não "
                       "aportar a mais.")
    ref_peso, origem = min(tetos, key=lambda x: x[0])
    motivos.append(f"Peso de referência: {_pct(ref_peso)}, {origem}"
                   + (f" (o modelo usa {_pct(peso)})" if ref_peso < peso
                      else "") + ".")
    return (INCLUIR, f"Qualidade {qual}, sem alerta eliminatório e sem teto "
            "da Inteligência. A inclusão se apoia na avaliação do próprio "
            "ativo, não no fato de ele estar no modelo.", motivos)


def _conclusao_da_classe(c: refm.LinhaClasse, alvo_estr: float | None
                         ) -> tuple[str, str]:
    dif = c.diferenca
    lado = "acima" if dif > 0 else "abaixo"
    cab = f"{c.rotulo}: {_pct(c.real)} na carteira, {_pct(c.alvo)} no modelo"
    if alvo_estr is None:
        return (SEM_AVALIACAO, f"{cab}. A sua estratégia não define alvo para "
                "a classe, então não há premissa para decidir: defina um alvo "
                "na estratégia se quiser seguir (ou não) o modelo.")
    tol = max(2.0, 0.2 * alvo_estr)
    longe = c.real - alvo_estr
    if abs(longe) <= tol:
        return (PERMANECER, f"{cab}, e {_pct(alvo_estr)} na sua estratégia. "
                "Você está perto do que a sua estratégia pede; a diferença "
                "para o modelo vem da alocação salva no Portfólio Global, "
                "que é outra. Para seguir o modelo, revise a estratégia ou a "
                "alocação do Portfólio Global.")
    lado_estr = "acima" if longe > 0 else "abaixo"
    concorda = (longe > 0) == (dif > 0)
    reforco = (f"O modelo aponta na mesma direção ({_pp(dif)} {lado})."
               if concorda else
               f"O modelo aponta para o outro lado ({_pp(dif)} {lado}); vale "
               "a sua estratégia, que é a premissa desta aba.")
    return (MUDAR, f"{cab}, e {_pct(alvo_estr)} na sua estratégia. A classe "
            f"está {_pp(longe)} {lado_estr} da sua estratégia: rebalancear "
            f"pelos aportes, sem vender. {reforco}")


def _alvo_na_base(alocacao: dict | None, classe: str, base: str) -> float | None:
    """O alvo da sua estratégia para ``classe`` na mesma base dos pesos do
    confronto. Sem renda fixa no modelo, a base é a parcela de risco, e o
    alvo de cada classe é reescalado por 100 / (100 − alvo de renda fixa)."""
    if not alocacao:
        return None
    cp = CLASSE_POLITICA.get(classe)
    if cp is None or alocacao.get(cp) is None:
        return None
    alvo = float(alocacao[cp])
    if base == refm.BASE_SEM_RENDA_FIXA:
        rf = float(alocacao.get("renda_fixa") or 0.0)
        if rf >= 100:
            return None
        alvo = alvo * 100 / (100 - rf)
    return alvo


# -- montagem ---------------------------------------------------------------------

def montar(ref: refm.ReferenciaModelo,
           vereditos: dict[str, vd.Veredito] | None,
           avaliacoes_modelo: dict[str, av_.Avaliacao | None] | None = None, *,
           alocacao_estrategia: dict[str, float] | None = None,
           limite_por_ativo: float | None = None) -> Confronto:
    """``ref`` de ``referencia_modelo``; ``vereditos`` de
    ``veredito.vereditos_da_carteira`` (a decisão da Inteligência por ativo
    da carteira); ``avaliacoes_modelo`` = a ``Avaliacao`` dos ativos do modelo
    fora da carteira (chave presente com ``None`` = a leitura falhou);
    ``alocacao_estrategia`` = ``ContextoInvestidor.alocacao_alvo`` (% do
    patrimônio por classe da política)."""
    if not ref.disponivel:
        return indisponivel(ref.motivo)
    vereditos = {refm.normalizar(k): v for k, v in (vereditos or {}).items()}
    avals = {refm.normalizar(k): a for k, a in (avaliacoes_modelo or {}).items()}

    def avaliacao(linha: refm.LinhaAtivo) -> av_.Avaliacao | None:
        v = vereditos.get(linha.ticker)
        if v is not None:
            return v.avaliacao
        return avals.get(linha.ticker)

    risco = [x for x in ref.ativos.values() if x.classe in CLASSES_ATIVO]
    carteira = qualidade((x.peso_real, avaliacao(x)) for x in risco
                         if x.peso_real)
    modelo = qualidade((x.peso_modelo, avaliacao(x)) for x in risco
                       if x.peso_modelo)
    por_classe = []
    for c in CLASSES_ATIVO:
        da = [x for x in risco if x.classe == c]
        if not da:
            continue
        por_classe.append(QualidadeClasse(
            classe=c, rotulo=_rotulo_classe(c),
            carteira=qualidade((x.peso_real, avaliacao(x)) for x in da
                               if x.peso_real),
            modelo=qualidade((x.peso_modelo, avaliacao(x)) for x in da
                             if x.peso_modelo)))
    nota_classe = {q.classe: q.carteira.nota for q in por_classe}
    real_classe = {c.classe: c.real for c in ref.classes}

    classes = []
    for c in ref.classes:
        if c.alvo is None:          # "outros": o modelo não tem alvo
            continue
        alvo_estr = _alvo_na_base(alocacao_estrategia, c.classe, ref.base)
        conclusao, texto = _conclusao_da_classe(c, alvo_estr)
        classes.append(DivergenciaClasse(
            classe=c.classe, rotulo=c.rotulo, alvo_modelo=c.alvo,
            real=c.real, alvo_estrategia=alvo_estr, conclusao=conclusao,
            texto=texto))

    divergencias = []
    for x in risco:
        if x.peso_real is None or abs(x.diferenca or 0.0) < LIMIAR_ATIVO_PP:
            continue
        v = vereditos.get(x.ticker)
        conclusao, texto, motivos = _conclusao_da_carteira(x, v)
        av = v.avaliacao if v is not None else None
        divergencias.append(Divergencia(
            ticker=x.ticker, nome=x.nome, classe=x.classe,
            situacao=x.situacao, peso_modelo=x.peso_modelo,
            peso_real=x.peso_real, conclusao=conclusao, texto=texto,
            motivos=tuple(motivos), decisao=v.decisao if v else None,
            qualidade=(av_.ROTULO_LEITURA[av.qualidade] if av else None),
            nota=NOTA_QUALIDADE.get(av.qualidade) if av else None))

    candidatos = []
    nao_avaliados = 0
    for x in ref.fora_da_carteira(len(ref.ativos)):
        avaliado = x.ticker in avals
        if not avaliado:
            nao_avaliados += 1
            continue
        av = avals[x.ticker]
        alvo_estr = _alvo_na_base(alocacao_estrategia, x.classe or "",
                                  ref.base)
        conclusao, texto, motivos = _conclusao_do_modelo(
            x, av, avaliado=True, nota_classe=nota_classe.get(x.classe),
            tem_classe=real_classe.get(x.classe, 0.0) > 0,
            alvo_estrategia=alvo_estr,
            real_classe=real_classe.get(x.classe, 0.0),
            limite_por_ativo=limite_por_ativo)
        candidatos.append(Divergencia(
            ticker=x.ticker, nome=x.nome, classe=x.classe,
            situacao=x.situacao, peso_modelo=x.peso_modelo, peso_real=None,
            conclusao=conclusao, texto=texto, motivos=tuple(motivos),
            qualidade=(av_.ROTULO_LEITURA[av.qualidade] if av else None),
            nota=NOTA_QUALIDADE.get(av.qualidade) if av else None))

    # Quem a Inteligência manda trocar ganha, nos motivos, o melhor candidato
    # do modelo na mesma classe -- a sugestão continua sendo a dela.
    incluir = [d for d in candidatos if d.conclusao == INCLUIR]
    for i, d in enumerate(divergencias):
        if d.conclusao != MUDAR or d.decisao not in (
                rs.ROTULO_DECISAO[rs.VENDER], rs.ROTULO_DECISAO[rs.SUBSTITUIR]):
            continue
        mesma = sorted((c for c in incluir if c.classe == d.classe),
                       key=lambda c: (-(c.nota or 0), -(c.peso_modelo or 0)))
        if mesma:
            c = mesma[0]
            divergencias[i] = replace(d, motivos=d.motivos + (
                f"No modelo, {c.ticker} ({c.nome}) é da mesma classe, tem "
                f"qualidade {(c.qualidade or '').lower()} e nenhum teto da "
                "Inteligência.",))

    divergencias.sort(key=lambda d: (-abs(d.diferenca), d.ticker))
    ordem = {INCLUIR: 0, NAO_INCLUIR: 1, SEM_AVALIACAO: 2}
    candidatos.sort(key=lambda d: (ordem[d.conclusao], -(d.nota or 0),
                                   -(d.peso_modelo or 0), d.ticker))
    aderencia = sum(min(x.peso_real or 0.0, x.peso_modelo or 0.0)
                    for x in ref.ativos.values()
                    if x.situacao == refm.NOS_DOIS)
    return Confronto(
        disponivel=True, base=ref.base, carteira=carteira, modelo=modelo,
        por_classe=tuple(por_classe), classes=tuple(classes),
        divergencias=tuple(divergencias), candidatos=tuple(candidatos),
        nao_avaliados=nao_avaliados, aderencia=aderencia, avisos=ref.avisos)
