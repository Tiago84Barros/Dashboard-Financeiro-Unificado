"""Os seis circuit breakers, e o que cada um desliga.

O requisito nomeia seis situações em que o sistema tem de parar sozinho. Elas
não são a mesma trava com seis nomes: cada uma desliga uma coisa diferente, e
tratar todas como "desliga tudo" seria tão errado quanto não ter nenhuma --
um preço que não carregou não pode calar a explicação do painel inteiro.

+---------------------------------+--------------------------------------------+
| gatilho                         | efeito                                     |
+=================================+============================================+
| dados vencidos                  | nenhuma recomendação emergencial           |
| provedores divergem             | confiança rebaixada (não bloqueia)         |
| serviço de preço falhou         | impacto atual não é calculado              |
| modelo fora dos limites         | saída do modelo rejeitada                  |
| LLM inventou número             | resposta descartada                        |
| auditoria falhou ao gravar      | nenhuma mudança estratégica                |
+---------------------------------+--------------------------------------------+

A regra de projeto que sustenta a tabela
-----------------------------------------
``ok=None`` é "não medido", nunca ``False`` -- lei do projeto. Aqui isso vira:
uma trava **não** dispara por falta de informação. Ela dispara quando algo foi
medido e o valor medido é ruim. Uma trava que dispara no escuro treina quem usa
o sistema a ignorá-la, e aí ela não protege mais nada.

O oposto também vale e é o erro clássico do
``memoria: gate-que-so-dava-false``: critério que nunca pode disparar não é
proteção, é decoração. Por isso :func:`avaliar` recebe cada sinal
explicitamente, e ``None`` fica registrado como *não verificado* na saída --
visível, em vez de silencioso.

Puro: sem rede, sem banco, sem LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── As seis travas ───────────────────────────────────────────────────────────
DADOS_VENCIDOS = "dados_vencidos"
PROVEDORES_DIVERGEM = "provedores_divergem"
PRECO_INDISPONIVEL = "preco_indisponivel"
MODELO_FORA_DOS_LIMITES = "modelo_fora_dos_limites"
LLM_INVENTOU_NUMERO = "llm_inventou_numero"
AUDITORIA_FALHOU = "auditoria_falhou"

# ── O que cada uma desliga ───────────────────────────────────────────────────
RECOMENDACAO_EMERGENCIAL = "recomendacao_emergencial"
IMPACTO_ATUAL = "impacto_atual"
SAIDA_DO_MODELO = "saida_do_modelo"
RESPOSTA_DA_LLM = "resposta_da_llm"
MUDANCA_ESTRATEGICA = "mudanca_estrategica"

#: Rebaixar confiança não é bloquear. Divergência entre fontes é informação
#: sobre a incerteza, e transformá-la em bloqueio esconderia o evento em vez de
#: qualificá-lo -- ``memoria: incerteza-com-tamanho-nao-bloqueia``.
REBAIXA_CONFIANCA = "rebaixa_confianca"

EFEITO: dict[str, str] = {
    DADOS_VENCIDOS: RECOMENDACAO_EMERGENCIAL,
    PROVEDORES_DIVERGEM: REBAIXA_CONFIANCA,
    PRECO_INDISPONIVEL: IMPACTO_ATUAL,
    MODELO_FORA_DOS_LIMITES: SAIDA_DO_MODELO,
    LLM_INVENTOU_NUMERO: RESPOSTA_DA_LLM,
    AUDITORIA_FALHOU: MUDANCA_ESTRATEGICA,
}

TEXTO: dict[str, str] = {
    DADOS_VENCIDOS:
        "Os dados usados estão vencidos. Nenhuma recomendação emergencial "
        "é emitida com base neles.",
    PROVEDORES_DIVERGEM:
        "As fontes divergem entre si. A confiança da análise foi rebaixada, "
        "e a divergência aparece na tela em vez de ser resolvida no escuro.",
    PRECO_INDISPONIVEL:
        "O serviço de preços falhou. O impacto atual não foi calculado -- "
        "e não foi estimado no lugar.",
    MODELO_FORA_DOS_LIMITES:
        "A saída do modelo caiu fora dos limites esperados e foi rejeitada.",
    LLM_INVENTOU_NUMERO:
        "A resposta do modelo citou números que o backend não publicou e "
        "foi descartada. A explicação exibida vem do backend.",
    AUDITORIA_FALHOU:
        "A trilha de auditoria não pôde ser gravada. Mudanças estratégicas "
        "ficam bloqueadas: sem registro não há como responder depois por que "
        "a mudança foi feita.",
}


@dataclass(frozen=True)
class Trava:
    """Uma trava avaliada.

    ``disparada`` é ``None`` quando o sinal não foi verificado. Três estados,
    não dois: "não disparou" e "não olhei" são afirmações diferentes, e
    colapsá-las publicaria segurança que ninguém mediu.
    """

    nome: str
    disparada: bool | None
    detalhe: str = ""

    @property
    def efeito(self) -> str:
        return EFEITO[self.nome]

    @property
    def bloqueia(self) -> bool:
        return self.disparada is True and self.efeito != REBAIXA_CONFIANCA

    def descrever(self) -> str:
        if self.disparada is None:
            return f"{self.nome}: não verificada"
        if not self.disparada:
            return f"{self.nome}: ok"
        base = TEXTO[self.nome]
        return f"{base} ({self.detalhe})" if self.detalhe else base


@dataclass(frozen=True)
class Estado:
    """O conjunto das seis travas e o que ele permite."""

    travas: tuple[Trava, ...]

    def de(self, nome: str) -> Trava | None:
        return next((t for t in self.travas if t.nome == nome), None)

    @property
    def disparadas(self) -> tuple[Trava, ...]:
        return tuple(t for t in self.travas if t.disparada is True)

    @property
    def nao_verificadas(self) -> tuple[Trava, ...]:
        return tuple(t for t in self.travas if t.disparada is None)

    @property
    def bloqueios(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(t.efeito for t in self.travas if t.bloqueia))

    @property
    def confianca_rebaixada(self) -> bool:
        t = self.de(PROVEDORES_DIVERGEM)
        return bool(t and t.disparada)

    def permite(self, acao: str) -> bool:
        """``False`` só quando uma trava que desliga ``acao`` disparou.

        Trava não verificada não bloqueia -- ela aparece em
        :attr:`nao_verificadas`, que é o lugar honesto para ela. Bloquear no
        escuro daria a mesma resposta para "está tudo bem" e "não sei", e a
        segunda é a que precisa de alguém olhando.
        """
        return acao not in self.bloqueios

    def motivos(self, acao: str) -> tuple[str, ...]:
        return tuple(t.descrever() for t in self.travas
                     if t.bloqueia and t.efeito == acao)

    def resumo_auditoria(self) -> dict:
        return {
            "disparadas": [t.nome for t in self.disparadas],
            "nao_verificadas": [t.nome for t in self.nao_verificadas],
            "bloqueios": list(self.bloqueios),
            "confianca_rebaixada": self.confianca_rebaixada,
        }


# ── Domínio declarado das saídas do modelo ───────────────────────────────────
#: Grandezas normalizadas do motor: índice, cobertura, notas, severidade e
#: confiança. Todas foram definidas em 0..1 pelos módulos que as produzem.
DOMINIO_UNITARIO = (0.0, 1.0)


def _fora(valor, faixa: tuple[float, float] = DOMINIO_UNITARIO) -> bool:
    """``True`` para o que caiu fora da faixa -- e para NaN e infinito.

    ``NaN`` merece o teste explícito: toda comparação com ele é falsa, então um
    ``minimo <= valor <= maximo`` escrito do jeito óbvio **aprova NaN**. Sem
    ``valor != valor``, a saída mais corrompida de todas seria a única a passar.
    """
    if valor is None:
        return False          # não medido não é fora dos limites
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return True
    if num != num or num in (float("inf"), float("-inf")):
        return True
    return not (faixa[0] <= num <= faixa[1])


def fora_dos_limites(*, indice=None, veredito=None) -> tuple[bool | None, str]:
    """Confere o domínio das saídas do modelo. ``None`` se não houve saída.

    Não julga se o número está *certo* -- julga se ele é sequer representável
    como aquilo que diz ser. Índice de antifragilidade 1,4 ou severidade ``NaN``
    não são análises ruins: são saída corrompida, e exibi-las com duas casas
    decimais lhes daria a mesma aparência de qualquer outra.

    Duck typing de propósito: esta camada não importa ``antifragilidade`` nem
    ``transicao``. O módulo é puro, e continuar puro é o que o mantém testável
    sem banco e sem o motor inteiro de pé.
    """
    achados: list[str] = []
    olhou = False

    if indice is not None:
        olhou = True
        for campo in ("valor", "bruto", "cobertura"):
            if _fora(getattr(indice, campo, None)):
                achados.append(f"índice.{campo} = {getattr(indice, campo)!r}")
        for parte in getattr(indice, "partes", ()) or ():
            if _fora(getattr(parte, "nota", None)):
                achados.append(
                    f"componente {getattr(parte, 'chave', '?')} = "
                    f"{getattr(parte, 'nota', None)!r}")

    if veredito is not None:
        olhou = True
        for campo in ("severidade", "confianca"):
            if _fora(getattr(veredito, campo, None)):
                achados.append(
                    f"veredito.{campo} = {getattr(veredito, campo)!r}")
        codigo = getattr(getattr(veredito, "nivel", None), "codigo", None)
        if _fora(codigo, (0.0, 4.0)):
            achados.append(f"nível = {codigo!r}")

    if not olhou:
        return None, "nenhuma saída de modelo foi produzida nesta execução"
    return bool(achados), "; ".join(achados)


def avaliar(
    *,
    dados_vencidos: bool | None = None,
    provedores_divergem: bool | None = None,
    preco_indisponivel: bool | None = None,
    modelo_fora_dos_limites: bool | None = None,
    llm_inventou_numero: bool | None = None,
    auditoria_falhou: bool | None = None,
    detalhes: dict[str, str] | None = None,
) -> Estado:
    """Avalia as seis travas a partir de sinais já medidos por quem chama.

    Nenhum argumento tem valor por omissão diferente de ``None``: quem não
    passar um sinal fica com a trava marcada como não verificada, e isso
    aparece na tela e na auditoria. É o contrário de assumir que está tudo bem.
    """
    detalhes = detalhes or {}
    sinais = (
        (DADOS_VENCIDOS, dados_vencidos),
        (PROVEDORES_DIVERGEM, provedores_divergem),
        (PRECO_INDISPONIVEL, preco_indisponivel),
        (MODELO_FORA_DOS_LIMITES, modelo_fora_dos_limites),
        (LLM_INVENTOU_NUMERO, llm_inventou_numero),
        (AUDITORIA_FALHOU, auditoria_falhou),
    )
    return Estado(tuple(
        Trava(nome, None if valor is None else bool(valor),
              detalhes.get(nome, ""))
        for nome, valor in sinais
    ))


def do_painel(pn, *, validacao=None, auditoria_ok: bool | None = None,
              indice=None, veredito=None, auditoria=None) -> Estado:
    """Deriva as travas de um :class:`~core.inteligencia.painel.Painel`.

    Ligar as travas ao painel importa: motor que ninguém consulta na decisão é
    decoração (``memoria: diagnostico-precisa-porta-de-entrada``). Aqui a porta
    de entrada é o mesmo objeto que a tela já desenha.
    """
    vencidos = bool(getattr(pn, "desatualizados", ()) or
                    getattr(pn, "provedores_fora", ()))
    noticias = getattr(pn, "noticias", ()) or ()
    divergem = any(getattr(n, "estado_verificacao", "") == "contestada"
                   for n in noticias) if noticias else None

    memoria = getattr(pn, "memoria", None)
    impacto = memoria.valor_de("Impacto atual estimado") if memoria else None
    sem_preco = (not impacto.medido) if impacto is not None else None

    inventou = None
    if validacao is not None:
        inventou = bool(validacao.numeros_inventados)

    fora, detalhe_limites = fora_dos_limites(indice=indice, veredito=veredito)

    # Ordem deliberada: gravação observada manda sobre sonda de leitura.
    # ``auditoria_ok`` vem de quem tentou gravar de verdade; ``auditoria`` é a
    # sonda de ``trilha.sonda``, que só sabe dizer "não responde" -- nunca
    # "gravou bem". Por isso a sonda entra primeiro e a observação a sobrepõe.
    falhou, detalhe_auditoria = None, ""
    if auditoria is not None:
        falhou, detalhe_auditoria = auditoria
    if auditoria_ok is not None:
        falhou = not auditoria_ok

    return avaliar(
        dados_vencidos=vencidos,
        provedores_divergem=divergem,
        preco_indisponivel=sem_preco,
        modelo_fora_dos_limites=fora,
        llm_inventou_numero=inventou,
        auditoria_falhou=falhou,
        detalhes={MODELO_FORA_DOS_LIMITES: detalhe_limites,
                  AUDITORIA_FALHOU: detalhe_auditoria},
    )
