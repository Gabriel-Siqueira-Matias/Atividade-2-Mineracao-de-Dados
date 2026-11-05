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


import json
import pandas as pd
from sqlalchemy import text
from time import perf_counter
from qualidade.assercoes import Suite
from datetime import datetime, timezone
from config import (ARQ_AVALIACOES, ARQ_SAC, eng_dw, eng_mysql, eng_origem)


AGORA = datetime.now(timezone.utc)
LOTE = 50_000          # leia em blocos: a base não cabe confortavelmente na RAM


# ---------------------------------------------------------------------------
# Funções para padronização de texto para imprimir no terminal
# ---------------------------------------------------------------------------
def secao(titulo):
    """Cabeçalho visual para separar blocos da extração."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Mensagem descritiva padronizada."""
    print(f"[INFO] {mensagem}")


def ok(mensagem):
    """Mensagem de sucesso padronizada."""
    print(f"[OK] {mensagem}")


# ---------------------------------------------------------------------------
# Função para gravar na camada staging
# ---------------------------------------------------------------------------
def grava_stg(df, tabela, engine, primeiro_bloco):
    df["_fonte"] = tabela.split("_")[0]
    df["_extraido_em"] = AGORA
    df.to_sql(tabela, engine, schema="stg",
              if_exists="replace" if primeiro_bloco else "append",
              # Inserção em lotes pelo SQLAlchemy/driver (sem SQL MULTI gigante).
              index=False, method=None, chunksize=5_000)


# ---------------------------------------------------------------------------
# Função para extração das tabelas do legado (MySQL)
# ---------------------------------------------------------------------------
def extrai_mysql(qa):
    # Inicia as conexões com os bancos de dados
    eng, dw = eng_mysql(), eng_dw()
    # Lista com o nome das tabelas do legado
    tabelas = ["CLIENTES", "PRODUTOS", "PEDIDOS", "ITENS_PEDIDO"]
    # Executa para cada tabela
    for tabela in tabelas:
        # Inicia o cronômetro da extração da tabela
        inicio_tabela = perf_counter()
        # Conecta com o banco de dados do legado e conta a quantidade de linhas dessa tabela
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()
        # Separa a tabela em blocos, conta a quantidade de linhas e grava no banco de dados stg
        primeiro_bloco = True
        linhas_lidas = 0
        for chunk in pd.read_sql(f"SELECT * FROM {tabela}", eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"legado_{tabela.lower()}"
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False
        # Verifica se a quantidade esperada é igual a lida e imprime o tempo de execução
        qa.igual(f"linhas legado.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] legado/{tabela.lower():<12} | {linhas_lidas:>8} linhas | {perf_counter()-inicio_tabela:.1f}s")


# ---------------------------------------------------------------------------
# Função para extração das tabelas do e-commerce (PostgreSQL)
# ---------------------------------------------------------------------------
def extrai_postgres(qa):
    # Inicia as conexões com os bancos de dados
    eng, dw = eng_origem(), eng_dw()
    # Lista com o nome das tabelas do e-commerce
    tabelas = ["customer", "product", "orders", "order_item", "category"]
    # Executa para cada tabela
    for tabela in tabelas:
        # Inicia o cronômetro da extração da tabela
        inicio_tabela = perf_counter()
        # Conecta com o banco de dados do e-commerce e conta a quantidade de linhas dessa tabela
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM shop.{tabela}")).scalar()
        # Separa a tabela em blocos, conta a quantidade de linhas e grava no banco de dados stg
        primeiro_bloco = True
        linhas_lidas = 0
        for chunk in pd.read_sql(f"SELECT * FROM shop.{tabela}", eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"ecommerce_{tabela}"
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False
        # Verifica se a quantidade esperada é igual a lida e imprime o tempo de execução
        qa.igual(f"linhas shop.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] ecom/{tabela:<14} | {linhas_lidas:>8} linhas | {perf_counter()-inicio_tabela:.1f}s")


# ---------------------------------------------------------------------------
# Extração do arquivo CSV do SAC
# ---------------------------------------------------------------------------
def extrai_sac(qa):
    # Inicia o cronômetro da extração do arquivo
    inicio = perf_counter()
    # Inicia a conexão com o banco de dados
    dw = eng_dw()
    # Guarda os dados do arquivo
    df = pd.read_csv(ARQ_SAC, sep=";", encoding="latin-1", dtype=str)
    # Cria uma coluna nova para guardar as posições originais
    df["_linha_origem"] = df.index + 2
    # Grava no banco de dados stg
    grava_stg(df, "atendimentos_sac", dw, True)
    # Verifica se tem algum dado nulo e imprime o tempo de execução
    qa.sem_nulos("protocolo do SAC", df["protocolo"])
    print(f"[tempo] sac/csv          | {len(df):>8} linhas | {perf_counter()-inicio:.1f}s")


# ---------------------------------------------------------------------------
# Extração do arquivo NDJSON de avaliações
# ---------------------------------------------------------------------------
def extrai_avaliacoes(qa):
    # Inicia o cronômetro da extração do arquivo
    inicio = perf_counter()
    # Inicia a conexão com o banco de dados
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
    print(f"[tempo] avaliacoes/json | {total:>8} linhas | {perf_counter()-inicio:.1f}s")


# ---------------------------------------------------------------------------
# Inicio do codigo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Inicia o cronômetro desta etapa
    inicio_total = perf_counter()
    secao("ETAPA 1 — EXTRAÇÃO")
    info("Carregando as fontes para a camada staging sem alterar o significado dos dados.")
    # Instancia a classe suite (rensponsavel pelas asserções)
    qa = Suite(etapa="01_extracao")
    # Extrae os dados das fontes de origem
    extrai_mysql(qa)
    extrai_postgres(qa)
    extrai_sac(qa)
    extrai_avaliacoes(qa)
    # Inicia o cronômetro do registro das asserções
    inicio_qa = perf_counter()
    # Regista os dados na tabela de asserções e imprime no terminal
    qa.registrar(eng_dw())
    qa.imprimir()
    # Regista o tempo de execução da etapa e do registro das asserções
    print(f"[tempo] qa/registro      | {perf_counter()-inicio_qa:.1f}s")
    ok("Etapa 1 concluída com sucesso.")
    print(f"[tempo] total etapa 1    | {perf_counter()-inicio_total:.1f}s")