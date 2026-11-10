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

from datetime import date, datetime

from core.publicacao_agenda import POR_CHAVE

# Qual publicador escreve a vitrine que cada tela lê. `us_vintages`,
# `us_prices` e companhia também são publicados, mas não são o que a tela mostra
# quando o usuário abre o módulo -- medir por eles diria "em dia" com a vitrine
# principal vencida.
ALVO_DO_MODULO = {
    "fii": "fii_selection",
    "us": "us_snapshot",
    "b3": "b3_metrics",
    "noticias": "noticias_vitrine",
    "espelho": "espelho_supabase",
    "macro": "macro_insumos",
}

# Folga entre "passou da cadência" e "não serve mais". Existe porque perder uma
# publicação é rotina (máquina desligada, rede caída) e não torna a vitrine
# inútil: fundamento muda pouco em três dias. Preço e liquidez mudam, e é por
# isso que a folga é pequena e o aviso diz o que confiar e o que não confiar.
TOLERANCIA_DIAS = 3

# Qual alvo traz o PREÇO que a tela mostra, quando ele não é o mesmo que
# publica a vitrine. Na B3 são dois: `b3_metrics` republica as métricas toda
# semana a partir do armazém, e `b3_brapi` traz a cotação. Entre 30/10 e
# 07/11/2026 a brapi recusou toda cotação e `b3_metrics` seguiu republicando:
# o carimbo de publicação renovava, o preço ficou em 29/10, e o selo dizia "em
# dia". Módulo fora deste mapa é medido só pela publicação.
ALVO_DO_PRECO = {
    "b3": "b3_brapi",
}

# Mediana da ÚLTIMA data de cada ticker, não `max(date)` da tabela: um ticker
# que a brapi soltou por acaso levava o máximo para 31/10 com a mediana em
# 29/10. Ticker deslistado puxa a mediana para trás só se for metade do
# cadastro.
SQL_DATA_DO_PRECO_B3 = """
    SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY ultima)
    FROM (SELECT max(date) AS ultima FROM market.historical_prices
          GROUP BY ticker) AS por_ticker"""


def idade_alvo(modulo: str) -> int:
    """Cadência de publicação da vitrine do módulo, em dias."""
    return POR_CHAVE[ALVO_DO_MODULO[modulo]].cadencia_dias


def idade_limite(modulo: str) -> int:
    """A partir daqui a tela declara a vitrine vencida."""
    return limite_do_alvo(ALVO_DO_MODULO[modulo])


def limite_do_alvo(chave: str) -> int | None:
    """Limite de idade de qualquer alvo da agenda: cadência + tolerância.

    É a mesma regra de `idade_limite`, aplicada pela chave do alvo e não pelo
    módulo da tela -- o vigia de automações (`core.vigia_automacoes`) confere
    todos os alvos, não só os seis que têm tela. Alvo por versão (safra PIT)
    não tem cadência de calendário e, portanto, não tem limite: ``None``.
    """
    cadencia = POR_CHAVE[chave].cadencia_dias
    return None if cadencia is None else cadencia + TOLERANCIA_DIAS


def idade_em_dias(valor, hoje: date | None = None) -> int | None:
    """Idade de um carimbo qualquer, ou ``None`` se não der para saber.

    Devolver ``None`` em vez de zero é deliberado: zero é uma afirmação de
    frescor, e um carimbo ilegível não afirma nada. Um selo que diz "publicada
    hoje" porque não conseguiu ler a data é pior do que selo nenhum.

    **Os dois lados da subtração ficam no mesmo calendário -- o local.** A versão
    anterior comparava o dia em UTC contra uma data vinda do relógio local, e
    das 21h à meia-noite no fuso do usuário (UTC-3) o dia em UTC já tinha virado:
    vitrine publicada agora saía com um dia de idade. Não é falha de teste, é
    alarme de dado vencido disparando sem dado vencido.

    Converter a data nua para UTC não resolveria: uma ``date`` não tem hora, ela
    cobre dois dias em UTC, e ancorá-la à meia-noite escolheria um dos dois por
    conveniência. O calendário local é o único referencial que todo carimbo
    aceita sem que se invente precisão -- e é também o que a frase "publicada
    hoje" significa para quem lê a tela.

    Em produção isso não muda nada: na Streamlit Cloud o relógio local é UTC.
    Muda na máquina do usuário, que é onde o publicador roda.

    ``hoje`` existe para a lógica pura que decide alertas poder ser testada sem
    relógio; omitido, é o dia local corrente.
    """
    if valor is None or valor == "":
        return None
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return None
    if isinstance(valor, datetime):
        # Carimbo com fuso (todo TIMESTAMPTZ do armazém) vira hora local; sem
        # fuso, já se assume que o relógio que o escreveu era este.
        valor = valor.astimezone().date() if valor.tzinfo else valor.date()
    if not isinstance(valor, date):
        return None
    return ((hoje or date.today()) - valor).days


def selo(modulo: str, carimbo, preco=None) -> dict:
    """Descreve o frescor da vitrine para a tela exibir.

    ``vencida`` só é ``True`` com idade medida: sem carimbo o selo diz que não
    sabe, e dizer "não sei" é diferente de dizer "está velha".

    ``preco`` é a data do preço que a tela mostra (`data_do_preco`), para o
    módulo em que ela não acompanha a publicação (`ALVO_DO_PRECO`). Preço além
    do limite do alvo que o traz vence o selo mesmo com a publicação em dia.
    Sem ``preco``, ou em módulo fora do mapa, vale só a publicação.
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

    vencida = idade is not None and idade > limite
    idade_preco = preco_as_of = limite_preco = None
    if modulo in ALVO_DO_PRECO:
        idade_preco = idade_em_dias(preco)
        limite_preco = limite_do_alvo(ALVO_DO_PRECO[modulo])
    if idade_preco is not None and idade_preco >= 0:
        preco_as_of = (preco.isoformat() if isinstance(preco, date)
                       else str(preco))[:10]
        if idade_preco > limite_preco:
            vencida = True
            texto = (f"{texto} Mas o preço mais recente é de {preco_as_of}, há "
                     f"{idade_preco} dia(s), além do limite de {limite_preco}: a "
                     f"cotação parou de chegar e a publicação seguiu sobre ela. "
                     f"Preço, liquidez e múltiplos derivados de preço estão "
                     f"desatualizados.")

    return {
        "modulo": modulo,
        "as_of": as_of,
        "idade": idade,
        "alvo": alvo,
        "limite": limite,
        "vencida": vencida,
        "atrasada": idade is not None and idade > alvo,
        "preco_as_of": preco_as_of,
        "idade_preco": idade_preco,
        "limite_preco": limite_preco,
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
    lp = dados.get("limite_preco")
    if lp is not None and (dados.get("idade_preco") or 0) > lp:
        return f"{rotulo}, preço de {dados['preco_as_of']} (vencida)"
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


def data_do_preco(modulo: str, engine=None):
    """Data do preço que a tela do módulo mostra, ou ``None``.

    Só para módulo em `ALVO_DO_PRECO`; os outros devolvem ``None`` e o selo
    fica com a publicação. Lê o mesmo banco que a tela lê (`get_engine`).
    """
    if modulo not in ALVO_DO_PRECO:
        return None
    from sqlalchemy import text

    if engine is None:
        from core.database import get_engine
        engine = get_engine()
    if engine is None:
        return None
    with engine.connect() as conn:
        return conn.execute(text(SQL_DATA_DO_PRECO_B3)).scalar()
