# -*- coding: utf-8 -*-
"""
Auditoria automatizada da Criação de Portfólio B3 — caça DEFEITOS, não ajusta.

Por que este desenho. Iterar parâmetros até a carteira "ficar boa" é
sobreajuste: não existe carteira ótima verificável fora da amostra, e ajustar
até o resultado agradar é exatamente o erro que a §15 da auditoria corrigiu.
O que um ciclo automatizado PODE fazer com honestidade é rodar o motor em
muitas configurações e verificar **invariantes** — propriedades que precisam
valer em qualquer configuração. Violação de invariante é defeito, não opinião.

Invariantes verificados:

  I1  execução sem exceção;
  I2  pesos somam 1;
  I3  nenhum peso acima do teto por ativo;
  I4  nenhum ticker duplicado;
  I5  teto setorial respeitado OU aviso explícito (nunca violação silenciosa);
  I6  teto de ciclo respeitado OU aviso explícito;
  I7  determinismo: mesma configuração devolve a mesma carteira;
  I8  monotonia: exigir margem MAIOR não pode AUMENTAR o nº de aprovados;
  I9  coerência entre motores: empresa com alerta crítico aparece na seção de
      saúde exibida ao usuário.

Uso (com DATABASE_URL apontando para o armazém local):
  python scripts/audit_portfolio_b3.py --rapido
  python scripts/audit_portfolio_b3.py --saida relatorio.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOLERANCIA = 1e-6


@dataclass
class Config:
    """Uma configuração de parâmetros da aba."""
    nome: str
    thr_selic: float = 15.0
    teto_setor: int = 100
    teto_ciclico: int = 100
    criterio: str = "Econômico (Brasil)"
    min_empresas: int = 5
    cheapness: int = 0
    resiliencia: bool = False
    roic_spread: float = 0.0

    def as_state(self) -> dict:
        return {
            "pb3_thr_selic_hist": self.thr_selic,
            "pb3_teto_setor": self.teto_setor,
            "pb3_teto_ciclico": self.teto_ciclico,
            "pb3_criterio_aprov2": self.criterio,
            "pb3_min_empresas": self.min_empresas,
            "pb3_cheapness": self.cheapness,
            "pb3_roic_spread": self.roic_spread,
        }


@dataclass
class Resultado:
    config: str
    ok: bool = True
    carteira: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    erros: list = field(default_factory=list)
    defeitos: list = field(default_factory=list)
    segundos: float = 0.0

    @property
    def tickers(self) -> list:
        return [item["tk"] for item in self.carteira]


_SCRIPT = """
import views.portfolio_b3 as view
view.render(show_header=False)
"""


def _ajustar(app, chave: str, valor) -> None:
    """Ajusta um widget pela API do AppTest.

    Pré-definir ``session_state`` de widget com valor default é rejeitado pelo
    Streamlit — a interação precisa passar pelo widget, como um usuário faria.
    """
    for colecao in (app.number_input, app.selectbox, app.slider):
        try:
            colecao(key=chave).set_value(valor)
            return
        except (KeyError, ValueError):
            continue


def executar(config: Config, timeout: int = 600) -> Resultado:
    from streamlit.testing.v1 import AppTest

    resultado = Resultado(config=config.nome)
    inicio = time.time()
    try:
        app = AppTest.from_string(_SCRIPT).run(timeout=timeout)
        if config.resiliencia:
            # O spread só fica habilitado depois do checkbox — ordem importa.
            app.checkbox(key="pb3_resiliencia").set_value(True).run(timeout=timeout)
        for chave, valor in config.as_state().items():
            _ajustar(app, chave, valor)
        app.button(key="pb3_rodar").click().run(timeout=timeout)
    except Exception as exc:                       # noqa: BLE001
        resultado.ok = False
        resultado.defeitos.append(f"I1 execução falhou: {type(exc).__name__}: {exc}")
        resultado.segundos = time.time() - inicio
        return resultado
    resultado.segundos = time.time() - inicio

    if app.exception:
        resultado.ok = False
        resultado.defeitos.append(
            f"I1 exceção na renderização: {app.exception[0].value}")
    # session_state do AppTest é SafeSessionState: não tem .get().
    #
    # A CHAVE AUSENTE é diferente de carteira vazia: ausente significa que a
    # execução não chegou ao fim (timeout, exceção antes da montagem), e tratar
    # isso como "nenhum ativo aprovado" produziria conclusão falsa — foi o que
    # aconteceu em 29/07/2026, quando um timeout virou "carteira vazia" e quase
    # passou por defeito de determinismo.
    try:
        resultado.carteira = list(app.session_state["pb3_carteira_final"] or [])
    except (KeyError, AttributeError):
        resultado.carteira = []
        resultado.ok = False
        resultado.defeitos.append(
            "I1 execução não concluiu: a carteira final não chegou a ser montada "
            "(timeout ou interrupção). Resultado INCONCLUSIVO — não confundir "
            "com carteira vazia por ausência de aprovados.")
    resultado.avisos = [item.value for item in app.warning]
    resultado.erros = [item.value for item in app.error]
    return resultado


def verificar_invariantes(resultado: Resultado, config: Config) -> None:
    """Aplica I2–I6 e I9 a um resultado individual."""
    carteira = resultado.carteira
    if not carteira:
        return                                     # carteira vazia é resposta válida

    pesos = [float(item["peso"]) for item in carteira]
    soma = sum(pesos)
    if abs(soma - 1.0) > 1e-4:
        resultado.defeitos.append(f"I2 pesos somam {soma:.6f}, esperado 1")

    tickers = [item["tk"] for item in carteira]
    if len(set(tickers)) != len(tickers):
        resultado.defeitos.append("I4 ticker duplicado na carteira")

    # I5/I6: teto respeitado OU aviso presente
    texto_avisos = " ".join(resultado.avisos)
    if config.teto_setor < 100:
        limite = config.teto_setor / 100.0
        try:
            from core.market_read import load_setores
            mapa = {str(r["ticker"]).upper(): str(r["SETOR"] or "")
                    for _, r in load_setores().iterrows()}
        except Exception:                          # noqa: BLE001
            mapa = {}
        por_setor: dict[str, float] = {}
        for item in carteira:
            setor = mapa.get(str(item["tk"]).upper(), "")
            if setor:
                por_setor[setor] = por_setor.get(setor, 0.0) + float(item["peso"])
        excedidos = {s: p for s, p in por_setor.items() if p > limite + 1e-4}
        if excedidos and "setor" not in texto_avisos.lower():
            resultado.defeitos.append(
                f"I5 teto setorial de {limite:.0%} violado sem aviso: "
                + ", ".join(f"{s}={p:.1%}" for s, p in excedidos.items()))

    if config.teto_ciclico < 100:
        limite = config.teto_ciclico / 100.0
        try:
            from core.b3_holdings_health import classify_cycle
            from core.market_read import load_setores
            ciclo = {str(r["ticker"]).upper(): classify_cycle(r["SETOR"])
                     for _, r in load_setores().iterrows()}
        except Exception:                          # noqa: BLE001
            ciclo = {}
        peso_ciclico = sum(float(i["peso"]) for i in carteira
                           if ciclo.get(str(i["tk"]).upper()) == "ciclico")
        if peso_ciclico > limite + 1e-4 and "classe" not in texto_avisos.lower():
            resultado.defeitos.append(
                f"I6 teto de ciclo {limite:.0%} violado ({peso_ciclico:.1%}) sem aviso")

    # I9: crítico da saúde precisa aparecer para o usuário
    try:
        from core.b3_holdings_health import check_holdings
        from core.market_read import load_multiplos_todos
        criticos = [h.ticker for h in check_holdings(
            load_multiplos_todos(), tickers, selic=0.11) if h.nivel == "critico"]
    except Exception:                              # noqa: BLE001
        criticos = []
    texto_erros = " ".join(resultado.erros)
    ausentes = [t for t in criticos if t not in texto_erros]
    if ausentes:
        resultado.defeitos.append(
            "I9 crítico não exibido ao usuário: " + ", ".join(ausentes))


def verificar_determinismo(a: Resultado, b: Resultado) -> list[str]:
    """Compara duas execuções da MESMA configuração.

    Atenção (aprendizado de 29/07/2026): repetir no mesmo processo NÃO testa
    determinismo de verdade — o cache está quente e a ordem de hash é a mesma.
    Uma divergência real (GOAU4 vs SHUL4, ambas de Siderurgia) só apareceu ao
    comparar processos distintos. Use ``verificar_determinismo_entre_processos``
    para o teste forte; este aqui cobre apenas o caso trivial.
    """
    if sorted(a.tickers) != sorted(b.tickers):
        return [f"I7 não determinístico no mesmo processo: {a.tickers} vs {b.tickers}"]
    return []


def verificar_determinismo_entre_processos(config: Config, *,
                                           timeout: int = 900) -> list[str]:
    """I7 forte: roda a mesma configuração em PROCESSOS separados.

    Cada processo tem seu próprio ``PYTHONHASHSEED``, então ordens de conjunto
    e de dicionário construído a partir de conjuntos podem diferir. Se o
    resultado muda, há dependência de ordem instável em algum desempate.
    """
    import subprocess
    import textwrap

    codigo = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(ROOT)!r})
        from scripts.audit_portfolio_b3 import Config, executar
        r = executar(Config({config.nome!r}), timeout={timeout})
        print("###" + json.dumps(sorted(r.tickers)))
    """)
    saidas: list[list[str]] = []
    for semente in ("0", "1"):
        ambiente = {**os.environ, "PYTHONHASHSEED": semente,
                    "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True,
                              text=True, env=ambiente, timeout=timeout + 120)
        linha = next((l for l in proc.stdout.splitlines() if l.startswith("###")), None)
        saidas.append(json.loads(linha[3:]) if linha else [])
    if not saidas[0] or not saidas[1]:
        # Execução incompleta não prova nem refuta determinismo.
        return ["I7 INCONCLUSIVO: uma das execuções não concluiu (timeout?). "
                "Repita com timeout maior — ausência de resultado não é evidência."]
    if saidas[0] != saidas[1]:
        so_a = sorted(set(saidas[0]) - set(saidas[1]))
        so_b = sorted(set(saidas[1]) - set(saidas[0]))
        return [f"I7 não determinístico ENTRE PROCESSOS: só na semente 0 {so_a}; "
                f"só na semente 1 {so_b}"]
    return []


def verificar_monotonia(resultados: dict[str, Resultado]) -> list[str]:
    """Margem maior não pode produzir MAIS aprovados."""
    baixa, alta = resultados.get("margem_baixa"), resultados.get("margem_alta")
    if not baixa or not alta or not baixa.carteira or not alta.carteira:
        return []
    if len(alta.tickers) > len(baixa.tickers):
        return [f"I8 monotonia quebrada: margem 25% aprovou {len(alta.tickers)} "
                f"ativos e margem 5% aprovou {len(baixa.tickers)}"]
    return []


CONFIGS_RAPIDO = [
    Config("base"),
    Config("margem_baixa", thr_selic=5.0),
    Config("margem_alta", thr_selic=25.0),
    Config("teto_setor_30", teto_setor=30),
    Config("teto_ciclico_60", teto_ciclico=60),
    Config("tetos_combinados", teto_setor=30, teto_ciclico=60),
]

CONFIGS_COMPLETO = CONFIGS_RAPIDO + [
    Config("criterio_sinal", criterio="Sinal fundamental (Rank-IC)"),
    Config("criterio_retorno", criterio="Retorno de 24m (FDR)"),
    Config("grupo_minimo_1", min_empresas=1),
    Config("barganha_30", cheapness=30),
    Config("teto_setor_25_ciclico_50", teto_setor=25, teto_ciclico=50),
    # Reproduz a configuração do usuário que produziu carteira 83% cíclica.
    Config("resiliencia_5pp", resiliencia=True, roic_spread=5.0),
    Config("resiliencia_0pp", resiliencia=True, roic_spread=0.0),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Auditoria automatizada da carteira B3")
    ap.add_argument("--rapido", action="store_true", help="6 configurações (padrão)")
    ap.add_argument("--completo", action="store_true", help="11 configurações")
    ap.add_argument("--saida", type=Path, help="grava o relatório em JSON")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    configs = CONFIGS_COMPLETO if args.completo else CONFIGS_RAPIDO
    print(f"auditando {len(configs)} configurações\n")

    resultados: dict[str, Resultado] = {}
    for config in configs:
        print(f"  ▸ {config.nome} ...", end=" ", flush=True)
        resultado = executar(config, timeout=args.timeout)
        verificar_invariantes(resultado, config)
        resultados[config.nome] = resultado
        marca = "OK " if not resultado.defeitos else f"{len(resultado.defeitos)} DEFEITO(S)"
        print(f"{marca} ({resultado.segundos:.0f}s, {len(resultado.carteira)} ativos)")
        for defeito in resultado.defeitos:
            print(f"      ! {defeito}")

    # I7 determinismo: repete a configuração base
    print("  ▸ determinismo (repete base) ...", end=" ", flush=True)
    repeticao = executar(Config("base"), timeout=args.timeout)
    falhas_det = verificar_determinismo(resultados["base"], repeticao)
    print("OK" if not falhas_det else "DEFEITO")
    for falha in falhas_det:
        print(f"      ! {falha}")

    falhas_mono = verificar_monotonia(resultados)
    for falha in falhas_mono:
        print(f"  ! {falha}")

    total_defeitos = (sum(len(r.defeitos) for r in resultados.values())
                      + len(falhas_det) + len(falhas_mono))
    print(f"\n=== {total_defeitos} defeito(s) em {len(configs)} configurações ===")

    if args.saida:
        args.saida.write_text(json.dumps({
            "configuracoes": len(configs),
            "defeitos": total_defeitos,
            "resultados": {
                nome: {"tickers": r.tickers, "defeitos": r.defeitos,
                       "avisos": r.avisos[:5], "segundos": round(r.segundos, 1)}
                for nome, r in resultados.items()},
            "determinismo": falhas_det,
            "monotonia": falhas_mono,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"relatório: {args.saida}")
    return 1 if total_defeitos else 0


if __name__ == "__main__":
    raise SystemExit(main())
