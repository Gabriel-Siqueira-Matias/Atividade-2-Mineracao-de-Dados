#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 4 — TABELA-BASE DE ANÁLISE (ABT) E EXPERIMENTO DE VAZAMENTO (Analytics Layer)

Objetivo:
    Construir a Tabela-Base de Análise (ABT) para prever se um cliente com histórico
    de pedidos até a data de corte (30/06/2025) realizará uma nova compra válida
    (não cancelada) na janela futura de 90 dias (entre 01/07/2025 e 28/09/2025).

Política de Construção e Variantes do Experimento:
  · Ambas as versões compartilham exatamente a mesma população elegível e o mesmo rótulo alvo;
  · Modo --correta (RDT-14): calcula 15 atributos preditivos utilizando estritamente eventos
    ocorridos até a data de corte, evitando qualquer contaminação temporal;
  · Modo --vazamento (RDT-15): introduz deliberadamente vazamento do futuro (data leakage) ao
    calcular recência e frequência sem filtro de data de corte e ao normalizar o valor monetário
    via z-score com média e desvio padrão globais antes da divisão entre treino e teste.

Comandos de Execução:
    docker compose exec etl python 04_abt.py --correta
    docker compose exec etl python 04_abt.py --vazamento
"""

import argparse
import pandas as pd
from sqlalchemy import text
from time import perf_counter
from qualidade.assercoes import Suite
from config import DATA_CORTE, DATA_FIM_ALVO, eng_dw


# ---------------------------------------------------------------------------
# Bloco: Funções Auxiliares de Interface e Log no Terminal
# ---------------------------------------------------------------------------
def secao(titulo):
    """Exibe um cabeçalho visual destacado para separar as fases de construção da ABT no terminal."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Exibe uma mensagem descritiva informativa padronizada com o prefixo [INFO]."""
    print(f"[INFO] {mensagem}")


def ok(mensagem):
    """Exibe uma mensagem de sucesso padronizada com o prefixo [OK]."""
    print(f"[OK] {mensagem}")


# ---------------------------------------------------------------------------
# Bloco: Definição SQL da População Elegível e da Variável Alvo (Recompra em 90 dias)
# ---------------------------------------------------------------------------
# A consulta ao período futuro (pós-corte) é estritamente permitida apenas para construir
# o rótulo supervisionado (alvo_recompra_90d), selecionando na CTE 'base' os clientes com
# histórico até a data de corte e verificando compras não canceladas na janela alvo.
SQL_ALVO = f"""
WITH base AS (
    SELECT DISTINCT c.cliente_sk
    FROM dw.dim_cliente c
    JOIN dw.fato_pedido p ON p.cliente_sk = c.cliente_sk
    WHERE p.data_pedido_sk <= DATE '{DATA_CORTE}'
),
compras_na_janela AS (
    SELECT DISTINCT p.cliente_sk
    FROM dw.fato_pedido p
    JOIN dw.dim_status s ON s.status_sk = p.status_sk
    WHERE p.data_pedido_sk >  DATE '{DATA_CORTE}'
    AND p.data_pedido_sk <= DATE '{DATA_FIM_ALVO}'
    AND s.status_conformado <> 'CANCELADO'   -- ajuste ao SEU mapeamento
)
SELECT b.cliente_sk,
    DATE '{DATA_CORTE}' AS data_corte,
    CASE WHEN j.cliente_sk IS NOT NULL THEN 1 ELSE 0 END AS alvo_recompra_90d
FROM base b
LEFT JOIN compras_na_janela j ON j.cliente_sk = b.cliente_sk
"""


# ---------------------------------------------------------------------------
# Bloco: Definição SQL dos 15 Atributos Históricos da ABT Correta (RDT-14)
# ---------------------------------------------------------------------------
# Extrai e agrega os 15 indicadores analíticos no grão de um registro por cliente,
# restringindo rigorosamente pedidos, entregas, atendimentos do SAC e avaliações à
# data de corte (DATA_CORTE). Clientes com menos de 2 compras válidas mantêm NULL
# em métricas de dispersão/intervalo (intervalo_medio_compras e desvio_valor_pedidos).
SQL_ATRIBUTOS_CORRETOS = f"""
WITH pedidos_historicos AS (
    SELECT p.cliente_sk, p.pedido_sk, p.data_pedido_sk, p.data_entrega_sk,
        p.valor_total, p.dias_entrega, s.status_conformado
    FROM dw.fato_pedido p
    JOIN dw.dim_status s ON s.status_sk = p.status_sk
    WHERE p.data_pedido_sk <= DATE '{DATA_CORTE}'
),
resumo_pedidos AS (
    SELECT cliente_sk,
        (DATE '{DATA_CORTE}' - MAX(data_pedido_sk) FILTER (
            WHERE status_conformado <> 'CANCELADO'))::integer AS recencia_dias,
        COUNT(*) FILTER (WHERE status_conformado <> 'CANCELADO')::integer
            AS frequencia_pedidos,
        COALESCE(SUM(valor_total) FILTER (
            WHERE status_conformado <> 'CANCELADO'), 0)::double precision
            AS valor_monetario,
        (AVG(valor_total) FILTER (
            WHERE status_conformado <> 'CANCELADO'))::double precision
            AS ticket_medio,
        (AVG(dias_entrega) FILTER (
            WHERE status_conformado = 'CONCLUIDO'
                AND data_entrega_sk <= DATE '{DATA_CORTE}'))::double precision
            AS prazo_medio_entrega,
        (COUNT(*) FILTER (WHERE status_conformado = 'CANCELADO'))::double precision
            / NULLIF(COUNT(*), 0) AS taxa_cancelamento,
        (DATE '{DATA_CORTE}' - MIN(data_pedido_sk))::integer AS tempo_como_cliente,
        -- 11: compras válidas realizadas nos 90 dias ANTERIORES ao corte (inclusive).
        COUNT(*) FILTER (
            WHERE status_conformado <> 'CANCELADO'
                AND data_pedido_sk >= DATE '{DATA_CORTE}' - 89
        )::integer AS pedidos_ultimos_90d,
        -- 12: média dos intervalos consecutivos; as diferenças somam
        -- (última compra - primeira compra). Com menos de 2: NULL.
        ((MAX(data_pedido_sk) FILTER (WHERE status_conformado <> 'CANCELADO')
            - MIN(data_pedido_sk) FILTER (WHERE status_conformado <> 'CANCELADO'))
            ::double precision /
            NULLIF(COUNT(*) FILTER (WHERE status_conformado <> 'CANCELADO') - 1, 0)
        ) AS intervalo_medio_compras,
        -- 14: desvio AMOSTRAL do total de pedidos válidos; menos de 2: NULL.
        (STDDEV_SAMP(valor_total) FILTER (
            WHERE status_conformado <> 'CANCELADO'))::double precision
            AS desvio_valor_pedidos
    FROM pedidos_historicos
    GROUP BY cliente_sk
),
categorias AS (
    SELECT h.cliente_sk,
        COUNT(DISTINCT pr.categoria_nivel1)::integer AS categorias_distintas,
        -- 13: itens vinculados dos pedidos válidos; órfãos não estão no DW.
        SUM(i.quantidade)::bigint AS qtd_total_itens
    FROM pedidos_historicos h
    JOIN dw.fato_item_pedido i ON i.pedido_sk = h.pedido_sk
    JOIN dw.dim_produto pr ON pr.produto_sk = i.produto_sk
    WHERE h.status_conformado <> 'CANCELADO'
    GROUP BY h.cliente_sk
),
atendimentos AS (
    SELECT a.cliente_sk, COUNT(*)::integer AS qtd_atendimentos_sac,
        -- 15: considera todos os atendimentos, não apenas reclamações.
        (DATE '{DATA_CORTE}' - MAX(a.data_abertura_sk))::integer
            AS dias_desde_ultimo_sac
    FROM dw.fato_atendimento a
    WHERE a.data_abertura_sk <= DATE '{DATA_CORTE}'
    AND a.cliente_sk IS NOT NULL
    GROUP BY a.cliente_sk
),
avaliacoes AS (
    SELECT p.cliente_sk,
        AVG(a.nota)::double precision AS nota_media_avaliacoes
    FROM dw.fato_avaliacao a
    JOIN dw.fato_pedido p ON p.pedido_sk = a.pedido_sk
    WHERE a.data_sk <= DATE '{DATA_CORTE}'
    AND p.data_pedido_sk <= DATE '{DATA_CORTE}'
    GROUP BY p.cliente_sk
)
SELECT r.cliente_sk,
    r.recencia_dias, r.frequencia_pedidos, r.valor_monetario,
    r.ticket_medio, r.prazo_medio_entrega, r.taxa_cancelamento,
    COALESCE(c.categorias_distintas, 0) AS categorias_distintas,
    COALESCE(s.qtd_atendimentos_sac, 0) AS qtd_atendimentos_sac,
    a.nota_media_avaliacoes, r.tempo_como_cliente,
    r.pedidos_ultimos_90d, r.intervalo_medio_compras,
    COALESCE(c.qtd_total_itens, 0)::bigint AS qtd_total_itens,
    r.desvio_valor_pedidos, s.dias_desde_ultimo_sac
FROM resumo_pedidos r
LEFT JOIN categorias c ON c.cliente_sk = r.cliente_sk
LEFT JOIN atendimentos s ON s.cliente_sk = r.cliente_sk
LEFT JOIN avaliacoes a ON a.cliente_sk = r.cliente_sk
"""


# ---------------------------------------------------------------------------
# Bloco: Verificação de Integridade Temporal por Amostragem Determinística
# ---------------------------------------------------------------------------
def verifica_amostra_corte(dw, abt, qa):
    """
    Audita a recência e a frequência de uma amostra determinística de até 100 clientes
    da ABT correta contra uma consulta independente executada diretamente na fato de pedidos,
    registrando na suíte de QA se houve qualquer vazamento de compras posteriores ao corte.
    """
    from sqlalchemy import bindparam

    # Ordena o DataFrame da ABT pela chave substituta do cliente e seleciona até 100 registros espaçados
    ordenados = abt.sort_values('cliente_sk')
    passo = max(1, len(ordenados) // 100)
    amostra = ordenados.iloc[::passo].head(100)

    # Caso a amostra esteja vazia, registra falha imediata na asserção de validação temporal
    if amostra.empty:
        qa.igual('amostra temporal sem divergências', 1, 0)
        return

    # Extrai a lista de chaves de clientes amostrada para filtrar a consulta de auditoria no banco
    ids = [int(v) for v in amostra['cliente_sk']]

    # Define a consulta SQL independente restrita a DATA_CORTE com expansão dinâmica de parâmetros IN
    stmt = text(f"""
        SELECT p.cliente_sk,
            (DATE '{DATA_CORTE}' - MAX(p.data_pedido_sk) FILTER (
                WHERE s.status_conformado <> 'CANCELADO'))::integer AS recencia_dias,
            COUNT(*) FILTER (WHERE s.status_conformado <> 'CANCELADO')::integer
                AS frequencia_pedidos
        FROM dw.fato_pedido p
        JOIN dw.dim_status s ON s.status_sk = p.status_sk
        WHERE p.data_pedido_sk <= DATE '{DATA_CORTE}'
        AND p.cliente_sk IN :ids
        GROUP BY p.cliente_sk
    """).bindparams(bindparam('ids', expanding=True))

    # Executa a consulta de auditoria no Data Warehouse passando o dicionário de IDs amostrados
    esperado = pd.read_sql(stmt, dw, params={
        'ids': ids,
    })

    # Cruza os indicadores da ABT com os valores recalculados na origem garantindo relação 1:1
    comparacao = amostra[[
        'cliente_sk',
        'recencia_dias',
        'frequencia_pedidos',
    ]].merge(esperado, on='cliente_sk', how='left', suffixes=('_abt', '_origem'), validate='one_to_one')

    # Converte as colunas comparadas para o tipo inteiro anulável do pandas (Int64)
    rec_abt = comparacao['recencia_dias_abt'].astype('Int64')
    rec_ref = comparacao['recencia_dias_origem'].astype('Int64')
    freq_abt = comparacao['frequencia_pedidos_abt'].astype('Int64')
    freq_ref = comparacao['frequencia_pedidos_origem'].astype('Int64')

    # Contabiliza divergências tratando pares simultaneamente nulos em recência como equivalentes
    divergencias = int((~(rec_abt.eq(rec_ref).fillna(rec_abt.isna() & rec_ref.isna()) & freq_abt.eq(freq_ref).fillna(False))).sum())

    # Registra na suíte de qualidade a asserção exigindo zero divergências temporais na amostra
    qa.igual(
        'amostra temporal sem divergências',
        divergencias,
        0,
        'Recência e frequência devem ignorar pedidos posteriores a 30/06/2025',
    )


# ---------------------------------------------------------------------------
# Bloco: Extração dos Atributos Preditivos da ABT Correta (Sem Vazamento)
# ---------------------------------------------------------------------------
def monta_atributos_corretos(dw):
    """
    Executa a consulta SQL_ATRIBUTOS_CORRETOS no Data Warehouse para retornar os
    15 atributos preditivos calculados exclusivamente com dados até a data de corte.
    """
    # Executa a leitura SQL no Data Warehouse e retorna o DataFrame com as 15 features históricas
    return pd.read_sql(text(SQL_ATRIBUTOS_CORRETOS), dw)


# ---------------------------------------------------------------------------
# Bloco: Construção dos Atributos Contaminados para o Experimento de Vazamento (RDT-15)
# ---------------------------------------------------------------------------
def monta_atributos_com_vazamento(dw):
    """
    Constrói a versão experimental contaminada da ABT introduzindo dois vazamentos deliberados:
    1. Recalcula recência e frequência utilizando toda a tabela fato_pedido sem limite de data;
    2. Aplica padronização z-score global sobre valor_monetario usando média e desvio de toda a população.
    """
    # Obtém inicialmente a base histórica de atributos corretos para preservar população e demais colunas
    atributos = monta_atributos_corretos(dw)
    colunas_originais = list(atributos.columns)

    # Executa consulta no DW sem filtro de data de corte, incorporando indevidamente pedidos futuros
    dados_futuros = pd.read_sql(text(f"""
        SELECT p.cliente_sk,
            (DATE '{DATA_CORTE}' - MAX(p.data_pedido_sk) FILTER (
                WHERE s.status_conformado <> 'CANCELADO'))::integer AS recencia_dias,
            COUNT(*) FILTER (WHERE s.status_conformado <> 'CANCELADO')::integer
                AS frequencia_pedidos
        FROM dw.fato_pedido p
        JOIN dw.dim_status s ON s.status_sk = p.status_sk
        -- VAZAMENTO PROPOSITAL: esta consulta NÃO tem filtro por data de corte.
        GROUP BY p.cliente_sk
    """), dw)

    # Remove as colunas históricas de recência e frequência e as substitui pelas versões com vazamento futuro
    atributos = atributos.drop(columns=[
        'recencia_dias',
        'frequencia_pedidos',
    ]).merge(dados_futuros, on='cliente_sk', how='left', validate='one_to_one')

    # Extrai a série de valor monetário e calcula o desvio padrão populacional (ddof=0) global
    valores = atributos['valor_monetario'].astype(float)
    desvio = valores.std(ddof=0)

    # Valida se o desvio padrão é válido e diferente de zero antes de aplicar a divisão
    if pd.isna(desvio) or desvio == 0:
        raise ValueError('Impossível normalizar valor_monetario: desvio padrão nulo')

    # Aplica normalização z-score global (vazamento de estatísticas do conjunto de teste) e reordena colunas
    atributos['valor_monetario'] = (valores - valores.mean()) / desvio
    return atributos[colunas_originais]


# ---------------------------------------------------------------------------
# Bloco: Persistência da Tabela-Base de Análise (ABT) no Esquema Analítico
# ---------------------------------------------------------------------------
def grava(df, dw, sufixo):
    """
    Persiste o DataFrame final da ABT no esquema 'abt' do Data Warehouse,
    direcionando para a tabela correspondente à variante executada (correta ou vazamento).
    """
    # Define o nome da tabela física de destino no esquema 'abt' conforme a flag selecionada
    tabela = "recompra_90d" if sufixo == "correta" else "recompra_90d_vazamento"

    # Recria a tabela de destino gravando os registros em lotes de 5.000 linhas com inserção múltipla
    df.to_sql(tabela, dw, schema="abt", if_exists="replace", index=False, method="multi", chunksize=5_000)
    ok(f"abt.{tabela}: {len(df)} linhas, {df.shape[1]} colunas")


# ---------------------------------------------------------------------------
# Bloco: Orquestração Principal, Validações de Qualidade (QA) e Gravação da ABT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Configura o parser de argumentos de linha de comando para seleção da variante da ABT
    ap = argparse.ArgumentParser()
    ap.add_argument("--correta", action="store_true")
    ap.add_argument("--vazamento", action="store_true")
    args = ap.parse_args()

    # Garante que exatamente uma das duas flags (--correta ou --vazamento) foi informada na execução
    if args.correta == args.vazamento:
        ap.error("escolha somente um: --correta ou --vazamento")

    # Inicia o cronômetro de execução total e exibe o cabeçalho da Etapa 4 no terminal
    inicio = perf_counter()
    secao("ETAPA 4 — ABT E EXPERIMENTO DE VAZAMENTO")
    info("Gerando a versão correta ou a versão com vazamento conforme a flag informada.")

    # Obtém a conexão com o Data Warehouse de destino e carrega a população base com o rótulo alvo
    dw = eng_dw()
    alvo = pd.read_sql(text(SQL_ALVO), dw)
    info(f"alvo: {len(alvo)} clientes | taxa de recompra = {alvo['alvo_recompra_90d'].mean():.3f}")

    # Inicializa a suíte de asserções de qualidade identificada pelo sufixo da variante em execução
    qa = Suite(etapa="04_abt_" + ("correta" if args.correta else "vazamento"))

    # Asserção 1: Verifica se a taxa de conversão positiva do alvo está dentro de uma faixa plausível (2% a 60%)
    qa.entre(
        "taxa do alvo",
        float(alvo["alvo_recompra_90d"].mean()),
        0.02,
        0.60,
        "classe extremamente desbalanceada indica erro na janela",
    )

    # Asserção 2: Garante que cada cliente aparece uma única vez na definição da população-alvo
    qa.unico("cliente_sk no alvo", alvo["cliente_sk"])

    # Constrói o conjunto de atributos preditivos de acordo com o modo selecionado na linha de comando
    atributos = (monta_atributos_corretos(dw) if args.correta else monta_atributos_com_vazamento(dw))

    # Realiza a junção entre a população-alvo e os atributos mantendo todos os clientes elegíveis
    antes = len(alvo)
    abt = alvo.merge(atributos, on="cliente_sk", how="left")

    # Asserção 3: Confere se a junção com os atributos preservou exatamente a cardinalidade da população-alvo
    qa.cardinalidade_preservada("junção alvo x atributos", antes, len(abt))

    # Asserção 4: Verifica ausência de valores nulos em atributos obrigatórios de clientes com histórico
    qa.sem_nulos("atributos históricos para toda a população", abt['frequencia_pedidos'])

    # Asserção 5 (exclusiva do modo --correta): Audita amostra de clientes para comprovar ausência de vazamento temporal
    if args.correta:
        verifica_amostra_corte(dw, abt, qa)

    # Exibe o relatório consolidado de QA no terminal e interrompe a gravação caso alguma asserção tenha falhado
    qa.imprimir()
    if qa.falhou():
        raise RuntimeError('ABT não gravada: uma ou mais asserções falharam')

    # Persiste a tabela ABT validada no esquema 'abt' e registra o histórico de asserções no banco
    grava(abt, dw, "correta" if args.correta else "vazamento")
    qa.registrar(dw)
    ok("Etapa 4 concluída.")
    print(f"[tempo] total etapa 4      | {perf_counter() - inicio:.1f}s")