#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 4 — TABELA-BASE DE ANÁLISE (ABT) E O EXPERIMENTO DE VAZAMENTO

Pergunta analítica:
    dado o comportamento do cliente ATÉ 2025-06-30, ele comprará de novo
    entre 2025-07-01 e 2025-09-28?

Esta etapa produz DUAS versões da mesma tabela:

    --correta     todos os atributos calculados apenas com dados até a data
                  de corte. É a versão que vale.

    --vazamento   a mesma coisa, só que com dois ou três atributos calculados
                  sobre a base inteira. É a versão errada, que vocês vão
                  construir de propósito, rodar no Orange e comparar.

O objetivo do experimento não é ver um número maior. É medir a distância
entre um resultado que parece excelente e um resultado que é válido —
e descobrir que a diferença entre os dois cabe em uma linha de código.

    docker compose exec etl python 04_abt.py --correta
    docker compose exec etl python 04_abt.py --vazamento
"""

import argparse
from time import perf_counter

import pandas as pd
from sqlalchemy import text

from config import DATA_CORTE, DATA_FIM_ALVO, eng_dw
from qualidade.assercoes import Suite


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
# ALVO — este está pronto, e é o único ponto do pipeline em que é legítimo
# olhar para o futuro, porque é exatamente isso que se quer prever.
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
# Regras de cálculo da ABT correta (RDT-14): sem data posterior ao corte.
# Um registro por cliente. Os casos sem compras válidas mantêm médias NULL.
# Nota: status de pedido e categoria de produto são fotografias do DW atual;
# se essas informações mudaram depois do corte, a fonte não guarda histórico.
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


def verifica_amostra_corte(dw, abt, qa):
    """4.3: confronta recência/frequência com cálculo independente até o corte."""
    from sqlalchemy import bindparam

    # Amostra determinística e reproduzível: até 100 SKs distribuídas pela ABT.
    ordenados = abt.sort_values('cliente_sk')
    passo = max(1, len(ordenados) // 100)
    amostra = ordenados.iloc[::passo].head(100)
    if amostra.empty:
        qa.igual('amostra temporal sem divergências', 1, 0)
        return

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
    esperado = pd.read_sql(stmt, dw, params={'ids': ids})
    comparacao = amostra[['cliente_sk', 'recencia_dias', 'frequencia_pedidos']].merge(
        esperado, on='cliente_sk', how='left', suffixes=('_abt', '_origem'),
        validate='one_to_one')
    rec_abt = comparacao['recencia_dias_abt'].astype('Int64')
    rec_ref = comparacao['recencia_dias_origem'].astype('Int64')
    freq_abt = comparacao['frequencia_pedidos_abt'].astype('Int64')
    freq_ref = comparacao['frequencia_pedidos_origem'].astype('Int64')
    divergencias = int((~(rec_abt.eq(rec_ref).fillna(rec_abt.isna() & rec_ref.isna())
                         & freq_abt.eq(freq_ref).fillna(False))).sum())
    qa.igual('amostra temporal sem divergências', divergencias, 0,
             'Recência e frequência devem ignorar pedidos posteriores a 30/06/2025')

def monta_atributos_corretos(dw):
    """
    4.1 — atributos preditivos. Todo agregado tem de ter, no WHERE,
    a cláusula   p.data_pedido_sk <= DATE '{corte}'.

    Sugestões (desenhem as suas, estas são só ponto de partida):
      recencia_dias          dias entre a última compra e a data de corte
      frequencia_pedidos     nº de pedidos não cancelados até o corte
      valor_monetario        soma de valor_total até o corte
      ticket_medio           valor_monetario / frequencia_pedidos
      prazo_medio_entrega    média de dias_entrega dos pedidos entregues
      taxa_cancelamento      cancelados / total
      categorias_distintas   nº de categorias nivel1 compradas
      canal_predominante     canal com mais pedidos
      qtd_atendimentos_sac   atendimentos abertos até o corte
      nota_media_avaliacoes  média das avaliações submetidas até o corte
      tempo_como_cliente     dias entre o primeiro pedido e o corte

    Mais 5 features propostas pela equipe:
      pedidos_ultimos_90d     contagem de compras não canceladas nos 90 dias antes do corte
      intervalo_medio_compras intervalo médio entre compras válidas (NULL com < 2)
      qtd_total_itens        soma das quantidades de itens em compras válidas
      desvio_valor_pedidos   desvio padrão amostral dos valores (NULL com < 2)
      dias_desde_ultimo_sac  dias desde o SAC mais recente (NULL sem atendimento)

    Cuidado com dois detalhes fáceis de errar:
      · clientes sem pedidos no período geram NaN em médias — decidam o
        tratamento e registrem (NaN não é zero);
      · atributo derivado de dim_cliente pode carregar informação atualizada
        depois do corte. Verifiquem antes de usar.
    """
    return pd.read_sql(text(SQL_ATRIBUTOS_CORRETOS), dw)


def monta_atributos_com_vazamento(dw):
    """
    4.2 — reproduza os MESMOS atributos, removendo o filtro de data em
    pelo menos dois deles (por exemplo recencia_dias e frequencia_pedidos),
    de modo que passem a enxergar a janela do alvo.

    Acrescente um terceiro vazamento mais sutil: normalize (z-score) um
    atributo numérico usando média e desvio calculados sobre TODAS as linhas,
    antes de qualquer partição treino/teste.

    Documente no RDT qual vazamento você introduziu em cada coluna.
    """
    # RDT-15: dois atributos passam a enxergar compras DEPOIS da data de corte.
    # A população de clientes e as demais colunas continuam iguais à versão correta.
    atributos = monta_atributos_corretos(dw)
    colunas_originais = list(atributos.columns)
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
    atributos = atributos.drop(columns=['recencia_dias', 'frequencia_pedidos']).merge(
        dados_futuros, on='cliente_sk', how='left', validate='one_to_one')

    # VAZAMENTO SUTIL: normalização global ANTES de dividir treino e teste.
    # O valor monetário continua histórico, mas sua média/desvio vêm da ABT toda.
    valores = atributos['valor_monetario'].astype(float)
    desvio = valores.std(ddof=0)
    if pd.isna(desvio) or desvio == 0:
        raise ValueError('Impossível normalizar valor_monetario: desvio padrão nulo')
    atributos['valor_monetario'] = (valores - valores.mean()) / desvio
    return atributos[colunas_originais]


def grava(df, dw, sufixo):
    tabela = "recompra_90d" if sufixo == "correta" else "recompra_90d_vazamento"
    df.to_sql(tabela, dw, schema="abt", if_exists="replace",
              index=False, method="multi", chunksize=5_000)
    ok(f"abt.{tabela}: {len(df)} linhas, {df.shape[1]} colunas")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--correta", action="store_true")
    ap.add_argument("--vazamento", action="store_true")
    args = ap.parse_args()
    if args.correta == args.vazamento:
        ap.error("escolha somente um: --correta ou --vazamento")

    inicio = perf_counter()
    secao("ETAPA 4 — ABT E EXPERIMENTO DE VAZAMENTO")
    info("Gerando a versão correta ou a versão com vazamento conforme a flag informada.")
    dw = eng_dw()
    alvo = pd.read_sql(text(SQL_ALVO), dw)
    info(f"alvo: {len(alvo)} clientes | taxa de recompra = {alvo['alvo_recompra_90d'].mean():.3f}")

    qa = Suite(etapa="04_abt_" + ("correta" if args.correta else "vazamento"))
    qa.entre("taxa do alvo", float(alvo["alvo_recompra_90d"].mean()), 0.02, 0.60,
             "classe extremamente desbalanceada indica erro na janela")
    qa.unico("cliente_sk no alvo", alvo["cliente_sk"])

    atributos = (monta_atributos_corretos(dw) if args.correta
                 else monta_atributos_com_vazamento(dw))

    antes = len(alvo)
    abt = alvo.merge(atributos, on="cliente_sk", how="left")
    qa.cardinalidade_preservada("junção alvo x atributos", antes, len(abt))
    qa.sem_nulos("atributos históricos para toda a população", abt['frequencia_pedidos'])

    # 4.3 — conferência independente e determinística por amostragem.
    if args.correta:
        verifica_amostra_corte(dw, abt, qa)

    qa.imprimir()
    if qa.falhou():
        raise RuntimeError('ABT não gravada: uma ou mais asserções falharam')

    grava(abt, dw, "correta" if args.correta else "vazamento")
    qa.registrar(dw)
    ok("Etapa 4 concluída.")
    print(f"[tempo] total etapa 4      | {perf_counter() - inicio:.1f}s")
