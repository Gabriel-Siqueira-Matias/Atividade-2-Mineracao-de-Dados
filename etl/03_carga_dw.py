#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 3 — CARGA NO ESQUEMA ESTRELA  (esqueleto: vocês completam)

Antes de escrever a primeira linha desta etapa, o relatório já deve conter
a DECLARAÇÃO DE GRÃO das duas tabelas-fato, na forma:

    "Uma linha de dw.fato_pedido representa ______________________________."
    "Uma linha de dw.fato_item_pedido representa _________________________."

Se a frase não couber sem um "e também", o grão está errado.

    docker compose exec etl python 03_carga_dw.py
"""

from datetime import date, timedelta
from time import perf_counter
import pandas as pd
from sqlalchemy import text
from config import eng_dw
from qualidade.assercoes import Suite


def carrega_dim_tempo(engine, ini=date(2022, 12, 1), fim=date(2026, 3, 31)):
    """Calendário do professor, sem apagar o DW que já contém pedidos."""
    MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]
    linhas = []
    d = ini
    while d <= fim:
        linhas.append(dict(data_sk=d, ano=d.year, trimestre=(d.month - 1) // 3 + 1,
                           mes=d.month, dia=d.day, dia_semana=d.isoweekday(),
                           nome_mes=MESES[d.month - 1],
                           eh_fim_semana=d.isoweekday() >= 6))
        d += timedelta(days=1)
    df = pd.DataFrame(linhas)
    sql = text("""
        INSERT INTO dw.dim_tempo
            (data_sk, ano, trimestre, mes, dia, dia_semana, nome_mes, eh_fim_semana)
        VALUES (:data_sk, :ano, :trimestre, :mes, :dia, :dia_semana,
                :nome_mes, :eh_fim_semana)
        ON CONFLICT (data_sk) DO NOTHING
    """)
    inseridos = engine.execute(sql, df.to_dict(orient="records")).rowcount
    print(f"\ndim_tempo: {len(df)} datas no intervalo, {inseridos} acrescentadas", flush=True)
    return inseridos


def carrega_dimensoes(qa):
    # 3.1 — dimensões já carregadas pela Etapa 2; apenas conferir.
    #       Recriá-las alteraria os SKs dos pedidos existentes.
    cx = qa.conexao
    for t in ("dim_cliente", "dim_produto", "dim_canal", "dim_status"):
        n = cx.execute(text(f"SELECT COUNT(*) FROM dw.{t}")).scalar_one()
        if n == 0:
            raise ValueError(f"A dimensão dw.{t} está vazia. Execute a Etapa 2 primeiro.")
        print(f"[OK] dw.{t}: {n} registros validados.")
    # Sem cliente DESCONHECIDO artificial aqui: os pedidos já estão carregados.
    for coluna in ("id_legado", "id_ecommerce"):
        n = cx.execute(text(f"SELECT COUNT(*) FROM dw.dim_produto WHERE {coluna} IS NOT NULL")).scalar_one()
        if n == 0:
            raise ValueError(f"dw.dim_produto.{coluna} só tem NULL. Corrija a Etapa 2.")


def carrega_fato_pedido(qa):
    # 3.2 — os 410.000 pedidos já foram carregados pela transformação do grupo.
    #       NÃO executar TRUNCATE CASCADE: apagaria as demais tabelas fato.
    cx = qa.conexao
    total = cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido")).scalar_one()
    esperado = cx.execute(text("""
        SELECT (SELECT COUNT(*) FROM stg.legado_pedidos) +
               (SELECT COUNT(*) FROM stg.ecommerce_orders)
    """)).scalar_one()
    if total != esperado or total == 0:
        raise ValueError(f"dw.fato_pedido: {total} pedidos; fontes: {esperado}. "
                         "Corrija a carga de pedidos antes de continuar.")
    print(f"[OK] dw.fato_pedido: {total} pedidos já carregados.")


def carrega_fato_item_pedido(engine, esperados):
    """Função dedicada para preencher dw.fato_item_pedido (Legado e E-commerce)."""
    cx = engine   # Conexão da transação única controlada no main.
        
    # Itens do Legado (com tratamento do desconto sentinela -1)
    t0 = perf_counter()
    cx.execute(text("""
        INSERT INTO dw.fato_item_pedido (pedido_sk, produto_sk, quantidade, valor_unitario, valor_desconto, valor_linha)
        SELECT 
            p.pedido_sk,
            pr.produto_sk,
            i."QTD" AS quantidade,
            i."VL_UNITARIO" AS valor_unitario,
            CASE WHEN i."VL_DESCONTO" < 0 THEN 0 ELSE i."VL_DESCONTO" END AS valor_desconto,
            (i."QTD"::numeric * i."VL_UNITARIO"::numeric) - CASE WHEN i."VL_DESCONTO" < 0 THEN 0 ELSE i."VL_DESCONTO" END AS valor_linha
        FROM stg.legado_itens_pedido i
        JOIN dw.fato_pedido p ON p.sistema_origem = 'LEGADO' AND p.id_origem = 'LEG-' || i."ID_PEDIDO"::text
        JOIN dw.dim_produto pr ON pr.id_legado = i."ID_PRODUTO";
    """))

    print(f"[tempo] Inserção dos itens LEGADO: {perf_counter() - t0:.1f}s", flush=True)
    # Itens do E-commerce
    t0 = perf_counter()
    cx.execute(text("""
        INSERT INTO dw.fato_item_pedido (pedido_sk, produto_sk, quantidade, valor_unitario, valor_desconto, valor_linha)
        SELECT 
            p.pedido_sk,
            pr.produto_sk,
            i.quantity AS quantidade,
            i.unit_price AS valor_unitario,
            i.discount AS valor_desconto,
            (i.quantity::numeric * i.unit_price::numeric) - i.discount AS valor_linha
        FROM stg.ecommerce_order_item i
        JOIN dw.fato_pedido p ON p.sistema_origem = 'ECOM' AND p.id_origem = 'ECOM-' || i.order_id::text
        JOIN dw.dim_produto pr ON pr.id_ecommerce = i.product_id;
    """))
    print(f"[tempo] Inserção dos itens ECOM: {perf_counter() - t0:.1f}s\n", flush=True)
    # Reutiliza a contagem de órfãos feita em carrega_fato_item(qa):
    # evita refazer duas junções grandes (1,39 milhão de registros) só para contar.
    inseridos = cx.execute(text("SELECT COUNT(*) FROM dw.fato_item_pedido")).scalar_one()
    if inseridos != esperados:
        raise ValueError(f"fato_item_pedido: {inseridos} inseridos, "
                         f"{esperados} elegíveis. Confira os IDs da dim_produto.")
    print(f"[OK] dw.fato_item_pedido: {inseridos} itens elegíveis inseridos.", flush=True)


def carrega_fato_atendimento(engine):
    """Carrega um atendimento por protocolo, preservando ausências legítimas."""
    cx = engine
    cx.execute(text("""
        INSERT INTO dw.fato_atendimento (
            protocolo, cliente_sk, pedido_sk, data_abertura_sk, data_fechamento_sk,
            canal, motivo_conformado, nota_satisfacao, horas_resolucao
        )
        SELECT
            s.protocolo,
            p.cliente_sk,    -- A chave do pedido é mais confiável do que parear só pelo CPF.
            p.pedido_sk,
            TO_DATE(s.data_abertura, 'DD/MM/YYYY') AS data_abertura_sk,
            CASE
                WHEN NULLIF(TRIM(s.data_fechamento), '') IS NULL
                  OR UPPER(TRIM(s.data_fechamento)) IN ('EM_ABERTO', 'N/A', 'NULL')
                  THEN NULL
                ELSE TO_DATE(s.data_fechamento, 'DD/MM/YYYY')
            END AS data_fechamento_sk,
            s.canal,
            UPPER(TRIM(s.motivo)) AS motivo_conformado,
            CASE WHEN NULLIF(TRIM(s.nota_satisfacao), '')::int BETWEEN 1 AND 5
                 THEN NULLIF(TRIM(s.nota_satisfacao), '')::int
                 ELSE NULL END AS nota_satisfacao,
            CASE WHEN NULLIF(REPLACE(TRIM(s.tempo_resolucao_horas), ',', '.'), '')::numeric >= 0
                 THEN NULLIF(REPLACE(TRIM(s.tempo_resolucao_horas), ',', '.'), '')::numeric
                 ELSE NULL END AS horas_resolucao
        FROM (
            -- O CSV contém repetições exatas; protocolo é UNIQUE no esquema DW.
            SELECT DISTINCT ON (protocolo) *
            FROM stg.atendimentos_sac
            ORDER BY protocolo, _linha_origem
        ) s
        LEFT JOIN dw.fato_pedido p
          ON p.sistema_origem = UPPER(TRIM(s.origem_sistema))
         AND p.id_origem = CASE UPPER(TRIM(s.origem_sistema))
              WHEN 'LEGADO' THEN 'LEG-' || TRIM(s.id_pedido_origem)
              WHEN 'ECOM' THEN 'ECOM-' || TRIM(s.id_pedido_origem)
              ELSE NULL END
        WHERE TO_DATE(s.data_abertura, 'DD/MM/YYYY') >= DATE '2000-01-01';
    """))
    esperados = cx.execute(text(
        "SELECT COUNT(DISTINCT protocolo) FROM stg.atendimentos_sac"
    )).scalar_one()
    carregados = cx.execute(text("SELECT COUNT(*) FROM dw.fato_atendimento")).scalar_one()
    if esperados != carregados:
        raise ValueError(f"SAC: {carregados} protocolos inseridos, esperados {esperados}")
    print(f"[OK] dw.fato_atendimento: {carregados} protocolos únicos.")


def carrega_fato_avaliacao(engine):
    """Carrega avaliações usando data original, sem data sintética."""
    cx = engine
    # data_sk é NOT NULL: rejeitar dados inválidos, em vez de inventar datas.
    invalidas = cx.execute(text("""
        SELECT COUNT(*) FROM stg.avaliacoes_marketplace
        WHERE submitted_at IS NULL
           OR LEFT(TRIM(submitted_at), 10) !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    """)).scalar_one()
    if invalidas:
        raise ValueError(f"{invalidas} avaliações sem data válida; corrija ou documente no RDT")
    cx.execute(text("""
        INSERT INTO dw.fato_avaliacao (
            review_id, pedido_sk, produto_sk, data_sk, nota, compra_verificada, votos_uteis
        )
        SELECT
            a.review_id,
            p.pedido_sk,
            COALESCE(pr_eco.produto_sk, pr_leg.produto_sk) AS produto_sk,
            LEFT(a.submitted_at, 10)::date AS data_sk,
            NULLIF(TRIM(a.rating), '')::int AS nota,
            a.verified_purchase::boolean AS compra_verificada,
            a.helpful_votes::int AS votos_uteis
        FROM stg.avaliacoes_marketplace a
        LEFT JOIN dw.fato_pedido p ON p.id_origem = a.order_ref
        LEFT JOIN dw.dim_produto pr_eco
          ON pr_eco.origem = 'ECOM'
         AND a.order_ref LIKE 'ECOM-%'
         AND pr_eco.sku = a.product_sku
        LEFT JOIN dw.dim_produto pr_leg
          ON pr_leg.origem = 'LEGADO'
         AND a.order_ref LIKE 'LEG-%'
         AND pr_leg.id_legado = NULLIF(SUBSTRING(a.product_sku FROM '^LEG-([0-9]+)$'), '')::int;
    """))
    esperados = cx.execute(text("SELECT COUNT(*) FROM stg.avaliacoes_marketplace")).scalar_one()
    carregados = cx.execute(text("SELECT COUNT(*) FROM dw.fato_avaliacao")).scalar_one()
    if esperados != carregados:
        raise ValueError(f"Avaliações: {carregados} inseridas, esperadas {esperados}")
    print(f"[OK] dw.fato_avaliacao: {carregados} avaliações.")


def carrega_fato_item(qa):
    # 3.3 — itens órfãos não entram no DW; permanecem em stg (RDT-10).
    cx = qa.conexao
    esperados = 0
    for sistema, origem, idcol, prefixo in (
        ("LEGADO", "legado_itens_pedido", '"ID_PEDIDO"', "LEG-"),
        ("ECOM", "ecommerce_order_item", "order_id", "ECOM-"),
    ):
        orfaos = cx.execute(text(f"""
            SELECT COUNT(*) FROM stg.{origem} i
            LEFT JOIN dw.fato_pedido p
              ON p.sistema_origem = :sistema
             AND p.id_origem = :prefixo || i.{idcol}::text
            WHERE p.pedido_sk IS NULL
        """), {"sistema": sistema, "prefixo": prefixo}).scalar_one()
        total = cx.execute(text(f"SELECT COUNT(*) FROM stg.{origem}")).scalar_one()
        esperados += total - orfaos
        print(f"Itens órfãos ({sistema}), mantidos no staging: {orfaos}", flush=True)
    carrega_fato_item_pedido(cx, esperados)


def carrega_fatos_auxiliares(qa):
    # 3.4 — mesma transação de fato_item_pedido; falhou, reverte tudo.
    cx = qa.conexao
    t0 = perf_counter()
    carrega_fato_atendimento(cx)
    print(f"[tempo] Atendimento: {perf_counter() - t0:.1f}s\n", flush=True)
    t0 = perf_counter()
    carrega_fato_avaliacao(cx)
    print(f"[tempo] Avaliações: {perf_counter() - t0:.1f}s\n", flush=True)




def conferir_carga_gravada(engine):
    """Conferência automática após cada recarga e COMMIT no banco."""
    with engine.connect() as cx:
        dados = cx.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM dw.fato_pedido) AS pedidos,
                (SELECT COUNT(*) FROM dw.fato_item_pedido) AS itens,
                (SELECT COUNT(*) FROM dw.fato_atendimento) AS sac,
                (SELECT COUNT(*) FROM dw.fato_avaliacao) AS avaliacoes,
                (SELECT COUNT(*) FROM stg.legado_pedidos) +
                (SELECT COUNT(*) FROM stg.ecommerce_orders) AS pedidos_origem,
                (SELECT COUNT(DISTINCT protocolo) FROM stg.atendimentos_sac) AS sac_origem,
                (SELECT COUNT(*) FROM stg.avaliacoes_marketplace) AS avaliacoes_origem
        """)).mappings().one()
        for k, v in dados.items():
            print(f"  {k}: {v}")
        qa = cx.execute(text("""
            SELECT executado_em,
                   COUNT(*) AS testes,
                   COUNT(*) FILTER (WHERE aprovado) AS aprovadas,
                   COUNT(*) FILTER (WHERE NOT aprovado) AS falhas
            FROM qa.execucao_assercao
            WHERE etapa = '03_carga_dw'
            GROUP BY executado_em ORDER BY executado_em DESC LIMIT 1
        """)).mappings().first()
        print("Última execução QA:", dict(qa) if qa else "não encontrada")
        aux_sem_pedido = cx.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM dw.fato_atendimento WHERE pedido_sk IS NULL) AS sac,
                (SELECT COUNT(*) FROM dw.fato_avaliacao WHERE pedido_sk IS NULL) AS avaliacoes
        """)).mappings().one()
        print("Auxiliares sem pedido:", dict(aux_sem_pedido))
        # Para ESCALA=1, o histórico foi 1.371.876 itens / 20.577 órfãos.
        # A carga já comparou os totais elegíveis antes do COMMIT;
        # esta conferência final verifica persistência, vínculos e QA.
        ok = (dados['pedidos'] == dados['pedidos_origem'] > 0
              and dados['sac'] == dados['sac_origem'] > 0
              and dados['avaliacoes'] == dados['avaliacoes_origem'] > 0
              and dados['itens'] > 0
              and aux_sem_pedido['sac'] == 0
              and aux_sem_pedido['avaliacoes'] == 0
              and qa is not None and qa['testes'] == 4
              and qa['aprovadas'] == 4 and qa['falhas'] == 0)
        print("[OK] Última carga validada." if ok
              else "[FALHA] Encontradas divergências: verifique os dados e o QA.")
        if not ok:
            raise SystemExit(1)


if __name__ == "__main__":
    dw = eng_dw()
    qa = Suite(etapa="03_carga_dw")
    inicio = perf_counter()
    print("\nIniciando Etapa 3 - Carga dw")
    # Uma transação para a carga integral: qualquer erro reverte tudo.
    with dw.begin() as cx:
        qa.conexao = cx
        carrega_dim_tempo(cx)
        carrega_dimensoes(qa)
        carrega_fato_pedido(qa)

        # RECARREGA, não acumula. TRUNCATE SEM CASCADE preserva pedido/dimensões.
        t0 = perf_counter()
        cx.execute(text("""
            TRUNCATE TABLE dw.fato_item_pedido, dw.fato_atendimento,
                           dw.fato_avaliacao RESTART IDENTITY
        """))
        print(f"[tempo] Preparação das fatos: {perf_counter() - t0:.1f}s\n", flush=True)
        t0 = perf_counter()
        carrega_fato_item(qa)
        print(f"[tempo] Itens: {perf_counter() - t0:.1f}s\n", flush=True)
        t0 = perf_counter()
        carrega_fatos_auxiliares(qa)
        print(f"[tempo] SAC e avaliações: {perf_counter() - t0:.1f}s\n", flush=True)

        t0 = perf_counter()
        # 3.5 — as quatro asserções obrigatórias do professor:
        # 1. Nenhuma FK órfã em fato_pedido.
        orfas = cx.execute(text("""
            SELECT COUNT(*) FROM dw.fato_pedido f
            LEFT JOIN dw.dim_cliente c ON f.cliente_sk = c.cliente_sk
            LEFT JOIN dw.dim_tempo t ON f.data_pedido_sk = t.data_sk
            LEFT JOIN dw.dim_canal cn ON f.canal_sk = cn.canal_sk
            WHERE c.cliente_sk IS NULL OR t.data_sk IS NULL OR cn.canal_sk IS NULL
        """)).scalar_one()
        qa.igual("sem FK órfã em fato_pedido", int(orfas), 0)

        # 2. Contagem de pedidos = soma das origens.
        total_fatos = cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido")).scalar_one()
        esperado_ped = cx.execute(text("""
            SELECT (SELECT COUNT(*) FROM stg.legado_pedidos) +
                   (SELECT COUNT(*) FROM stg.ecommerce_orders)
        """)).scalar_one()
        qa.igual("contagem total de fato_pedido", int(total_fatos), int(esperado_ped))

        # 3. Reconciliar as DUAS origens, com o mesmo critério monetário da Etapa 2.
        divergentes = cx.execute(text("""
            WITH itens_leg AS (
                SELECT "ID_PEDIDO" AS id,
                       SUM("QTD"::numeric * "VL_UNITARIO"::numeric) AS mercadoria,
                       SUM(CASE WHEN "VL_DESCONTO" < 0 THEN 0
                                ELSE "VL_DESCONTO"::numeric END) AS desconto
                FROM stg.legado_itens_pedido GROUP BY "ID_PEDIDO"
            ), itens_eco AS (
                SELECT order_id AS id,
                       SUM(quantity::numeric * unit_price::numeric) AS mercadoria,
                       SUM(discount::numeric) AS desconto
                FROM stg.ecommerce_order_item GROUP BY order_id
            ), origem AS (
                SELECT 'LEGADO' AS sistema,
                       SUM(COALESCE(i.mercadoria, 0) + ABS(p."VALOR_FRETE"::numeric)
                           - COALESCE(i.desconto, 0)) AS total
                FROM stg.legado_pedidos p LEFT JOIN itens_leg i ON i.id = p."ID_PEDIDO"
                UNION ALL
                SELECT 'ECOM',
                       SUM(COALESCE(i.mercadoria, 0) + ABS(p.freight_amount::numeric)
                           - COALESCE(i.desconto, 0))
                FROM stg.ecommerce_orders p LEFT JOIN itens_eco i ON i.id = p.order_id
            ), destino AS (
                SELECT sistema_origem AS sistema, SUM(valor_total) AS total
                FROM dw.fato_pedido GROUP BY sistema_origem
            )
            SELECT COUNT(*) FROM origem o
            LEFT JOIN destino d ON d.sistema = o.sistema
            WHERE o.total IS NULL OR d.total IS NULL
               OR ABS(o.total - d.total) > 0.001 * GREATEST(ABS(o.total), 1)
        """)).scalar_one()
        qa.igual("reconciliacao financeira (LEGADO e ECOM, ±0,1%)", int(divergentes), 0)

        # 4. Nenhum prazo de entrega negativo.
        negativos = cx.execute(text("""
            SELECT COUNT(*) FROM dw.fato_pedido WHERE dias_entrega < 0
        """)).scalar_one()
        qa.igual("nenhum pedido com dias_entrega negativo", int(negativos), 0)

        qa.imprimir()
        if qa.falhou():
            raise RuntimeError("Asserções falharam; toda a carga foi revertida")
        print(f"[tempo] Asserções: {perf_counter() - t0:.1f}s", flush=True)

    # Registrar QA depois do COMMIT dos dados. Não refaz as transformações.
    qa.registrar(dw)
    t0 = perf_counter()
    conferir_carga_gravada(dw)
    print(f"[tempo] Conferência da carga gravada: {perf_counter() - t0:.1f}s", flush=True)
    print(f"[OK] Etapa 3: recarga completa, QA registrado e dados conferidos. Total: {perf_counter() - inicio:.1f}s\n", flush=True)
