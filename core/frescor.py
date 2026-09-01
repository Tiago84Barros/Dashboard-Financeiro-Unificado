"""Selo de frescor: há quantos dias foi publicada a vitrine que a tela está lendo.

A tela de FIIs já dizia isso desde o PR #190; EUA e B3 não diziam nada. A
assimetria era o problema: as três telas leem vitrine publicada a partir do
armazém local, as três podem estar lendo dado de semanas atrás, e só uma
avisava. Nas outras duas, um ranking calculado sobre preço velho tem exatamente
a mesma aparência de um calculado sobre preço de ontem.

**Alvo e limite são grandezas diferentes.** O alvo é a cadência com que o
publicador escreve a vitrine; o limite é quando a tela passa a declará-la
vencida. Confundir os dois faria a tela reclamar de uma vitrine que está no
prazo que a própria agenda define -- alarme que dispara sem motivo é alarme que
se aprende a ignorar.

O alvo é **derivado** de `core.publicacao_agenda`, não copiado. Copiar criaria
duas fontes para o mesmo número e a divergência não daria erro nenhum: mudar a
cadência de `us_snapshot` de 7 para 30 dias deixaria a tela avisando "vencida"
todo dia 11, para sempre, sobre uma vitrine perfeitamente no prazo.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from core.publicacao_agenda import POR_CHAVE

# Qual publicador escreve a vitrine que cada tela lê. `us_vintages`,
# `us_prices` e companhia também são publicados, mas não são o que a tela mostra
# quando o usuário abre o módulo -- medir por eles diria "em dia" com a vitrine
# principal vencida.
ALVO_DO_MODULO = {
    "fii": "fii_selection",
    "us": "us_snapshot",
    "b3": "b3_metrics",
}

# Folga entre "passou da cadência" e "não serve mais". Existe porque perder uma
# publicação é rotina (máquina desligada, rede caída) e não torna a vitrine
# inútil: fundamento muda pouco em três dias. Preço e liquidez mudam, e é por
# isso que a folga é pequena e o aviso diz o que confiar e o que não confiar.
TOLERANCIA_DIAS = 3


def idade_alvo(modulo: str) -> int:
    """Cadência de publicação da vitrine do módulo, em dias."""
    return POR_CHAVE[ALVO_DO_MODULO[modulo]].cadencia_dias


def idade_limite(modulo: str) -> int:
    """A partir daqui a tela declara a vitrine vencida."""
    return idade_alvo(modulo) + TOLERANCIA_DIAS


def idade_em_dias(valor) -> int | None:
    """Idade de um carimbo qualquer, ou ``None`` se não der para saber.

    Devolver ``None`` em vez de zero é deliberado: zero é uma afirmação de
    frescor, e um carimbo ilegível não afirma nada. Um selo que diz "publicada
    hoje" porque não conseguiu ler a data é pior do que selo nenhum.
    """
    if valor is None or valor == "":
        return None
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return None
    if isinstance(valor, datetime):
        valor = valor.astimezone(timezone.utc).date() if valor.tzinfo else valor.date()
    if not isinstance(valor, date):
        return None
    return (datetime.now(timezone.utc).date() - valor).days


def selo(modulo: str, carimbo) -> dict:
    """Descreve o frescor da vitrine para a tela exibir.

    ``vencida`` só é ``True`` com idade medida: sem carimbo o selo diz que não
    sabe, e dizer "não sei" é diferente de dizer "está velha".
    """
    alvo = idade_alvo(modulo)
    limite = idade_limite(modulo)
    idade = idade_em_dias(carimbo)
    as_of = None
    if isinstance(carimbo, datetime):
        as_of = carimbo.date().isoformat()
    elif isinstance(carimbo, date):
        as_of = carimbo.isoformat()
    elif carimbo:
        as_of = str(carimbo)[:10]

    if idade is None:
        texto = ("Sem carimbo de publicação: não é possível dizer de quando são "
                 "estes dados.")
    elif idade < 0:
        # Carimbo no futuro é relógio mexido ou coluna errada -- foi assim que a
        # idade da B3 mediu -121 dias, lendo 31/12 do exercício de referência
        # como se fosse a data da publicação.
        texto = (f"Carimbo de publicação no futuro ({as_of}); a medida de frescor "
                 f"não é confiável.")
    elif idade > limite:
        texto = (f"Publicada em {as_of}, há {idade} dia(s) — o alvo é {alvo} dia(s) "
                 f"e a vitrine deixa de valer aos {limite}. Fundamentos mudam pouco "
                 f"nesse intervalo; preço, liquidez e múltiplos derivados de preço "
                 f"mudam. Trate o ranking como indicativo até a próxima publicação.")
    elif idade > alvo:
        texto = (f"Publicada em {as_of}, há {idade} dia(s) — passou do alvo de "
                 f"{alvo} dia(s), ainda dentro do limite de {limite}.")
    else:
        texto = f"Publicada em {as_of}, há {idade} dia(s)."

    return {
        "modulo": modulo,
        "as_of": as_of,
        "idade": idade,
        "alvo": alvo,
        "limite": limite,
        "vencida": idade is not None and idade > limite,
        "atrasada": idade is not None and idade > alvo,
        "texto": texto,
    }


def resumo_curto(dados: dict) -> str:
    """Uma linha para o cabeçalho da página, sem esconder o que não se sabe."""
    idade = dados.get("idade")
    if idade is None:
        return "sem carimbo"
    if idade < 0:
        return "carimbo inválido"
    rotulo = "hoje" if idade == 0 else f"há {idade} dia" + ("s" if idade > 1 else "")
    if dados.get("vencida"):
        return f"{rotulo} (vencida)"
    if dados.get("atrasada"):
        return f"{rotulo} (atrasada)"
    return rotulo


def carimbo_do_modulo(modulo: str):
    """Quando a vitrine do módulo foi publicada, lida da fonte de cada um.

    Cada módulo carimba num lugar diferente, e o da B3 é uma armadilha: a coluna
    `data` de `load_multiplos_todos` é 31/12 do exercício de REFERÊNCIA, uma
    data contábil que fica no futuro o ano corrente inteiro. Medir frescor por
    ela deu -121 dias e aprovaria para sempre. Quem sabe quando a vitrine foi
    escrita é `updated_at` de `market.calculated_metrics`.
    """
    if modulo == "us":
        from core import us_read

        # `data_status` devolve o carimbo da fonte que a tela está de fato
        # lendo, e ela muda com o ambiente: no deploy é `generated_at` da
        # vitrine; na máquina local, apontada para o armazém, é `ingested_at`
        # das demonstrações. São grandezas diferentes e as duas estão certas --
        # cada uma diz quando foi escrito o dado que AQUELA tela mostra. Fixar
        # uma delas faria o selo mentir no outro ambiente.
        return us_read.data_status().get("last_update")
    if modulo == "b3":
        from core.market_health import market_health_summary

        return ((market_health_summary() or {}).get("frescor") or {}).get("ultimo_calc")
    if modulo == "fii":
        import core.market_read as mr

        return mr.load_fii_methodology_inputs().attrs.get("snapshot_as_of")
    raise KeyError(modulo)
