"""Controle CNPJ: o financeiro da empresa, separado do pessoal.

Tudo que é da empresa mora em tabelas ``pj_*`` próprias, nunca em
``transactions``. O financeiro pessoal tem dezenas de consultas sobre
``transactions`` (mês, orçamento, saldo, IR, chat); uma coluna "é da empresa"
ali obrigaria cada uma delas a lembrar de filtrar, e a que esquecesse somaria
o faturamento da empresa à renda da pessoa. Tabela separada não depende de
ninguém lembrar.

Módulos:

- :mod:`core.pj.ofx` e :mod:`core.pj.extratos` -- leitura de extrato, com
  registro de formatos (OFX hoje; CSV e PDF entram sem mexer no resto);
- :mod:`core.pj.plano_contas` -- categorias da empresa e grupos da DRE;
- :mod:`core.pj.simples` -- alíquota efetiva e DAS estimado do Simples;
- :mod:`core.pj.relatorios` -- resumo do mês, DRE e série mensal (puros);
- :mod:`core.pj.repositorio` -- banco, sempre filtrado pelo dono.
"""
