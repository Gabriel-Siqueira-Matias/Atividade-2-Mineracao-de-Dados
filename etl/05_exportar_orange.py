#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 5 — EXPORTAÇÃO PARA O ORANGE  (pronto, não precisa alterar)

Gera dois CSVs em /saida, prontos para o widget File do Orange:

    abt_recompra_correta.csv
    abt_recompra_vazamento.csv

O cabeçalho sai no formato de três linhas do Orange, que já declara tipo e
papel de cada coluna — assim vocês não precisam reconfigurar o widget a cada
recarga, e o alvo nunca entra como atributo por engano.

    docker compose exec etl python 05_exportar_orange.py
"""

import os

import pandas as pd
from sqlalchemy import text

from config import DIR_SAIDA, eng_dw


def secao(titulo):
    """Cabeçalho visual para a exportação do Orange."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Mensagem descritiva padronizada."""
    print(f"[INFO] {mensagem}")


def ok(mensagem):
    """Mensagem de sucesso padronizada."""
    print(f"[OK] {mensagem}")

ALVO = "alvo_recompra_90d"
IGNORAR = {"cliente_sk", "data_corte"}


def tipo_orange(serie):
    if pd.api.types.is_bool_dtype(serie):
        return "discrete"
    if pd.api.types.is_numeric_dtype(serie):
        return "continuous"
    return "discrete" if serie.nunique(dropna=True) <= 30 else "string"


def exporta(tabela, arquivo, dw):
    try:
        df = pd.read_sql(text(f"SELECT * FROM abt.{tabela}"), dw)
    except Exception as e:                                   # noqa: BLE001
        print(f"[AVISO] abt.{tabela} indisponível: {type(e).__name__}")
        return

    colunas = list(df.columns)
    tipos, papeis = [], []
    for c in colunas:
        if c == ALVO:
            tipos.append("discrete")
            papeis.append("class")
        elif c in IGNORAR:
            tipos.append(tipo_orange(df[c]))
            papeis.append("meta")
        else:
            tipos.append(tipo_orange(df[c]))
            papeis.append("")

    destino = os.path.join(DIR_SAIDA, arquivo)
    with open(destino, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(colunas) + "\n")
        f.write(",".join(tipos) + "\n")
        f.write(",".join(papeis) + "\n")
    df.to_csv(destino, mode="a", header=False, index=False, encoding="utf-8")

    ok(f"{destino} ({len(df)} linhas, {len(colunas)} colunas)")
    info(f"taxa do alvo: {df[ALVO].mean():.3f} de positivos")


if __name__ == "__main__":
    os.makedirs(DIR_SAIDA, exist_ok=True)
    secao("ETAPA 5 — EXPORTAÇÃO PARA O ORANGE")
    info("Gerando os CSVs com as três linhas de cabeçalho exigidas pelo Orange.")
    dw = eng_dw()
    exporta("recompra_90d", "abt_recompra_correta.csv", dw)
    exporta("recompra_90d_vazamento", "abt_recompra_vazamento.csv", dw)
    secao("ORIENTAÇÃO DE USO NO ORANGE")
    print("""
No Orange, monte o mesmo fluxo para os dois arquivos:

    File -> Data Sampler (70/30, estratificado) -> Naive Bayes -> Test & Score
                                               -> Logistic Regression
                                               -> Tree
            Test & Score -> Confusion Matrix
                         -> ROC Analysis

Use o MESMO conjunto de teste e as MESMAS configurações nos dois. A única
diferença entre os fluxos deve ser o arquivo de entrada.
""")
