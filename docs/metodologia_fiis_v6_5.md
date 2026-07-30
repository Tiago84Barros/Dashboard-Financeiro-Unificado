# Metodologia Integrada de FIIs v6.5

## Objetivo

A v6.5 melhora a qualidade do look-through e elimina uma inconsistência entre
a pré-seleção inteira-mista e o otimizador contínuo. Nenhum campo ausente é
convertido em zero e nenhum limite de publicação foi relaxado.

## Mudanças de qualidade

1. Regiões informadas como UF e macrorregião passam a ser consolidadas nas cinco
   macrorregiões do IBGE. Isso impede que `SP` e `Sudeste`, por exemplo, sejam
   tratados como exposições independentes.
2. Em fundos híbridos, dimensões imobiliárias e de crédito só são consideradas
   aplicáveis quando a exposição econômica correspondente é material (20% ou
   mais). Na ausência da composição, mantém-se o comportamento conservador.
3. O otimizador recebe uma preferência pequena (`0,06`) por evidência nominal
   suplementar observada em devedor, indexador e região. Essa preferência não
   substitui score, confiança, renda nem os gates.
4. A pré-seleção passa a respeitar todas as exposições nominais observadas,
   mesmo quando a cobertura total da dimensão ainda é insuficiente para
   declará-la controlada. Assim, ela não entrega ao solver final um subconjunto
   contraditório.
5. O relatório PIT retém uma amostra limitada maior das falhas do otimizador,
   evitando que os períodos iniciais sem universo escondam falhas posteriores.

## Identificação da versão

- metodologia: `6.5.0`;
- modelo integrado: `6.5.0`;
- estratégia: `fii_integrated_robust_optimizer.v6.5`;
- fórmula de score: `br-fii-integrated-income-resilience-6.4.0`.

A fórmula de score por tipo não mudou. A nova versão da metodologia decorre das
regras de qualidade, aplicabilidade e otimização.

## Validação point-in-time local

Execução em 30 de julho de 2026, usando snapshots historicamente observáveis e
o IFIX oficial como benchmark:

- status: aprovado;
- períodos com retorno utilizável: 57;
- viabilidade do otimizador: 100%;
- períodos com violação de restrição: 0;
- cobertura média de correlação: 100%;
- retorno médio mensal líquido: 0,7145%;
- retorno médio mensal do IFIX: 0,6362%;
- excesso médio mensal: 0,0783 ponto percentual;
- drawdown máximo: -10,61%;
- turnover anualizado: 2,41 vezes.

Esses resultados são históricos e não constituem promessa de retorno futuro.

## Limitações que permanecem

- A identidade de locatários não está disponível na fonte estruturada usada.
- A identidade de devedores ainda é incompleta para muitos CRIs. O limiar de
  correspondência de 60% foi preservado; emissor ou cedente não é tratado como
  devedor.
- Gestor, locatário, devedor e indexador podem continuar aparecendo como
  dimensões não resolvidas quando a cobertura da carteira ficar abaixo do gate.
- A publicação remota exige que o snapshot e a validação `6.5.0` sejam
  transferidos juntos; validações de estratégias anteriores não são aceitas.
