"""
design/painel_medicao.py — onde o administrador lê a medição das telas.

Só o administrador vê (quem decide é o ``app.py``). Duas peças:

* uma linha no rodapé da barra lateral com o tempo da execução que acabou;
* um expansor no fim da página com a mediana por tela e o histórico da sessão.

Fica no FIM da página de propósito: nada aqui pode entrar acima do conteúdo,
porque o Streamlit devolve as abas à primeira quando o número de elementos
acima delas muda. A lógica (fases, mediana, histórico) mora em
``core.medicao_rota``; aqui só se desenha.
"""
from __future__ import annotations

import streamlit as st

from core.medicao_rota import agregar

_TIPOS = {"abertura": "abertura", "troca": "troca de página", "interacao": "clique na tela"}


def _seg(ms: float) -> str:
    return f"{ms / 1000:.1f} s".replace(".", ",")


def linha_sidebar(linha: dict) -> None:
    banco = _seg(linha["sql_ms"])
    st.sidebar.caption(
        f"⏱️ {_TIPOS.get(linha['tipo'], linha['tipo'])}: {_seg(linha['total_ms'])} "
        f"· banco {banco} em {linha['sql_n']} consulta{'' if linha['sql_n'] == 1 else 's'}"
    )


def painel_medicao(historico: list[dict]) -> None:
    if not historico:
        return
    import pandas as pd

    ultima = historico[-1]
    with st.expander(f"⏱️ Medição de tempo das telas (admin) — última: {_seg(ultima['total_ms'])}"):
        st.caption(
            "Tempo do servidor, por execução do script: preâmbulo (tema, login, menu), "
            "importação da tela e montagem. **Banco** é o tempo dentro de consultas SQL; "
            "**fora do banco** é Python, APIs externas e montagem dos elementos. Não inclui "
            "a ida até o navegador. Consultas servidas pelo cache não aparecem, e consultas "
            "feitas em threads paralelas também não. Troque de página algumas vezes e baixe "
            "as tabelas (ícone no canto de cada uma) para comparar."
        )
        agregado = agregar(historico)
        if agregado:
            st.markdown("**Mediana por tela** (só execuções concluídas)")
            df = pd.DataFrame(agregado)
            df["tipo"] = df["tipo"].map(lambda t: _TIPOS.get(t, t))
            st.dataframe(
                df,
                hide_index=True,
                width="stretch",
                column_config={
                    "rota": "Tela",
                    "tipo": "Tipo",
                    "execucoes": "Execuções",
                    "total_mediana_ms": "Total (ms, mediana)",
                    "total_max_ms": "Total (ms, máx.)",
                    "sql_mediana_ms": "Banco (ms)",
                    "sql_n_mediana": "Consultas",
                    "fora_do_banco_mediana_ms": "Fora do banco (ms)",
                    "import_max_ms": "Importação (ms, máx.)",
                },
            )
        st.markdown("**Execuções desta sessão** (mais recente primeiro)")
        hist = pd.DataFrame(list(reversed(historico)))
        hist["tipo"] = hist["tipo"].map(lambda t: _TIPOS.get(t, t))
        st.dataframe(hist, hide_index=True, width="stretch")
