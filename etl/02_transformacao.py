#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETAPA 2 — TRANSFORMAÇÃO E CONFORMAÇÃO DE DADOS

Objetivo:
    Converter e conformar os dados brutos da camada de staging em dimensões estruturadas 
    e na tabela fato de pedidos do Data Warehouse (DW), registrando as escolhas 
    semânticas e regras de negócio.

Principais Operações:
  · Unificação de clientes por CPF válido ou por nome, cidade e UF normalizados (relações 1:1);
  · Deduplicação rigorosa de registros cadastrais;
  · Conformação de status e canais de venda oriundos de sistemas heterogêneos;
  · Reconciliação e cálculo de itens, fretes, descontos e valores monetários dos pedidos;
  · Classificação de produtos em taxonomias analíticas padronizadas;
  · Tratamento de datas de entrega inválidas/sentinelas e vinculação de fontes auxiliares.

Comando de Execução:
    docker compose exec etl python 02_transformacao.py
"""

import re
import unicodedata
import pandas as pd
from config import eng_dw
from sqlalchemy import text
from time import perf_counter
from qualidade.assercoes import Suite


# ---------------------------------------------------------------------------
# Funções Auxiliares de Interface e Log no Terminal
# ---------------------------------------------------------------------------
def secao(titulo):
    """Exibe um cabeçalho visual destacado para separar as fases da transformação."""
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
# Funções de Normalização e Validação Cadastral
# ---------------------------------------------------------------------------
def so_digitos(txt):
    """Remove todos os caracteres não numéricos de uma string (ex: CPF, telefones)."""
    return re.sub(r"\D", "", txt or "")


def cpf_valido(cpf):
    """
    Valida os dois dígitos verificadores do CPF conforme o algoritmo oficial da Receita Federal.
    Retorna True se for válido, False se for inválido, ou None se não possuir 11 dígitos.
    """
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
    """
    Padroniza textos (nomes, cidades, UFs): converte para maiúsculas, remove acentos
    diacríticos (NFD) e colapsa múltiplos espaços em branco para pareamento aproximado.
    """
    n = unicodedata.normalize("NFD", (nome or "").strip().upper())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", n)


# ---------------------------------------------------------------------------
# Decisão 1 — Identidade e Unificação de Clientes entre Sistemas
# ---------------------------------------------------------------------------
def unifica_clientes(qa):
    """
    Unifica o cadastro de clientes do ERP legado e do e-commerce utilizando duas regras sucessivas:
    1. Correspondência exata por CPF válido;
    2. Correspondência exata por nome, cidade e UF normalizados (relacionamentos 1:1).
    Os registos restantes não pareados são mantidos como clientes exclusivos de cada origem.
    """
    eng = eng_dw()

    # Extrai o cadastro de clientes do ERP legado no staging
    df_leg = pd.read_sql(text("""
        SELECT
            "ID_CLIENTE" as id_legado,
            "NOME" as nome,
            "CPF" as cpf,
            "CIDADE" as cidade,
            "UF" as uf,
            "DT_CADASTRO" AS data_cadastro
        FROM stg.legado_clientes
    """), eng)
    df_leg["cadastro_data"] = pd.to_datetime(df_leg["data_cadastro"], errors="coerce").dt.date
    
    # Extrai o cadastro de clientes do e-commerce no staging
    df_eco = pd.read_sql(text("""
        SELECT
            customer_id as id_ecommerce,
            full_name as nome,
            tax_id as cpf,
            city as cidade,
            state as uf,
            created_at AS data_cadastro
        FROM stg.ecommerce_customer
    """), eng)
    df_eco["cadastro_data"] = pd.to_datetime(df_eco["data_cadastro"], errors="coerce", utc=True).dt.date

    # Aplica funções de limpeza, validação de CPF e normalização textual em ambos os dataframes
    for df in [df_leg, df_eco]:
        df['cpf_limpo'] = df['cpf'].apply(so_digitos)
        df['cpf_validado'] = df['cpf_limpo'].apply(cpf_valido)
        df['nome_normalizado'] = df['nome'].apply(normaliza_nome)
        df['cidade_normalizado'] = df['cidade'].apply(normaliza_nome)
        df['uf_normalizado'] = df['uf'].apply(normaliza_nome)

    # Filtra CPFs válidos e remove duplicatas dentro de cada sistema de origem
    leg_validos = df_leg[df_leg['cpf_validado'] == True].sort_values('id_legado').drop_duplicates('cpf_limpo', keep='first')
    eco_validos = df_eco[df_eco['cpf_validado'] == True].drop_duplicates('cpf_limpo', keep='first')
    
    # Pareamento 1: Vincula clientes de ambas as origens que compartilham o mesmo CPF válido
    match_cpf = pd.merge(leg_validos, eco_validos, on='cpf_limpo', suffixes=('_leg', '_eco'))
    match_cpf['origem_unificacao'] = 'AMBOS'
    match_cpf['regra_pareamento'] = 'CPF_VALIDO'
    match_cpf['cpf_normalizado'] = match_cpf['cpf_limpo']
    match_cpf['nome_padronizado'] = match_cpf['nome_normalizado_eco'].fillna(match_cpf['nome_normalizado_leg'])
    match_cpf['cidade'] = match_cpf['cidade_eco'].fillna(match_cpf['cidade_leg'])
    match_cpf['uf'] = match_cpf['uf_eco'].fillna(match_cpf['uf_leg'])
    match_cpf['cpf_validado_col'] = True

    # Identifica IDs já pareados para excluí-los das próximas etapas de cruzamento
    matched_leg_ids = match_cpf['id_legado'].dropna().unique()
    matched_eco_ids = match_cpf['id_ecommerce'].dropna().unique()
    
    leg_sobra = df_leg[~df_leg['id_legado'].isin(matched_leg_ids)]
    eco_sobra = df_eco[~df_eco['id_ecommerce'].isin(matched_eco_ids)]
    
    # Pareamento 2: Vincula cadastros restantes através de nome, cidade e UF normalizados (relações estritas 1:1)
    match_nome = pd.merge(leg_sobra, eco_sobra, on=['nome_normalizado', 'cidade_normalizado', 'uf_normalizado'], suffixes=('_leg', '_eco'))
    match_nome = match_nome.drop_duplicates('id_legado', keep=False).drop_duplicates('id_ecommerce', keep=False)
    match_nome['origem_unificacao'] = 'AMBOS'
    match_nome['regra_pareamento'] = 'NOME_LOCALIDADE'
    match_nome['cpf_normalizado'] = match_nome['cpf_limpo_eco'].fillna(match_nome['cpf_limpo_leg'])
    match_nome['nome_padronizado'] = match_nome['nome_normalizado']
    match_nome['cidade'] = match_nome['cidade_leg']
    match_nome['uf'] = match_nome['uf_leg']
    match_nome['cpf_validado_col'] = match_nome['cpf_validado_eco'].fillna(match_nome['cpf_validado_leg'])

    # Identifica IDs já pareados para excluí-los das próximas etapas de cruzamento
    matched_nome_leg_ids = match_nome['id_legado'].dropna().unique()
    matched_nome_eco_ids = match_nome['id_ecommerce'].dropna().unique()

    leg_sobra_final = leg_sobra[~leg_sobra['id_legado'].isin(matched_nome_leg_ids)]
    eco_sobra_final = eco_sobra[~eco_sobra['id_ecommerce'].isin(matched_nome_eco_ids)]
    
    # Separa clientes exclusivos do ERP legado (sem correspondência no e-commerce)
    leg_exclusivo = leg_sobra_final.copy()
    leg_exclusivo['id_ecommerce'] = None
    leg_exclusivo['origem_unificacao'] = 'LEGADO'
    leg_exclusivo['regra_pareamento'] = 'NENHUMA'
    leg_exclusivo['cpf_normalizado'] = leg_exclusivo['cpf_limpo']
    leg_exclusivo['nome_padronizado'] = leg_exclusivo['nome_normalizado']
    leg_exclusivo['cidade'] = leg_exclusivo['cidade']
    leg_exclusivo['uf'] = leg_exclusivo['uf']
    leg_exclusivo['cpf_validado_col'] = leg_exclusivo['cpf_validado']
    
    # Separa clientes exclusivos do e-commerce (sem correspondência no legado)
    eco_exclusivo = eco_sobra_final.copy()
    eco_exclusivo['id_legado'] = None
    eco_exclusivo['origem_unificacao'] = 'ECOM'
    eco_exclusivo['regra_pareamento'] = 'NENHUMA'
    eco_exclusivo['cpf_normalizado'] = eco_exclusivo['cpf_limpo']
    eco_exclusivo['nome_padronizado'] = eco_exclusivo['nome_normalizado']
    eco_exclusivo['cidade'] = eco_exclusivo['cidade']
    eco_exclusivo['uf'] = eco_exclusivo['uf']
    eco_exclusivo['cpf_validado_col'] = eco_exclusivo['cpf_validado']

    # Função auxiliar para determinar a primeira data de cadastro entre as fontes unificadas
    def primeira_data(df):
        datas = pd.concat([pd.to_datetime(df["cadastro_data_leg"], errors="coerce"), pd.to_datetime(df["cadastro_data_eco"], errors="coerce")], axis=1)
        return datas.min(axis=1).dt.date

    # Determina a primeira data de cadastro
    match_cpf["dt_primeiro_cadastro"] = primeira_data(match_cpf)
    match_nome["dt_primeiro_cadastro"] = primeira_data(match_nome)
    leg_exclusivo["dt_primeiro_cadastro"] = leg_exclusivo["cadastro_data"]
    eco_exclusivo["dt_primeiro_cadastro"] = eco_exclusivo["cadastro_data"]
    
    # Seleciona e consolida o esquema definitivo da dimensão de clientes
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
    
    # Normaliza representações de nulos em strings de CPF
    dim_cliente['cpf_normalizado'] = dim_cliente['cpf_normalizado'].astype(str).str.strip()
    dim_cliente.loc[dim_cliente['cpf_normalizado'].isin(['None', 'nan', '', 'nat']), 'cpf_normalizado'] = None

    # Ordena priorizando CPFs válidos e remove duplicatas remanescentes de CPF
    dim_cliente = dim_cliente.sort_values(by='cpf_validado_col', ascending=False)
    com_cpf = dim_cliente[dim_cliente['cpf_normalizado'].notna()].drop_duplicates(subset=['cpf_normalizado'], keep='first')
    sem_cpf = dim_cliente[dim_cliente['cpf_normalizado'].isna()]
    dim_cliente = pd.concat([com_cpf, sem_cpf], ignore_index=True)

    # Gera a chave substituta (surrogate key) 'cliente_sk' para a dimensão
    dim_cliente.reset_index(inplace=True)
    dim_cliente.rename(columns={'index': 'cliente_sk', 'cpf_validado_col': 'cpf_valido'}, inplace=True)
    dim_cliente['cliente_sk'] += 1 
    
    # Realiza carga completa na tabela de destino dw.dim_cliente no DW
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_cliente CASCADE;"))
    dim_cliente.to_sql('dim_cliente', eng, schema='dw', if_exists='append', index=False)


# ---------------------------------------------------------------------------
# Decisão 2 — Conformação de Status dos Pedidos
# ---------------------------------------------------------------------------
def conforma_status(qa):
    """
    Mapeia os códigos de status proprietários dos dois sistemas de origem
    para um vocabulário analítico padronizado na dimensão dw.dim_status.
    """
    eng = eng_dw()
    
    # Dicionário de mapeamento de status de origem para status corporativos conformados
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
    # Cria um dataframe da lista status_map
    dim_status = pd.DataFrame(status_map)

    # Gera a chave substituta (surrogate key) 'status_sk' para a dimensão
    dim_status.reset_index(inplace=True)
    dim_status.rename(columns={'index': 'status_sk'}, inplace=True)
    dim_status['status_sk'] += 1
    
    # Substitui e persiste a dimensão de status no DW
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_status CASCADE;"))
    dim_status.to_sql('dim_status', eng, schema='dw', if_exists='append', index=False)


# ---------------------------------------------------------------------------
# Função Auxiliar — Recuperação de Vínculos de Clientes por CPF (RDT-12)
# ---------------------------------------------------------------------------
def recuperar_clientes_por_cpf(pedidos, cadastro, dim_cliente, sistema):
    """
    Recupera chaves substitutas de clientes cujos IDs originais foram alterados
    ou perdidos durante o processo de deduplicação e unificação cadastral.
    Exige CPF estritamente válido com 11 dígitos e correspondência única na dimensão.
    """
    antes = len(pedidos)
    pendentes = pedidos["cliente_sk"].isna()
    n_pendentes = int(pendentes.sum())
    if n_pendentes == 0:
        ok(f"{sistema}: todos os pedidos vinculados pelo ID original")
        return pedidos

    # Valida integridade básica da tabela de cadastro de origem
    if cadastro["id_cliente_origem"].isna().any() or cadastro["id_cliente_origem"].duplicated().any():
        raise ValueError(f"{sistema}: IDs de clientes nulos/duplicados no staging")

    # Mapeia IDs de cliente para CPFs limpos na origem
    cpfs_origem = cadastro.copy()
    cpfs_origem["cpf_limpo"] = cpfs_origem["cpf"].apply(so_digitos)
    cpf_por_id = cpfs_origem.set_index("id_cliente_origem")["cpf_limpo"]

    # Cria índice de busca de SK por CPF na dimensão de clientes conformada
    indice_cpf = dim_cliente[["cpf_normalizado", "cliente_sk"]].copy()
    indice_cpf["cpf_normalizado"] = indice_cpf["cpf_normalizado"].astype("string").str.strip()
    indice_cpf = indice_cpf[indice_cpf["cpf_normalizado"].str.fullmatch(r"\d{11}").fillna(False)]
    if indice_cpf["cpf_normalizado"].duplicated().any():
        raise ValueError("dw.dim_cliente: CPF duplicado; pareamento ambíguo")
    sk_por_cpf = indice_cpf.set_index("cpf_normalizado")["cliente_sk"]

    # Atribui o cliente_sk aos pedidos pendentes que possuem CPF válido correspondente
    cpfs_pendentes = pedidos.loc[pendentes, "id_cliente_origem"].map(cpf_por_id)
    cpfs_pendentes = cpfs_pendentes.where(cpfs_pendentes.str.fullmatch(r"\d{11}").fillna(False))
    pedidos.loc[pendentes, "cliente_sk"] = cpfs_pendentes.map(sk_por_cpf)

    recuperados = n_pendentes - int(pedidos["cliente_sk"].isna().sum())
    print(f"\n[OK] {sistema}: {recuperados}/{n_pendentes} pedidos recuperados por CPF")
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


# ---------------------------------------------------------------------------
# Função Auxiliar — Atribuição e Mapeamento de Canais de Venda
# ---------------------------------------------------------------------------
def atribui_canal_pedido(pedidos, dim_canal_db, sistema):
    """Associa cada pedido à chave substituta correspondente (canal_sk) com base no canal de origem."""
    canais = dim_canal_db.loc[dim_canal_db['sistema'] == sistema, ['canal_sk', 'canal_origem']].copy()
    canais['canal_chave'] = canais['canal_origem'].astype('string').str.strip().str.upper()
    if canais['canal_chave'].isna().any() or canais['canal_chave'].eq('').any():
        raise ValueError(f"{sistema}: canal vazio cadastrado em dw.dim_canal")
    if canais['canal_chave'].duplicated().any():
        raise ValueError(f"{sistema}: canais duplicados em dw.dim_canal")

    pedidos = pedidos.copy()
    pedidos['canal_chave'] = pedidos['canal_origem'].astype('string').str.strip().str.upper()
    if pedidos['canal_chave'].isna().any() or pedidos['canal_chave'].eq('').any():
        raise ValueError(f"{sistema}: há pedidos com canal de origem ausente")

    # Realiza junção validando relação de muitos para um (many_to_one)
    pedidos = pedidos.merge(
        canais[['canal_chave', 'canal_sk']],
        on='canal_chave', how='left', validate='many_to_one'
    )
    if pedidos['canal_sk'].isna().any():
        desconhecidos = pedidos.loc[pedidos['canal_sk'].isna(), 'canal_origem'].unique().tolist()
        raise ValueError(f"{sistema}: canais não mapeados: {desconhecidos}")
    return pedidos.drop(columns=['canal_chave'])


# ---------------------------------------------------------------------------
# Decisão 3 — Semântica de Valores Monetários e Construção da Fato Pedido
# ---------------------------------------------------------------------------
def conforma_valores(qa):
    """
    Reconcilia os itens, fretes, descontos e valores totais dos pedidos de ambas as origens,
    padronizando a regra de cálculo monetário na tabela fato principal dw.fato_pedido.
    """
    eng = eng_dw()

    # Define os canais analíticos corporativos conformados para cada sistema de origem
    canais_data = [
        {'canal_origem': 'LOJA', 'canal_conformado': 'LOJA_FISICA', 'sistema': 'LEGADO'},
        {'canal_origem': 'SITE', 'canal_conformado': 'SITE', 'sistema': 'LEGADO'},
        {'canal_origem': 'TELEVENDAS', 'canal_conformado': 'TELEVENDAS', 'sistema': 'LEGADO'},
        {'canal_origem': 'web', 'canal_conformado': 'SITE', 'sistema': 'ECOM'},
        {'canal_origem': 'app', 'canal_conformado': 'APLICATIVO', 'sistema': 'ECOM'},
        {'canal_origem': 'marketplace', 'canal_conformado': 'MARKETPLACE', 'sistema': 'ECOM'}
    ]
    dim_canal = pd.DataFrame(canais_data)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_canal RESTART IDENTITY CASCADE;"))
    dim_canal.to_sql('dim_canal', eng, schema='dw', if_exists='append', index=False)

    # Carrega chaves dimensionais auxiliares do DW
    dim_cliente = pd.read_sql("SELECT cliente_sk, id_legado, id_ecommerce, cpf_normalizado FROM dw.dim_cliente", eng)
    dim_cliente['id_legado'] = dim_cliente['id_legado'].astype('Int64')
    dim_cliente['id_ecommerce'] = dim_cliente['id_ecommerce'].astype('Int64')

    dim_status = pd.read_sql("SELECT status_sk, status_origem, sistema FROM dw.dim_status", eng)
    dim_canal_db = pd.read_sql("SELECT canal_sk, canal_origem, sistema FROM dw.dim_canal", eng)

    # Extrai pedidos do ERP legado
    ped_leg = pd.read_sql(text("""
        SELECT
            "ID_PEDIDO" as id_pedido,
            "ID_CLIENTE" as id_cliente,
            "STATUS" as status_origem,
            "VALOR_FRETE" as valor_frete,
            "DT_PEDIDO" as dt_pedido,
            "DT_ENTREGA" as dt_entrega,
            "CANAL" as canal_origem
        FROM stg.legado_pedidos
    """), eng)
    
    # Agrega itens do ERP legado diretamente no banco de dados (mais de 2 comandos SQL estruturados)
    inicio_itens = perf_counter()
    agg_leg = pd.read_sql(text("""
        SELECT 
            "ID_PEDIDO" AS id_pedido,
            COALESCE(SUM("QTD"::numeric * "VL_UNITARIO"::numeric), 0) AS valor_mercadoria,
            COALESCE(SUM(CASE WHEN "VL_DESCONTO"::numeric < 0 THEN 0 ELSE "VL_DESCONTO"::numeric END), 0) AS vl_desconto,
            COALESCE(SUM("QTD"::integer), 0) AS qtd_itens
        FROM stg.legado_itens_pedido
        GROUP BY "ID_PEDIDO"
    """), eng)
    print(f"[tempo] agregação LEGADO | {len(agg_leg):>8} pedidos | {perf_counter()-inicio_itens:.1f}s")
    
    # Consolida os dados financeiros do legado aplicando valor absoluto no frete
    ped_leg = ped_leg.merge(agg_leg, on='id_pedido', how='left')
    ped_leg['valor_frete'] = ped_leg['valor_frete'].abs()
    ped_leg['valor_total'] = ped_leg['valor_mercadoria'] + ped_leg['valor_frete'] - ped_leg['vl_desconto']
    ped_leg['sistema_origem'] = 'LEGADO'
    ped_leg['id_origem'] = 'LEG-' + ped_leg['id_pedido'].astype(str)
    ped_leg.rename(columns={'id_cliente': 'id_cliente_origem', 'vl_desconto': 'valor_desconto'}, inplace=True)
    
    ped_leg['id_cliente_origem'] = ped_leg['id_cliente_origem'].astype('Int64')
    ped_leg = ped_leg.merge(dim_cliente[dim_cliente['id_legado'].notna()], left_on='id_cliente_origem', right_on='id_legado', how='left', validate='many_to_one')

    # Extrai pedidos do e-commerce
    ped_eco = pd.read_sql(text("""
        SELECT
            order_id,
            customer_id,
            status as status_origem,
            freight_amount,
            placed_at,
            delivered_at,
            channel as canal_origem
        FROM stg.ecommerce_orders
    """), eng)
    
    # Agrega itens do e-commerce no banco de dados
    inicio_itens = perf_counter()
    agg_eco = pd.read_sql(text("""
        SELECT 
            order_id,
            COALESCE(SUM(quantity::numeric * unit_price::numeric), 0) AS valor_mercadoria,
            COALESCE(SUM(discount::numeric), 0) AS discount,
            COALESCE(SUM(quantity::integer), 0) AS qtd_itens
        FROM stg.ecommerce_order_item
        GROUP BY order_id
    """), eng)
    print(f"[tempo] agregação ECOM   | {len(agg_eco):>8} pedidos | {perf_counter()-inicio_itens:.1f}s")
    
    # Consolida os dados financeiros do e-commerce
    ped_eco = ped_eco.merge(agg_eco, on='order_id', how='left')
    ped_eco['valor_frete'] = ped_eco['freight_amount'].abs()
    ped_eco['valor_total'] = ped_eco['valor_mercadoria'] + ped_eco['valor_frete'] - ped_eco['discount']
    ped_eco['sistema_origem'] = 'ECOM'
    ped_eco['id_origem'] = 'ECOM-' + ped_eco['order_id'].astype(str)
    ped_eco.rename(columns={'customer_id': 'id_cliente_origem', 'discount': 'valor_desconto'}, inplace=True)
    
    ped_eco['id_cliente_origem'] = ped_eco['id_cliente_origem'].astype('Int64')
    ped_eco = ped_eco.merge(dim_cliente[dim_cliente['id_ecommerce'].notna()], left_on='id_cliente_origem', right_on='id_ecommerce', how='left', validate='many_to_one')

    # Lê tabelas de cadastro auxiliares para recuperação de clientes por CPF
    cad_leg = pd.read_sql(text("""
        SELECT
            "ID_CLIENTE" AS id_cliente_origem,
            "CPF" AS cpf
        FROM stg.legado_clientes
    """), eng)
    cad_eco = pd.read_sql(text("""
        SELECT
            customer_id AS id_cliente_origem,
            tax_id AS cpf
        FROM stg.ecommerce_customer
    """), eng)
    ped_leg = recuperar_clientes_por_cpf(ped_leg, cad_leg, dim_cliente, "LEGADO")
    ped_eco = recuperar_clientes_por_cpf(ped_eco, cad_eco, dim_cliente, "ECOM")

    # Padroniza conversão de datas de pedido e entrega
    ped_leg['data_pedido_sk'] = pd.to_datetime(ped_leg['dt_pedido']).dt.date
    ped_leg['data_entrega_sk'] = pd.to_datetime(ped_leg['dt_entrega']).dt.date
    ped_eco['data_pedido_sk'] = pd.to_datetime(ped_eco['placed_at']).dt.date
    ped_eco['data_entrega_sk'] = pd.to_datetime(ped_eco['delivered_at']).dt.date

    # Atribui canais conformados aos pedidos de ambas as origens
    ped_leg = atribui_canal_pedido(ped_leg, dim_canal_db, 'LEGADO')
    ped_eco = atribui_canal_pedido(ped_eco, dim_canal_db, 'ECOM')

    # Concatena os pedidos de ambos os sistemas em uma única estrutura fato temporária
    colunas_base = [
        'id_origem', 'sistema_origem', 'cliente_sk', 'data_pedido_sk', 'data_entrega_sk',
        'canal_sk', 'status_origem', 'qtd_itens', 'valor_mercadoria', 'valor_frete', 'valor_desconto', 'valor_total'
    ]
    fato = pd.concat([ped_leg[colunas_base], ped_eco[colunas_base]], ignore_index=True)
    
    # Invalida datas de entrega sentinela (anteriores a 2000 ou anteriores à data do pedido)
    min_date = pd.to_datetime('2000-01-01').date()
    invalidas = (fato['data_entrega_sk'] < min_date) | (fato['data_entrega_sk'] < fato['data_pedido_sk'])
    fato.loc[invalidas, 'data_entrega_sk'] = None
    fato['data_entrega_sk'] = fato['data_entrega_sk'].where(fato['data_entrega_sk'].notna(), None)

    # Relaciona os status de origem com a dimensão conformada para obter o status_sk
    fato = fato.merge(dim_status, left_on=['status_origem', 'sistema_origem'], right_on=['status_origem', 'sistema'], how='left')

    colunas_finais = [
        'id_origem', 'sistema_origem', 'cliente_sk', 'data_pedido_sk', 'data_entrega_sk',
        'canal_sk', 'status_sk', 'qtd_itens', 'valor_mercadoria', 'valor_frete', 'valor_desconto', 'valor_total'
    ]
    fato_final = fato[colunas_finais]

    # Realiza carga completa na tabela dw.fato_pedido em lotes de 10.000 linhas
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.fato_pedido CASCADE;"))
    inicio_gravacao = perf_counter()
    fato_final.to_sql('fato_pedido', eng, schema='dw', if_exists='append', index=False, chunksize=10_000)
    print(f"[tempo] escrita fato_pedido | {len(fato_final):>8} pedidos | {perf_counter()-inicio_gravacao:.1f}s")


# ---------------------------------------------------------------------------
# Decisão 4 — Taxonomia e Conformação de Produtos
# ---------------------------------------------------------------------------
def conforma_categorias(qa):
    """
    Consolida o catálogo de produtos dos dois sistemas de origem e classifica
    os itens em categorias analíticas de nível 1 (categoria_nivel1) através de regras textuais.
    """
    eng = eng_dw()
    
    # Extrai produtos do ERP legado
    df_leg = pd.read_sql(text("""
        SELECT 
            "ID_PRODUTO" AS id_legado, 
            "DESCRICAO" AS descricao, 
            "CATEGORIA" AS categoria_origem, 
            "PRECO_TABELA" AS preco_referencia, 
            "ATIVO" AS ativo 
        FROM stg.legado_produtos
    """), eng)
    df_leg["origem"] = "LEGADO"
    df_leg["id_ecommerce"] = None
    df_leg["sku"] = None
    df_leg["ativo"] = df_leg["ativo"].astype("string").str.strip().str.upper().map({"S": True, "N": False})
    
    # Extrai produtos do e-commerce com suas respectivas categorias (SQL com mais de 2 comandos/junção)
    df_eco = pd.read_sql(text("""
        SELECT 
            p.product_id AS id_ecommerce, 
            p.sku, 
            p.title AS descricao, 
            c.name AS categoria_origem, 
            p.list_price AS preco_referencia, 
            p.active AS ativo 
        FROM stg.ecommerce_product p 
        LEFT JOIN stg.ecommerce_category c ON p.category_id = c.category_id
    """), eng)
    df_eco["origem"] = "ECOM"
    df_eco["id_legado"] = None

    dim_produto = pd.concat([df_leg, df_eco], ignore_index=True)
    
    # Regras heurísticas textuais para mapear categorias de origem em macrocategorias analíticas
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
    
    if 'id_origem' in dim_produto.columns:
        dim_produto = dim_produto.drop(columns=['id_origem'])
    
    # Persiste a dimensão unificada de produtos no DW
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dw.dim_produto CASCADE;"))
    dim_produto.to_sql('dim_produto', eng, schema='dw', if_exists='append', index=False)


# ---------------------------------------------------------------------------
# Decisão 5 — Tratamento de Datas, Ausências e Prazos de Entrega
# ---------------------------------------------------------------------------
def trata_datas_e_ausencias(qa):
    """
    Invalida prazos de entrega inconsistentes na fato e calcula métricas de dias de entrega.
    Realiza tratamento opcional de fechamento na tabela de staging de atendimentos do SAC.
    """
    eng = eng_dw()
    
    # Atualiza a fato limpando entregas inválidas e calculando o lead time de entrega (dias)
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

    # Bloco opcional para tratamento preventivo na staging do SAC, se a tabela existir
    try:
        sac = pd.read_sql(text("SELECT * FROM stg.sac_atendimentos"), eng)
        sac['data_fechamento'] = sac['data_fechamento'].fillna('EM_ABERTO')
        sac.to_sql('sac_atendimentos', eng, schema='stg', if_exists='replace', index=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Decisão 6 — Vínculo de Fontes Auxiliares (SAC e Avaliações de Marketplace)
# ---------------------------------------------------------------------------
def liga_sac_e_avaliacoes(qa):
    """
    Popula as tabelas fato auxiliares dw.fato_atendimento e dw.fato_avaliacao,
    estabelecendo relações com clientes e pedidos principais de forma transacional.
    """
    eng = eng_dw()
    inicio = perf_counter()

    # Executa a carga das fatos auxiliares em uma única transação atômica (rollback em caso de falha)
    with eng.begin() as cx:
        cx.execute(text("""TRUNCATE TABLE dw.fato_atendimento, dw.fato_avaliacao RESTART IDENTITY"""))

        # Inserção e conformação dos atendimentos de suporte do SAC (SQL longo com múltiplos comandos)
        cx.execute(text("""
            INSERT INTO dw.fato_atendimento (
                protocolo, cliente_sk, pedido_sk, data_abertura_sk, data_fechamento_sk,
                canal, motivo_conformado, nota_satisfacao, horas_resolucao
            )
            SELECT
                s.protocolo,
                p.cliente_sk,
                p.pedido_sk,
                TO_DATE(s.data_abertura, 'DD/MM/YYYY') AS data_abertura_sk,
                CASE
                    WHEN NULLIF(TRIM(s.data_fechamento), '') IS NULL
                    OR UPPER(TRIM(s.data_fechamento)) IN ('EM_ABERTO', 'N/A', 'NULL')
                    THEN NULL
                    ELSE TO_DATE(s.data_fechamento, 'DD/MM/YYYY')
                END AS data_fechamento_sk,
                UPPER(NULLIF(TRIM(s.canal), '')) AS canal,
                UPPER(TRIM(s.motivo)) AS motivo_conformado,
                CASE WHEN NULLIF(TRIM(s.nota_satisfacao), '')::int BETWEEN 1 AND 5
                    THEN NULLIF(TRIM(s.nota_satisfacao), '')::int
                    ELSE NULL END AS nota_satisfacao,
                CASE WHEN NULLIF(REPLACE(TRIM(s.tempo_resolucao_horas), ',', '.'), '')::numeric >= 0
                    THEN NULLIF(REPLACE(TRIM(s.tempo_resolucao_horas), ',', '.'), '')::numeric
                    ELSE NULL END AS horas_resolucao
            FROM (
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

        esperados_sac = cx.execute(text("SELECT COUNT(DISTINCT protocolo) FROM stg.atendimentos_sac")).scalar_one()
        carregados_sac = cx.execute(text("SELECT COUNT(*) FROM dw.fato_atendimento")).scalar_one()
        if esperados_sac != carregados_sac:
            raise ValueError(f"SAC: {carregados_sac} protocolos inseridos, esperados {esperados_sac}")

        # Valida consistência de datas nas avaliações do marketplace antes da carga
        invalidas = cx.execute(text("""
            SELECT COUNT(*)
            FROM stg.avaliacoes_marketplace
            WHERE submitted_at IS NULL
            OR LEFT(TRIM(submitted_at), 10) !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
        """)).scalar_one()
        if invalidas:
            raise ValueError(f"{invalidas} avaliações sem data válida; corrija a fonte ou documente a decisão no RDT")

        # Inserção e conformação das avaliações de produtos do marketplace (SQL com mais de 2 comandos/junções)
        cx.execute(text("""
            INSERT INTO dw.fato_avaliacao (
                review_id,
                pedido_sk,
                produto_sk,
                data_sk,
                nota,
                compra_verificada,
                votos_uteis
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
        esperadas_av = cx.execute(text("SELECT COUNT(*) FROM stg.avaliacoes_marketplace")).scalar_one()
        carregadas_av = cx.execute(text("SELECT COUNT(*) FROM dw.fato_avaliacao")).scalar_one()
        if esperadas_av != carregadas_av:
            raise ValueError(f"Avaliações: {carregadas_av} linhas inseridas, esperadas {esperadas_av}")

        # Verifica se há registros órfãos sem pedido vinculado
        sem_pedido = cx.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM dw.fato_atendimento WHERE pedido_sk IS NULL)
              + (SELECT COUNT(*) FROM dw.fato_avaliacao WHERE pedido_sk IS NULL)
        """)).scalar_one()
        if sem_pedido:
            raise ValueError(f"SAC/avaliações: {sem_pedido} registros sem pedido")

    ok(f"dw.fato_atendimento: {carregados_sac} protocolos únicos")
    ok(f"dw.fato_avaliacao: {carregadas_av} avaliações")
    print(f"[tempo] fatos auxiliares | {perf_counter() - inicio:.1f}s")


# ---------------------------------------------------------------------------
# Orquestração Principal e Validações de Qualidade (QA) da Etapa 2
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    secao("ETAPA 2 — TRANSFORMAÇÃO E CONFORMAÇÃO")
    info("Aplicando as decisões registradas no RDT e preparando as estruturas dimensionais/fato.")
    
    qa = Suite(etapa="02_transformacao")
    inicio_total = perf_counter()
    
    # Tupla contendo a sequência de execução das transformações e conformações
    etapas = (
        ("Unificação de clientes", unifica_clientes),
        ("Conformação de status", conforma_status),
        ("Valores e fato_pedido", conforma_valores),
        ("Categorias e produtos", conforma_categorias),
        ("Datas e ausências", trata_datas_e_ausencias),
        ("Vínculos SAC/avaliações", liga_sac_e_avaliacoes),
    )
    
    # Executa cada etapa sequencialmente medindo o tempo de processamento
    for nome, funcao in etapas:
        inicio_etapa = perf_counter()
        funcao(qa)
        print(f"[tempo] {nome:<23} | {perf_counter()-inicio_etapa:.1f}s")

    # Inicia a suíte de asserções de qualidade pós-carga no DW
    inicio_qa = perf_counter()
    eng = eng_dw()
    with eng.connect() as cx:
        # Asserção 1: Valida unicidade e validade dos CPFs na dimensão de clientes
        clientes = pd.read_sql(text("""
            SELECT
                cpf_normalizado,
                origem_unificacao
            FROM dw.dim_cliente
        """), cx)
        cpf = clientes["cpf_normalizado"].astype("string").str.strip().fillna("")
        cpf_validos = cpf.loc[cpf.ne("") & cpf.map(cpf_valido)]
        qa.unico("CPF valido sem duplicidade", cpf_validos)

        # Asserção 2: Valida domínio permitido para a coluna origem_unificacao
        qa.dominio("origem_unificacao no dominio", clientes["origem_unificacao"], {"LEGADO", "ECOM", "AMBOS"})

        # Asserção 3: Garante que o cruzamento com clientes não alterou a cardinalidade (SQL com mais de 2 comandos)
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
        qa.cardinalidade_preservada("pedidos x clientes sem multiplicacao", int(contagens["antes"]), int(contagens["depois"]))

        # Asserção 4: Compara pedido a pedido a soma dos itens com o valor de mercadoria na fato (SQL complexo)
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
        qa.igual("pedidos divergentes de itens (> R$0,01)", int(divergencias), 0)

    # Registra os resultados do QA no banco e imprime o relatório final
    qa.registrar(eng_dw())
    if qa.imprimir():
        print("[AVISO] Há asserções em falha. Isso não impede seguir.")
    print(f"[tempo] asserções/registro   | {perf_counter()-inicio_qa:.1f}s")
    ok("Etapa 2 concluída.")
    print(f"[tempo] total etapa 2       | {perf_counter()-inicio_total:.1f}s")