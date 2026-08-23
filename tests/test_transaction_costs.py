"""Modelo de custos de transacao — B3 calibrado, outras classes explicitas.

Fase 3b Task 3: a carteira global tem B3, FII e EUA, mas o modelo so foi
calibrado para B3. Estes testes fixam o comportamento de sempre para b3 (nada
pode mudar) e verificam que outras classes nunca caem, por acidente, em um
numero que parece apurado sem ser.
"""
import math

import pytest

from core.transaction_costs import (
    CLASSE_B3,
    CLASSE_FII,
    CLASSE_US,
    CostConfig,
    custo_compra,
    custo_por_classe,
    custo_venda,
    is_large_cap,
    overhead_anual_estimado,
)

# ──────────────────────────────────────────────────────────────────────────
# Pin: comportamento de b3 e identico ao de antes do Task 3
# ──────────────────────────────────────────────────────────────────────────

def test_is_large_cap_chamada_com_um_argumento_continua_funcionando():
    # Callers existentes (core/allocation_calibration.py) chamam
    # is_large_cap(ticker) sem classe. Isso nao pode quebrar nem mudar.
    assert is_large_cap("PETR4") is True
    assert is_large_cap("VALE3") is True
    assert is_large_cap("XYZW3") is False


def test_is_large_cap_classe_b3_explicita_e_igual_ao_default():
    assert is_large_cap("PETR4", CLASSE_B3) is True
    assert is_large_cap("XYZW3", CLASSE_B3) is False


def test_cost_config_default_calibrado_e_classe_b3():
    cfg = CostConfig()
    assert cfg.calibrado is True
    assert cfg.classe == CLASSE_B3


def test_brasil_pf_default_pin_numerico():
    cfg = CostConfig.brasil_pf_default()
    assert cfg.calibrado is True
    assert cfg.classe == CLASSE_B3
    # Compra de large cap: metade do spread de 10 bps
    fee_compra = custo_compra("PETR4", 10_000.0, cfg)
    assert fee_compra == pytest.approx(10_000.0 * (10.0 / 2.0 / 10_000.0))
    # Compra de small cap: metade do spread de 30 bps
    fee_compra_small = custo_compra("XYZW3", 10_000.0, cfg)
    assert fee_compra_small == pytest.approx(10_000.0 * (30.0 / 2.0 / 10_000.0))
    # Venda com lucro acima da isencao mensal gera IR
    fee_venda, ir = custo_venda("PETR4", 25_000.0, 5_000.0, 0.0, cfg)
    assert fee_venda == pytest.approx(25_000.0 * (10.0 / 2.0 / 10_000.0))
    excesso = 25_000.0 - 20_000.0
    frac = excesso / 25_000.0
    assert ir == pytest.approx(5_000.0 * frac * 0.15)


def test_desligado_continua_zerando_custos():
    cfg = CostConfig.desligado()
    assert cfg.ativo is False
    assert cfg.calibrado is True  # desligado nao e "nao calibrado"
    assert custo_compra("PETR4", 10_000.0, cfg) == 0.0
    assert custo_venda("PETR4", 10_000.0, 5_000.0, 0.0, cfg) == (0.0, 0.0)


def test_overhead_anual_estimado_pin_numerico():
    cfg = CostConfig.brasil_pf_default()
    overhead = overhead_anual_estimado(cfg, rotation_pct_aa=0.40)
    spread_mix = 0.70 * 10.0 + 0.30 * 30.0
    round_trip = 2.0 * 0.40 * spread_mix
    ir_est = 1200 * 0.15 * 0.80
    assert overhead == pytest.approx(round_trip + ir_est)


# ──────────────────────────────────────────────────────────────────────────
# is_large_cap class-aware: heuristica de prefixo so vale para b3
# ──────────────────────────────────────────────────────────────────────────

def test_is_large_cap_fora_de_b3_nunca_usa_heuristica_de_prefixo():
    # "VALE3" bateria o prefixo IBOV se fosse avaliado como b3 — mas com
    # classe explicita "us"/"fii" a heuristica nao se aplica.
    assert is_large_cap("VALE3", CLASSE_US) is False
    assert is_large_cap("VALE3", CLASSE_FII) is False


def test_is_large_cap_us_nao_confunde_megacap_liquida_com_small_cap():
    # ADBE/TJX/PGR nao batem nenhum prefixo do IBOV — o ponto do bug
    # original. Com classe "us" a resposta e False de forma deliberada
    # (nao ha lista calibrada), nunca um "sim" ou "nao" por acidente.
    for ticker in ("ADBE", "TJX", "PGR"):
        assert is_large_cap(ticker, CLASSE_US) is False


# ──────────────────────────────────────────────────────────────────────────
# Estado "nao calibrado": nunca um numero plausivel
# ──────────────────────────────────────────────────────────────────────────

def test_nao_calibrado_marca_calibrado_falso_e_guarda_a_classe():
    cfg = CostConfig.nao_calibrado(CLASSE_US)
    assert cfg.calibrado is False
    assert cfg.classe == CLASSE_US


def test_nao_calibrado_campos_numericos_sao_sentinela_nan():
    cfg = CostConfig.nao_calibrado(CLASSE_FII)
    assert math.isnan(cfg.corretagem_fixa)
    assert math.isnan(cfg.spread_bps_large)
    assert math.isnan(cfg.spread_bps_small)
    assert math.isnan(cfg.ir_rate)
    assert math.isnan(cfg.isencao_mes)


def test_nao_calibrado_custo_compra_e_nan_nunca_zero_nem_numero_plausivel():
    cfg = CostConfig.nao_calibrado(CLASSE_US)
    custo = custo_compra("ADBE", 10_000.0, cfg)
    assert math.isnan(custo)


def test_nao_calibrado_custo_venda_e_nan_em_ambos_os_componentes():
    cfg = CostConfig.nao_calibrado(CLASSE_FII)
    fee, ir = custo_venda("HGLG11", 10_000.0, 2_000.0, 0.0, cfg)
    assert math.isnan(fee)
    assert math.isnan(ir)


def test_nao_calibrado_com_lucro_zero_ainda_e_nan_nao_vira_zero_silencioso():
    # Ponto sensivel: comparacoes com NaN sao sempre False em Python, entao
    # um guard ingenuo por "lucro > isencao" deixaria o IR passar como 0.0
    # silenciosamente. O contrato exige NaN explicito, nunca esse 0.0 falso.
    cfg = CostConfig.nao_calibrado(CLASSE_US)
    fee, ir = custo_venda("ADBE", 10_000.0, 0.0, 0.0, cfg)
    assert math.isnan(fee)
    assert math.isnan(ir)


def test_overhead_anual_estimado_nao_calibrado_e_nan():
    cfg = CostConfig.nao_calibrado(CLASSE_US)
    assert math.isnan(overhead_anual_estimado(cfg))


def test_valor_bruto_nao_positivo_continua_zero_mesmo_nao_calibrado():
    # ativo/valor_bruto invalido sai antes de tocar em calibrado — nao muda.
    cfg = CostConfig.nao_calibrado(CLASSE_US)
    assert custo_compra("ADBE", 0.0, cfg) == 0.0
    assert custo_venda("ADBE", -1.0, 0.0, 0.0, cfg) == (0.0, 0.0)


# ──────────────────────────────────────────────────────────────────────────
# custo_por_classe: resolve pelo mapa, cai para nao_calibrado se ausente
# ──────────────────────────────────────────────────────────────────────────

def test_custo_por_classe_resolve_do_mapa():
    b3_cfg = CostConfig.brasil_pf_default()
    mapa = {CLASSE_B3: b3_cfg}
    assert custo_por_classe(CLASSE_B3, mapa) is b3_cfg


def test_custo_por_classe_ausente_do_mapa_cai_para_nao_calibrado():
    mapa = {CLASSE_B3: CostConfig.brasil_pf_default()}
    resolvido = custo_por_classe(CLASSE_US, mapa)
    assert resolvido.calibrado is False
    assert resolvido.classe == CLASSE_US


def test_custo_por_classe_mapa_vazio_cai_para_nao_calibrado():
    resolvido = custo_por_classe(CLASSE_FII, {})
    assert resolvido.calibrado is False
    assert resolvido.classe == CLASSE_FII


def test_custo_por_classe_determinismo_independe_da_ordem_do_mapa():
    mapa_a = {CLASSE_B3: CostConfig.brasil_pf_default(), CLASSE_US: CostConfig(classe=CLASSE_US, calibrado=True)}
    mapa_b = {CLASSE_US: CostConfig(classe=CLASSE_US, calibrado=True), CLASSE_B3: CostConfig.brasil_pf_default()}
    assert custo_por_classe(CLASSE_US, mapa_a).classe == custo_por_classe(CLASSE_US, mapa_b).classe
    assert custo_por_classe(CLASSE_B3, mapa_a).classe == custo_por_classe(CLASSE_B3, mapa_b).classe


# ──────────────────────────────────────────────────────────────────────────
# Config calibrada de verdade para uma classe fora de b3 (usuario calibrou)
# ──────────────────────────────────────────────────────────────────────────

def test_classe_us_calibrada_pelo_usuario_produz_custo_real_nao_nan():
    # Quando o usuario de fato calibra (nao usa nao_calibrado), o custo sai
    # normal — o ponto e que a ausencia de calibracao e que precisa ser
    # visivel, nao a classe em si.
    cfg = CostConfig(
        classe=CLASSE_US,
        calibrado=True,
        spread_bps_large=5.0,
        spread_bps_small=5.0,
        ir_rate=0.0,
        isencao_mes=0.0,
    )
    custo = custo_compra("ADBE", 10_000.0, cfg)
    assert custo == pytest.approx(10_000.0 * (5.0 / 2.0 / 10_000.0))
    assert not math.isnan(custo)
