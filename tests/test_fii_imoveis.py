import core.fii_imoveis as fim


def test_regiao_por_uf():
    assert fim.regiao_por_uf("SP") == "Sudeste"
    assert fim.regiao_por_uf("sp") == "Sudeste"
    assert fim.regiao_por_uf("RS") == "Sul"
    assert fim.regiao_por_uf("BA") == "Nordeste"
    assert fim.regiao_por_uf("GO") == "Centro-Oeste"
    assert fim.regiao_por_uf("AM") == "Norte"
    assert fim.regiao_por_uf("XX") is None
    assert fim.regiao_por_uf(None) is None


def test_split_cidade_uf():
    assert fim.split_cidade_uf("Cajamar/SP") == ("Cajamar", "SP")
    assert fim.split_cidade_uf("Extrema - MG") == ("Extrema", "MG")
    assert fim.split_cidade_uf("Rio de Janeiro, RJ") == ("Rio de Janeiro", "RJ")
    assert fim.split_cidade_uf("Localização desconhecida") == ("Localização desconhecida", None)
    assert fim.split_cidade_uf(None) == (None, None)


def test_num_e_pct():
    assert fim._num("25.000 m²") == 25000.0
    assert fim._num("1.234,56") == 1234.56
    assert fim._num("—") is None
    assert fim._pct("5,0%") == 0.05
    assert fim._pct(None) is None


_HTML = """
<html><body>
  <div id="imoveis-section">
    <article class="card imovel">
      <h4>Galpão Logístico Cajamar</h4>
      <div class="title">Área</div><div class="value">25.000 m²</div>
      <div class="title">Vacância</div><div class="value">5,0%</div>
      <div class="title">Localização</div><div class="value">Cajamar/SP</div>
    </article>
    <article class="card imovel">
      <h4>CD Extrema</h4>
      <div class="title">Área</div><div class="value">12.500 m²</div>
      <div class="title">Localização</div><div class="value">Extrema - MG</div>
    </article>
  </div>
</body></html>
"""


def test_parse_imoveis():
    imoveis = fim.parse_imoveis(_HTML)
    assert len(imoveis) == 2
    nomes = {im["nome_imovel"] for im in imoveis}
    assert nomes == {"Galpão Logístico Cajamar", "CD Extrema"}

    by_nome = {im["nome_imovel"]: im for im in imoveis}
    g = by_nome["Galpão Logístico Cajamar"]
    assert g["area_m2"] == 25000.0
    assert g["vacancia"] == 0.05
    assert g["cidade"] == "Cajamar"
    assert g["uf"] == "SP"
    assert g["regiao"] == "Sudeste"

    e = by_nome["CD Extrema"]
    assert e["uf"] == "MG"
    assert e["regiao"] == "Sudeste"
    assert e["area_m2"] == 12500.0


def test_parse_imoveis_vazio():
    assert fim.parse_imoveis("<html><body><p>nada aqui</p></body></html>") == []


def test_localizar_status_invest():
    # UF no atributo do card
    assert fim.localizar("COVOLAN", "SP") == (None, "SP")
    # UF inferida do nome (sigla ao final)
    assert fim.localizar("BTLG Dutra - RJ", "") == (None, "RJ")
    # nome de estado por extenso ao final
    assert fim.localizar("Galpao Industrial Itambe - Sao Paulo", "") == (None, "SP")
    # "Cidade UF" no último segmento
    assert fim.localizar("Assai - Bangu RJ", "") == ("Bangu", "RJ")
    # cidade-polo via dicionário (Camaçari -> BA)
    assert fim.localizar("BTLG - Camacari", None) == ("Camacari", "BA")
    # nome inteiro é uma cidade-polo; com sufixo numérico
    assert fim.localizar("Uberlandia", None) == ("Uberlandia", "MG")
    assert fim.localizar("Itupeva G300", None)[1] == "SP"
    # "Cidade/UF" embutido
    assert fim.localizar("GPA - Brooklin - Sao Paulo/SP", "") == ("Sao Paulo", "SP")
    # tenant sem geografia -> sem cidade/uf
    assert fim.localizar("VOLKSWAGEM", None) == (None, None)


# Estrutura REAL do Status Invest (cards .property), capturada do site.
_SI_HTML = """
<div class="portfolio-properties">
  <div class="card-list-box" data-navname="portfolio-properties">
   <div class="list">
    <div class="property income card">
      <div class="main-info">
        <strong class="uf mr-1" title="UF"></strong>
        <div class="objective"><small class="label">OBJETIVO</small><strong class="value">RENDA</strong></div>
        <strong class="value" title="Tamanho"><i class="material-icons">flip_to_back</i><span>8.058,00m²</span></strong>
      </div>
      <div class="name"><span>Galpao Industrial Itambe - Sao Paulo</span></div>
      <div class="info"><div><small class="label">VACÂNCIA</small><strong class="value">0,000%</strong></div></div>
    </div>
    <div class="property income card">
      <div class="main-info">
        <strong class="uf mr-1" title="UF"></strong>
        <strong class="value" title="Tamanho"><span>33.383,26m²</span></strong>
      </div>
      <div class="name"><span>Galpao Cravinhos - RJ</span></div>
      <div class="info"><div><small class="label">VACÂNCIA</small><strong class="value">100,000%</strong></div></div>
    </div>
   </div>
  </div>
</div>
"""


def test_parse_imoveis_status_invest():
    imoveis = fim.parse_imoveis(_SI_HTML)
    assert len(imoveis) == 2
    by = {im["nome_imovel"]: im for im in imoveis}
    g = by["Galpao Industrial Itambe - Sao Paulo"]
    assert g["area_m2"] == 8058.0
    assert g["vacancia"] == 0.0
    assert g["uf"] == "SP" and g["regiao"] == "Sudeste"
    c = by["Galpao Cravinhos - RJ"]
    assert c["area_m2"] == 33383.26
    assert c["vacancia"] == 1.0           # 100%
    assert c["uf"] == "RJ" and c["regiao"] == "Sudeste"


def test_vacancia_media_ponderada():
    imoveis = [
        {"area_m2": 100.0, "vacancia": 0.0},
        {"area_m2": 100.0, "vacancia": 1.0},
    ]
    assert fim.vacancia_media(imoveis) == 0.5
    # sem área -> média simples
    assert fim.vacancia_media([{"vacancia": 0.2}, {"vacancia": 0.4}]) == 0.3
    assert fim.vacancia_media([]) is None
