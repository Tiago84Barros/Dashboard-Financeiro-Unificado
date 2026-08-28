"""Vantagem fora da amostra medida, datada e versionada — para os três motores.

A-162 deixou a terceira dimensão da comparação de rigor ("Vantagem fora da
amostra") **não apurada** para B3 e EUA. Não era falta de estatística: as duas
telas já calculam o número. É que ele nasce e morre dentro de um `st.rerun` --
o Rank-IC do universo da B3 sai de `pooled_yearly_ics` no meio da Criação de
Portfólio, e o intervalo bootstrap do excesso dos EUA sai de `walk_forward`.
Nenhum dos dois chega ao disco, então a tela de Grau de Confiança, que roda em
outra sessão, não tem o que ler. Um motor que mede e esquece é, para efeito de
auditoria, idêntico a um motor que nunca mediu.

Este módulo é o meio-termo entre os dois: quem tem o armazém local mede com uma
configuração DECLARADA (`scripts/medir_vantagem_oos.py`), grava aqui, e a tela
publicada lê. Mesmo padrão de `core/us_survivorship.py` e do manifesto do RAG.

Três decisões que mudam o resultado e não são óbvias:

1. **O veredito é o intervalo, não a média.** Excesso médio positivo com
   intervalo que atravessa o zero não é vantagem -- é a mesma leitura que
   reprovou o motor de FII em A-162, e vale igual para B3 e EUA. Aprovar exige
   `ic_low > 0`.

2. **Medição de outra versão não vale para esta.** Subir `SCORE_VERSION` sem
   remedir muda o que o motor faz sem mudar o que ele alega; a medição carrega
   a versão em que foi feita e vira "não apurada" quando a versão diverge --
   nunca vira aprovação herdada.

3. **Ausência não é reprovação.** Sem arquivo, sem intervalo, ou com número
   ilegível, o portão devolve `None` ("não apurado"), que fica fora do
   denominador da nota. Reprovar por falta de medição puniria quem ainda não
   mediu com o mesmo peso de quem mediu e não achou vantagem.

A configuração usada fica gravada junto: um excesso medido com 20 ativos e
outro com 5 não são o mesmo número, e sem a configuração ninguém consegue
reproduzir nem contestar.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CAMINHO_MEDICAO = Path(__file__).resolve().parents[1] / "data" / "vantagem_oos.json"

MOTORES = ("b3", "fii", "us")

# Um Rank-IC de 0,096 impresso como "+9,60%" seria lido como retorno excedente.
# O formato acompanha a metrica porque as moedas dos motores sao diferentes:
# excesso por periodo e percentual; correlacao de postos e coeficiente puro.
FORMATO_PERCENTUAL, FORMATO_COEFICIENTE = "percentual", "coeficiente"

# Estados possíveis do portão. `None` é "não apurado" e sai do denominador.
APROVADO, REPROVADO, NAO_APURADO = True, False, None


def nova_medicao(*, motor: str, versao_metodologia: str, metrica: str,
                 media: float | None, ic_low: float | None, ic_high: float | None,
                 n_periodos: int, configuracao: str, fonte: str,
                 formato: str = FORMATO_PERCENTUAL,
                 janela: tuple[str, str] | None = None,
                 extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """Monta o registro de uma medição, carimbado com data e procedência."""
    if motor not in MOTORES:
        raise ValueError(f"motor desconhecido: {motor!r}")
    registro: dict[str, Any] = {
        "motor": motor,
        "medido_em": datetime.now(timezone.utc).date().isoformat(),
        "versao_metodologia": str(versao_metodologia),
        "metrica": str(metrica),
        "media": None if media is None else float(media),
        "ic_low": None if ic_low is None else float(ic_low),
        "ic_high": None if ic_high is None else float(ic_high),
        "ic_confianca": 0.95,
        "formato": str(formato),
        "n_periodos": int(n_periodos),
        "configuracao": str(configuracao),
        "fonte": str(fonte),
    }
    if janela:
        registro["janela"] = {"inicio": str(janela[0]), "fim": str(janela[1])}
    if extras:
        registro["extras"] = dict(extras)
    return registro


def _caminho(caminho: Path | str | None) -> Path:
    """Resolve o caminho na CHAMADA, nunca no import.

    `def f(caminho=CAMINHO_MEDICAO)` congela o valor no momento em que o módulo
    é importado: teste e script que trocam `CAMINHO_MEDICAO` depois disso
    continuariam lendo o arquivo de produção sem nenhum erro visível.
    """
    return Path(CAMINHO_MEDICAO if caminho is None else caminho)


def gravar_medicao(medicao: dict[str, Any],
                   caminho: Path | str | None = None) -> Path:
    """Grava/atualiza a medição de um motor preservando a dos outros."""
    caminho = _caminho(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    try:
        atual = json.loads(caminho.read_text(encoding="utf-8"))
        if not isinstance(atual, dict):
            atual = {}
    except Exception:  # noqa: BLE001 -- arquivo ausente ou corrompido
        atual = {}
    atual[str(medicao["motor"])] = medicao
    caminho.write_text(
        json.dumps(atual, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    return caminho


def carregar_medicao(motor: str,
                     caminho: Path | str | None = None) -> dict[str, Any] | None:
    """Última medição gravada do motor, ou None se não houver nenhuma."""
    try:
        dados = json.loads(_caminho(caminho).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("medicao de vantagem indisponivel: %s", type(exc).__name__)
        return None
    registro = dados.get(str(motor)) if isinstance(dados, dict) else None
    return registro if isinstance(registro, dict) else None


def _numero(valor: Any) -> float | None:
    """float() tolerante que também recusa NaN — NaN passa em float() e mente."""
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return None if n != n else n


def avaliar(motor: str, versao_atual: str,
            caminho: Path | str | None = None) -> tuple[bool | None, str]:
    """Veredito do portão de vantagem e a frase que o justifica.

    Devolve ``(APROVADO|REPROVADO|NAO_APURADO, detalhe)``. O detalhe sempre diz
    de onde veio o número -- um "não apurado" sem motivo é indistinguível de um
    bug, e foi assim que a nota de metodologia ficou inflada por meses.
    """
    med = carregar_medicao(motor, caminho)
    if not med:
        return NAO_APURADO, "nenhuma medicao de vantagem fora da amostra gravada"

    versao = str(med.get("versao_metodologia") or "")
    if versao and str(versao_atual) and versao != str(versao_atual):
        return NAO_APURADO, (
            f"medicao e da metodologia {versao}, o motor roda {versao_atual}: "
            f"resultado de outra versao nao atesta esta")

    low, high = _numero(med.get("ic_low")), _numero(med.get("ic_high"))
    if low is None or high is None:
        return NAO_APURADO, "medicao gravada sem intervalo de confianca do excesso"

    # 4 casas no coeficiente porque a decisao mora perto do zero: a B3 reprova
    # com limite inferior -0,0004, que com 3 casas imprime "-0.000" e se le
    # como zero -- o numero que sustenta o veredito sumiria do texto que o
    # justifica.
    fmt = ((lambda x: f"{x:+.4f}")
           if str(med.get("formato")) == FORMATO_COEFICIENTE
           else (lambda x: f"{x:+.2%}"))
    n = int(med.get("n_periodos") or 0)
    metrica = str(med.get("metrica") or "excesso por periodo")
    quando = str(med.get("medido_em") or "?")
    faixa = (f"IC 95% do {metrica}: {fmt(low)} a {fmt(high)} "
             f"em {n} periodos, medido em {quando}")
    if low > 0:
        return APROVADO, faixa
    return REPROVADO, (f"{faixa} -- atravessa o zero, entao a vantagem nao e "
                       f"distinguivel de acaso{_poder_preditivo(med)}")


def _poder_preditivo(med: dict[str, Any]) -> str:
    """Ressalva do Rank-IC quando o excesso reprova mas a ordenacao acerta.

    Sao duas afirmacoes diferentes e o app precisa das duas: nos EUA o score
    ordena o universo com Rank-IC medio 0,096 e t=3,73 -- ordenacao real -- e
    ainda assim a carteira de 20 nomes nao supera o equal-weight. Omitir o
    Rank-IC faria o portao parecer dizer "o score nao serve", que e mais forte
    do que o dado sustenta; omitir o excesso faria o oposto.
    """
    extras = med.get("extras") or {}
    ic, t = _numero(extras.get("rank_ic_medio")), _numero(extras.get("rank_ic_t"))
    if ic is None or t is None or abs(t) < 2.0:
        return ""
    return (f". O score AINDA ASSIM ordena o universo (Rank-IC medio "
            f"{ic:+.3f}, t={t:.2f}): ha poder preditivo sem excesso "
            f"sobre a carteira igualmente ponderada")
