#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 3 — CARGA NO ESQUEMA ESTRELA (Data Warehouse Layer)

Objetivo:
    Complementar a carga do Data Warehouse (DW) com a tabela fato de itens de pedidos,
    preservando intactas as dimensões e as tabelas fato de pedidos, atendimentos do SAC
    e avaliações do marketplace que já foram construídas e conformadas na Etapa 2.

Política de Carga e Qualidade:
  · Os itens sem pedido vinculado (órfãos) permanecem retidos no staging (RDT-10);
  · A recarga da fato de itens e as validações ocorrem em uma única transação atômica
    (qualquer falha nas asserções reverte automaticamente todas as inserções via rollback);
  · As verificações finais englobam integridade referencial de chaves estrangeiras (FKs),
    preservação de cardinalidade, reconciliação financeira com tolerância de ±0,1%,
    validade dos prazos de entrega e auditoria pós-commit.

Comando de Execução:
    docker compose exec etl python 03_carga_dw.py
"""

from datetime import date, timedelta
from time import perf_counter
import pandas as pd
from sqlalchemy import text
from config import eng_dw
from qualidade.assercoes import Suite


# ---------------------------------------------------------------------------
# Funções Auxiliares de Interface e Log no Terminal
# ---------------------------------------------------------------------------
def secao(titulo):
    """Exibe um cabeçalho visual destacado para separar os blocos da etapa de carga no terminal."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Exibe uma mensagem informativa padronizada com o prefixo [INFO] e descarga imediata do buffer."""
    print(f"[INFO] {mensagem}", flush=True)


def ok(mensagem):
    """Exibe uma mensagem de sucesso padronizada com o prefixo [OK] e descarga imediata do buffer."""
    print(f"[OK] {mensagem}", flush=True)


# ---------------------------------------------------------------------------
# Geração e Carga da Dimensão de Tempo (Calendário Analítico)
# ---------------------------------------------------------------------------
def carrega_dim_tempo(engine, ini=date(2022, 12, 1), fim=date(2026, 3, 31)):
    """
    Gera e carrega a dimensão de calendário (dw.dim_tempo) para o intervalo especificado,
    extraindo atributos cronológicos como ano, trimestre, mês, dia da semana e indicador de fim de semana.
    """
    # Lista ordenada com os nomes dos meses em português para rotulagem analítica
    MESES = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    linhas = []
    d = ini

    # Itera dia a dia do início ao fim do período para construir os dicionários do calendário
    while d <= fim:
        linhas.append(dict(
            data_sk=d,
            ano=d.year,
            trimestre=(d.month - 1) // 3 + 1,
            mes=d.month,
            dia=d.day,
            dia_semana=d.isoweekday(),
            nome_mes=MESES[d.month - 1],
            eh_fim_semana=d.isoweekday() >= 6,
        ))
        d += timedelta(days=1)

    # Converte a lista de dicionários em DataFrame do pandas para persistência no banco
    df = pd.DataFrame(linhas)

    # Limpa a tabela dw.dim_tempo e insere os registros gerados na camada dimensional
    with engine.begin() as cx:
        cx.execute(text("TRUNCATE dw.dim_tempo CASCADE"))
    df.to_sql("dim_tempo", engine, schema="dw", if_exists="append", index=False)
    print(f"dim_tempo: {len(df)} datas")
    return len(df)


# ---------------------------------------------------------------------------
# Validação das Dimensões Conformadas na Etapa 2
# ---------------------------------------------------------------------------
def carrega_dimensoes(qa):
    """
    Confere a existência de registros em todas as tabelas dimensionais carregadas na Etapa 2,
    sem recriá-las para não alterar as chaves substitutas (surrogate keys) já vinculadas aos pedidos.
    """
    # Obtém a conexão ativa pertencente à transação única controlada pela suíte de qualidade
    cx = qa.conexao

    # Percorre e valida individualmente se cada dimensão do esquema estrela possui dados carregados
    for t in (
        "dim_cliente",
        "dim_produto",
        "dim_canal",
        "dim_status",
        "dim_tempo",
    ):
        # Executa contagem rápida na dimensão atual para verificar se foi devidamente populada
        n = cx.execute(text(f"SELECT COUNT(*) FROM dw.{t}")).scalar_one()
        if n == 0:
            raise ValueError(f"A dimensão dw.{t} está vazia. Execute a Etapa 2 primeiro.")
        ok(f"dw.{t}: {n} registros validados.")

    # Garante que a dimensão de produtos possui chaves naturais preenchidas para ambos os sistemas de origem
    for coluna in (
        "id_legado",
        "id_ecommerce",
    ):
        # Conta quantos produtos possuem o identificador da respectiva origem preenchido (não nulo)
        n = cx.execute(text(f"""
            SELECT COUNT(*)
            FROM dw.dim_produto
            WHERE {coluna} IS NOT NULL
        """)).scalar_one()
        if n == 0:
            raise ValueError(f"dw.dim_produto.{coluna} só tem NULL. Corrija a Etapa 2.")


# ---------------------------------------------------------------------------
# Validação de Volumetria da Tabela Fato de Pedidos
# ---------------------------------------------------------------------------
def carrega_fato_pedido(qa):
    """
    Valida se a tabela fato principal (dw.fato_pedido) construída na Etapa 2 está íntegra
    e se a sua contagem total corresponde exatamente à soma dos pedidos das duas fontes no staging.
    """
    # Obtém a conexão ativa pertencente à transação única controlada pela suíte de qualidade
    cx = qa.conexao

    # Consulta a quantidade total de pedidos atualmente armazenados na tabela fato do DW
    total = cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido")).scalar_one()

    # Soma a quantidade de pedidos brutos nas tabelas de staging do ERP legado e do e-commerce
    esperado = cx.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM stg.legado_pedidos) +
            (SELECT COUNT(*) FROM stg.ecommerce_orders)
    """)).scalar_one()

    # Interrompe a execução caso a tabela fato esteja vazia ou apresente divergência em relação às origens
    if total != esperado or total == 0:
        raise ValueError(f"dw.fato_pedido: {total} pedidos; fontes: {esperado}. Corrija a carga de pedidos antes de continuar.")
    ok(f"dw.fato_pedido: {total} pedidos já carregados.")


# ---------------------------------------------------------------------------
# Carga Efetiva da Tabela Fato de Itens de Pedido
# ---------------------------------------------------------------------------
def carrega_fato_item_pedido(engine, esperados):
    """
    Insere na tabela dw.fato_item_pedido os itens de ambas as origens que possuem vínculo
    válido de pedido e de produto no DW, reconciliando a contagem inserida com o total elegível.
    """
    # Utiliza a conexão ativa da transação única controlada pelo bloco principal
    cx = engine
        
    # Inicia a medição de tempo dedicada à inserção dos itens do ERP legado
    t0 = perf_counter()

    # Insere itens do ERP legado vinculando pedido_sk e produto_sk (INNER JOIN exclui órfãos da fato)
    # e substituindo descontos negativos (sentinelas) por zero no cálculo do valor líquido da linha
    cx.execute(text("""
        INSERT INTO dw.fato_item_pedido (pedido_sk, produto_sk, quantidade, valor_unitario, valor_desconto, valor_linha)
        SELECT 
            p.pedido_sk,
            pr.produto_sk,
            i."QTD" AS quantidade,
            i."VL_UNITARIO" AS valor_unitario,
            CASE WHEN i."VL_DESCONTO" < 0 THEN 0 ELSE i."VL_DESCONTO" END AS valor_desconto,
            (i."QTD"::numeric * i."VL_UNITARIO"::numeric)
                - CASE WHEN i."VL_DESCONTO" < 0 THEN 0 ELSE i."VL_DESCONTO" END AS valor_linha
        FROM stg.legado_itens_pedido i
        JOIN dw.fato_pedido p ON p.sistema_origem = 'LEGADO' AND p.id_origem = 'LEG-' || i."ID_PEDIDO"::text
        JOIN dw.dim_produto pr ON pr.id_legado = i."ID_PRODUTO";
    """))
    print(f"[tempo] itens legado        | {perf_counter() - t0:.1f}s", flush=True)

    # Inicia a medição de tempo dedicada à inserção dos itens do e-commerce
    t0 = perf_counter()

    # Insere itens do e-commerce vinculando pedido_sk e produto_sk (INNER JOIN exclui órfãos da fato)
    # e calculando o valor líquido da linha a partir da quantidade, preço unitário e desconto
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

    # Consulta o total de itens inseridos na tabela fato_item_pedido após as duas cargas
    inseridos = cx.execute(text("SELECT COUNT(*) FROM dw.fato_item_pedido")).scalar_one()

    # Garante que todos os itens elegíveis (com pedido válido) foram efetivamente inseridos
    if inseridos != esperados:
        raise ValueError(f"fato_item_pedido: {inseridos} inseridos, {esperados} elegíveis. Confira os IDs da dim_produto.")
    ok(f"dw.fato_item_pedido: {inseridos} itens elegíveis inseridos.")


# ---------------------------------------------------------------------------
# Apuração de Itens Órfãos e Elegíveis no Staging (RDT-10)
# ---------------------------------------------------------------------------
def carrega_fato_item(qa):
    """
    Identifica e contabiliza os itens órfãos (sem pedido correspondente na fato_pedido)
    que permanecerão retidos no staging, apurando o total exato de itens elegíveis para a fato.
    """
    # Obtém a conexão ativa pertencente à transação única controlada pela suíte de qualidade
    cx = qa.conexao
    esperados = 0

    # Itera sobre os parâmetros das duas fontes (separando apenas a lista/tupla mais externa)
    for sistema, origem, idcol, prefixo in (
        ("LEGADO", "legado_itens_pedido", '"ID_PEDIDO"', "LEG-"),
        ("ECOM", "ecommerce_order_item", "order_id", "ECOM-"),
    ):
        # Contabiliza no staging os itens órfãos cujo pedido de origem não existe em dw.fato_pedido
        orfaos = cx.execute(text(f"""
            SELECT COUNT(*) FROM stg.{origem} i
            LEFT JOIN dw.fato_pedido p
            ON p.sistema_origem = :sistema
            AND p.id_origem = :prefixo || i.{idcol}::text
            WHERE p.pedido_sk IS NULL
        """), {
            "sistema": sistema,
            "prefixo": prefixo,
        }).scalar_one()

        # Consulta o total bruto de itens existentes na tabela de staging da origem atual
        total = cx.execute(text(f"SELECT COUNT(*) FROM stg.{origem}")).scalar_one()

        # Acumula o número de itens elegíveis subtraindo os órfãos do total bruto da origem
        esperados += total - orfaos
        info(f"Itens órfãos ({sistema}) mantidos no staging: {orfaos}")

    # Executa a inserção dos itens elegíveis na tabela fato validando contra o total esperado
    carrega_fato_item_pedido(cx, esperados)


# ---------------------------------------------------------------------------
# Auditoria e Conferência Pós-Commit da Carga no DW
# ---------------------------------------------------------------------------
def conferir_carga_gravada(engine):
    """
    Abre uma nova conexão após o commit da transação para auditar as contagens definitivas
    das tabelas fato, verificar a ausência de registros órfãos nas fatos auxiliares e validar
    o registro de aprovação das quatro asserções de qualidade da Etapa 3.
    """
    # Abre conexão com o Data Warehouse para inspecionar os dados efetivamente gravados pós-commit
    with engine.connect() as cx:
        # Reconta todas as tabelas fato do DW e compara com as contagens originais do staging
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

        # Imprime no terminal o resumo detalhado das contagens de cada tabela auditada
        for k, v in dados.items():
            print(f"  - {k:<18}: {v}")

        # Consulta o registro mais recente da suíte de asserções de qualidade para a etapa '03_carga_dw'
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

        # Verifica se existem atendimentos do SAC ou avaliações sem pedido vinculado nas fatos auxiliares
        aux_sem_pedido = cx.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM dw.fato_atendimento WHERE pedido_sk IS NULL) AS sac,
                (SELECT COUNT(*) FROM dw.fato_avaliacao WHERE pedido_sk IS NULL) AS avaliacoes
        """)).mappings().one()
        info(f"Auxiliares sem pedido: {dict(aux_sem_pedido)}")

        # Avalia conjuntamente todas as condições de integridade, volumetria e aprovação das 4 asserções de QA
        ok = (
            dados['pedidos'] == dados['pedidos_origem'] > 0
            and dados['sac'] == dados['sac_origem'] > 0
            and dados['avaliacoes'] == dados['avaliacoes_origem'] > 0
            and dados['itens'] > 0
            and aux_sem_pedido['sac'] == 0
            and aux_sem_pedido['avaliacoes'] == 0
            and qa is not None and qa['testes'] == 4
            and qa['aprovadas'] == 4 and qa['falhas'] == 0
        )
        print("[OK] Última carga validada." if ok else "[FALHA] Encontradas divergências: verifique os dados e o QA.")
        if not ok:
            raise SystemExit(1)


# ---------------------------------------------------------------------------
# Orquestração Principal, Transação de Carga e Asserções de Qualidade (QA)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    secao("ETAPA 3 — CARGA NO ESQUEMA ESTRELA")
    info("Recarregando itens e conferindo o DW; SAC, avaliações e dim_tempo vêm da Etapa 2.")

    # Obtém a conexão com o Data Warehouse de destino e inicializa a suíte de asserções da Etapa 3
    dw = eng_dw()
    qa = Suite(etapa="03_carga_dw")
    inicio = perf_counter()

    # Inicia uma transação única para conferir estruturas, recarregar itens e validar asserções
    with dw.begin() as cx:
        # Vincula a conexão da transação ativa à instância da suíte de qualidade
        qa.conexao = cx

        # Confere a consistência das dimensões e da fato de pedidos populadas na Etapa 2
        carrega_dimensoes(qa)
        carrega_fato_pedido(qa)

        # Inicia a medição de tempo e limpa apenas a tabela dw.fato_item_pedido antes da recarga
        t0 = perf_counter()
        cx.execute(text("""TRUNCATE TABLE dw.fato_item_pedido RESTART IDENTITY"""))
        print(f"[tempo] preparação fatos   | {perf_counter() - t0:.1f}s", flush=True)

        # Inicia a medição de tempo e executa a apuração e carga dos itens elegíveis na fato
        t0 = perf_counter()
        carrega_fato_item(qa)
        print(f"[tempo] carga de itens     | {perf_counter() - t0:.1f}s", flush=True)
        t0 = perf_counter()

        # Inicia a medição de tempo dedicada à execução das quatro asserções de qualidade da carga
        t0 = perf_counter()

        # Asserção 1: Verifica integridade referencial garantindo ausência de FKs órfãs em dw.fato_pedido
        orfas = cx.execute(text("""
            SELECT COUNT(*) FROM dw.fato_pedido f
            LEFT JOIN dw.dim_cliente c ON f.cliente_sk = c.cliente_sk
            LEFT JOIN dw.dim_tempo t ON f.data_pedido_sk = t.data_sk
            LEFT JOIN dw.dim_canal cn ON f.canal_sk = cn.canal_sk
            WHERE c.cliente_sk IS NULL OR t.data_sk IS NULL OR cn.canal_sk IS NULL
        """)).scalar_one()
        qa.igual("sem FK órfã em fato_pedido", int(orfas), 0)

        # Asserção 2: Confere se a contagem total de pedidos na fato corresponde à soma das duas origens
        total_fatos = cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido")).scalar_one()
        esperado_ped = cx.execute(text("""
            SELECT (SELECT COUNT(*) FROM stg.legado_pedidos) +
                (SELECT COUNT(*) FROM stg.ecommerce_orders)
        """)).scalar_one()
        qa.igual("contagem total de fato_pedido", int(total_fatos), int(esperado_ped))

        # Asserção 3: Reconcilia os totais financeiros por sistema entre staging e DW com tolerância de ±0,1%
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

        # Asserção 4: Verifica ausência de pedidos com prazo de entrega (dias_entrega) negativo na fato
        negativos = cx.execute(text("""
            SELECT COUNT(*)
            FROM dw.fato_pedido
            WHERE dias_entrega < 0
        """)).scalar_one()
        qa.igual("nenhum pedido com dias_entrega negativo", int(negativos), 0)

        # Exibe o relatório de asserções no terminal; se houver falha, interrompe e reverte a transação (rollback)
        qa.imprimir()
        if qa.falhou():
            raise RuntimeError("Asserções falharam; toda a carga foi revertida")
        print(f"[tempo] asserções          | {perf_counter() - t0:.1f}s", flush=True)

    # Persiste os resultados das asserções no banco de dados somente após o commit da transação
    qa.registrar(dw)

    # Inicia a medição de tempo e executa a auditoria final pós-commit sobre os dados e vínculos gravados
    t0 = perf_counter()
    conferir_carga_gravada(dw)
    print(f"[tempo] conferência final  | {perf_counter() - t0:.1f}s", flush=True)
    ok("Etapa 3 concluída: itens recarregados, QA e conferência final realizados.")
    print(f"[tempo] total etapa 3      | {perf_counter() - inicio:.1f}s", flush=True)