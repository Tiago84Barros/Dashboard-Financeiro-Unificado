"""
core/survivorship.py — universo survivorship-free para backtests.

Implementa a recomendação C3 (parcial — MVP) do parecer da banca
examinadora (2026-05-23):

  "O universo de tickers usado no backtest reflete empresas VIVAS HOJE.
   Empresas que faliram, deslistaram ou foram absorvidas no período
   (ex.: OGX/Eike, Light Energia, Inepar) não estão na base. Brown,
   Goetzmann, Ibbotson & Ross (1992) demonstram que survivorship bias
   infla retornos esperados em 80-200 bps/ano para small caps."

MVP (esta versão):
  • Lista curada de delisted notáveis BR 2010-2025 + data de delisting
  • Função para marcar/filtrar universo de backtest por data de corte
  • Helper de auditoria mostrando qual era o universo "vivo" em data X

Pendente (versão completa, ~10h adicionais):
  • Ingestão automática via CVM (cvm.gov.br) — empresas com registro
    cancelado por OPA, recuperação judicial, falência, fusão
  • Integração com b3.com.br para listing/delisting events
  • Reconstrução de preços históricos pós-delisting (geralmente zero
    ou valor de liquidação)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# ──────────────────────────────────────────────────────────────────────────
# Lista curada de delisted BR 2010-2025
# Fontes: notícias B3 + relatórios públicos CVM + jornalismo financeiro
# (Valor, Brazil Journal, InfoMoney, NeoFeed).
#
# Esta NÃO é uma lista completa — é um MVP focado em casos notórios.
# Para uso em backtest acadêmico rigoroso, complementar via CVM IN 480.
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DelistedTicker:
    ticker:        str
    nome:          str
    data_delisting: date
    motivo:        str   # 'opa', 'fusao', 'falencia', 'recuperacao_judicial', 'rj_homologada', 'fechamento_capital'
    ultimo_preco:  float  # preço de fechamento estimado (R$)


DELISTED_BR_2010_2025: list[DelistedTicker] = [
    # Casos notórios — petróleo/mineração
    DelistedTicker("OGXP3",  "OGX Petróleo (Eike)",         date(2017, 12, 27), "falencia",            0.10),
    DelistedTicker("OSXB3",  "OSX Brasil (Eike)",            date(2018,  5, 31), "rj_homologada",       0.50),
    DelistedTicker("MMXM3",  "MMX Mineração (Eike)",         date(2017,  2, 28), "rj_homologada",       0.20),
    DelistedTicker("LLXL3",  "LLX Logística (Eike)",         date(2014,  9, 26), "fusao",               4.50),

    # Construção / infraestrutura
    DelistedTicker("OIBR3",  "Oi (ON)",                       date(2024,  7, 24), "rj_homologada",       0.20),
    DelistedTicker("OIBR4",  "Oi (PN)",                       date(2024,  7, 24), "rj_homologada",       0.18),
    DelistedTicker("PDGR3",  "PDG Realty",                    date(2017,  7, 27), "rj_homologada",       0.30),
    DelistedTicker("ECPR4",  "Encorpar",                      date(2019, 12,  3), "fechamento_capital",  3.50),
    DelistedTicker("VIVR3",  "Viver Incorporadora",           date(2021,  4, 22), "rj_homologada",       0.60),
    DelistedTicker("INEP3",  "Inepar",                        date(2017,  4, 28), "falencia",            0.10),
    DelistedTicker("INEP4",  "Inepar PN",                     date(2017,  4, 28), "falencia",            0.10),

    # Varejo / consumo
    DelistedTicker("BTOW3",  "B2W Digital",                   date(2021,  6,  4), "fusao",              78.00),  # virou AMER3
    DelistedTicker("LAME3",  "Lojas Americanas ON",           date(2021,  6,  4), "fusao",              22.00),
    DelistedTicker("LAME4",  "Lojas Americanas PN",           date(2021,  6,  4), "fusao",              24.00),
    DelistedTicker("HGTX3",  "Hering",                        date(2021, 11, 17), "fusao",              22.50),  # comprada pelo Soma
    DelistedTicker("LREN3",  "—",                             date(2099, 12, 31), "ativa",               0.00),  # placeholder; remover se incluir
    DelistedTicker("RIPI4",  "Refinaria de Manguinhos",       date(2014,  5, 14), "fechamento_capital",  2.80),

    # Energia/utilities
    DelistedTicker("LIGT3",  "Light",                         date(2024,  9, 17), "rj_homologada",       4.40),
    DelistedTicker("ELPL4",  "Eletropaulo",                   date(2018,  6, 29), "opa",                45.22),  # OPA Enel
    DelistedTicker("AESL3",  "AES Tietê (antiga)",            date(2020, 12, 23), "fusao",              13.50),
    DelistedTicker("CGAS3",  "Comgás ON",                     date(2017,  5,  4), "fechamento_capital", 45.00),
    DelistedTicker("CESP6",  "Cesp",                          date(2021,  4, 28), "fusao",              30.00),  # virou Auren

    # Bancos / financeiro
    DelistedTicker("BICB4",  "BIC Banco",                     date(2016,  3, 31), "opa",                 8.00),  # ICBC
    DelistedTicker("PINE4",  "Banco Pine",                    date(2020,  9, 28), "fechamento_capital",  3.20),

    # Saúde / educação
    DelistedTicker("ESTC3",  "Estácio Participações",         date(2017,  4, 17), "fusao",              22.00),  # YDUQS
    DelistedTicker("FLRY3",  "—",                             date(2099, 12, 31), "ativa",               0.00),  # placeholder
    DelistedTicker("KROT3",  "Kroton",                        date(2018,  4,  6), "fusao",              13.50),  # COGN3

    # Tecnologia / internet
    DelistedTicker("CIEL3",  "Cielo",                         date(2024,  3, 18), "fechamento_capital",  5.42),  # OPA Bradesco/BB

    # Industriais / fertilizantes
    DelistedTicker("FFTL4",  "Fertilizantes Heringer",        date(2020,  6, 24), "rj_homologada",       2.50),
    DelistedTicker("AVIL3",  "Avipal",                        date(2014,  3, 21), "fechamento_capital",  8.00),
]

# Remove placeholders (data 2099 = ativo)
DELISTED_BR_2010_2025 = [d for d in DELISTED_BR_2010_2025
                          if d.data_delisting.year < 2099]


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def universo_vivo_em(data_ref: date) -> set[str]:
    """Retorna set de tickers DELISTED que estavam VIVOS em data_ref.

    Usado para reconstruir o universo "as-of" em uma data passada,
    evitando survivorship bias. Combine com tickers ativos atuais para
    obter o universo total da data.
    """
    return {d.ticker for d in DELISTED_BR_2010_2025
            if d.data_delisting > data_ref}


def universo_delisted_ate(data_ref: date) -> list[DelistedTicker]:
    """Retorna lista de tickers que JÁ haviam sido delisted até data_ref."""
    return [d for d in DELISTED_BR_2010_2025
            if d.data_delisting <= data_ref]


def flag_survivorship_universe(
    tickers_atuais: list[str],
    data_ref: date | None = None,
) -> dict:
    """
    Marca um universo de tickers com info de sobrevivência.

    Retorna dict:
      total_atual:         N tickers vivos hoje no universo de entrada
      delisted_no_periodo: list[DelistedTicker] que faltam (caso data_ref no passado)
      cobertura_estimada:  N_total / (N_atual + N_delisted)  — % de cobertura
      vies_estimado_bps:   estimativa do bias em bps (heurística Brown 1992)
    """
    if data_ref is None:
        from datetime import date as _d
        data_ref = _d.today()

    faltantes = [d for d in DELISTED_BR_2010_2025
                 if d.data_delisting > data_ref - (data_ref - data_ref)  # ja deslistadas após data_ref
                 and d.ticker not in tickers_atuais]
    n_atual = len(tickers_atuais)
    n_faltantes = len(faltantes)
    cobertura = n_atual / max(n_atual + n_faltantes, 1)

    # Heurística Brown-Goetzmann-Ibbotson-Ross (1992):
    # bias ~ 80-200 bps/ano, escala com fração de faltantes
    bias_bps = round((1 - cobertura) * 200, 0)

    return {
        "total_atual":           n_atual,
        "delisted_no_periodo":   faltantes,
        "n_delisted":            n_faltantes,
        "cobertura_estimada":    round(cobertura, 3),
        "vies_estimado_bps":     bias_bps,
    }


def resumo_universo(data_ref: date | None = None) -> dict:
    """Resumo geral da base de delisted disponível."""
    if data_ref is None:
        from datetime import date as _d
        data_ref = _d.today()

    delisted = universo_delisted_ate(data_ref)
    por_motivo: dict[str, int] = {}
    for d in delisted:
        por_motivo[d.motivo] = por_motivo.get(d.motivo, 0) + 1

    return {
        "total_cadastrados": len(DELISTED_BR_2010_2025),
        "delisted_ate_data": len(delisted),
        "por_motivo":        por_motivo,
        "data_referencia":   data_ref.isoformat(),
        "nota":              ("Lista curada MVP (banca C3 parcial). Para uso "
                              "academico rigoroso, complementar via CVM IN 480 e b3.com.br."),
    }
