#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 4 — TABELA-BASE DE ANÁLISE (ABT) E EXPERIMENTO DE VAZAMENTO

Objetivo: prever se um cliente com histórico até 30/06/2025 realizará
uma nova compra não cancelada entre 01/07/2025 e 28/09/2025.

O script gera duas versões com a mesma população e o mesmo alvo:
  · --correta: calcula 15 atributos com registros limitados à data de corte;
  · --vazamento: substitui recência e frequência por valores que consideram
    pedidos posteriores ao corte e normaliza valor_monetario com média e
    desvio de todos os clientes, antes de dividir treino e teste.

Comandos: docker compose exec etl python 04_abt.py --correta
          docker compose exec etl python 04_abt.py --vazamento
"""


import argparse
import pandas as pd
from sqlalchemy import text
from time import perf_counter
from qualidade.assercoes import Suite
from config import DATA_CORTE, DATA_FIM_ALVO, eng_dw


# ---------------------------------------------------------------------------
# Funções auxiliares para formatar as mensagens do experimento ABT
# ---------------------------------------------------------------------------
def secao(titulo):
    """Cabeçalho visual para separar blocos da ABT."""
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
# Alvo: consultar o futuro é permitido para gerar o rótulo, não as features.
# Usa a população com pedidos até o corte e compras válidas na janela posterior.
# ---------------------------------------------------------------------------
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
# Atributos da ABT correta (RDT-14): agregados limitados à data de corte.
# Uma linha por cliente; médias sem observações suficientes permanecem NULL.
# Status e categoria refletem o DW atual, que não guarda histórico completo
# de alterações dessas informações após o corte (limitação documentada).
# ---------------------------------------------------------------------------
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
# Função para conferir a recência e frequência da ABT correta por amostragem
# ---------------------------------------------------------------------------
def verifica_amostra_corte(dw, abt, qa):
    """
    Compara recência e frequência de até 100 clientes com consulta histórica
    independente à fato de pedidos, registrando divergências no QA.
    """
    from sqlalchemy import bindparam

    # Amostra determinística de até 100 chaves de clientes distribuídas na ABT.
    # Ordena clientes por chave e seleciona até 100 posições determinísticas.
    ordenados = abt.sort_values('cliente_sk')
    passo = max(1, len(ordenados) // 100)
    amostra = ordenados.iloc[::passo].head(100)
    if amostra.empty:
        qa.igual('amostra temporal sem divergências', 1, 0)
        return

    # Recalcula na fato de pedidos duas métricas usando apenas o histórico.
    ids = [int(v) for v in amostra['cliente_sk']]
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
    # Compara as métricas extraídas da ABT com as calculadas diretamente do DW.
    esperado = pd.read_sql(stmt, dw, params={'ids': ids})
    comparacao = amostra[['cliente_sk', 'recencia_dias', 'frequencia_pedidos']].merge(esperado, on='cliente_sk', how='left', suffixes=('_abt', '_origem'),validate='one_to_one')
    rec_abt = comparacao['recencia_dias_abt'].astype('Int64')
    rec_ref = comparacao['recencia_dias_origem'].astype('Int64')
    freq_abt = comparacao['frequencia_pedidos_abt'].astype('Int64')
    freq_ref = comparacao['frequencia_pedidos_origem'].astype('Int64')
    # Trata recências nulas de modo específico e registra a contagem de diferenças.
    divergencias = int((~(rec_abt.eq(rec_ref).fillna(rec_abt.isna() & rec_ref.isna()) & freq_abt.eq(freq_ref).fillna(False))).sum())
    qa.igual('amostra temporal sem divergências', divergencias, 0, 'Recência e frequência devem ignorar pedidos posteriores a 30/06/2025')


# ---------------------------------------------------------------------------
# Função para obter os 15 atributos históricos da ABT correta
# ---------------------------------------------------------------------------
def monta_atributos_corretos(dw):
    """
    Consulta os 15 atributos preditivos históricos definidos em
    SQL_ATRIBUTOS_CORRETOS, com pedidos limitados a DATA_CORTE.
    Médias, intervalos e desvios insuficientes podem permanecer nulos.

    A seleção de status e categoria reflete os valores atuais do DW,
    pois não há histórico temporal completo dessas dimensões.
    """
    return pd.read_sql(text(SQL_ATRIBUTOS_CORRETOS), dw)


# ---------------------------------------------------------------------------
# Função para gerar atributos contaminados intencionalmente para comparação
# ---------------------------------------------------------------------------
def monta_atributos_com_vazamento(dw):
    """
    Retorna os mesmos atributos com dois vazamentos temporais deliberados:
    recencia_dias e frequencia_pedidos calculados sem corte de data.
    Aplica ainda z-score global ao valor_monetario antes da divisão
    treino/teste; não deve ser usado para previsão real.
    """
    # RDT-15: recência e frequência passam a utilizar compras posteriores ao corte.
    # Outros atributos continuam históricos, exceto valor_monetario normalizado.
    # Parte da mesma ABT histórica, preservando a população e as outras features.
    atributos = monta_atributos_corretos(dw)
    colunas_originais = list(atributos.columns)
    # Consulta pedidos de todas as datas, inclusive posteriores a DATA_CORTE.
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
    # Substitui recência e frequência pelas versões que enxergam dados futuros.
    atributos = atributos.drop(columns=['recencia_dias', 'frequencia_pedidos']).merge(dados_futuros, on='cliente_sk', how='left', validate='one_to_one')

    # Vazamento sutil: z-score ajustado à base inteira antes da partição.
    # O valor monetário base é histórico; a média e o desvio incluem o teste.
    # Obtém média e desvio da população toda antes de separar treino e teste.
    valores = atributos['valor_monetario'].astype(float)
    desvio = valores.std(ddof=0)
    # Evita divisão por zero na padronização z-score.
    if pd.isna(desvio) or desvio == 0:
        raise ValueError('Impossível normalizar valor_monetario: desvio padrão nulo')
    # Padroniza valor_monetario globalmente e mantém a ordem original das colunas.
    atributos['valor_monetario'] = (valores - valores.mean()) / desvio
    return atributos[colunas_originais]


# ---------------------------------------------------------------------------
# Função para gravar no DW a versão selecionada da ABT
# ---------------------------------------------------------------------------
def grava(df, dw, sufixo):
    # Escolhe o nome da tabela a partir da opção de execução.
    tabela = "recompra_90d" if sufixo == "correta" else "recompra_90d_vazamento"
    # Recria a tabela ABT no esquema abt e exibe seu tamanho.
    df.to_sql(tabela, dw, schema="abt", if_exists="replace", index=False, method="multi", chunksize=5_000)
    ok(f"abt.{tabela}: {len(df)} linhas, {df.shape[1]} colunas")


# ---------------------------------------------------------------------------
# Ponto de entrada da construção das ABTs e das asserções
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Define as opções mutuamente exclusivas de construção da ABT.
    ap = argparse.ArgumentParser()
    ap.add_argument("--correta", action="store_true")
    ap.add_argument("--vazamento", action="store_true")
    args = ap.parse_args()
    # Exige que uma e somente uma versão seja selecionada.
    if args.correta == args.vazamento:
        ap.error("escolha somente um: --correta ou --vazamento")

    # Abre o cronômetro e lê a população e o alvo calculados pelo SQL_ALVO.
    inicio = perf_counter()
    secao("ETAPA 4 — ABT E EXPERIMENTO DE VAZAMENTO")
    info("Gerando a versão correta ou a versão com vazamento conforme a flag informada.")
    dw = eng_dw()
    alvo = pd.read_sql(text(SQL_ALVO), dw)
    info(f"alvo: {len(alvo)} clientes | taxa de recompra = {alvo['alvo_recompra_90d'].mean():.3f}")

    # Cria a suíte de QA específica da variante selecionada.
    qa = Suite(etapa="04_abt_" + ("correta" if args.correta else "vazamento"))
    qa.entre("taxa do alvo", float(alvo["alvo_recompra_90d"].mean()), 0.02, 0.60, "classe extremamente desbalanceada indica erro na janela")
    qa.unico("cliente_sk no alvo", alvo["cliente_sk"])

    # Escolhe os atributos históricos ou os contaminados intencionalmente.
    atributos = (monta_atributos_corretos(dw) if args.correta else monta_atributos_com_vazamento(dw))

    # Junta alvo e atributos mantendo todos os clientes da população-alvo.
    antes = len(alvo)
    abt = alvo.merge(atributos, on="cliente_sk", how="left")
    qa.cardinalidade_preservada("junção alvo x atributos", antes, len(abt))
    qa.sem_nulos("atributos históricos para toda a população", abt['frequencia_pedidos'])

    # Somente a ABT correta recebe a conferência temporal independente.
    # Confere recência e frequência em amostra determinística da versão correta.
    if args.correta:
        verifica_amostra_corte(dw, abt, qa)

    # Imprime o resultado da qualidade e interrompe se houver falha.
    qa.imprimir()
    if qa.falhou():
        raise RuntimeError('ABT não gravada: uma ou mais asserções falharam')

    # Grava a ABT, registra as asserções e informa a duração.
    grava(abt, dw, "correta" if args.correta else "vazamento")
    qa.registrar(dw)
    ok("Etapa 4 concluída.")
    print(f"[tempo] total etapa 4      | {perf_counter() - inicio:.1f}s")