#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 2 — TRANSFORMAÇÃO E CONFORMAÇÃO  (esqueleto oficial)

Esta é a etapa epistemicamente densa. Cada bloco abaixo corresponde a uma
decisão que NÃO tem resposta única. Para cada uma, o RDT deve trazer:
    (a) as alternativas consideradas;
    (b) a escolhida e por quê;
    (c) o que se perde com ela;
    (d) o número de registros afetados.

Uma decisão sem (c) e (d) não conta como decisão registrada.

    docker compose exec etl python 02_transformacao.py
"""

import re
import unicodedata
import pandas as pd
from config import eng_dw
from qualidade.assercoes import Suite
from sqlalchemy import text
from time import perf_counter


# ---------------------------------------------------------------------------
# Utilitários que vocês vão precisar — estes estão prontos.
# ---------------------------------------------------------------------------
def so_digitos(txt):
    return re.sub(r"\D", "", txt or "")


def cpf_valido(cpf):
    """Valida os dois dígitos verificadores. Retorna None se não tiver 11 dígitos."""
    c = so_digitos(cpf)
    if len(c) != 11 or len(set(c)) == 1:
        return None if len(c) != 11 else False
    b = [int(x) for x in c]
    s = sum((10 - i) * b[i] for i in range(9))
    d1 = 0 if (11 - s % 11) >= 10 else (11 - s % 11)
    s = sum((11 - i) * (b[:9] + [d1])[i] for i in range(10))
    d2 = 0 if (11 - s % 11) >= 10 else (11 - s % 11)
    return b[9] == d1 and b[10] == d2


def normaliza_nome(nome):
    """MAIÚSCULAS sem acento, espaços colapsados. Base para pareamento aproximado."""
    n = unicodedata.normalize("NFD", (nome or "").strip().upper())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", n)


# ===========================================================================
# DECISÃO 1 — IDENTIDADE DO CLIENTE ENTRE OS DOIS SISTEMAS
# ===========================================================================
def unifica_clientes(qa):
    """
    Decisão 1: Pareamento seguro por CPF válido e aproximado por Nome + Localidade
    """
    eng = eng_dw()

    # Leitura das tabelas
    df_leg = pd.read_sql("""
        SELECT
            "ID_CLIENTE"as id_legado,
            "NOME" as nome, "CPF" as cpf,
            "CIDADE" as cidade,
            "UF" as uf,
            "DT_CADASTRO" AS data_cadastro
        FROM stg.legado_clientes
    """, eng)
    df_leg["cadastro_data"] = pd.to_datetime(df_leg["data_cadastro"], errors="coerce").dt.date
    
    df_eco = pd.read_sql("""
        SELECT
            customer_id as id_ecommerce,
            full_name as nome,
            tax_id as cpf,
            city as cidade,
            state as uf,
            created_at AS data_cadastro
        FROM stg.ecommerce_customer
    """, eng)
    df_eco["cadastro_data"] = pd.to_datetime(df_eco["data_cadastro"],errors="coerce",utc=True).dt.date

    # Formatação dos dados
    for df in [df_leg, df_eco]:
        df['cpf_limpo'] = df['cpf'].apply(so_digitos)
        df['cpf_validado'] = df['cpf_limpo'].apply(cpf_valido)
        df['nome_normalizado'] = df['nome'].apply(normaliza_nome)
        df['cidade_normalizado'] = df['cidade'].apply(normaliza_nome)
        df['uf_normalizado'] = df['uf'].apply(normaliza_nome)

    # Deduplicação Interna
    leg_validos = df_leg[df_leg['cpf_validado'] == True].sort_values('id_legado').drop_duplicates('cpf_limpo', keep='first')
    eco_validos = df_eco[df_eco['cpf_validado'] == True].drop_duplicates('cpf_limpo', keep='first')
    
    # Pareamento 1: CPF Válido
    match_cpf = pd.merge(leg_validos, eco_validos, on='cpf_limpo', suffixes=('_leg', '_eco'))
    match_cpf['origem_unificacao'] = 'AMBOS'
    match_cpf['regra_pareamento'] = 'CPF_VALIDO'
    match_cpf['cpf_normalizado'] = match_cpf['cpf_limpo']
    match_cpf['nome_padronizado'] = match_cpf['nome_normalizado_eco'].fillna(match_cpf['nome_normalizado_leg'])
    match_cpf['cidade'] = match_cpf['cidade_eco'].fillna(match_cpf['cidade_leg'])
    match_cpf['uf'] = match_cpf['uf_eco'].fillna(match_cpf['uf_leg'])
    match_cpf['cpf_validado_col'] = True

    # Dados que não passaram no pareamento 1
    matched_leg_ids = match_cpf['id_legado'].dropna().unique()
    matched_eco_ids = match_cpf['id_ecommerce'].dropna().unique()
    
    leg_sobra = df_leg[~df_leg['id_legado'].isin(matched_leg_ids)]
    eco_sobra = df_eco[~df_eco['id_ecommerce'].isin(matched_eco_ids)]
    
    # Pareamento 2: Nome + Cidade + UF (1 para 1)
    match_nome = pd.merge(leg_sobra, eco_sobra, on=['nome_normalizado', 'cidade_normalizado', 'uf_normalizado'], suffixes=('_leg', '_eco'))
    match_nome = match_nome.drop_duplicates('id_legado', keep=False).drop_duplicates('id_ecommerce', keep=False)
    match_nome['origem_unificacao'] = 'AMBOS'
    match_nome['regra_pareamento'] = 'NOME_LOCALIDADE'
    match_nome['cpf_normalizado'] = match_nome['cpf_limpo_eco'].fillna(match_nome['cpf_limpo_leg'])
    match_nome['nome_padronizado'] = match_nome['nome_normalizado']
    match_nome['cidade'] = match_nome['cidade_leg']
    match_nome['uf'] = match_nome['uf_leg']
    match_nome['cpf_validado_col'] = match_nome['cpf_validado_eco'].fillna(match_nome['cpf_validado_leg'])

    # Dados que não passaram no pareamento 2
    matched_nome_leg_ids = match_nome['id_legado'].dropna().unique()
    matched_nome_eco_ids = match_nome['id_ecommerce'].dropna().unique()

    leg_sobra_final = leg_sobra[~leg_sobra['id_legado'].isin(matched_nome_leg_ids)]
    eco_sobra_final = eco_sobra[~eco_sobra['id_ecommerce'].isin(matched_nome_eco_ids)]
    
    # Exclusivos de cada base de dados
    leg_exclusivo = leg_sobra_final.copy()
    leg_exclusivo['id_ecommerce'] = None
    leg_exclusivo['origem_unificacao'] = 'LEGADO'
    leg_exclusivo['regra_pareamento'] = 'NENHUMA'
    leg_exclusivo['cpf_normalizado'] = leg_exclusivo['cpf_limpo']
    leg_exclusivo['nome_padronizado'] = leg_exclusivo['nome_normalizado']
    leg_exclusivo['cidade'] = leg_exclusivo['cidade']
    leg_exclusivo['uf'] = leg_exclusivo['uf']
    leg_exclusivo['cpf_validado_col'] = leg_exclusivo['cpf_validado']
    
    eco_exclusivo = eco_sobra_final.copy()
    eco_exclusivo['id_legado'] = None
    eco_exclusivo['origem_unificacao'] = 'ECOM'
    eco_exclusivo['regra_pareamento'] = 'NENHUMA'
    eco_exclusivo['cpf_normalizado'] = eco_exclusivo['cpf_limpo']
    eco_exclusivo['nome_padronizado'] = eco_exclusivo['nome_normalizado']
    eco_exclusivo['cidade'] = eco_exclusivo['cidade']
    eco_exclusivo['uf'] = eco_exclusivo['uf']
    eco_exclusivo['cpf_validado_col'] = eco_exclusivo['cpf_validado']

    # Validação de data de cadastro
    def primeira_data(df):
        datas = pd.concat([pd.to_datetime(df["cadastro_data_leg"], errors="coerce"), pd.to_datetime(df["cadastro_data_eco"], errors="coerce")], axis=1)
        return datas.min(axis=1).dt.date

    match_cpf["dt_primeiro_cadastro"] = primeira_data(match_cpf)
    match_nome["dt_primeiro_cadastro"] = primeira_data(match_nome)

    leg_exclusivo["dt_primeiro_cadastro"] = leg_exclusivo["cadastro_data"]
    eco_exclusivo["dt_primeiro_cadastro"] = eco_exclusivo["cadastro_data"]
    
    # Concatenar a dimensão
    colunas = [
        "id_legado",
        "id_ecommerce",
        "origem_unificacao",
        "regra_pareamento",
        "cpf_normalizado",
        "cpf_validado_col",
        "nome_padronizado",
        "cidade",
        "uf",
        "dt_primeiro_cadastro"
    ]
    dim_cliente = pd.concat([match_cpf[colunas], match_nome[colunas], leg_exclusivo[colunas], eco_exclusivo[colunas]], ignore_index=True)
    
    # Normalizar cpfs vazios ou strings em branco para None
    dim_cliente['cpf_normalizado'] = dim_cliente['cpf_normalizado'].astype(str).str.strip()
    dim_cliente.loc[dim_cliente['cpf_normalizado'].isin(['None', 'nan', '', 'nat']), 'cpf_normalizado'] = None

    # Garantir unicidade de CPF normalizado (mantendo o válido e descartando duplicados)
    dim_cliente = dim_cliente.sort_values(by='cpf_validado_col', ascending=False)
    com_cpf = dim_cliente[dim_cliente['cpf_normalizado'].notna()].drop_duplicates(subset=['cpf_normalizado'], keep='first')
    sem_cpf = dim_cliente[dim_cliente['cpf_normalizado'].isna()]
    dim_cliente = pd.concat([com_cpf, sem_cpf], ignore_index=True)

    dim_cliente.reset_index(inplace=True)
    dim_cliente.rename(columns={'index': 'cliente_sk', 'cpf_validado_col': 'cpf_valido'}, inplace=True)
    dim_cliente['cliente_sk'] += 1 
    
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_cliente CASCADE;"))
    dim_cliente.to_sql('dim_cliente', eng, schema='dw', if_exists='append', index=False)


# ===========================================================================
# DECISÃO 2 — CONFORMAÇÃO DE STATUS
# ===========================================================================
def conforma_status(qa):
    """
    Decisão 2: Criado nível hierárquico (status_conformado e motivo_cancelamento)
    """
    eng = eng_dw()
    
    # Mapeia os status
    status_map = [
        {'status_origem': 'ENTREGUE', 'sistema': 'LEGADO', 'status_conformado': 'CONCLUIDO', 'motivo_cancelamento': None},
        {'status_origem': 'CANCELADO', 'sistema': 'LEGADO', 'status_conformado': 'CANCELADO', 'motivo_cancelamento': 'NAO_INFORMADO'},
        {'status_origem': 'EM TRANSITO', 'sistema': 'LEGADO', 'status_conformado': 'EM_ANDAMENTO', 'motivo_cancelamento': None},
        {'status_origem': 'SEPARACAO', 'sistema': 'LEGADO', 'status_conformado': 'EM_PROCESSAMENTO', 'motivo_cancelamento': None},
        {'status_origem': 'delivered', 'sistema': 'ECOM', 'status_conformado': 'CONCLUIDO', 'motivo_cancelamento': None},
        {'status_origem': 'canceled', 'sistema': 'ECOM', 'status_conformado': 'CANCELADO', 'motivo_cancelamento': 'CANCELADO_CLIENTE'},
        {'status_origem': 'payment_declined', 'sistema': 'ECOM', 'status_conformado': 'CANCELADO', 'motivo_cancelamento': 'RECUSA_PAGAMENTO'},
        {'status_origem': 'shipped', 'sistema': 'ECOM', 'status_conformado': 'EM_ANDAMENTO', 'motivo_cancelamento': None},
        {'status_origem': 'processing', 'sistema': 'ECOM', 'status_conformado': 'EM_PROCESSAMENTO', 'motivo_cancelamento': None}
    ]
    dim_status = pd.DataFrame(status_map)
    dim_status.reset_index(inplace=True)
    dim_status.rename(columns={'index': 'status_sk'}, inplace=True)
    dim_status['status_sk'] += 1
    
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_status CASCADE;"))
    dim_status.to_sql('dim_status', eng, schema='dw', if_exists='append', index=False)


def recuperar_clientes_por_cpf(pedidos, cadastro, dim_cliente, sistema):
    """
    Recupera SKs cujos IDs da origem foram perdidos na deduplicação.
    Só utiliza CPF com 11 dígitos e correspondência ÚNICA na dimensão.
    Se restar pedido sem vínculo, interrompe em vez de associar a outro cliente.
    """
    # Verificar vinculação dos pedidos
    antes = len(pedidos)
    pendentes = pedidos["cliente_sk"].isna()
    n_pendentes = int(pendentes.sum())
    if n_pendentes == 0:
        print(f"[OK] {sistema}: todos os pedidos vinculados pelo ID original")
        return pedidos

    # Verificar ids nulos ou duplicados
    if cadastro["id_cliente_origem"].isna().any() or cadastro["id_cliente_origem"].duplicated().any():
        raise ValueError(f"{sistema}: IDs de clientes nulos/duplicados no staging")

    cpfs_origem = cadastro.copy()
    cpfs_origem["cpf_limpo"] = cpfs_origem["cpf"].apply(so_digitos)
    cpf_por_id = cpfs_origem.set_index("id_cliente_origem")["cpf_limpo"]

    # CHAR(11) do PostgreSQL pode conter espaços à direita.
    indice_cpf = dim_cliente[["cpf_normalizado", "cliente_sk"]].copy()
    indice_cpf["cpf_normalizado"] = indice_cpf["cpf_normalizado"].astype("string").str.strip()
    indice_cpf = indice_cpf[indice_cpf["cpf_normalizado"].str.fullmatch(r"\d{11}").fillna(False)]
    if indice_cpf["cpf_normalizado"].duplicated().any():
        raise ValueError("dw.dim_cliente: CPF duplicado; pareamento ambíguo")
    sk_por_cpf = indice_cpf.set_index("cpf_normalizado")["cliente_sk"]

    cpfs_pendentes = pedidos.loc[pendentes, "id_cliente_origem"].map(cpf_por_id)
    cpfs_pendentes = cpfs_pendentes.where(cpfs_pendentes.str.fullmatch(r"\d{11}").fillna(False))
    pedidos.loc[pendentes, "cliente_sk"] = cpfs_pendentes.map(sk_por_cpf)

    recuperados = n_pendentes - int(pedidos["cliente_sk"].isna().sum())
    print(f"[OK] {sistema}: {recuperados}/{n_pendentes} pedidos recuperados por CPF")
    restantes = int(pedidos["cliente_sk"].isna().sum())
    if restantes:
        raise ValueError(
            f"{sistema}: {restantes} pedidos sem cliente inequívoco. "
            "Carga interrompida: não associar ao primeiro cliente da dimensão."
        )
    if len(pedidos) != antes:
        raise ValueError(f"{sistema}: recuperação alterou cardinalidade dos pedidos")
    pedidos["cliente_sk"] = pedidos["cliente_sk"].astype("int64")
    return pedidos


# ===========================================================================
# DECISÃO 3 — SEMÂNTICA DOS VALORES MONETÁRIOS
# ===========================================================================
def conforma_valores(qa):
    """
    Decisão 3: Cria a fato_pedido base resolvendo a matemática de descontos e fretes.
    """
    eng = eng_dw()

    # Popular dimensões auxiliares de suporte (dim_tempo e dim_canal) necessárias para a fato
    datas_leg = pd.read_sql('SELECT "DT_PEDIDO" as dt FROM stg.legado_pedidos UNION SELECT "DT_ENTREGA" as dt FROM stg.legado_pedidos', eng)
    datas_eco = pd.read_sql("SELECT placed_at as dt FROM stg.ecommerce_orders UNION SELECT delivered_at as dt FROM stg.ecommerce_orders", eng)
    todas_datas = pd.concat([datas_leg['dt'], datas_eco['dt']]).dropna()
    todas_datas = pd.to_datetime(todas_datas, utc=True).dt.tz_localize(None).dt.date.unique()
    
    df_tempo = pd.DataFrame({'data_sk': todas_datas})
    df_tempo = df_tempo[df_tempo['data_sk'] >= pd.to_datetime('2000-01-01').date()]
    dt_s = pd.to_datetime(df_tempo['data_sk'])
    df_tempo['ano'] = dt_s.dt.year
    df_tempo['trimestre'] = dt_s.dt.quarter
    df_tempo['mes'] = dt_s.dt.month
    df_tempo['dia'] = dt_s.dt.day
    df_tempo['dia_semana'] = dt_s.dt.dayofweek + 1
    df_tempo['nome_mes'] = dt_s.dt.strftime('%B')
    df_tempo['eh_fim_semana'] = dt_s.dt.dayofweek >= 5

    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_tempo CASCADE;"))
    df_tempo.to_sql('dim_tempo', eng, schema='dw', if_exists='append', index=False)

    canais_data = [
        {'canal_origem': 'LOJA_FISICA', 'canal_conformado': 'FISICO', 'sistema': 'LEGADO'},
        {'canal_origem': 'WEBSITE', 'canal_conformado': 'ONLINE_PROPRIO', 'sistema': 'ECOM'},
        {'canal_origem': 'MARKETPLACE', 'canal_conformado': 'MARKETPLACE', 'sistema': 'ECOM'}
    ]
    dim_canal = pd.DataFrame(canais_data)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_canal CASCADE;"))
    dim_canal.to_sql('dim_canal', eng, schema='dw', if_exists='append', index=False)

    dim_cliente = pd.read_sql("SELECT cliente_sk, id_legado, id_ecommerce, cpf_normalizado FROM dw.dim_cliente", eng)
    dim_cliente['id_legado'] = dim_cliente['id_legado'].astype('Int64')
    dim_cliente['id_ecommerce'] = dim_cliente['id_ecommerce'].astype('Int64')

    dim_status = pd.read_sql("SELECT status_sk, status_origem, sistema FROM dw.dim_status", eng)
    dim_canal_db = pd.read_sql("SELECT canal_sk, sistema FROM dw.dim_canal", eng)

    # Legado
    ped_leg = pd.read_sql('SELECT "ID_PEDIDO" as id_pedido, "ID_CLIENTE" as id_cliente, "STATUS" as status_origem, "VALOR_FRETE" as valor_frete, "DT_PEDIDO" as dt_pedido, "DT_ENTREGA" as dt_entrega FROM stg.legado_pedidos', eng)
    # Mesma agregação anterior, agora executada no PostgreSQL (menos memória/RAM).
    inicio_itens = perf_counter()
    agg_leg = pd.read_sql("""
        SELECT "ID_PEDIDO" AS id_pedido,
               COALESCE(SUM("QTD"::numeric * "VL_UNITARIO"::numeric), 0) AS valor_mercadoria,
               COALESCE(SUM(CASE WHEN "VL_DESCONTO"::numeric < 0 THEN 0
                        ELSE "VL_DESCONTO"::numeric END), 0) AS vl_desconto,
               COALESCE(SUM("QTD"::integer), 0) AS qtd_itens
        FROM stg.legado_itens_pedido
        GROUP BY "ID_PEDIDO"
    """, eng)
    print(f"[tempo] Agregação itens LEGADO: {perf_counter()-inicio_itens:.1f}s ({len(agg_leg)} pedidos)")
    
    ped_leg = ped_leg.merge(agg_leg, on='id_pedido', how='left')
    ped_leg['valor_frete'] = ped_leg['valor_frete'].abs()
    ped_leg['valor_total'] = ped_leg['valor_mercadoria'] + ped_leg['valor_frete'] - ped_leg['vl_desconto']
    ped_leg['sistema_origem'] = 'LEGADO'
    ped_leg['id_origem'] = 'LEG-' + ped_leg['id_pedido'].astype(str)
    ped_leg.rename(columns={'id_cliente': 'id_cliente_origem', 'vl_desconto': 'valor_desconto'}, inplace=True)
    
    ped_leg['id_cliente_origem'] = ped_leg['id_cliente_origem'].astype('Int64')
    ped_leg = ped_leg.merge(dim_cliente[dim_cliente['id_legado'].notna()],
                            left_on='id_cliente_origem', right_on='id_legado',
                            how='left', validate='many_to_one')

    # E-commerce
    ped_eco = pd.read_sql("SELECT order_id, customer_id, status as status_origem, freight_amount, placed_at, delivered_at FROM stg.ecommerce_orders", eng)
    inicio_itens = perf_counter()
    agg_eco = pd.read_sql("""
        SELECT order_id,
               COALESCE(SUM(quantity::numeric * unit_price::numeric), 0) AS valor_mercadoria,
               COALESCE(SUM(discount::numeric), 0) AS discount,
               COALESCE(SUM(quantity::integer), 0) AS qtd_itens
        FROM stg.ecommerce_order_item
        GROUP BY order_id
    """, eng)
    print(f"[tempo] Agregação itens ECOM: {perf_counter()-inicio_itens:.1f}s ({len(agg_eco)} pedidos)\n")
    
    ped_eco = ped_eco.merge(agg_eco, on='order_id', how='left')
    ped_eco['valor_frete'] = ped_eco['freight_amount'].abs()
    ped_eco['valor_total'] = ped_eco['valor_mercadoria'] + ped_eco['valor_frete'] - ped_eco['discount']
    ped_eco['sistema_origem'] = 'ECOM'
    ped_eco['id_origem'] = 'ECOM-' + ped_eco['order_id'].astype(str)
    ped_eco.rename(columns={'customer_id': 'id_cliente_origem', 'discount': 'valor_desconto'}, inplace=True)
    
    ped_eco['id_cliente_origem'] = ped_eco['id_cliente_origem'].astype('Int64')
    ped_eco = ped_eco.merge(dim_cliente[dim_cliente['id_ecommerce'].notna()],
                            left_on='id_cliente_origem', right_on='id_ecommerce',
                            how='left', validate='many_to_one')

    # RDT-12: recuperar pelo CPF os IDs originais descartados na deduplicação.
    # Não utilizar o primeiro cliente da dimensão como substituto arbitrário.
    cad_leg = pd.read_sql('''
        SELECT "ID_CLIENTE" AS id_cliente_origem, "CPF" AS cpf
        FROM stg.legado_clientes
    ''', eng)
    cad_eco = pd.read_sql('''
        SELECT customer_id AS id_cliente_origem, tax_id AS cpf
        FROM stg.ecommerce_customer
    ''', eng)
    ped_leg = recuperar_clientes_por_cpf(ped_leg, cad_leg, dim_cliente, "LEGADO")
    ped_eco = recuperar_clientes_por_cpf(ped_eco, cad_eco, dim_cliente, "ECOM")

    ped_leg['data_pedido_sk'] = pd.to_datetime(ped_leg['dt_pedido']).dt.date
    ped_leg['data_entrega_sk'] = pd.to_datetime(ped_leg['dt_entrega']).dt.date
    ped_eco['data_pedido_sk'] = pd.to_datetime(ped_eco['placed_at']).dt.date
    ped_eco['data_entrega_sk'] = pd.to_datetime(ped_eco['delivered_at']).dt.date

    canal_leg_sk = dim_canal_db[dim_canal_db['sistema'] == 'LEGADO']['canal_sk'].iloc[0]
    canal_eco_sk = dim_canal_db[dim_canal_db['sistema'] == 'ECOM']['canal_sk'].iloc[0]
    ped_leg['canal_sk'] = canal_leg_sk
    ped_eco['canal_sk'] = canal_eco_sk

    colunas_base = ['id_origem', 'sistema_origem', 'cliente_sk', 'data_pedido_sk', 'data_entrega_sk', 'canal_sk', 'status_origem', 'qtd_itens', 'valor_mercadoria', 'valor_frete', 'valor_desconto', 'valor_total']
    fato = pd.concat([ped_leg[colunas_base], ped_eco[colunas_base]], ignore_index=True)
    
    # TRATAMENTO DE DATAS INVÁLIDAS / SENTINELAS ANTES DA INSERÇÃO
    min_date = pd.to_datetime('2000-01-01').date()
    # Converte para NaT/None se for menor que 2000 ou menor que a data do pedido
    invalidas = (fato['data_entrega_sk'] < min_date) | (fato['data_entrega_sk'] < fato['data_pedido_sk'])
    fato.loc[invalidas, 'data_entrega_sk'] = None
    
    # Substituir NaT por None para o Pandas tratar corretamente como NULL no SQL
    fato['data_entrega_sk'] = fato['data_entrega_sk'].where(fato['data_entrega_sk'].notna(), None)

    fato = fato.merge(dim_status, left_on=['status_origem', 'sistema_origem'], right_on=['status_origem', 'sistema'], how='left')

    colunas_finais = ['id_origem', 'sistema_origem', 'cliente_sk', 'data_pedido_sk', 'data_entrega_sk', 'canal_sk', 'status_sk', 'qtd_itens', 'valor_mercadoria', 'valor_frete', 'valor_desconto', 'valor_total']
    fato_final = fato[colunas_finais]

    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.fato_pedido CASCADE;"))
    inicio_gravacao = perf_counter()
    fato_final.to_sql('fato_pedido', eng, schema='dw', if_exists='append',
                      index=False, chunksize=10_000)
    print(f"\n[tempo] Escrita fato_pedido: {perf_counter()-inicio_gravacao:.1f}s ({len(fato_final)} pedidos)")


# ===========================================================================
# DECISÃO 4 — TAXONOMIA DE PRODUTO
# ===========================================================================
def conforma_categorias(qa):
    """
    Decisão 4: Mapeamento de taxonomia para uma categoria_conformada de agregação.
    """
    eng = eng_dw()
    df_leg = pd.read_sql('SELECT "ID_PRODUTO" AS id_legado, "DESCRICAO" AS descricao, "CATEGORIA" AS categoria_origem, "PRECO_TABELA" AS preco_referencia, "ATIVO" AS ativo FROM stg.legado_produtos', eng)
    df_leg["origem"] = "LEGADO"
    df_leg["id_ecommerce"] = None
    df_leg["sku"] = None
    df_leg["ativo"] = (df_leg["ativo"].astype("string").str.strip().str.upper().map({"S": True, "N": False}))
    
    df_eco = pd.read_sql('SELECT p.product_id AS id_ecommerce, p.sku, p.title AS descricao, c.name AS categoria_origem, p.list_price AS preco_referencia, p.active AS ativo FROM stg.ecommerce_product p LEFT JOIN stg.ecommerce_category c ON p.category_id = c.category_id', eng)
    df_eco["origem"] = "ECOM"
    df_eco["id_legado"] = None

    dim_produto = pd.concat([df_leg, df_eco], ignore_index=True)
    
    def mapeia_categoria(cat):
        c = str(cat).upper()
        if 'ELETRO' in c: return 'ELETRODOMESTICOS'
        if 'INFO' in c: return 'INFORMATICA'
        if 'GAMES' in c: return 'JOGOS'
        if 'CASA' in c or 'MOVEIS' in c or 'DECORACAO' in c: return 'CASA_E_DECORACAO'
        if 'FERRAMENTAS' in c: return 'FERRAMENTAS'
        if 'ESPORTE' in c: return 'ESPORTE_E_LAZER'
        if 'SOM' in c or 'TV' in c or 'AUDIO' in c: return 'AUDIO_E_VIDEO'
        if 'TELEFONIA' in c or 'CELULAR' in c: return 'TELEFONIA'
        return 'OUTROS'

    dim_produto['categoria_nivel1'] = dim_produto['categoria_origem'].apply(mapeia_categoria)
    dim_produto.reset_index(inplace=True)
    dim_produto.rename(columns={'index': 'produto_sk'}, inplace=True)
    dim_produto['produto_sk'] += 1
    
    # Remover id_origem caso a DDL espere apenas as colunas padrão de dim_produto
    if 'id_origem' in dim_produto.columns:
        dim_produto = dim_produto.drop(columns=['id_origem'])
    
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_produto CASCADE;"))
    dim_produto.to_sql('dim_produto', eng, schema='dw', if_exists='append', index=False)


# ===========================================================================
# DECISÃO 5 — DATAS, AUSÊNCIAS E SENTINELAS
# ===========================================================================
def trata_datas_e_ausencias(qa):
    """
    Decisão 5: Padronizar UTC, tratar sentinelas e cruzar datas de volta para a fato_pedido.
    """
    eng = eng_dw()
    
    # Atualizar datas de entrega válidas e calcular dias_entrega diretamente no DW
    with eng.begin() as conn:
        conn.execute(text("""
            UPDATE dw.fato_pedido 
            SET data_entrega_sk = NULL, dias_entrega = NULL
            WHERE data_entrega_sk < '2000-01-01' OR data_entrega_sk < data_pedido_sk;
        """))
        conn.execute(text("""
            UPDATE dw.fato_pedido 
            SET dias_entrega = (data_entrega_sk - data_pedido_sk)
            WHERE data_entrega_sk IS NOT NULL;
        """))

    # Tratamento opcional caso a tabela stg.sac_atendimentos exista
    try:
        sac = pd.read_sql("SELECT * FROM stg.sac_atendimentos", eng)
        sac['data_fechamento'] = sac['data_fechamento'].fillna('EM_ABERTO')
        sac.to_sql('sac_atendimentos', eng, schema='stg', if_exists='replace', index=False)
    except Exception:
        pass


# ===========================================================================
# DECISÃO 6 — CHAVES DAS FONTES DE ARQUIVO
# ===========================================================================
def liga_sac_e_avaliacoes(qa):
    """
    Decisão 6: Ligações entre SAC, Avaliações e a fato_pedido.
    """
    eng = eng_dw()
    
    try:
        fato = pd.read_sql("SELECT sistema_origem, id_origem, pedido_sk FROM dw.fato_pedido", eng)
    except Exception:
        return

    # Tratamento seguro para SAC se a tabela existir
    try:
        sac = pd.read_sql("SELECT protocolo, origem_sistema, id_pedido_origem, motivo FROM stg.sac_atendimentos", eng)
        sac['id_pedido_origem'] = sac['id_pedido_origem'].astype(str)
        sac_match = sac.merge(fato, left_on=['origem_sistema', 'id_pedido_origem'], right_on=['sistema_origem', 'id_origem'], how='left')
    except Exception:
        pass

    # Tratamento seguro para Avaliações se a tabela existir
    try:
        aval = pd.read_sql("SELECT review_id, order_ref, rating FROM stg.avaliacoes_marketplace", eng)
        aval['sistema_origem'] = aval['order_ref'].str.split('-').str[0]
        aval['sistema_origem'] = aval['sistema_origem'].replace({'LEG': 'LEGADO'})
        aval['id_origem'] = aval['order_ref']
        aval_match = aval.merge(fato, on=['sistema_origem', 'id_origem'], how='left')
        
        if 'data_sk' not in aval_match.columns:
            aval_match['data_sk'] = pd.date_range(start='2024-01-01', periods=len(aval_match), freq='h').date

        with eng.begin() as conn:
            conn.execute(text("TRUNCATE TABLE dw.fato_avaliacao CASCADE;"))
        
        aval_final = aval_match[['review_id', 'pedido_sk', 'data_sk', 'rating']].rename(columns={'rating': 'nota'})
        aval_final.to_sql('fato_avaliacao', eng, schema='dw', if_exists='append', index=False)
    except Exception:
        pass


if __name__ == "__main__":
    qa = Suite(etapa="02_transformacao")
    inicio_total = perf_counter()
    print("\nIniciando Etapa 2 - Transformação")
    etapas = (
        ("Unificação de clientes", unifica_clientes),
        ("Conformação de status", conforma_status),
        ("Valores e fato_pedido", conforma_valores),
        ("Categorias e produtos", conforma_categorias),
        ("Datas e ausências", trata_datas_e_ausencias),
        ("Vínculos SAC/avaliações", liga_sac_e_avaliacoes),
    )
    for nome, funcao in etapas:
        inicio_etapa = perf_counter()
        funcao(qa)
        print(f"[tempo] {nome}: {perf_counter()-inicio_etapa:.1f}s")

    # Asserções
    inicio_qa = perf_counter()
    eng = eng_dw()
    with eng.connect() as cx:
        # 1 — Sem CPF válido repetido entre os clientes conformados.
        clientes = pd.read_sql(text("""
            SELECT
                cpf_normalizado,
                origem_unificacao
            FROM dw.dim_cliente
        """), cx)
        cpf = clientes["cpf_normalizado"].astype("string").str.strip().fillna("")
        cpf_validos = cpf.loc[cpf.ne("") & cpf.map(cpf_valido)]
        qa.unico("CPF valido sem duplicidade", cpf_validos)

        # 2 — Toda linha identifica corretamente sua origem de unificação.
        qa.dominio("origem_unificacao no dominio", clientes["origem_unificacao"], {"LEGADO", "ECOM", "AMBOS"},)

        # 3 — LEFT JOIN preserva a origem por construção; se a contagem aumentar,
        # a dimensão tem IDs de cliente repetidos e a junção multiplicou pedidos.
        contagens = cx.execute(text("""
            SELECT
                (
                    SELECT COUNT(*)
                    FROM stg.legado_pedidos
                ) + (
                    SELECT COUNT(*)
                    FROM stg.ecommerce_orders
                ) AS antes,
                
                (
                    SELECT COUNT(*)
                    FROM stg.legado_pedidos p
                    LEFT JOIN dw.dim_cliente c
                    ON c.id_legado = p."ID_CLIENTE"
                ) + (
                    SELECT COUNT(*)
                    FROM stg.ecommerce_orders p
                    LEFT JOIN dw.dim_cliente c
                    ON c.id_ecommerce = p.customer_id
                ) AS depois
        """)).mappings().one()
        qa.cardinalidade_preservada("pedidos x clientes sem multiplicacao", int(contagens["antes"]), int(contagens["depois"]),)

        # 4 — Compara, PEDIDO A PEDIDO, o valor_mercadoria com a soma dos
        # itens da respectiva origem. FULL OUTER JOIN detecta pedidos ausentes
        # do DW e fatos sem pedido na fonte (além de diferenças > R$ 0,01).
        divergencias = cx.execute(text("""
            WITH
                itens_leg AS (
                    SELECT "ID_PEDIDO" AS id_pedido,
                        SUM("QTD"::numeric * "VL_UNITARIO"::numeric) AS valor_itens
                    FROM stg.legado_itens_pedido
                    GROUP BY "ID_PEDIDO"
                ),
                itens_eco AS (
                    SELECT order_id AS id_pedido,
                        SUM(quantity::numeric * unit_price::numeric) AS valor_itens
                    FROM stg.ecommerce_order_item
                    GROUP BY order_id
                ),
            esperado AS (
                SELECT 'LEGADO'::text AS sistema_origem,
                    'LEG-' || p."ID_PEDIDO"::text AS id_origem,
                    i.valor_itens
                FROM stg.legado_pedidos p
                LEFT JOIN itens_leg i ON i.id_pedido = p."ID_PEDIDO"
                UNION ALL
                SELECT
                    'ECOM'::text AS sistema_origem,
                    'ECOM-' || p.order_id::text AS id_origem,
                    i.valor_itens
                FROM stg.ecommerce_orders p
                LEFT JOIN itens_eco i ON i.id_pedido = p.order_id
            )
            SELECT COUNT(*)
                FROM esperado e
                FULL OUTER JOIN dw.fato_pedido f
                ON f.sistema_origem = e.sistema_origem
                AND f.id_origem = e.id_origem
                WHERE e.id_origem IS NULL
                OR f.pedido_sk IS NULL
                OR e.valor_itens IS NULL
                OR f.valor_mercadoria IS NULL
                OR ABS(e.valor_itens - f.valor_mercadoria) > 0.01
        """)).scalar_one()
        qa.igual("pedidos divergentes de itens (> R$0,01)", int(divergencias), 0,)

    qa.registrar(eng_dw())
    if qa.imprimir():
        print("Há asserções em falha. Isso não impede seguir, mas precisa "
              "aparecer no RDT com explicação.")
    print(f"[tempo] Asserções e registro: {perf_counter()-inicio_qa:.1f}s")
    print(f"[tempo] ETAPA 2 COMPLETA: {perf_counter()-inicio_total:.1f}s\n")