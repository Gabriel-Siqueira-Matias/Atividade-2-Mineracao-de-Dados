#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 1 — EXTRAÇÃO  (esqueleto: vocês completam)

Objetivo: trazer as quatro fontes para o esquema `stg` do DW, SEM transformar
significado. Renomear, converter tipo e decidir o que é nulo vem depois.

Regra da camada stg (seção 2.3 do referencial):
  · nomes de coluna espelham a origem;
  · nenhum valor é descartado, corrigido ou reinterpretado;
  · acrescente apenas metadados de procedência:
        _fonte           de onde veio
        _extraido_em     quando
        _linha_origem    posição na origem, quando fizer sentido

Decisão obrigatória a registrar no RDT antes de codificar:
  carga completa ou incremental? Justifique com base no que as origens oferecem.

    docker compose exec etl python 01_extracao.py
"""

from datetime import datetime, timezone
import pandas as pd
from sqlalchemy import text
import json
from config import (ARQ_AVALIACOES, ARQ_SAC, eng_dw, eng_mysql, eng_origem)
from qualidade.assercoes import Suite
from time import perf_counter


AGORA = datetime.now(timezone.utc)
LOTE = 50_000          # leia em blocos: a base não cabe confortavelmente na RAM


def grava_stg(df, tabela, engine, primeiro_bloco):
    df["_fonte"] = tabela.split("_")[0]
    df["_extraido_em"] = AGORA
    df.to_sql(tabela, engine, schema="stg",
              if_exists="replace" if primeiro_bloco else "append",
              # Inserção em lotes pelo SQLAlchemy/driver (sem SQL MULTI gigante).
              index=False, method=None, chunksize=5_000)


def extrai_mysql(qa):
    eng, dw = eng_mysql(), eng_dw()
    tabelas = ["CLIENTES", "PRODUTOS", "PEDIDOS", "ITENS_PEDIDO"]
    for tabela in tabelas:
        inicio_tabela = perf_counter()
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()
        primeiro_bloco = True
        linhas_lidas = 0
        for chunk in pd.read_sql(f"SELECT * FROM {tabela}", eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"legado_{tabela.lower()}"
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False
        qa.igual(f"linhas legado.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] Extração LEGADO/{tabela}: {perf_counter()-inicio_tabela:.1f}s ({linhas_lidas} linhas)")


def extrai_postgres(qa):
    eng, dw = eng_origem(), eng_dw()
    tabelas = ["customer", "product", "orders", "order_item", "category"]
    for tabela in tabelas:
        inicio_tabela = perf_counter()
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM shop.{tabela}")).scalar()
        primeiro_bloco = True
        linhas_lidas = 0
        for chunk in pd.read_sql(f"SELECT * FROM shop.{tabela}", eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"ecommerce_{tabela}"
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False
        qa.igual(f"linhas shop.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] Extração ECOM/{tabela}: {perf_counter()-inicio_tabela:.1f}s ({linhas_lidas} linhas)")


def extrai_sac(qa):
    inicio = perf_counter()
    dw = eng_dw()
    df = pd.read_csv(ARQ_SAC, sep=";", encoding="latin-1", dtype=str)
    df["_linha_origem"] = df.index + 2
    grava_stg(df, "atendimentos_sac", dw, True)
    qa.sem_nulos("protocolo do SAC", df["protocolo"])
    print(f"[tempo] Extração SAC: {perf_counter()-inicio:.1f}s ({len(df)} linhas)")


def extrai_avaliacoes(qa):
    inicio = perf_counter()
    dw = eng_dw()
    registros = []
    review_ids = []                 # necessário para qa.unico, inclusive entre lotes
    primeiro_bloco = True
    total = 0

    with open(ARQ_AVALIACOES, "r", encoding="utf-8") as f:
        for i, linha in enumerate(f):
            obj = json.loads(linha)
            linha_stg = {
                "review_id": obj.get("review_id"),
                "order_ref": (obj.get("order") or {}).get("ref"),
                "product_sku": (obj.get("product") or {}).get("sku"),
                "submitted_at": obj.get("submitted_at"),
                "rating": str(obj.get("rating")) if obj.get("rating") is not None else None,
                "verified_purchase": obj.get("verified_purchase"),
                "title": obj.get("title"),
                "comment": obj.get("comment"),
                "helpful_votes": obj.get("helpful_votes"),
                "_linha_origem": i + 1
            }
            registros.append(linha_stg)
            review_ids.append(linha_stg["review_id"])
            total += 1

            if len(registros) >= LOTE:
                grava_stg(pd.DataFrame(registros), "avaliacoes_marketplace", dw, primeiro_bloco)
                primeiro_bloco = False
                registros.clear()

    if registros:
        grava_stg(pd.DataFrame(registros), "avaliacoes_marketplace", dw, primeiro_bloco)
    elif primeiro_bloco:
        # Mesmo arquivo vazio gera a tabela staging, como no fluxo original.
        colunas = ["review_id", "order_ref", "product_sku", "submitted_at", "rating",
                   "verified_purchase", "title", "comment", "helpful_votes", "_linha_origem"]
        grava_stg(pd.DataFrame(columns=colunas), "avaliacoes_marketplace", dw, True)

    qa.unico("review_id", pd.Series(review_ids, name="review_id", dtype="object"))
    print(f"[tempo] Extração avaliações: {perf_counter()-inicio:.1f}s ({total} linhas)")


if __name__ == "__main__":
    inicio_total = perf_counter()
    print("\nIniciando Etapa 1 - Extração")
    qa = Suite(etapa="01_extracao")
    extrai_mysql(qa)
    extrai_postgres(qa)
    extrai_sac(qa)
    extrai_avaliacoes(qa)
    inicio_qa = perf_counter()
    qa.registrar(eng_dw())
    qa.imprimir()
    print(f"[tempo] QA e registro: {perf_counter()-inicio_qa:.1f}s")
    print(f"[tempo] ETAPA 1 COMPLETA: {perf_counter()-inicio_total:.1f}s\n")