"""core/carteira20.py -- registro imutável da Carteira 20 B3.

O que é
-------
Acompanhamento ao vivo de uma carteira de 20 ações da B3 com revisão
semestral. O sinal saiu da pesquisa ``PESQUISA 2026 - PREVISAO`` (candidato
C2 sem ``dy_12m``). No laboratório o resultado foi promissor, mas não
comprovado: o teste de 1996–2009 deu +2,4% a.a. sobre pesos iguais, com t de
0,8. Só um registro feito ANTES de os retornos acontecerem separa sinal de
sorte. Este módulo existe para que esse registro não possa ser reescrito
depois sem que se perceba.

Registro append-only com cadeia de hashes
-----------------------------------------
``data/public/carteira20_registro.jsonl`` tem um registro JSON por linha.
Cada registro carrega o ``hash`` do anterior (``hash_anterior``) e o seu
próprio, calculado sobre a forma canônica de todos os outros campos. Mudar um
byte de qualquer registro antigo quebra o hash dele e o elo do seguinte.

Recalcular a cadeia inteira depois de uma edição produziria uma cadeia
válida de novo. Por isso a verificação tem mais duas pontas: o teste
``tests/test_carteira20.py`` fixa os hashes já publicados e exige que o
arquivo publicado na main seja prefixo byte a byte do arquivo atual.

Nada é editado. Uma correção é um registro novo, do tipo ``correcao``, que
cita o registro corrigido pelo ``seq`` e pelo ``hash``.

Tipos
-----
- ``protocolo``: o primeiro registro. Congela a receita, o calendário das
  revisões, a medição e a expectativa da pesquisa ANTES da primeira revisão.
- ``revisao``: a pontuação de todo o universo na data de formação, as 20
  escolhidas com os pesos-alvo, os hashes do código e dos dados de entrada e
  o pregão de execução. ``registrado_em`` precisa ser anterior à abertura
  desse pregão. A verificação confere isso, porque é o que torna a previsão
  uma previsão.
- ``correcao``: aponta um registro anterior, diz o motivo e, se for o caso,
  traz o corpo que substitui o da revisão corrigida.

Este módulo não treina nada nem lê o armazém. O cálculo pesado fica em
``scripts/carteira20_revisao.py``, que roda localmente. A aba só lê o
arquivo.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core import pregao

RAIZ = Path(__file__).resolve().parents[1]
REGISTRO = RAIZ / "data" / "public" / "carteira20_registro.jsonl"

VERSAO_FORMATO = 1
TIPOS = ("protocolo", "revisao", "correcao")
N_ACOES = 20

FUSO_B3 = ZoneInfo("America/Sao_Paulo")
ABERTURA_B3 = time(10, 0)

#: Meses de formação das revisões depois da inaugural.
MESES_DE_REVISAO = (4, 10)

#: Feriados de data fixa em que a B3 não abre. Os móveis não alcançam as
#: datas de revisão: a Sexta-Feira Santa cai no máximo em 23 de abril, antes
#: do último pregão do mês, e o Carnaval e o Corpus Christi caem em outros
#: meses.
FERIADOS_FIXOS = frozenset({(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2),
                            (11, 15), (11, 20), (12, 24), (12, 25), (12, 31)})

# Tolerância da soma dos pesos-alvo. Os pesos são 1/20 gravados com 6 casas.
_TOL_PESOS = 1e-6


def canonico(obj) -> str:
    """Forma canônica do JSON: chaves ordenadas, sem espaços, UTF-8 cru.

    ``allow_nan=False`` porque NaN não é JSON. Um NaN gravado viraria um
    registro que outro leitor rejeita, e o hash de um registro ilegível não
    prova nada.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def calcular_hash(registro: dict) -> str:
    """SHA-256 da forma canônica de todos os campos menos o próprio ``hash``."""
    sem_hash = {k: v for k, v in registro.items() if k != "hash"}
    return hashlib.sha256(canonico(sem_hash).encode("utf-8")).hexdigest()


def sha256_arquivo(caminho: Path | str) -> str:
    h = hashlib.sha256()
    with Path(caminho).open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def montar_registro(tipo: str, corpo: dict, anteriores: list[dict], *,
                    registrado_em: datetime) -> dict:
    """Registro novo encadeado ao último de ``anteriores``."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo desconhecido: {tipo!r}")
    if registrado_em.tzinfo is None:
        raise ValueError("registrado_em precisa de fuso horário")
    reg = {
        "versao_formato": VERSAO_FORMATO,
        "seq": len(anteriores),
        "tipo": tipo,
        "registrado_em": registrado_em.isoformat(timespec="seconds"),
        "hash_anterior": anteriores[-1]["hash"] if anteriores else None,
        "corpo": corpo,
    }
    reg["hash"] = calcular_hash(reg)
    return reg


def ler_texto(caminho: Path | str = REGISTRO) -> str:
    p = Path(caminho)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def ler(caminho: Path | str = REGISTRO) -> list[dict]:
    """Registros na ordem do arquivo. Linha ilegível levanta ``ValueError``."""
    registros = []
    for n, linha in enumerate(ler_texto(caminho).splitlines(), start=1):
        if not linha.strip():
            continue
        try:
            registros.append(json.loads(linha))
        except json.JSONDecodeError as e:
            raise ValueError(f"linha {n} do registro não é JSON: {e}") from e
    return registros


@dataclass(frozen=True)
class StatusCadeia:
    integra: bool
    n: int
    ultimo_hash: str | None
    erros: tuple[str, ...]


def abertura_do_pregao(dia: str) -> datetime:
    return datetime.combine(date.fromisoformat(dia), ABERTURA_B3, tzinfo=FUSO_B3)


def _erros_revisao(seq, corpo: dict, registrado_em: datetime | None) -> list[str]:
    erros = []
    carteira = corpo.get("carteira") or []
    tickers = {c.get("ticker") for c in carteira}
    if len(carteira) != N_ACOES or len(tickers) != N_ACOES:
        erros.append(f"seq {seq}: a carteira precisa de {N_ACOES} tickers distintos "
                     f"(tem {len(tickers)})")
    soma = sum(float(c.get("peso_alvo") or 0) for c in carteira)
    if abs(soma - 1.0) > _TOL_PESOS * N_ACOES:
        erros.append(f"seq {seq}: os pesos-alvo somam {soma:.6f}, não 1")
    formacao, execucao = corpo.get("data_formacao"), corpo.get("pregao_execucao")
    if not formacao or not execucao:
        erros.append(f"seq {seq}: faltam data_formacao ou pregao_execucao")
        return erros
    if not formacao < execucao:
        erros.append(f"seq {seq}: o pregão de execução precisa ser posterior à formação")
    if registrado_em is not None and not registrado_em < abertura_do_pregao(execucao):
        erros.append(f"seq {seq}: registrado depois da abertura do pregão de "
                     f"execução ({execucao}) -- não é previsão")
    return erros


def verificar_cadeia(registros: list[dict]) -> StatusCadeia:
    """Confere hashes, elos, ordem e as regras de cada tipo."""
    erros: list[str] = []
    anterior = None
    for i, reg in enumerate(registros):
        seq = reg.get("seq")
        if seq != i:
            erros.append(f"posição {i}: seq {seq!r} fora de ordem")
        tipo = reg.get("tipo")
        if tipo not in TIPOS:
            erros.append(f"seq {seq}: tipo desconhecido {tipo!r}")
        if i == 0 and tipo != "protocolo":
            erros.append("o primeiro registro precisa ser o protocolo")
        if i > 0 and tipo == "protocolo":
            erros.append(f"seq {seq}: protocolo novo não é permitido; use uma correção")
        if reg.get("hash_anterior") != (anterior["hash"] if anterior else None):
            erros.append(f"seq {seq}: hash_anterior não bate com o registro anterior")
        if reg.get("hash") != calcular_hash(reg):
            erros.append(f"seq {seq}: hash não confere -- o registro foi alterado")
        try:
            quando = datetime.fromisoformat(reg.get("registrado_em") or "")
        except ValueError:
            quando = None
            erros.append(f"seq {seq}: registrado_em ilegível")
        if quando is not None and quando.tzinfo is None:
            quando = None
            erros.append(f"seq {seq}: registrado_em sem fuso horário")
        if quando is not None and anterior is not None:
            try:
                if quando < datetime.fromisoformat(anterior["registrado_em"]):
                    erros.append(f"seq {seq}: registrado antes do registro anterior")
            except (KeyError, TypeError, ValueError):
                pass
        corpo = reg.get("corpo") or {}
        if tipo == "revisao":
            erros += _erros_revisao(seq, corpo, quando)
        elif tipo == "correcao":
            erros += _erros_correcao(seq, corpo, registros[:i])
        anterior = reg
    return StatusCadeia(integra=not erros, n=len(registros),
                        ultimo_hash=registros[-1].get("hash") if registros else None,
                        erros=tuple(erros))


def _erros_correcao(seq, corpo: dict, anteriores: list[dict]) -> list[str]:
    erros = []
    ref = corpo.get("referencia") or {}
    alvo = ref.get("seq")
    citado = anteriores[alvo] if isinstance(alvo, int) and 0 <= alvo < len(anteriores) else None
    if citado is None:
        erros.append(f"seq {seq}: a correção precisa citar um registro anterior")
    elif citado.get("hash") != ref.get("hash"):
        erros.append(f"seq {seq}: a correção cita o seq {alvo} com hash diferente")
    if not str(corpo.get("motivo") or "").strip():
        erros.append(f"seq {seq}: correção sem motivo")
    if corpo.get("substitui") is not None:
        if citado is None or citado.get("tipo") != "revisao":
            erros.append(f"seq {seq}: só uma revisão pode ser substituída")
        else:
            # Sem conferir o horário: a substituição é, por definição, tardia.
            # Ela muda o corpo vigente, e a tela mostra que houve correção.
            erros += _erros_revisao(seq, corpo["substitui"], None)
    return erros


def verificar_prefixo(texto_publicado: str, texto_atual: str) -> list[str]:
    """O arquivo publicado precisa ser prefixo byte a byte do atual.

    Append-only quer dizer exatamente isso: só se acrescenta linha no fim.
    """
    if texto_atual.startswith(texto_publicado):
        return []
    antigas, novas = texto_publicado.splitlines(), texto_atual.splitlines()
    for n, linha in enumerate(antigas, start=1):
        if n > len(novas) or novas[n - 1] != linha:
            return [f"a linha {n} do registro publicado foi alterada ou removida"]
    return ["o registro publicado não é prefixo do atual"]


def anexar(tipo: str, corpo: dict, *, caminho: Path | str = REGISTRO,
           registrado_em: datetime | None = None) -> dict:
    """Acrescenta um registro ao fim do arquivo e devolve o registro gravado.

    Recusa gravar sobre uma cadeia quebrada: encadear um registro novo a um
    elo inválido daria a ele a aparência de integridade que a cadeia não tem.
    """
    p = Path(caminho)
    texto = ler_texto(p)
    registros = ler(p)
    status = verificar_cadeia(registros)
    if not status.integra:
        raise ValueError("cadeia quebrada; nada foi gravado: " + "; ".join(status.erros))
    quando = registrado_em or datetime.now(FUSO_B3).replace(microsecond=0)
    reg = montar_registro(tipo, corpo, registros, registrado_em=quando)
    novo = verificar_cadeia(registros + [reg])
    if not novo.integra:
        raise ValueError("registro novo inválido; nada foi gravado: " + "; ".join(novo.erros))
    p.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if not texto or texto.endswith("\n") else "\n"
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(sep + canonico(reg) + "\n")
    return reg


# -- Calendário ---------------------------------------------------------------

def e_pregao(dia: date) -> bool:
    """Dia útil sem feriado fixo da B3 nem feriado observado no calendário."""
    if dia.weekday() >= 5 or (dia.month, dia.day) in FERIADOS_FIXOS:
        return False
    return pregao._e_dia_util(dia, pregao.B3)


def ultimo_pregao_do_mes(ano: int, mes: int) -> date:
    dia = (date(ano + mes // 12, mes % 12 + 1, 1)) - timedelta(days=1)
    while not e_pregao(dia):
        dia -= timedelta(days=1)
    return dia


def proximo_pregao(dia: date) -> date:
    dia += timedelta(days=1)
    while not e_pregao(dia):
        dia += timedelta(days=1)
    return dia


def calendario_planejado(primeiro_ano: int, ultimo_ano: int) -> list[dict]:
    """Formação no último pregão de abril e de outubro, execução no seguinte."""
    saida = []
    for ano in range(primeiro_ano, ultimo_ano + 1):
        for mes in MESES_DE_REVISAO:
            formacao = ultimo_pregao_do_mes(ano, mes)
            saida.append({"data_formacao": formacao.isoformat(),
                          "pregao_execucao": proximo_pregao(formacao).isoformat()})
    return saida


# -- Leitura para a tela ------------------------------------------------------

def protocolo(registros: list[dict]) -> dict | None:
    if registros and registros[0].get("tipo") == "protocolo":
        return registros[0]["corpo"]
    return None


def revisoes(registros: list[dict]) -> list[dict]:
    """Revisões na ordem, com as correções que substituem o corpo aplicadas.

    Cada item é ``{"registro": <revisão original>, "corpo": <corpo vigente>,
    "correcoes": [<correções que a citam>]}``. A revisão original nunca some
    da lista: a correção muda o corpo vigente, não o histórico.
    """
    saida = {r["seq"]: {"registro": r, "corpo": r["corpo"], "correcoes": []}
             for r in registros if r.get("tipo") == "revisao"}
    for r in registros:
        if r.get("tipo") != "correcao":
            continue
        corpo = r.get("corpo") or {}
        alvo = (corpo.get("referencia") or {}).get("seq")
        if alvo in saida:
            saida[alvo]["correcoes"].append(r)
            if corpo.get("substitui") is not None:
                saida[alvo]["corpo"] = corpo["substitui"]
    return [saida[k] for k in sorted(saida)]


def vigente(registros: list[dict]) -> dict | None:
    rv = revisoes(registros)
    return rv[-1] if rv else None
