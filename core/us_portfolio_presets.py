"""Perfis pré-configurados da Criação de Portfólio — Empresas Americanas.

Espelha ``core/b3_portfolio_presets`` no propósito: a aba tem ~18 parâmetros e
alguns, mal calibrados, degradam a carteira sem que isso apareça na tela. Os
valores aqui NÃO são gosto — saíram de uma varredura sobre o universo real
(2.831 empresas com score, 03/08/2026), variando um parâmetro por vez a partir
da configuração de fábrica e medindo elegíveis, indústrias aprovadas, ativos,
setores e conflitos de restrição.

**O perfil recomendado NÃO é a configuração de fábrica**, e essa é a descoberta
central da varredura. Baixar o piso de tamanho para US$ 300 mi e pedir UM líder
por indústria (em vez de dois) melhora todos os eixos ao mesmo tempo:

    fábrica (US$ 1 bi, 2 líderes)   30 ativos ·  7 setores · 20 indústrias · 72,4
    recomendado (300 mi, 1 líder)   30 ativos · 10 setores · 30 indústrias · 73,2

Parece contraintuitivo — afrouxar o filtro e melhorar a nota — mas é o efeito de
o score ser RELATIVO À INDÚSTRIA. Com dois líderes por indústria, o segundo
colocado de uma indústria grande ocupa a vaga que seria do melhor de outra
indústria; com um só, cada posição é a melhor do seu próprio nicho. E o piso de
tamanho mais baixo não traz empresa impossível de comprar: a carteira resultante
tem giro mediano de US$ 91 mi/dia e mínimo de US$ 2,2 mi/dia, porque o piso de
negociabilidade age separado do de tamanho.

Carteira idêntica em três sementes de hash distintas (PYTHONHASHSEED 0, 12345 e
99999), incluindo ordem e pesos.

Puro (sem Streamlit, sem banco). Coberto por tests/test_us_portfolio_presets.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "us-presets-1.0.0"

RECOMENDADO = "Equilibrado (recomendado)"
DEFENSIVO = "Defensivo (large caps líquidas)"
AMPLO = "Amplo (diagnóstico, não para decidir)"
PERSONALIZADO = "Personalizado"


@dataclass(frozen=True)
class Preset:
    """Uma combinação de parâmetros com a evidência que a sustenta."""
    nome: str
    resumo: str
    valores: dict = field(default_factory=dict)
    evidencias: tuple[str, ...] = ()
    ressalva: str = ""


PRESETS: dict[str, Preset] = {
    RECOMENDADO: Preset(
        nome=RECOMENDADO,
        resumo="Um líder por indústria e piso de tamanho baixo, com o piso de "
               "negociabilidade cuidando da liquidez. Foi a combinação que "
               "mediu melhor em diversificação E em nota, ao mesmo tempo.",
        valores={
            "us_create_market_cap": "≥ US$ 300 mi",
            "us_create_turnover": "≥ US$ 1 milhão",
            "us_create_quality_floor": True,
            "us_create_coverage": 50,
            "us_create_years": 5,
            "us_create_group": 4,
            "us_create_leaders": 1,
            "us_create_topn": 30,
            "us_create_entry_score": 55,
            "us_create_edge": 2.0,
            "us_create_resilience": False,
            "us_create_max_weight": 8,
            "us_create_max_industry": 15,
            "us_create_max_sector": 25,
            "us_create_weighting": "score",
        },
        evidencias=(
            "Contra a configuração de fábrica: 10 setores e 30 indústrias, "
            "contra 7 e 20; nota 73,2 contra 72,4; maior setor 20% contra 25%.",
            "Um líder por indústria em vez de dois: com dois, o segundo "
            "colocado de uma indústria grande toma a vaga do melhor de outra.",
            "Piso de tamanho em US$ 300 mi não traz empresa incomprável — a "
            "carteira sai com giro mediano de US$ 91 mi/dia e mínimo de "
            "US$ 2,2 mi/dia, porque quem filtra liquidez é o piso de volume.",
            "Amostra mínima de 4 empresas por indústria mantida: nenhuma das "
            "30 posições vem de indústria com menos de 4 empresas.",
            "Carteira idêntica em três sementes de hash, com ordem e pesos.",
            "O piso de score de entrada fica em 55, mas é INERTE: de 30 a 55 o "
            "resultado é idêntico, e mesmo a 65 a carteira final não muda — só "
            "encolhe o conjunto aprovado. Quem corta de fato é a amostra "
            "mínima por indústria e a vantagem relativa.",
        ),
    ),
    DEFENSIVO: Preset(
        nome=DEFENSIVO,
        resumo="Só empresas grandes e muito líquidas, com exigência de retorno "
               "acima do Treasury. Carteira menor e mais concentrada em nomes "
               "conhecidos, por construção.",
        valores={
            "us_create_market_cap": "≥ US$ 5 bi",
            "us_create_turnover": "≥ US$ 5 milhões",
            "us_create_quality_floor": True,
            "us_create_coverage": 50,
            "us_create_years": 10,
            "us_create_group": 4,
            "us_create_leaders": 1,
            "us_create_topn": 20,
            "us_create_entry_score": 55,
            "us_create_edge": 2.0,
            "us_create_resilience": True,
            "us_create_resilience_spread": 0.0,
            "us_create_max_weight": 10,
            "us_create_max_industry": 15,
            "us_create_max_sector": 25,
            "us_create_weighting": "score",
        },
        evidencias=(
            "20 ativos em 7 setores e 13 indústrias, nota 71,1.",
            "Valor de mercado mediano de US$ 31 bi (mínimo US$ 5,2 bi) e giro "
            "mediano de US$ 220 mi/dia (mínimo US$ 47 mi/dia).",
            "Exigir retorno acima do Treasury custa pouco aqui: 44 indústrias "
            "aprovadas caem para 40 a 0 p.p. e 37 a 5 p.p., sem reduzir a "
            "carteira — diferente do B3, onde o mesmo filtro cortava de 10 "
            "para 6 ativos.",
        ),
        ressalva="Menos ativos e menos indústrias significam mais risco por "
                 "nome. E large cap não é sinônimo de defensiva: o filtro é de "
                 "tamanho e liquidez, não de estabilidade de resultado — "
                 "confira a seção Travessia de Recessão.",
    ),
    AMPLO: Preset(
        nome=AMPLO,
        resumo="Universo máximo para EXPLORAR o que o motor enxerga, sem tetos "
               "de concentração e sem os pisos. Não use para decidir alocação.",
        valores={
            "us_create_market_cap": "≥ US$ 300 mi",
            "us_create_turnover": "Sem piso",
            # Desligado DE PROPÓSITO: este perfil existe para ver o universo
            # inteiro, inclusive o que os pisos barrariam.
            "us_create_quality_floor": False,
            "us_create_coverage": 30,
            "us_create_years": 3,
            "us_create_group": 2,
            "us_create_leaders": 1,
            "us_create_topn": 80,
            "us_create_entry_score": 30,
            "us_create_edge": 0.0,
            "us_create_resilience": False,
            "us_create_max_weight": 20,
            "us_create_max_industry": 40,
            "us_create_max_sector": 50,
            "us_create_weighting": "equal",
        },
        evidencias=(
            "80 ativos em 11 setores e 65 indústrias, nota 72,0.",
            "Amostra mínima de 2 empresas por indústria: 'líder' passa a "
            "significar 'melhor de dois', e nenhuma medida de poder preditivo "
            "é calculável nesse tamanho.",
            "Sem piso de volume voltam as 6 ações preferenciais que giram "
            "menos de US$ 1 mi/dia (BUSEP, NTRSO, SIGIP, SLNHP e outras) — nos "
            "EUA preferencial é quase-dívida, não a PN brasileira.",
        ),
        ressalva="Perfil de diagnóstico. Os tetos frouxos deixam a carteira "
                 "concentrar num único fator, e a amostra de 2 empresas por "
                 "indústria não sustenta a ideia de 'líder'.",
    ),
}


def avaliar_configuracao(valores: dict) -> list[str]:
    """Alertas sobre configurações com custo MEDIDO. Nunca bloqueia.

    Só entram casos que a varredura de 03/08/2026 quantificou. Alerta sem
    número medido atrás vira ruído, e ruído treina o usuário a ignorar todos.
    """
    alertas: list[str] = []

    def _num(chave, padrao=0.0) -> float:
        try:
            return float(valores.get(chave))          # type: ignore[arg-type]
        except (TypeError, ValueError):
            return float(padrao)

    if _num("us_create_coverage", 50) >= 90:
        alertas.append(
            "Cobertura mínima de 90% deixa 174 empresas elegíveis e apenas 5 "
            "indústrias aprovadas: a carteira cai para 10 ativos em 3 setores "
            "e o otimizador precisa afrouxar 3 tetos para caber. Exigir dado "
            "quase completo seleciona por qualidade de arquivo, não de empresa.")

    grupo = _num("us_create_group", 4)
    if grupo >= 15:
        alertas.append(
            "Amostra mínima de 15 empresas por indústria aprova só 7 "
            "indústrias e produz carteira VAZIA. A mediana das indústrias "
            "americanas não sustenta esse corte.")
    elif grupo <= 2:
        alertas.append(
            "Amostra mínima de 2 empresas por indústria: 'líder da indústria' "
            "passa a significar 'melhor de dois', e nenhuma medida de poder "
            "preditivo é calculável nesse tamanho. Sobe de 44 para 94 "
            "indústrias aprovadas — mais aprovação, menos evidência.")

    # O piso de score de entrada é INERTE em quase toda a sua faixa: de 30 a 55
    # o resultado é idêntico (44 indústrias, 30 ativos), e mesmo a 65 a carteira
    # final não muda — só encolhe o conjunto aprovado. Isso é documentação, não
    # alerta: alerta deve significar CUSTO, e transformar inércia em aviso faria
    # o perfil recomendado nascer com um, treinando o usuário a ignorar todos.
    # O fato está registrado nas evidências do perfil e no teste.
    if _num("us_create_entry_score", 55) >= 75:
        alertas.append(
            "Piso de score de entrada em 75 ou mais não aprova NENHUMA "
            "indústria: a melhor do universo alcança 75,3. A carteira sai "
            "vazia, e sem este aviso o motivo não apareceria na tela.")

    if str(valores.get("us_create_market_cap") or "") == "≥ US$ 10 bi":
        alertas.append(
            "Piso de tamanho em US$ 10 bi deixa 350 empresas e 17 indústrias "
            "aprovadas, e a nota cai de 73,6 para 68,2. Nos EUA, exigir "
            "mega-cap REDUZ a qualidade média da seleção: o score é relativo "
            "à indústria, e as líderes de nicho ficam de fora.")

    turnover = str(valores.get("us_create_turnover") or "")
    if turnover in ("", "Sem piso"):
        alertas.append(
            "Sem piso de negociabilidade voltam ao universo 6 papéis que giram "
            "menos de US$ 1 mi/dia — quase todos AÇÕES PREFERENCIAIS (BUSEP, "
            "NTRSO, SIGIP, SLNHP). Nos EUA preferencial é quase-dívida, e o "
            "banco não tem campo que a separe da ordinária.")
    elif turnover == "≥ US$ 20 milhões":
        alertas.append(
            "Piso de US$ 20 mi/dia remove 113 empresas do universo (802 → 689) "
            "e 8 indústrias aprovadas, sem mudar a carteira final. Exigir mais "
            "liquidez do que o seu aporte precisa custa empresa boa sem "
            "reduzir risco.")

    lideres = _num("us_create_leaders", 1)
    if lideres >= 3:
        alertas.append(
            f"{lideres:g} líderes por indústria concentram a carteira em menos "
            "nichos: 19 indústrias e 7 setores contra 30 e 10 com um líder por "
            "indústria. O segundo colocado de uma indústria toma a vaga do "
            "melhor de outra.")

    topn = _num("us_create_topn", 30)
    teto = _num("us_create_max_weight", 8) / 100
    if topn and teto and teto < 1 / topn - 1e-9:
        alertas.append(
            f"Teto de {teto:.0%} por ativo é matematicamente impossível com "
            f"{topn:g} ativos (mínimo {1 / topn:.1%}). O app ajusta e declara, "
            "mas a carteira deixa de respeitar o limite que você pediu.")
    if topn >= 80:
        alertas.append(
            "Com 80 ativos a nota média cai para 68,5 (contra 73,2 com 30): "
            "cada posição adicional é, por construção, pior que a anterior.")

    if "us_create_quality_floor" in valores and \
            not valores.get("us_create_quality_floor"):
        alertas.append(
            "Piso absoluto de qualidade DESLIGADO. No universo de 03/08/2026 "
            "isso não muda a carteira — o score de entrada já ordena as "
            "'Excluída' para fora antes do corte —, mas remove a única guarda "
            "contra uma empresa marcada como excluída pelo laboratório entrar "
            "caso score e penalidade de risco se descolem.")
    return alertas


def identificar_perfil(valores: dict) -> str:
    """Nome do perfil que corresponde exatamente aos valores, ou Personalizado."""
    for nome, preset in PRESETS.items():
        if all(valores.get(chave) == esperado
               for chave, esperado in preset.valores.items()):
            return nome
    return PERSONALIZADO
