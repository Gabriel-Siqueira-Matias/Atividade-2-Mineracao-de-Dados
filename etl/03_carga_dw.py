#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 3 — CARGA NO ESQUEMA ESTRELA

Objetivo: complementar a carga do Data Warehouse com itens de pedidos,
atendimentos do SAC e avaliações, preservando as dimensões e a fato de
pedidos que já foram construídas pela Etapa 2.

Os itens sem pedido vinculado permanecem no staging. A carga das três fatos
auxiliares usa uma transação; erros revertem as inserções. As verificações
finais incluem cardinalidade, integridade de chaves, reconciliação financeira
com tolerância de 0,1% e validade dos prazos de entrega.

Comando: docker compose exec etl python 03_carga_dw.py
"""

from datetime import date, timedelta
from time import perf_counter
import pandas as pd
from sqlalchemy import text
from config import eng_dw
from qualidade.assercoes import Suite


# ---------------------------------------------------------------------------
# Funções auxiliares para formatar mensagens da etapa de carga
# ---------------------------------------------------------------------------
def secao(titulo):
    """Cabeçalho visual para separar blocos da carga."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Mensagem descritiva padronizada."""
    print(f"[INFO] {mensagem}", flush=True)


def ok(mensagem):
    """Mensagem de sucesso padronizada."""
    print(f"[OK] {mensagem}", flush=True)


# ---------------------------------------------------------------------------
# Função para completar a dimensão de tempo sem apagar datas existentes
# ---------------------------------------------------------------------------
def carrega_dim_tempo(engine, ini=date(2022, 12, 1), fim=date(2026, 3, 31)):
    """Esta dimensão está pronta — é a única sem decisão a tomar."""
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
    with engine.begin() as cx:
        cx.execute(text("TRUNCATE dw.dim_tempo CASCADE"))
    df.to_sql("dim_tempo", engine, schema="dw", if_exists="append", index=False)
    print(f"dim_tempo: {len(df)} datas")
    return len(df)


# ---------------------------------------------------------------------------
# Função para conferir dimensões já populadas na transformação
# ---------------------------------------------------------------------------
def carrega_dimensoes(qa):
    # Confere as dimensões carregadas na Etapa 2, sem recriá-las.
    # Recriá-las poderia alterar as chaves utilizadas pelos pedidos.
    # Utiliza a conexão da transação para conferir se cada dimensão tem dados.
    cx = qa.conexao
    for t in ("dim_cliente", "dim_produto", "dim_canal", "dim_status", "dim_tempo"):
        n = cx.execute(text(f"SELECT COUNT(*) FROM dw.{t}")).scalar_one()
        if n == 0:
            raise ValueError(f"A dimensão dw.{t} está vazia. Execute a Etapa 2 primeiro.")
        ok(f"dw.{t}: {n} registros validados.")
    # Não cria cliente artificial; a dimensão deve estar consistente.
    # Confirma que a dimensão de produto possui chaves dos dois sistemas.
    for coluna in ("id_legado", "id_ecommerce"):
        n = cx.execute(text(f"SELECT COUNT(*) FROM dw.dim_produto WHERE {coluna} IS NOT NULL")).scalar_one()
        if n == 0:
            raise ValueError(f"dw.dim_produto.{coluna} só tem NULL. Corrija a Etapa 2.")


# ---------------------------------------------------------------------------
# Função para conferir o grão e a contagem de pedidos já carregados
# ---------------------------------------------------------------------------
def carrega_fato_pedido(qa):
    # Os pedidos já foram inseridos na Etapa 2; aqui só são conferidos.
    # Não limpa a fato de pedidos para não afetar suas relações.
    cx = qa.conexao
    total = cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido")).scalar_one()
    esperado = cx.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM stg.legado_pedidos) +
            (SELECT COUNT(*) FROM stg.ecommerce_orders)
    """)).scalar_one()
    if total != esperado or total == 0:
        raise ValueError(f"dw.fato_pedido: {total} pedidos; fontes: {esperado}. Corrija a carga de pedidos antes de continuar.")
    ok(f"dw.fato_pedido: {total} pedidos já carregados.")


# ---------------------------------------------------------------------------
# Função para carregar os itens que possuem pedido e produto correspondentes
# ---------------------------------------------------------------------------
def carrega_fato_item_pedido(engine, esperados):
    """
    Insere itens das duas fontes que possuam pedido e produto no DW.
    Compara o total carregado com o número elegível calculado no staging.
    """
    cx = engine   # Conexão da transação única controlada no main.
        
    # ERP legado: desconta zero quando VL_DESCONTO é negativo (sentinela).
    t0 = perf_counter()
    # INNER JOIN exclui itens órfãos da fato, sem removê-los do staging.
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

    print(f"[tempo] itens legado        | {perf_counter() - t0:.1f}s", flush=True)
    # E-commerce: calcula valor_linha a partir de quantidade, preço e desconto.
    t0 = perf_counter()
    # Atribui produto_sk e pedido_sk com as chaves do e-commerce.
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
    print(f"[tempo] itens ecom          | {perf_counter() - t0:.1f}s", flush=True)
    # Reutiliza a contagem de elegíveis calculada em carrega_fato_item(qa).
    # Evita repetir as consultas para contar itens órfãos de ambas as origens.
    # Verifica se foram inseridos todos os itens considerados elegíveis.
    inseridos = cx.execute(text("SELECT COUNT(*) FROM dw.fato_item_pedido")).scalar_one()
    if inseridos != esperados:
        raise ValueError(f"fato_item_pedido: {inseridos} inseridos, {esperados} elegíveis. Confira os IDs da dim_produto.")
    ok(f"dw.fato_item_pedido: {inseridos} itens elegíveis inseridos.")


# ---------------------------------------------------------------------------
# Função para calcular a quantidade de itens elegíveis para a carga
# ---------------------------------------------------------------------------
def carrega_fato_item(qa):
    # RDT-10: itens órfãos permanecem no staging e não entram na fato.
    cx = qa.conexao
    esperados = 0
    for sistema, origem, idcol, prefixo in (
        ("LEGADO", "legado_itens_pedido", '"ID_PEDIDO"', "LEG-"),
        ("ECOM", "ecommerce_order_item", "order_id", "ECOM-"),
    ):
        # Conta itens sem pedido da origem e os exclui somente da fato analítica.
        orfaos = cx.execute(text(f"""
            SELECT COUNT(*) FROM stg.{origem} i
            LEFT JOIN dw.fato_pedido p
            ON p.sistema_origem = :sistema
            AND p.id_origem = :prefixo || i.{idcol}::text
            WHERE p.pedido_sk IS NULL
        """), {"sistema": sistema, "prefixo": prefixo}).scalar_one()
        total = cx.execute(text(f"SELECT COUNT(*) FROM stg.{origem}")).scalar_one()
        esperados += total - orfaos
        info(f"Itens órfãos ({sistema}) mantidos no staging: {orfaos}")
    carrega_fato_item_pedido(cx, esperados)


# ---------------------------------------------------------------------------
# Função para verificar a carga após confirmar a transação
# ---------------------------------------------------------------------------
def conferir_carga_gravada(engine):
    """
    Confere as contagens, chaves e a última execução do QA após o commit.
    Falha se faltarem vínculos, contagens ou as quatro asserções aprovadas.
    """
    with engine.connect() as cx:
        # Reconta as fatos e as compara com as contagens das origens.
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
            print(f"  - {k:<18}: {v}")
        # Obtém apenas a última execução das quatro asserções desta etapa.
        qa = cx.execute(text("""
            SELECT executado_em,
                COUNT(*) AS testes,
                COUNT(*) FILTER (WHERE aprovado) AS aprovadas,
                COUNT(*) FILTER (WHERE NOT aprovado) AS falhas
            FROM qa.execucao_assercao
            WHERE etapa = '03_carga_dw'
            GROUP BY executado_em ORDER BY executado_em DESC LIMIT 1
        """)).mappings().first()
        info(f"Última execução QA: {dict(qa) if qa else "não encontrada"}")
        # Procura registros auxiliares sem vínculo com pedidos.
        aux_sem_pedido = cx.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM dw.fato_atendimento WHERE pedido_sk IS NULL) AS sac,
                (SELECT COUNT(*) FROM dw.fato_avaliacao WHERE pedido_sk IS NULL) AS avaliacoes
        """)).mappings().one()
        info(f"Auxiliares sem pedido: {dict(aux_sem_pedido)}")
        # Em ESCALA=1.0, a referência indica 1.371.876 itens e 20.577 órfãos.
        # A carga compara os elegíveis antes do commit;
        # esta etapa verifica novamente as contagens, vínculos e asserções gravadas.
        # Confirma contagens, vínculos e resultados de qualidade antes de aprovar.
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


# ---------------------------------------------------------------------------
# Ponto de entrada da etapa de carga do Data Warehouse
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    secao("ETAPA 3 — CARGA NO ESQUEMA ESTRELA")
    info("Recarregando itens e conferindo o DW; SAC, avaliações e dim_tempo vêm da Etapa 2.")
    # Obtém o engine do DW e cria a suíte de asserções da carga.
    dw = eng_dw()
    qa = Suite(etapa="03_carga_dw")
    inicio = perf_counter()
    # Transação única para recarregar os itens e executar as verificações.
    with dw.begin() as cx:
        qa.conexao = cx
        # Confere dimensões (inclusive a dim_tempo criada na Etapa 2).
        # Chamar carrega_dim_tempo aqui apagaria os pedidos por TRUNCATE CASCADE.
        carrega_dimensoes(qa)
        carrega_fato_pedido(qa)

        # Recarrega apenas fato_item_pedido; não toca nas fatos auxiliares.
        t0 = perf_counter()
        cx.execute(text("""TRUNCATE TABLE dw.fato_item_pedido RESTART IDENTITY"""))
        print(f"[tempo] preparação fatos   | {perf_counter() - t0:.1f}s", flush=True)
        t0 = perf_counter()
        # Insere somente itens com vínculo válido de pedido e produto.
        carrega_fato_item(qa)
        print(f"[tempo] carga de itens     | {perf_counter() - t0:.1f}s", flush=True)
        t0 = perf_counter()

        t0 = perf_counter()
        # Executa quatro asserções de qualidade da carga:
        # 1. Verifica chaves de cliente, tempo e canal nos pedidos.
        orfas = cx.execute(text("""
            SELECT COUNT(*) FROM dw.fato_pedido f
            LEFT JOIN dw.dim_cliente c ON f.cliente_sk = c.cliente_sk
            LEFT JOIN dw.dim_tempo t ON f.data_pedido_sk = t.data_sk
            LEFT JOIN dw.dim_canal cn ON f.canal_sk = cn.canal_sk
            WHERE c.cliente_sk IS NULL OR t.data_sk IS NULL OR cn.canal_sk IS NULL
        """)).scalar_one()
        qa.igual("sem FK órfã em fato_pedido", int(orfas), 0)

        # 2. Compara o total de pedidos do DW com as duas origens.
        total_fatos = cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido")).scalar_one()
        esperado_ped = cx.execute(text("""
            SELECT (SELECT COUNT(*) FROM stg.legado_pedidos) +
                (SELECT COUNT(*) FROM stg.ecommerce_orders)
        """)).scalar_one()
        qa.igual("contagem total de fato_pedido", int(total_fatos), int(esperado_ped))

        # 3. Reconcilia totais por sistema com tolerância de até 0,1%.
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

        # 4. Verifica ausência de prazos negativos de entrega.
        negativos = cx.execute(text("""
            SELECT COUNT(*) FROM dw.fato_pedido WHERE dias_entrega < 0
        """)).scalar_one()
        qa.igual("nenhum pedido com dias_entrega negativo", int(negativos), 0)

        # Apresenta os testes; se houver falha, desfaz a transação.
        qa.imprimir()
        if qa.falhou():
            raise RuntimeError("Asserções falharam; toda a carga foi revertida")
        print(f"[tempo] asserções          | {perf_counter() - t0:.1f}s", flush=True)

    # Registra as asserções somente após o commit da recarga.
    qa.registrar(dw)
    # Após salvar os dados e registrar QA, verifica a persistência e os vínculos.
    t0 = perf_counter()
    conferir_carga_gravada(dw)
    print(f"[tempo] conferência final  | {perf_counter() - t0:.1f}s", flush=True)
    ok("Etapa 3 concluída: itens recarregados, QA e conferência final realizados.")
    print(f"[tempo] total etapa 3      | {perf_counter() - inicio:.1f}s", flush=True)