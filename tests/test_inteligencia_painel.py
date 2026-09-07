"""Guardas do objeto estruturado que a tela e a LLM consomem.

Aqui moram os cenários de integração que o requisito lista: atualização
recente, atualização vencida, falha da API, evento sem histórico, crise ativa,
crise encerrada, score alterado e score mantido. Os dois cenários de LLM estão
em ``tests/test_inteligencia_llm.py``, que é onde a ancoragem é exercida.

O teste mais importante deste arquivo é
:func:`test_texto_exibido_reancora_no_numero_publicado`: ele liga a formatação
da tela ao verificador de ancoragem. Sem ele, arredondar para exibir volta a
transformar a citação correta da tela em "número inventado".
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core.eventos_extremos import antifragilidade as af
from core.eventos_extremos import evidencias as ev
from core.eventos_extremos import niveis, transicao
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz
from core.llm_grounding import check_grounding, parse_number
from core.memoria_mercado import estimativa as memest
from core.memoria_mercado import scores as S

AGORA = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


# ── Fixtures ────────────────────────────────────────────────────────────────
def carteira(n: int = 16) -> pd.DataFrame:
    paises = ["BR"] * 11 + ["US"] * 3 + ["JP", "DE"]
    moedas = ["BRL"] * 11 + ["USD"] * 3 + ["JPY", "EUR"]
    return pd.DataFrame([{
        "symbol": f"AT{i:02d}", "weight_global": 1.0 / n,
        "sector": f"S{i % 8}", "country": paises[i % len(paises)],
        "currency": moedas[i % len(moedas)],
        "asset_class": "caixa" if i < 2 else "acoes"} for i in range(n)])


def indice_saudavel():
    return af.calcular(carteira(), liquidez=0.25, correlacao_estresse=0.10,
                       qualidade_credito=0.95, perda_simulada=0.05)


def decisao(*, simbolo="PETR4", conj=None, estrut=None):
    e = S.estrutural(estrut or {"rentabilidade": 78, "endividamento": 70,
                                "crescimento": 65, "eficiencia": 60,
                                "governanca": 72})
    c = S.conjuntural(conj or {"noticias": -10, "macro": -5,
                               "memoria_mercado": -8, "tecnico": 0})
    return S.avaliar(e, c, simbolo=simbolo)


def veredito(*, sev_mercado=0.8, oficial=True, abrangencia=None, anterior=None):
    conj = ev.Conjunto(
        informacional=ev.informacional(
            fonte_oficial=oficial, n_fontes_independentes=3,
            confiabilidade_maxima=0.95, concordancia=0.9,
            horas_desde_publicacao=1.0, materialidade=0.9),
        mercado=ev.mercado({"volatilidade": 3.5, "indices": 0.22,
                            "liquidez": 0.6, "correlacao": 0.7}),
        carteira=ev.carteira(exposicao_direta=0.35, concentracao_hhi=0.12,
                             liquidez_disponivel=0.20, perda_simulada=0.25))
    return transicao.avaliar(conj, abrangencia=abrangencia, anterior=anterior,
                             evento_id="ev-teste", agora=AGORA)


def estimativa_publicavel(n=12):
    return memest.Estimativa(
        tipo_evento="crise_bancaria", simbolo="PETR4", faixa=(-0.18, -0.04),
        valor_central=-0.11, horizonte=(5, 40), horizonte_base=20,
        direcao="baixa", n_amostra=n, similaridade=68.0, confianca="media",
        experimental=False, publicavel=True, intervalo_historico=(-0.32, 0.05),
        mediana_historica=-0.09, condicoes_invalidam=(),
        limitacoes=("retorno anormal contra o Ibovespa",))


def item(**extra):
    base = dict(id="n1", titulo="Banco X suspende resgates", fonte="valor.com.br",
                publicado_em=AGORA - dt.timedelta(hours=2),
                tickers=("ITUB4",), setores=("Financeiro",), paises=("BR",),
                n_fontes=1)
    base.update(extra)
    return P.ItemNoticia(**base)


# ── Frescor: atualização recente, vencida e falha da API ────────────────────
def test_atualizacao_recente_nao_e_destacada():
    pn = P.montar(frescor=(qz.Frescor("Notícias", AGORA - dt.timedelta(hours=1), 6),),
                  agora=AGORA)
    assert pn.desatualizados == ()
    assert pn.ultima_atualizacao == AGORA - dt.timedelta(hours=1)


def test_atualizacao_vencida_e_destacada_e_entra_nas_limitacoes():
    pn = P.montar(frescor=(qz.Frescor("Notícias", AGORA - dt.timedelta(hours=30), 6),),
                  agora=AGORA)
    assert [f.rotulo for f in pn.desatualizados] == ["Notícias"]
    assert any("Desatualizado" in lim for lim in pn.limitacoes)


def test_falha_da_api_aparece_como_provedor_fora_e_avisa_que_silencio_nao_e_calmaria():
    pn = P.montar(provedores=(qz.Provedor("newsapi", False, "HTTP 503"),
                              qz.Provedor("brapi", True)),
                  agora=AGORA)
    assert [p.nome for p in pn.provedores_fora] == ["newsapi"]
    texto = " ".join(pn.limitacoes)
    assert "newsapi" in texto and "não calmaria" in texto


def test_ultima_atualizacao_e_a_mais_antiga_e_nao_a_mais_nova():
    """Publicar a mais recente faz metade velha do painel parecer atual."""
    pn = P.montar(frescor=(
        qz.Frescor("Notícias", AGORA - dt.timedelta(hours=1), 6),
        qz.Frescor("Carteira", AGORA - dt.timedelta(hours=20), 24)), agora=AGORA)
    assert pn.ultima_atualizacao == AGORA - dt.timedelta(hours=20)


def test_fonte_indisponivel_nao_conta_como_atualizacao():
    pn = P.montar(frescor=(
        qz.Frescor("Notícias", AGORA, 6, disponivel=False, erro="timeout"),),
        agora=AGORA)
    assert pn.ultima_atualizacao is None


# ── A seção que não pôde ser calculada continua aparecendo ──────────────────
def test_painel_sem_nenhum_motor_ainda_renderiza_as_tres_secoes():
    pn = P.montar(agora=AGORA)
    titulos = [b.titulo for b in pn.blocos]
    assert titulos == ["Situação de crise", "Antifragilidade da carteira",
                       "Memória de mercado"]


def test_crise_nao_avaliada_nao_pode_parecer_ausencia_de_crise():
    b = P.bloco_crise(None)
    assert b.valores[0].qualidade == qz.AUSENTE
    assert any("não significa ausência de risco" in lim for lim in b.limitacoes)


def test_antifragilidade_nao_calculada_nao_afirma_resistencia():
    b = P.bloco_antifragilidade(None)
    assert any("nada aqui autoriza concluir" in lim for lim in b.limitacoes)


# ── Crise ativa e crise encerrada ───────────────────────────────────────────
def test_crise_ativa_publica_nivel_severidade_e_a_regra_que_a_sustenta():
    v = veredito()
    b = P.bloco_crise(v, evento=item(estado_verificacao="confirmada_fonte_primaria"))
    assert v.nivel.codigo >= niveis.NIVEL_ATENCAO
    nivel = b.valor_de("Nível de crise")
    assert nivel is not None and str(v.nivel.codigo) in str(nivel.valor)
    assert b.detalhe_tecnico, "a justificativa auditável tem que viajar junto"
    conf = b.valor_de("Confirmação")
    assert "fonte oficial" in str(conf.valor)


def test_crise_encerrada_volta_para_nivel_zero_sem_apagar_a_secao():
    calmo = ev.Conjunto(
        informacional=ev.informacional(
            fonte_oficial=False, n_fontes_independentes=1,
            confiabilidade_maxima=0.4, concordancia=0.9,
            horas_desde_publicacao=200.0, materialidade=0.05),
        mercado=ev.mercado({"volatilidade": 1.0, "indices": 0.0,
                            "liquidez": 0.0, "correlacao": 0.1}),
        carteira=ev.carteira(exposicao_direta=0.0, concentracao_hhi=0.08,
                             liquidez_disponivel=0.30, perda_simulada=0.02))
    v = transicao.avaliar(calmo, abrangencia=niveis.ABRANGENCIA_ATIVO,
                          evento_id="ev-teste", agora=AGORA)
    b = P.bloco_crise(v)
    assert v.nivel.codigo == niveis.NIVEL_NORMAL
    assert b.valor_de("Nível de crise").medido, "o bloco não some ao encerrar"


def test_nivel_barrado_por_teto_publica_o_bruto_ao_lado():
    """"O 4 foi avaliado e barrado" é diferente de "o 4 nunca esteve na mesa"."""
    v = veredito(abrangencia=niveis.ABRANGENCIA_ATIVO)
    b = P.bloco_crise(v)
    if v.nivel_bruto != v.nivel.codigo:
        assert b.valor_de("Nível antes dos tetos") is not None
    else:
        pytest.skip("cenário não acionou teto")


def test_exposicao_nao_calculada_nao_vira_exposicao_zero():
    b = P.bloco_crise(veredito(), exposicao=None)
    vulner = b.valor_de("Ativos vulneráveis")
    assert vulner is not None and not vulner.medido


# ── Antifragilidade: os doze componentes sempre publicados ──────────────────
def test_os_doze_componentes_saem_medidos_ou_nao():
    b = P.bloco_antifragilidade(indice_saudavel())
    rotulos = {v.rotulo for v in b.valores}
    for chave in af.COMPONENTES:
        assert af.ROTULOS[chave] in rotulos, chave


def test_indice_sem_nucleo_medido_nao_publica_nota_mas_publica_partes():
    i = af.calcular(carteira().drop(columns=["asset_class"]))
    b = P.bloco_antifragilidade(i)
    assert not b.valor_de("Índice de antifragilidade").medido
    assert b.valor_de(af.ROTULOS[af.C_CONC_SETOR]).medido, (
        "o que foi medido continua publicado")
    assert b.limitacoes


def test_cobertura_viaja_ao_lado_da_nota():
    b = P.bloco_antifragilidade(indice_saudavel())
    assert b.valor_de("Cobertura dos componentes").medido


# ── Memória de mercado: com e sem histórico ─────────────────────────────────
def test_evento_sem_historico_nao_inventa_faixa():
    b = P.bloco_memoria(None)
    assert not b.valores[0].medido
    assert any("Sem amostra histórica" in lim for lim in b.limitacoes)


def test_amostra_pequena_publica_o_tamanho_e_a_limitacao():
    b = P.bloco_memoria(estimativa_publicavel(n=3))
    assert b.valor_de("Tamanho da amostra").valor == 3
    assert any("não sustenta inferência" in lim for lim in b.limitacoes)


def test_impacto_sai_em_faixa_e_nunca_como_numero_unico():
    b = P.bloco_memoria(estimativa_publicavel())
    v = b.valor_de("Impacto atual estimado")
    assert v.qualidade == qz.ESTIMATIVA and v.faixa is not None
    assert " a " in v.texto


def test_estimativa_nao_publicavel_nao_vira_impacto():
    est = estimativa_publicavel()
    est = memest.Estimativa(**{**est.__dict__, "publicavel": False})
    b = P.bloco_memoria(est)
    assert not b.valor_de("Impacto atual estimado").medido


def test_tempo_de_recuperacao_nao_medido_nao_vira_zero():
    b = P.bloco_memoria(estimativa_publicavel(), tempo_recuperacao=None)
    assert not b.valor_de("Tempo histórico de recuperação").medido


# ── Fundamentos + Cenário: score alterado e score mantido ───────────────────
def test_score_mantido_diz_que_nada_mudou():
    d = decisao()
    be = P.bloco_empresa(d, anterior=d)
    assert be.mudou is False
    assert be.o_que_mudou == ("Nada mudou desde a última avaliação.",)


def test_score_alterado_nomeia_o_que_mudou_e_em_quanto():
    antes = decisao(conj={"noticias": 5, "macro": 0, "memoria_mercado": 0,
                          "tecnico": 0})
    depois = decisao(conj={"noticias": -80, "macro": -70, "memoria_mercado": -75,
                           "tecnico": -60})
    be = P.bloco_empresa(depois, anterior=antes)
    assert be.mudou is True
    texto = " ".join(be.o_que_mudou)
    assert "→" in texto, "a mudanca tem de dizer de-para, nao so que mudou"
    assert "Aporte passou a ser bloqueado" in texto
    assert "Aporte suspenso" in texto


def test_suspensao_de_aporte_nao_vira_sugestao_de_venda():
    """Teto de ação do repositório: nada aqui reduz posição existente."""
    d = decisao(conj={"noticias": -95, "macro": -90, "memoria_mercado": -90,
                      "tecnico": -85})
    be = P.bloco_empresa(d)
    if be.situacao == P.SIT_SUSPENSAO:
        assert "Nenhuma venda" in be.bloco.explicacao_simples
    assert not d.altera_posicao_existente


def test_score_combinado_e_recusado_por_padrao_com_o_motivo():
    be = P.bloco_empresa(decisao())
    v = be.bloco.valor_de("Score final combinado")
    assert not v.medido and "escalas são diferentes" in v.observacao


def test_toda_explicacao_simples_recusa_garantia_de_retorno():
    for conj in ({"noticias": 60}, {"noticias": -90}, {"noticias": 0}):
        be = P.bloco_empresa(decisao(conj=conj))
        assert "não é garantia de retorno" in be.bloco.explicacao_simples.lower()


def test_prioridade_anterior_ausente_nao_vira_zero():
    be = P.bloco_empresa(decisao())
    assert not be.bloco.valor_de("Prioridade anterior de aporte").medido


def test_o_que_invalidaria_a_analise_nunca_sai_vazio():
    be = P.bloco_empresa(decisao())
    assert be.invalidariam


def test_toda_situacao_tem_icone_e_rotulo():
    for s in P.SITUACOES:
        ap = P.APARENCIA_SITUACAO[s]
        assert ap["icone"].strip() and ap["rotulo"].strip()


# ── Notícias: fonte, data, hora e os filtros ────────────────────────────────
def test_toda_noticia_mostra_fonte_data_e_hora():
    it = item()
    assert "valor.com.br" in it.carimbo and "02/09/2026" in it.carimbo


def test_noticia_sem_data_diz_que_nao_tem_data():
    assert "não informada" in item(publicado_em=None).carimbo


def test_noticia_nao_confirmada_e_hipotese_e_nao_fato():
    assert item().qualidade_conteudo == qz.HIPOTESE
    assert not item().confirmado


def test_direcao_indefinida_nao_vira_neutra():
    v = P._direcao_valor("indefinida", confirmado=True)
    assert not v.medido and "nem alta nem baixa" in v.observacao


def test_filtros_ignoram_acento_e_caixa():
    itens = (item(empresas=("Petrobrás",), tickers=("PETR4",)),
             item(id="n2", empresas=("Vale",), tickers=("VALE3",)))
    assert len(P.filtrar(itens, empresa="petrobras")) == 1
    assert len(P.filtrar(itens, ticker="vale3")) == 1
    assert len(P.filtrar(itens, pais="br")) == 2


def test_filtro_por_confirmacao_separa_hipotese_de_fato():
    itens = (item(), item(id="n2", estado_verificacao="confirmada_fonte_primaria"))
    assert len(P.filtrar(itens, confirmadas=True)) == 1
    assert len(P.filtrar(itens, confirmadas=False)) == 1


def test_noticia_do_ticker_chega_na_secao_da_empresa():
    pn = P.montar(decisoes=[decisao(simbolo="ITUB4")],
                  noticias=(item(), item(id="n2", tickers=("PETR4",))),
                  agora=AGORA)
    be = pn.empresa("ITUB4")
    assert [n.id for n in be.noticias] == ["n1"]
    assert any("Banco X" in e for e in be.evidencias)


# ── A ponte com o verificador de ancoragem ──────────────────────────────────
def test_texto_exibido_reancora_no_numero_publicado():
    """O que a tela imprime tem de ser lido de volta como o mesmo número.

    Regressão: o formatador arredondava para duas casas enquanto
    ``numeros()`` guardava três. A LLM citava a tela corretamente e o
    verificador a reprovava por inventar dado.
    """
    pn = P.montar(indice=indice_saudavel(), decisoes=[decisao()],
                  est=estimativa_publicavel(), agora=AGORA)
    publicados = set(pn.numeros())
    for bloco in list(pn.blocos) + [e.bloco for e in pn.empresas]:
        for v in bloco.valores:
            if not v.medido or not isinstance(v.valor, (int, float)):
                continue
            lido = parse_number(v.texto.replace(v.unidade, "").strip())
            if lido is None:
                continue  # ambíguo: o verificador não o cobra, e tudo bem
            assert any(abs(lido - p) <= 1e-6 for p in publicados), (
                f"{v.rotulo}: a tela mostra {v.texto!r} e isso não está em "
                "numeros()")


def test_resposta_que_cita_o_painel_passa_e_a_que_inventa_nao():
    pn = P.montar(indice=indice_saudavel(), agora=AGORA)
    contexto = "\n".join(v.descrever() for v in pn.antifragilidade.valores)
    indice = pn.antifragilidade.valor_de("Índice de antifragilidade")
    boa = check_grounding(f"O índice está em {indice.texto}.", contexto)
    assert boa.ratio == 1.0
    ruim = check_grounding("O índice está em 0,9987 e cairá 41,3%.", contexto)
    assert ruim.ratio < 1.0
