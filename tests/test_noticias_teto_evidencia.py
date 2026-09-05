"""Teto de evidência no índice de relevância (A-146).

Por que este arquivo existe
---------------------------
Média ponderada tem dois modos de falha, e o módulo de relevância só se defendia
do primeiro. Contra "quem foi medido em menos dimensões tira nota maior" existe
a cobertura mínima. Contra "componentes altos compensam um déficit
eliminatório" não existia nada -- e essa é a porta por onde entra conteúdo
plantado.

Medido com o motor real, antes da correção:

    fabricada  77,8   (domínio nunca visto, fonte única)
    pandemia   78,3   (Reuters, três fontes)
    guerra     73,1   (Reuters, três fontes)

Dos sete componentes, cinco são declarados pela própria notícia: o tipo do
evento (materialidade e persistência), o ticker citado (relação e exposição) e
o instante da publicação (novidade). São 0,75 do peso nas mãos de quem escreve,
contra 0,25 de confiabilidade e confirmação.

A correção não soma nada: **limita**. A evidência externa vira teto, a nota
bruta continua registrada, e o rebaixamento sai escrito.

O que este arquivo cobra, e a ordem importa
-------------------------------------------
1. O caso do A-146 deixa de empatar com evento real corroborado.
2. Notícia bem corroborada **não** é tocada -- teto que morde todo mundo é
   apenas uma escala menor, e não teria corrigido nada.
3. A faixa é decidida depois do teto. Rebaixar a nota e classificar pela antiga
   devolveria o defeito pela porta dos fundos, já que é a faixa que os portões
   e a ordenação leem.
4. A nota bruta sobrevive: convenção não pode apagar o observado.
5. Evidência não medida não vira teto -- seria punir ausência de medição, que é
   a regra que o módulo inteiro sustenta.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.noticias import fontes, relevancia, taxonomia
from core.noticias.modelos import Entidades, Noticia

AGORA = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _noticia(dominio: str, tipo: str, *, ents: Entidades | None = None,
             horas: float = 1.0, titulo: str = "t") -> Noticia:
    url = f"https://{dominio}/materia"
    return Noticia(
        id_dedup=f"{dominio}-{tipo}", hash_conteudo="h", titulo=titulo,
        url=url, url_canonica=url, fonte=fontes.classificar(url),
        publicado_em=AGORA - timedelta(hours=horas), tipo_evento=tipo,
        entidades=ents or Entidades(tickers=("PETR4",),
                                    empresas=("Petrobras",)),
    )


def _calcular(noticia: Noticia, *, n_fontes: int = 1, primaria: bool = False):
    return relevancia.calcular(noticia, agora=AGORA,
                               n_fontes_independentes=n_fontes,
                               confirmado_por_primaria=primaria)


# ───────────────────────────── o caso do A-146 ───────────────────────────────

def test_noticia_fabricada_nao_empata_com_evento_real_corroborado():
    """O achado, na forma em que foi medido."""
    fabricada = _calcular(_noticia(
        "ultimasnoticiasbrasil.info", "recuperacao_judicial",
        titulo="URGENTE: Petrobras vai a falencia amanha, dizem fontes"))
    pandemia = _calcular(
        _noticia("reuters.com", "pandemia",
                 ents=Entidades(paises=("BR", "US")), horas=3),
        n_fontes=3)

    assert fabricada.nota < pandemia.nota - 10, (
        f"fabricada={fabricada.nota} pandemia={pandemia.nota}: domínio "
        "desconhecido volta a competir com evento real corroborado")
    assert fabricada.faixa == taxonomia.FAIXA_INFORMATIVA


def test_o_autor_da_noticia_nao_alcanca_a_faixa_de_revisao_sozinho():
    """A propriedade que a correção compra, no pior caso possível para ela.

    Tudo o que o autor controla no máximo: o tipo de evento mais material da
    taxonomia, um ticker da carteira, publicação agora mesmo. Se ainda assim a
    faixa de revisão abrir, o teto não está protegendo nada.
    """
    pior = max(taxonomia.TIPOS, key=lambda t: t.materialidade)
    r = _calcular(_noticia("dominio-que-nunca-vimos.example", pior.chave))

    assert r.faixa != taxonomia.FAIXA_REVISAO
    assert r.nota <= r.teto_evidencia


def test_o_teto_nao_toca_noticia_bem_corroborada():
    """Teto que morde todos é só uma escala menor, e não corrige nada."""
    r = _calcular(_noticia("reuters.com", "fato_relevante"), n_fontes=3)

    assert r.nota == r.nota_bruta
    assert r.nota >= taxonomia.LIMITE_REVISAO


def test_fonte_primaria_nao_tem_teto_efetivo():
    r = _calcular(_noticia("cvm.gov.br", "fato_relevante"), primaria=True)

    assert r.teto_evidencia == pytest.approx(100.0)
    assert r.nota == r.nota_bruta


# ─────────────────────── a mecânica, e por que ela é assim ───────────────────

def test_a_faixa_e_decidida_depois_do_teto():
    """Classificar pela nota antiga devolveria o defeito por outro caminho: a
    faixa é o que os portões e a ordenação leem, não a nota.
    """
    r = _calcular(_noticia("blog.desconhecido.example", "fusao_aquisicao"))

    assert r.nota < r.nota_bruta, "o caso escolhido deixou de ser rebaixado"
    assert r.faixa == relevancia._faixa(r.nota)
    assert r.faixa != relevancia._faixa(r.nota_bruta)


def test_a_nota_bruta_sobrevive_ao_rebaixamento():
    """Convenção não pode apagar o observado: o teto é regra deste motor, e o
    número que ele limitou é medição.
    """
    r = _calcular(_noticia("blog.desconhecido.example", "fusao_aquisicao"))

    assert r.nota_bruta > r.nota
    assert any("limitada" in t for t in r.limitacoes)
    escrito = " ".join(r.limitacoes)
    assert f"{r.nota_bruta:.0f}" in escrito and f"{r.nota:.0f}" in escrito


def test_o_rebaixamento_e_persistido_na_trilha():
    """A limitação vai para ``noticias_avaliacoes.limitacoes``. Rebaixamento que
    só existe em memória não é auditável depois do fato.
    """
    from core.noticias import armazenamento, impacto
    from core.noticias.modelos import NoticiaAvaliada

    n = _noticia("blog.desconhecido.example", "fusao_aquisicao")
    rel = _calcular(n)
    avaliada = NoticiaAvaliada(
        noticia=n, relevancia=rel,
        impacto=impacto.estimar(tipo_evento=n.tipo_evento,
                                sentimento=n.sentimento,
                                confiabilidade_fonte=n.confiabilidade,
                                estado_verificacao=taxonomia.VERIF_NAO_VERIFICADA,
                                cobertura_relevancia=rel.cobertura))

    linha = armazenamento.linha_avaliacao(avaliada)

    assert "limitada" in linha["limitacoes"]
    assert linha["nota"] == rel.nota


def test_evidencia_nao_medida_nao_vira_teto():
    """Fonte ausente é ausência de medição, não fonte ruim.

    Zerar aqui puniria quem não foi medido -- a regra que o módulo inteiro
    sustenta, e que o piso de confiabilidade das *fontes* já quebra de
    propósito num lugar só (fonte desconhecida ganha 0,20, não ``None``).
    """
    n = _noticia("reuters.com", "fato_relevante")
    sem_fonte = Noticia(**{**n.__dict__, "fonte": None})

    r = relevancia.calcular(
        sem_fonte, agora=AGORA,
        pesos=relevancia.Pesos(materialidade=0.35, relacao_ativo=0.30,
                               confiabilidade=0.15, novidade=0.10,
                               confirmacao=0.0, persistencia=0.05,
                               exposicao=0.05))

    assert r.teto_evidencia is None
    assert r.nota == r.nota_bruta


def test_o_teto_cruza_a_faixa_de_revisao_exatamente_na_ancora_declarada():
    """A reta tem duas âncoras defensáveis, e uma delas é normativa: evidência
    de 2/3 é o mínimo para 80. Se alguém mexer em ``TETO_BASE`` sem repensar a
    faixa, isto avisa.
    """
    assert relevancia.teto_de_evidencia(1.0) == pytest.approx(100.0)
    assert relevancia.teto_de_evidencia(2.0 / 3.0) == pytest.approx(
        taxonomia.LIMITE_REVISAO, abs=0.05)
    assert relevancia.teto_de_evidencia(0.0) < taxonomia.LIMITE_OBSERVACAO
    assert relevancia.teto_de_evidencia(None) is None


def test_confiabilidade_de_classe_desconhecida_nao_alcanca_a_ancora():
    """O teto só protege se o piso da classe desconhecida ficar abaixo dela --
    e isso liga dois módulos que ninguém releria junto.
    """
    piso = fontes.CONFIABILIDADE_POR_CLASSE[fontes.CLASSE_DESCONHECIDA]
    pesos = relevancia.PESOS_PADRAO.como_dicionario()
    # Melhor caso do atacante: confirmação máxima que ele pode alegar sozinho.
    evidencia = (pesos[relevancia.CONFIABILIDADE] * piso
                 + pesos[relevancia.CONFIRMACAO] * 1.0) / (
        pesos[relevancia.CONFIABILIDADE] + pesos[relevancia.CONFIRMACAO])

    assert relevancia.teto_de_evidencia(evidencia) < taxonomia.LIMITE_REVISAO


# ─────────────────────────── a safra da versão nova ──────────────────────────

def test_a_versao_da_metodologia_subiu_com_a_mudanca_de_escala():
    """Duas réguas com o mesmo carimbo seriam indistinguíveis no acervo."""
    from core.noticias.armazenamento import VERSAO_METODOLOGIA

    assert VERSAO_METODOLOGIA != "1.0.0"


def test_existe_caminho_para_reconstruir_a_safra_sem_recoletar():
    """Subir a versão sem safra correspondente esvazia a tela em silêncio -- é
    a memória ``versao-de-metodologia-sem-safra``. Aqui o esvaziamento é
    visível, mas visível não é resolvido.
    """
    from scripts import reavaliar_acervo

    assert callable(reavaliar_acervo.reavaliar)
    assert callable(reavaliar_acervo.main)


def test_a_reavaliacao_nao_reenvelhece_a_noticia(monkeypatch):
    """O defeito que a primeira execução deste script mostrou.

    Reavaliar com ``agora`` derruba a novidade de toda matéria antiga, e o diff
    atribui à correção uma queda que é só a passagem do tempo. Medido: as 48
    linhas do acervo "mudavam", 48 delas sem o teto ter encostado.
    """
    from scripts import reavaliar_acervo

    coletado = AGORA - timedelta(days=30)
    linha = {
        "id_dedup": "x", "hash_conteudo": "h", "simhash": None,
        "titulo": "t", "resumo": None, "url": "https://reuters.com/a",
        "url_canonica": "https://reuters.com/a", "dominio": "reuters.com",
        "veiculo": "Reuters", "autor": None,
        "publicado_em": coletado, "coletado_em": coletado,
        "provedor": "p", "idioma": "pt", "pais": "BR",
        "entidades": "{\"tickers\": [\"PETR4\"]}",
        "tipo_evento": "fato_relevante", "evento_id": None,
        "sentimento_api": None, "sentimento_app4": None,
        "rotulo_sentimento": None, "metodo_sentimento": None,
        "n_fontes_independentes": 3, "confirmado_por_primaria": False,
        "estado_verificacao": taxonomia.VERIF_INDEPENDENTE, "nota_antiga": None,
    }

    class _Conn:
        def execute(self, sql, params=None):
            class _R:
                def mappings(_self):
                    texto = str(sql)
                    return [] if "MIN(publicado_em)" in texto else [linha]
            return _R()

    monkeypatch.setattr("core.noticias.perfil_carteira.carregar",
                        lambda **_k: (__import__(
                            "core.noticias.perfil_carteira", fromlist=["x"]
                        ).PERFIL_VAZIO, ()))
    monkeypatch.setattr("core.noticias.bases_historicas.carregar",
                        lambda *_a, **_k: ({}, ()))

    avaliadas, _, _ = reavaliar_acervo.reavaliar(_Conn(), de="1.0.0")

    novidade = avaliadas[0].relevancia.componentes[relevancia.NOVIDADE]
    assert novidade == pytest.approx(1.0), (
        "a novidade foi medida contra o relógio de hoje, e não contra o "
        "instante da coleta")
