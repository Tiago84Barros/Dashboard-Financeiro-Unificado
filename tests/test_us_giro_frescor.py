"""Os tres defeitos que fizeram a Criacao de Portfolio dos EUA bloquear tudo.

Em 07/09/2026 o botao "criar carteira" nao publicava nenhuma carteira. Nao
estava quebrado: o portao de negociabilidade reprovou as 2.612 empresas porque
a vitrine trazia giro de 20/08 contra um teto de 7 dias. Tres defeitos
independentes se somaram, e cada um destes testes cobre um.

A  `daily` sem --tickers iterava sobre lista vazia e devolvia sucesso — e era
   exatamente o comando que a mensagem de bloqueio mandava rodar.
B  O publicador nao aplicava a regra de frescor de quem consome: a vitrine de
   31/08 saiu com medicao de 20/08, ja vencida ao nascer.
C  A mensagem de bloqueio nao dizia de quando era a medicao nem quantos dias
   ela tinha, entao "vitrine velha" e "universo sem giro" liam igual.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pandas as pd

RAIZ = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- defeito A
def test_daily_resolve_universo_do_banco_quando_falta_tickers():
    import run_us_ingest

    class _Conn:
        def __init__(self):
            self.sql = None

        def execute(self, stmt, *a, **k):
            self.sql = str(stmt)

            class _R:
                @staticmethod
                def fetchall():
                    return [("ZZZ",), ("AAPL",)]
            return _R()

    conn = _Conn()
    assert run_us_ingest.simbolos_para_refresco(conn) == ["ZZZ", "AAPL"]
    # Ordem por serie mais parada primeiro: o simbolo que ha mais tempo nao
    # atualiza e o que mais precisa da passagem.
    assert "order by p.ultima asc" in " ".join(conn.sql.lower().split())
    # E precisa alcancar quem JA tem serie; `prices` cobre so quem nunca teve.
    assert "not exists" not in conn.sql.lower()


def test_daily_com_universo_vazio_nao_reporta_sucesso():
    """Zero simbolo e universo vazio, nao trabalho concluido."""
    fonte = (RAIZ / "run_us_ingest.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    bloco = None
    for no in ast.walk(arvore):
        if not isinstance(no, ast.If):
            continue
        if ast.unparse(no.test) == "args.command == 'daily'":
            bloco = no
    assert bloco is not None, "o comando daily sumiu do CLI"
    corpo = ast.unparse(bloco)
    assert "'ok': False" in corpo, "daily voltou a devolver sucesso com zero simbolo"
    assert "ingest_prices_only" in corpo, (
        "daily precisa atualizar preco de quem ja tem serie"
    )
    # Sem esta linha o comando volta a nao ter universo: a funcao de resolucao
    # pode existir, passar nos testes dela, e ninguem chama-la.
    assert "simbolos_para_refresco(conn" in corpo, (
        "daily nao resolve mais o universo do banco quando falta --tickers"
    )


def test_daily_limita_o_lote():
    import run_us_ingest

    class _Conn:
        sql = None

        def execute(self, stmt, *a, **k):
            type(self).sql = str(stmt)

            class _R:
                @staticmethod
                def fetchall():
                    return []
            return _R()

    run_us_ingest.simbolos_para_refresco(_Conn(), limit=25)
    assert "LIMIT 25" in _Conn.sql


# --------------------------------------------------------------- defeito B
def _linha(as_of):
    return {"symbol": "AAPL", "metrics": {"giro_diario_usd": 1e7,
                                          "giro_diario_usd_at": as_of}}


def test_publicador_mede_a_idade_do_giro():
    from scripts.publish_us_snapshot import idade_do_giro

    agora = datetime(2026, 8, 31, tzinfo=timezone.utc)
    # O caso real: vitrine de 31/08 carregando medicao de 20/08.
    assert idade_do_giro([_linha("2026-08-20T00:00:00+00:00")], now=agora) == 11
    assert idade_do_giro([_linha("2026-08-30T00:00:00+00:00")], now=agora) == 1
    # A mais NOVA manda: uma linha fresca no meio de velhas nao pode ser diluida.
    assert idade_do_giro(
        [_linha("2026-08-01T00:00:00+00:00"), _linha("2026-08-30T00:00:00+00:00")],
        now=agora,
    ) == 1


def test_publicador_sem_data_legivel_devolve_none():
    from scripts.publish_us_snapshot import idade_do_giro

    assert idade_do_giro([]) is None
    assert idade_do_giro([{"symbol": "A", "metrics": {}}]) is None
    assert idade_do_giro([_linha("nao e data")]) is None
    # Sem fuso nao da para comparar com honestidade: tratar como ilegivel.
    assert idade_do_giro([_linha("2026-08-30T00:00:00")]) is None
    # metrics como texto JSON (o publicador reserializa antes de gravar).
    bruto = {"symbol": "A",
             "metrics": '{"giro_diario_usd_at": "2026-08-30T00:00:00+00:00"}'}
    assert idade_do_giro([bruto], now=datetime(2026, 8, 31, tzinfo=timezone.utc)) == 1


def test_publicador_barra_giro_vencido():
    from core.us_liquidity import LIQUIDITY_MAX_AGE_DAYS
    from scripts.publish_us_snapshot import veredito_de_frescor

    pode, recado = veredito_de_frescor(LIQUIDITY_MAX_AGE_DAYS + 1)
    assert pode is False, "publicaria uma vitrine que nasce reprovada no portao"
    assert str(LIQUIDITY_MAX_AGE_DAYS + 1) in recado and "ERRO" in recado
    # O caso real de 31/08: medicao de 20/08, 11 dias.
    assert veredito_de_frescor(11)[0] is False
    # No limite ainda publica: o teto e do consumidor, nao um a menos.
    assert veredito_de_frescor(LIQUIDITY_MAX_AGE_DAYS)[0] is True
    assert veredito_de_frescor(0)[0] is True
    # Sem data legivel tambem barra — giro sem data nao e evidencia.
    assert veredito_de_frescor(None)[0] is False
    # A valvula existe, e so ela destrava.
    assert veredito_de_frescor(999, permitir_velho=True)[0] is True


def test_barreira_de_frescor_acontece_antes_da_gravacao():
    fonte = (RAIZ / "scripts" / "publish_us_snapshot.py").read_text(encoding="utf-8")
    assert "veredito_de_frescor(idade_do_giro(rows)" in fonte, (
        "o publicador parou de consultar o frescor do que esta publicando"
    )
    assert (fonte.index("veredito_de_frescor(idade_do_giro(rows)")
            < fonte.index("_ensure_schema(tgt)")), (
        "a checagem caiu para depois da gravacao remota"
    )


# --------------------------------------------------------------- defeito C
def test_medicao_mais_recente_devolve_data_e_idade():
    from core.us_liquidity import medicao_mais_recente

    agora = datetime(2026, 9, 7, tzinfo=timezone.utc)
    quando, idade = medicao_mais_recente(
        {"AAPL": "2026-08-20T00:00:00+00:00", "MSFT": "2026-08-19T00:00:00+00:00"},
        now=agora,
    )
    assert quando == datetime(2026, 8, 20, tzinfo=timezone.utc)
    assert idade == 18
    assert medicao_mais_recente({}) is None
    assert medicao_mais_recente({"A": None}) is None
    assert medicao_mais_recente({"A": "2026-08-20T00:00:00"}) is None  # sem fuso


def test_bloqueio_diz_de_quando_e_a_medicao():
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        _apply_liquidity_gate,
    )

    velho = (datetime.now(timezone.utc) - timedelta(days=18)).isoformat()
    work = pd.DataFrame({
        "symbol": ["AAPL", "MSFT"],
        "giro_diario_usd": [5e7, 4e7],
        "giro_diario_usd_at": [velho, velho],
    })
    params = USPortfolioCreationParams(min_daily_turnover_usd=1_000_000.0)
    _, meta = _apply_liquidity_gate(work, params, [])

    bloco = meta["liquidity_block"]
    assert bloco, "com giro vencido e piso > 0 tem que bloquear"
    assert meta["liquidity_last_measured_age_days"] == 18
    assert "18 dia" in bloco, "a idade da medicao nao chegou na tela"
    assert "máximo 7" in bloco, "o teto da regra nao chegou na tela"
    assert "desatualizada" in bloco, (
        "a tela precisa dizer que o defeito e a vitrine, nao o universo"
    )


def test_bloqueio_sem_data_nao_inventa_idade():
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        _apply_liquidity_gate,
    )

    work = pd.DataFrame({"symbol": ["AAPL"], "giro_diario_usd": [5e7]})
    params = USPortfolioCreationParams(min_daily_turnover_usd=1_000_000.0)
    _, meta = _apply_liquidity_gate(work, params, [])
    assert meta["liquidity_block"]
    assert meta["liquidity_last_measured_age_days"] is None
    assert "dia(s) atrás" not in meta["liquidity_block"]
    assert "nem a idade" in meta["liquidity_block"]


def test_giro_fresco_nao_bloqueia():
    """Contraexemplo: sem ele os testes acima passariam com um bloqueio constante."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        _apply_liquidity_gate,
    )

    hoje = datetime.now(timezone.utc).isoformat()
    work = pd.DataFrame({
        "symbol": ["AAPL", "MSFT"],
        "giro_diario_usd": [5e7, 4e7],
        "giro_diario_usd_at": [hoje, hoje],
    })
    params = USPortfolioCreationParams(min_daily_turnover_usd=1_000_000.0)
    restante, meta = _apply_liquidity_gate(work, params, [])
    assert meta["liquidity_block"] is None
    assert len(restante) == 2
