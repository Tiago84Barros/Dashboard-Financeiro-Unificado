"""Calibração quantitativa do Motor Conjuntural -- medir antes de acreditar.

A instrução desta entrega abre com uma frase que é o resumo do pacote: *"Não
trate pesos inicialmente sugeridos como verdades definitivas."* Os pesos de
:mod:`core.noticias.relevancia`, as duas notas por tipo de evento em
:mod:`core.noticias.taxonomia` e o limiar de "movimento relevante" foram
escritos com o motivo ao lado -- e nenhum deles foi medido contra história
nenhuma. Este pacote é onde eles passam a ser medidos.

O que este pacote NÃO é
-----------------------
Não é :mod:`core.memoria_mercado.calibracao`. Aquele módulo faz o *walk-forward*
dos pesos de similaridade: dado um conjunto de eventos já medidos, ele pergunta
se o Fator de Similaridade melhora a faixa publicada. Este pacote é uma camada
acima e responde outra pergunta: **de onde vêm os eventos**, o que conta como
reação, e se o motor inteiro -- classificação, relevância, probabilidade --
acerta o suficiente para sair do laboratório.

Camadas
-------
``limiar``    o que é "movimento relevante", por classe de ativo e por
              volatilidade do próprio ativo -- em vez de um número absoluto
              único para tudo
``catalogo``  de onde saem os eventos históricos ponto-no-tempo, e quais tipos
              da taxonomia **não têm fonte** (que é a maior parte deles)
``metricas``  precisão, recall, F1, matriz de confusão, erro de magnitude,
              cobertura de faixa, erro de direção e calibração de probabilidade
``pesos``     conjuntos de pesos versionados, com rollback e com os portões que
              impedem a promoção de um conjunto pior

Duas regras que valem no pacote inteiro
---------------------------------------
**Ponto-no-tempo, sempre.** Toda estatística usada para decidir sobre o evento
de uma data usa apenas o que existia antes daquela data. Medir volatilidade com
a série inteira e depois "testar" nela é medir o quanto a série descreve a si
mesma. Ver ``memoria: ordenacao-nao-e-vantagem``.

**Não medido não é zero.** Tipo de evento sem fonte histórica sai
``calibrado=False`` e mantém o prior declarado. Inventar uma amostra para
preencher a tabela é o defeito que este repositório já pagou várias vezes --
``memoria: declaracao-de-rigor-nao-verificada``.
"""
from __future__ import annotations

#: Versão da metodologia de calibração. Entra na chave de qualquer safra
#: gravada, pela mesma disciplina de ``core.memoria_mercado``: subir a versão
#: sem reconstruir a safra esvazia o painel em silêncio
#: (``memoria: versao-de-metodologia-sem-safra``).
CALIBRACAO_VERSAO = "1.0.0"

__all__ = ["CALIBRACAO_VERSAO"]
