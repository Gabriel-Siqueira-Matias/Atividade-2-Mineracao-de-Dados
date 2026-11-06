#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 1 — EXTRAÇÃO

Objetivo: carregar as quatro fontes no esquema `stg` do DW, sem aplicar as
regras de conformação de clientes, status, valores e datas das etapas seguintes.

Política de carga: completa; a primeira escrita recria cada tabela de staging.

Comando: docker compose exec etl python 01_extracao.py
"""


import json
import pandas as pd
from sqlalchemy import text
from time import perf_counter
from qualidade.assercoes import Suite
from datetime import datetime, timezone
from config import (ARQ_AVALIACOES, ARQ_SAC, eng_dw, eng_mysql, eng_origem)


AGORA = datetime.now(timezone.utc)
# tamanho dos blocos de leitura SQL e de escrita do NDJSON
LOTE = 50_000


# ---------------------------------------------------------------------------
# Funções auxiliares para formatar as mensagens exibidas no terminal
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
# Função para gravar os lotes no esquema staging do DW
# ---------------------------------------------------------------------------
def grava_stg(df, tabela, engine, primeiro_bloco):
    # Identifica a procedência pelo prefixo do nome da tabela de destino.
    df["_fonte"] = tabela.split("_")[0]
    # Usa o mesmo instante de extração para os registros desta execução.
    df["_extraido_em"] = AGORA
    # Recria a tabela no primeiro bloco e acrescenta os seguintes; escreve em lotes menores.
    df.to_sql(tabela, engine, schema="stg",
              if_exists="replace" if primeiro_bloco else "append",
              # Inserção pelo SQLAlchemy/driver em lotes de até 5.000 linhas.
              index=False, method=None, chunksize=5_000)


# ---------------------------------------------------------------------------
# Função para extrair as tabelas do ERP legado (MySQL)
# ---------------------------------------------------------------------------
def extrai_mysql(qa):
    # Obtém os engines SQLAlchemy da origem MySQL e do DW de destino.
    eng, dw = eng_mysql(), eng_dw()
    # Define as tabelas de origem que serão extraídas.
    tabelas = ["CLIENTES", "PRODUTOS", "PEDIDOS", "ITENS_PEDIDO"]
    # Percorre cada tabela do ERP legado.
    for tabela in tabelas:
        # Inicia o cronômetro da extração da tabela
        inicio_tabela = perf_counter()
        # Consulta na origem a quantidade esperada de linhas.
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()
        # Lê em blocos, conta as linhas extraídas e grava cada bloco no staging.
        primeiro_bloco = True
        linhas_lidas = 0
        for chunk in pd.read_sql(f"SELECT * FROM {tabela}", eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"legado_{tabela.lower()}"
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False
        # Compara a contagem extraída com a origem e informa o tempo gasto.
        qa.igual(f"linhas legado.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] legado/{tabela.lower():<12} | {linhas_lidas:>8} linhas | {perf_counter()-inicio_tabela:.1f}s")


# ---------------------------------------------------------------------------
# Função para extrair as tabelas do e-commerce (PostgreSQL)
# ---------------------------------------------------------------------------
def extrai_postgres(qa):
    # Obtém os engines SQLAlchemy da origem PostgreSQL e do DW de destino.
    eng, dw = eng_origem(), eng_dw()
    # Define as tabelas de origem que serão extraídas.
    tabelas = ["customer", "product", "orders", "order_item", "category"]
    # Percorre cada tabela do e-commerce.
    for tabela in tabelas:
        # Inicia o cronômetro da extração da tabela
        inicio_tabela = perf_counter()
        # Consulta na origem a quantidade esperada de linhas.
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM shop.{tabela}")).scalar()
        # Lê em blocos, conta as linhas extraídas e grava cada bloco no staging.
        primeiro_bloco = True
        linhas_lidas = 0
        for chunk in pd.read_sql(f"SELECT * FROM shop.{tabela}", eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"ecommerce_{tabela}"
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False
        # Compara a contagem extraída com a origem e informa o tempo gasto.
        qa.igual(f"linhas shop.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] ecom/{tabela:<14} | {linhas_lidas:>8} linhas | {perf_counter()-inicio_tabela:.1f}s")


# ---------------------------------------------------------------------------
# Função para extrair o arquivo CSV de atendimentos do SAC
# ---------------------------------------------------------------------------
def extrai_sac(qa):
    # Inicia o cronômetro da leitura e gravação do arquivo.
    inicio = perf_counter()
    # Obtém o engine SQLAlchemy do DW de destino.
    dw = eng_dw()
    # Lê o CSV com separador ';', codificação latin-1 e colunas textuais.
    df = pd.read_csv(ARQ_SAC, sep=";", encoding="latin-1", dtype=str)
    # Registra o número da linha no arquivo original, considerando o cabeçalho.
    df["_linha_origem"] = df.index + 2
    # Grava os dados do SAC no staging, recriando a tabela de destino.
    grava_stg(df, "atendimentos_sac", dw, True)
    # Verifica nulos especificamente no campo protocolo e informa o tempo gasto.
    qa.sem_nulos("protocolo do SAC", df["protocolo"])
    print(f"[tempo] sac/csv             | {len(df):>8} linhas | {perf_counter()-inicio:.1f}s")


# ---------------------------------------------------------------------------
# Função para extrair o arquivo NDJSON de avaliações do marketplace
# ---------------------------------------------------------------------------
def extrai_avaliacoes(qa):
    # Inicia o cronômetro da leitura e gravação do arquivo.
    inicio = perf_counter()
    # Obtém o engine SQLAlchemy do DW de destino.
    dw = eng_dw()
    # Acumula os registros do bloco atual e os IDs de todas as avaliações para o QA.
    registros = []
    review_ids = []
    # Percorre cada linha do arquivo NDJSON para gravar os registros em blocos.
    primeiro_bloco = True
    total = 0
    with open(ARQ_AVALIACOES, "r", encoding="utf-8") as f:
        for i, linha in enumerate(f):
            # Converte o objeto JSON da linha em um dicionário Python.
            obj = json.loads(linha)
            # Seleciona campos e achata os objetos aninhados order e product.
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
                # Preserva a posição original no NDJSON, que não possui cabeçalho.
                "_linha_origem": i + 1
            }
            # Acumula a avaliação, guarda seu ID para o QA e atualiza a contagem.
            registros.append(linha_stg)
            review_ids.append(linha_stg["review_id"])
            total += 1
            # Envia ao staging o bloco assim que atingir o limite configurado.
            if len(registros) >= LOTE:
                # Grava o bloco no staging e limpa só os registros do bloco atual.
                grava_stg(pd.DataFrame(registros), "avaliacoes_marketplace", dw, primeiro_bloco)
                primeiro_bloco = False
                registros.clear()
    # Ao fim da leitura, verifica se restou um último bloco incompleto.
    if registros:
        # Grava os registros restantes no staging.
        grava_stg(pd.DataFrame(registros), "avaliacoes_marketplace", dw, primeiro_bloco)
    # Se nenhum bloco foi escrito, o arquivo não continha registros.
    elif primeiro_bloco:
        # Cria a tabela vazia com as colunas esperadas, sem linhas de dados.
        colunas = [
            "review_id",
            "order_ref",
            "product_sku",
            "submitted_at",
            "rating",
            "verified_purchase",
            "title",
            "comment",
            "helpful_votes",
            "_linha_origem"
        ]
        grava_stg(pd.DataFrame(columns=colunas), "avaliacoes_marketplace", dw, True)
    # Valida a unicidade de review_id em todo o arquivo e informa o tempo gasto.
    qa.unico("review_id", pd.Series(review_ids, name="review_id", dtype="object"))
    print(f"[tempo] avaliacoes/json     | {total:>8} linhas | {perf_counter()-inicio:.1f}s")


# ---------------------------------------------------------------------------
# Ponto de entrada da etapa de extração
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Inicia o cronômetro desta etapa
    inicio_total = perf_counter()
    secao("ETAPA 1 — EXTRAÇÃO")
    info("Carregando as fontes para a camada staging sem alterar o significado dos dados.")
    # Cria a suíte de asserções de qualidade da etapa de extração.
    qa = Suite(etapa="01_extracao")
    # Executa a extração das quatro fontes de dados.
    extrai_mysql(qa)
    extrai_postgres(qa)
    extrai_sac(qa)
    extrai_avaliacoes(qa)
    # Marca o início da etapa de registro e apresentação das asserções.
    inicio_qa = perf_counter()
    # Registra os resultados do QA no DW e imprime o resumo no terminal.
    qa.registrar(eng_dw())
    qa.imprimir()
    # Informa o tempo do QA e, ao final, o tempo total de extração.
    print(f"[tempo] qa/registro      | {perf_counter()-inicio_qa:.1f}s")
    ok("Etapa 1 concluída com sucesso.")
    print(f"[tempo] total etapa 1    | {perf_counter()-inicio_total:.1f}s")