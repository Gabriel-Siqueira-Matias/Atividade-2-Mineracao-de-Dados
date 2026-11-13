#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 1 — EXTRAÇÃO DE DADOS (Staging Layer)

Objetivo:
    Carregar as quatro fontes de dados heterogêneas para o esquema `stg` do Data 
    Warehouse (DW) de forma bruta. Nenhum tratamento de conformidade de clientes, 
    normalização de status, conversão de valores ou ajuste de datas deve ser 
    aplicado nesta etapa.

Política de Carga:
    Carga completa (full load). A primeira escrita executa a substituição total 
    (`replace`) de cada tabela de staging, enquanto os blocos seguintes realizam 
    o incremento (`append`).

Comando de Execução:
    docker compose exec etl python 01_extracao.py
"""

import json
from datetime import datetime, timezone
from time import perf_counter
import pandas as pd
from config import ARQ_AVALIACOES, ARQ_SAC, eng_dw, eng_mysql, eng_origem
from qualidade.assercoes import Suite
from sqlalchemy import text

# Carimbo de data/hora universal (UTC) fixado no início da execução para garantir
# rastreabilidade consistente e idêntica em todos os registros inseridos no lote.
AGORA = datetime.now(timezone.utc)

# Tamanho padrão dos lotes (chunksize) para leitura em SQL e escrita de NDJSON,
# otimizando o consumo de memória RAM durante o processamento de grandes volumes.
LOTE = 50_000


# ---------------------------------------------------------------------------
# Funções Auxiliares de Interface e Log no Terminal
# ---------------------------------------------------------------------------
def secao(titulo):
    """Exibe um cabeçalho visual destacado para separar as etapas no terminal."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Exibe uma mensagem informativa padronizada com o prefixo [INFO]."""
    print(f"[INFO] {mensagem}")


def ok(mensagem):
    """Exibe uma mensagem de sucesso padronizada com o prefixo [OK]."""
    print(f"[OK] {mensagem}")


# ---------------------------------------------------------------------------
# Função de Persistência no Staging do DW
# ---------------------------------------------------------------------------
def grava_stg(df, tabela, engine, primeiro_bloco):
    """
    Grava um DataFrame pandas no esquema de staging (`stg`) do Data Warehouse.
    
    Parâmetros:
        df (pd.DataFrame): Dados a serem gravados.
        tabela (str): Nome da tabela de destino no DW.
        engine (Engine): SQLAlchemy engine conectado ao DW.
        primeiro_bloco (bool): Define se recria a tabela (True) ou faz append (False).
    """
    # Identifica a procedência exata dos dados extraindo o prefixo do nome da tabela
    df["_fonte"] = tabela.split("_")[0]
    
    # Atribui o timestamp unificado de extração a todos os registros do lote atual
    df["_extraido_em"] = AGORA

    # Escreve os dados no banco utilizando sub-lotes de 5.000 linhas via driver SQL
    df.to_sql(
        tabela,
        engine,
        schema="stg",
        if_exists="replace" if primeiro_bloco else "append",
        index=False,
        method=None,
        chunksize=5_000,
    )


# ---------------------------------------------------------------------------
# Extração do ERP Legado (MySQL)
# ---------------------------------------------------------------------------
def extrai_mysql(qa):
    """
    Extrai as tabelas transacionais do ERP legado em MySQL e valida a integridade
    da volumetria comparando a contagem de linhas de origem com o staging.
    """
    # Obtém a conexão com o banco MySQL de origem e a conexão com o DW de destino
    eng, dw = eng_mysql(), eng_dw()
    
    # Relação exata das tabelas legadas a serem extraídas do ERP
    tabelas = [
        "CLIENTES",
        "PRODUTOS",
        "PEDIDOS",
        "ITENS_PEDIDO",
    ]

    for tabela in tabelas:
        # Inicia a contagem de tempo dedicada exclusivamente a esta tabela
        inicio_tabela = perf_counter()

        # Consulta rápida na origem para obter a contagem exata de linhas esperadas
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()

        primeiro_bloco = True
        linhas_lidas = 0

        # Lê a tabela do banco relacional de forma paginada/em blocos (LOTE)
        # Como a consulta possui apenas 1 comando SQL e < 90 caracteres, mantemos em linha única
        for chunk in pd.read_sql(text(f"SELECT * FROM {tabela}"), eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"legado_{tabela.lower()}"
            
            # Persiste o bloco atual na tabela de staging correspondente
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False

        # Valida via suíte de qualidade se o total lido corresponde ao esperado na origem
        qa.igual(f"linhas legado.{tabela}", linhas_lidas, esperadas)
        
        # Imprime métrica detalhada de desempenho e contagem de linhas da tabela
        print(f"[tempo] legado/{tabela.lower():<12} | {linhas_lidas:>8} linhas | {perf_counter()-inicio_tabela:.1f}s")


# ---------------------------------------------------------------------------
# Extração do E-commerce (PostgreSQL)
# ---------------------------------------------------------------------------
def extrai_postgres(qa):
    """
    Extrai as tabelas operacionais da plataforma de e-commerce hospedada em PostgreSQL
    e valida a volumetria extraída contra a base de origem.
    """
    # Obtém a conexão com o banco PostgreSQL de origem e a conexão com o DW de destino
    eng, dw = eng_origem(), eng_dw()
    
    # Tabelas mapeadas dentro do esquema 'shop' do e-commerce
    tabelas = [
        "customer",
        "product",
        "orders",
        "order_item",
        "category",
    ]

    for tabela in tabelas:
        inicio_tabela = perf_counter()

        # Executa consulta de contagem na origem para auditoria de volumetria
        with eng.connect() as cx:
            esperadas = cx.execute(text(f"SELECT COUNT(*) FROM shop.{tabela}")).scalar()

        primeiro_bloco = True
        linhas_lidas = 0

        # Efetua a leitura particionada por lotes para mitigar consumo excessivo de memória
        # Consulta com apenas 1 comando SQL e < 90 caracteres mantida em linha única
        for chunk in pd.read_sql(text(f"SELECT * FROM shop.{tabela}"), eng, chunksize=LOTE):
            linhas_lidas += len(chunk)
            nome_destino = f"ecommerce_{tabela}"
            
            # Persiste o bloco extraído no esquema de staging do Data Warehouse
            grava_stg(chunk, nome_destino, dw, primeiro_bloco)
            primeiro_bloco = False

        # Valida a consistência do número de linhas lidas em relação à origem
        qa.igual(f"linhas shop.{tabela}", linhas_lidas, esperadas)
        print(f"[tempo] ecom/{tabela:<14} | {linhas_lidas:>8} linhas | {perf_counter()-inicio_tabela:.1f}s")


# ---------------------------------------------------------------------------
# Extração do Atendimento SAC (Arquivo CSV)
# ---------------------------------------------------------------------------
def extrai_sac(qa):
    """
    Lê o ficheiro CSV contendo os registros de suporte e atendimento do SAC,
    atribui metadados de rastreio de linha e persiste na camada de staging.
    """
    inicio = perf_counter()
    
    # Obtém a conexão com o Data Warehouse de destino para persistir os dados de atendimento do SAC
    dw = eng_dw()

    # Lê o CSV forçando todas as colunas como string (dtype=str) para evitar
    # a perda de zeros à esquerda em códigos, CPFs ou números de protocolo.
    df = pd.read_csv(ARQ_SAC, sep=";", encoding="latin-1", dtype=str)

    # Cria coluna de auditoria indicando a linha física original no arquivo CSV
    # (Adiciona 2: índice 0 do pandas + 1 linha correspondente ao cabeçalho do arquivo)
    df["_linha_origem"] = df.index + 2

    # Grava os dados no staging recriando a tabela de destino
    grava_stg(df, "atendimentos_sac", dw, True)

    # Validação de qualidade: assegura que o campo essencial 'protocolo' não contém nulos
    qa.sem_nulos("protocolo do SAC", df["protocolo"])
    print(f"[tempo] sac/csv             | {len(df):>8} linhas | {perf_counter()-inicio:.1f}s")


# ---------------------------------------------------------------------------
# Extração das Avaliações do Marketplace (Ficheiro NDJSON)
# ---------------------------------------------------------------------------
def extrai_avaliacoes(qa):
    """
    Processa o ficheiro NDJSON (Newline Delimited JSON) de avaliações de produtos,
    realiza o achatamento (flattening) de estruturas JSON aninhadas e carrega no DW.
    """
    inicio = perf_counter()
    
    # Obtém a conexão com o Data Warehouse de destino para persistir as avaliações do marketplace
    dw = eng_dw()
    
    registros = []
    review_ids = []
    primeiro_bloco = True
    total = 0

    # Abre o ficheiro linha por linha para processar fluxos massivos sem esgotar a RAM
    with open(ARQ_AVALIACOES, "r", encoding="utf-8") as f:
        for i, linha in enumerate(f):
            obj = json.loads(linha)

            # Extrai e achata campos estruturados de dicionários aninhados (order e product)
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
                "_linha_origem": i + 1,  # Posição física exata no ficheiro NDJSON
            }
            
            registros.append(linha_stg)
            review_ids.append(linha_stg["review_id"])
            total += 1

            # Assim que o acumulador atinge o limite do lote, descarrega no banco
            if len(registros) >= LOTE:
                grava_stg(pd.DataFrame(registros), "avaliacoes_marketplace", dw, primeiro_bloco)
                primeiro_bloco = False
                registros.clear()

    # Descarrega eventuais registros restantes que ficaram pendentes no último lote incompleto
    if registros:
        grava_stg(pd.DataFrame(registros), "avaliacoes_marketplace", dw, primeiro_bloco)
    
    # Tratamento para ficheiro vazio: garante a criação da tabela com a estrutura de colunas correta
    elif primeiro_bloco:
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
            "_linha_origem",
        ]
        grava_stg(pd.DataFrame(columns=colunas), "avaliacoes_marketplace", dw, True)

    # Validação de integridade: garante que o identificador 'review_id' é estritamente único
    qa.unico("review_id", pd.Series(review_ids, name="review_id", dtype="object"))
    print(f"[tempo] avaliacoes/json     | {total:>8} linhas | {perf_counter()-inicio:.1f}s")


# ---------------------------------------------------------------------------
# Orquestração Principal e Registo da Qualidade (QA)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Inicia o cronômetro para medir a duração total da etapa de extração
    inicio_total = perf_counter()
    
    secao("ETAPA 1 — EXTRAÇÃO")
    info("Carregando as fontes para a camada staging sem alterar o significado dos dados.")

    # Inicializa a suíte de asserções de qualidade específica para esta etapa
    qa = Suite(etapa="01_extracao")

    # Executa sequencialmente a extração de cada uma das quatro fontes de dados
    extrai_mysql(qa)
    extrai_postgres(qa)
    extrai_sac(qa)
    extrai_avaliacoes(qa)

    # Inicia a medição de tempo dedicada ao registro das métricas e asserções de QA
    inicio_qa = perf_counter()
    
    # Persiste os resultados das asserções no banco de dados e imprime o relatório no terminal
    qa.registrar(eng_dw())
    qa.imprimir()

    print(f"[tempo] qa/registro      | {perf_counter()-inicio_qa:.1f}s")
    ok("Etapa 1 concluída com sucesso.")
    print(f"[tempo] total etapa 1    | {perf_counter()-inicio_total:.1f}s")