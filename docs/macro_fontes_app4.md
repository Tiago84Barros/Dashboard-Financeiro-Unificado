# Perfil de fontes macro — APP4 Core

O perfil abaixo prioriza leitura de cenário para carteiras brasileiras e
globais. Todo dado coletado é persistido somente no PostgreSQL Docker local.

| Fonte | Papel no APP4 | Situação inicial |
|---|---|---|
| World Bank | PIB, inflação, dívida, comércio e indicadores estruturais de Brasil, EUA e economias relevantes | ativar após conferir o perfil |
| ECB | câmbio USD/EUR, BRL/EUR, juros e condições monetárias da Europa | ativar após conferir o perfil |
| FRED/ALFRED | juros, inflação, emprego, PIB, curva e spreads dos EUA | selecionada; exige `FRED_API_KEY` |
| IMF | projeções, fiscal, reservas e setor externo | selecionada; aguarda mapeamento SDMX validado |
| OECD | atividade antecedente e confiança | selecionada; aguarda mapeamento SDMX validado |
| BIS | crédito, bancos e risco sistêmico | selecionada; aguarda mapeamento SDMX validado |
| Eurostat | inflação, emprego e PIB europeu detalhado | selecionada; aguarda conector JSON-stat/SDMX específico |
| Trading Economics | calendário; não é fonte-base do histórico | opcional, desativada até credencial/plano |
| APP4 `public.macro` | ponte doméstica para Selic, IPCA, câmbio, PIB, dívida e confiança | leitura na origem; cópia somente no Docker, com procedência legada incompleta |

## Séries iniciais validadas

```env
# World Bank — anual; repetir o código por país é intencional.
MACRO_WORLD_BANK_SERIES=NY.GDP.MKTP.KD.ZG:BRA,NY.GDP.MKTP.KD.ZG:USA,FP.CPI.TOTL.ZG:BRA,FP.CPI.TOTL.ZG:USA,GC.DOD.TOTL.GD.ZS:BRA,GC.DOD.TOTL.GD.ZS:USA

# ECB — dataflow|chave SDMX, mensal.
MACRO_ECB_SERIES=EXR|M.USD.EUR.SP00.A:EA20,EXR|M.BRL.EUR.SP00.A:EA20

# FRED — exige chave válida antes de habilitar.
MACRO_FRED_SERIES=FEDFUNDS:US,CPIAUCSL:US,UNRATE:US,GDPC1:US,DGS10:US,BAMLH0A0HYM2:US
```

Os códigos ECB seguem o formato e exemplos oficiais do dataflow EXR. Os
indicadores World Bank devem ser confirmados no catálogo antes da primeira
carga, e os códigos FRED são coletados somente após a credencial ser fornecida.
Este perfil não cria ordem de compra, venda ou rebalanceamento. A camada de
carteiras só propõe pesos contextuais limitados e explicáveis; a publicação da
carteira continua dependendo de confirmação humana.
