#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auditoriar posterior aos seis comandos do professor.

Uso (executado automaticamente pelo .ps1):
    python -u auditar_pipeline.py --desde-id 123

Cria /saida/auditoria_pipeline.json.
Não modifica as tabelas do ETL nem substitui as asserções oficiais.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from sqlalchemy import text
from config import DIR_SAIDA, eng_dw

CONTAGENS = {
    "stg.legado_clientes": 47250,
    "stg.legado_produtos": 3000,
    "stg.legado_pedidos": 130000,
    "stg.legado_itens_pedido": 397086,
    "stg.ecommerce_customer": 85000,
    "stg.ecommerce_product": 12000,
    "stg.ecommerce_orders": 280000,
    "stg.ecommerce_order_item": 995367,
    "stg.ecommerce_category": 21,
    "stg.atendimentos_sac": 96140,
    "stg.avaliacoes_marketplace": 160000,
    "dw.dim_cliente": 115674,
    "dw.dim_produto": 15000,
    "dw.dim_status": 9,
    "dw.dim_canal": 3,
    "dw.fato_pedido": 410000,
    "dw.fato_item_pedido": 1371876,
    "dw.fato_atendimento": 95000,
    "dw.fato_avaliacao": 160000,
    "abt.recompra_90d": 108064,
    "abt.recompra_90d_vazamento": 108064,
}

ETAPAS_QA = {
    "01_extracao": 11,
    "02_transformacao": 4,
    "03_carga_dw": 4,
    "04_abt_correta": 5,
    "04_abt_vazamento": 4,
}

CSV_ARQUIVOS = {
    "abt_recompra_correta.csv": "abt.recompra_90d",
    "abt_recompra_vazamento.csv": "abt.recompra_90d_vazamento",
}


def csv_referencia(path, esperado, anterior=None):
    """Assinatura independente da ordem das linhas, ignorando nenhuma coluna."""
    with path.open("r", newline="", encoding="utf-8-sig") as arq:
        reader = csv.reader(arq)
        try:
            colunas = next(reader)
            tipos = next(reader)
            papeis = next(reader)
        except StopIteration as exc:
            raise ValueError(f"Cabeçalho Orange incompleto: {path}") from exc

        if len(colunas) != 18 or len(set(colunas)) != 18:
            raise ValueError(f"{path.name}: esperado 18 colunas únicas, recebido {len(colunas)}")
        if len(tipos) != 18 or len(papeis) != 18:
            raise ValueError(f"{path.name}: linhas de tipo/papel inconsistentes")
        if set(c for c, r in zip(colunas, papeis) if r == 'class') != {'alvo_recompra_90d'}:
            raise ValueError(f"{path.name}: alvo não marcado como classe do Orange")
        if set(c for c, r in zip(colunas, papeis) if r == 'meta') != {'cliente_sk', 'data_corte'}:
            raise ValueError(f"{path.name}: metadados do Orange incorretos")
        restantes = {c for c, r in zip(colunas, papeis) if r == ''}
        if len(restantes) != 15:
            raise ValueError(f"{path.name}: não foram encontradas 15 features")

        i_id = colunas.index('cliente_sk')
        i_alvo = colunas.index('alvo_recompra_90d')
        linhas = []
        alvos = {}
        n_pos = 0
        for i, valores in enumerate(reader, start=4):
            if len(valores) != 18:
                raise ValueError(f"{path.name} linha {i}: quantidade de colunas incorreta")
            id_cliente = int(valores[i_id])
            if id_cliente in alvos:
                raise ValueError(f"{path.name}: cliente_sk duplicado: {id_cliente}")
            alvo = int(valores[i_alvo])
            if alvo not in (0, 1):
                raise ValueError(f"{path.name}: classe inesperada: {alvo}")
            alvos[id_cliente] = alvo
            n_pos += alvo
            linhas.append((id_cliente, valores))
        if len(linhas) != esperado:
            raise ValueError(f"{path.name}: {len(linhas)} linhas, esperado {esperado}")
        if n_pos != 24043:
            raise ValueError(f"{path.name}: {n_pos} recompras, esperado 24043")
        if anterior is not None and alvos != anterior:
            raise ValueError(f"{path.name}: clientes ou variáveis-alvo divergentes da outra ABT")
        linhas.sort(key=lambda par: par[0])
        h = hashlib.sha256()
        for elemento in (colunas, tipos, papeis):
            h.update(json.dumps(elemento, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            h.update(b'\n')
        for _, valores in linhas:
            h.update(json.dumps(valores, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            h.update(b'\n')
    return {
        "linhas": len(linhas), "colunas": len(colunas), "features": 15,
        "positivos": n_pos, "sha256_ordenado_cliente_sk": h.hexdigest()
    }, alvos


def auditar(desde_id):
    resumo = {"versao": 1, "esperados_escala": "ESCALA=1.0", "contagens": {}, "indicadores": {}, "qa": {}, "csv": {}}
    eng = eng_dw()
    with eng.connect() as cx:
        for tabela, esperado in CONTAGENS.items():
            obtido = int(cx.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar_one())
            resumo["contagens"][tabela] = obtido
            if obtido != esperado:
                raise ValueError(f"{tabela}: {obtido} registros, esperado {esperado}")
            print(f"[OK] {tabela}: {obtido:,} registros")

        if cx.execute(text("SELECT COUNT(*) FROM dw.fato_pedido WHERE cliente_sk IS NULL")).scalar_one():
            raise ValueError("Existem pedidos sem cliente_sk")
        for tabela in ('dw.fato_atendimento', 'dw.fato_avaliacao'):
            sem_pedido = int(cx.execute(text(f"SELECT COUNT(*) FROM {tabela} WHERE pedido_sk IS NULL")).scalar_one())
            if sem_pedido:
                raise ValueError(f"{tabela}: {sem_pedido} registros sem pedido_sk")
        resumo["indicadores"]["sac_sem_pedido"] = 0
        resumo["indicadores"]["avaliacoes_sem_pedido"] = 0
        for tabela in ('abt.recompra_90d', 'abt.recompra_90d_vazamento'):
            stats = cx.execute(text(f"""
                SELECT COUNT(DISTINCT cliente_sk) AS clientes,
                       COALESCE(SUM(alvo_recompra_90d), 0) AS positivos
                FROM {tabela}
            """)).mappings().one()
            if int(stats['clientes']) != 108064 or int(stats['positivos']) != 24043:
                raise ValueError(f"{tabela}: IDs únicos ou taxa de recompra divergentes")
            resumo["indicadores"][f"{tabela}_positivos"] = int(stats['positivos'])
        negativos = int(cx.execute(text("""
            SELECT COUNT(*) FROM abt.recompra_90d WHERE recencia_dias < 0
        """)).scalar_one())
        if negativos:
            raise ValueError(f"ABT correta: {negativos} recências negativas (vazamento temporal?)")
        resumo["indicadores"]["recencias_negativas_abt_correta"] = 0

        linhas_qa = cx.execute(text("""
            SELECT etapa, nome, aprovado, id
            FROM qa.execucao_assercao
            WHERE id > :ultimo
            ORDER BY id
        """), {"ultimo": desde_id}).mappings().all()
        contagens_qa = Counter()
        for l in linhas_qa:
            contagens_qa[l['etapa']] += 1
            if not l['aprovado']:
                raise ValueError(f"Asserção FALHOU na execução atual: {l['etapa']} / {l['nome']} (id {l['id']})")
        for etapa, minimo in ETAPAS_QA.items():
            quantidade = contagens_qa[etapa]
            if quantidade < minimo:
                raise ValueError(f"Faltam asserções desta execução em {etapa}: {quantidade}, esperado >= {minimo}")
            resumo["qa"][etapa] = quantidade
            print(f"[OK] QA {etapa}: {quantidade} asserções aprovadas")

    saida = Path(DIR_SAIDA)
    anterior = None
    for arquivo, origem in CSV_ARQUIVOS.items():
        info, alvos = csv_referencia(saida / arquivo, CONTAGENS[origem], anterior)
        anterior = alvos
        resumo["csv"][arquivo] = info
        print(f"[OK] CSV {arquivo}: {info['linhas']:,} linhas, "
              f"{info['colunas']} colunas, SHA256={info['sha256_ordenado_cliente_sk'][:16]}...")

    destino = saida / 'auditoria_pipeline.json'
    destino.write_text(json.dumps(resumo, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    print(f"[OK] Auditoria e assinaturas registradas em: {destino}")
    return resumo


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--desde-id', required=True, type=int, help='Último ID de QA anterior à reexecução')
    args = ap.parse_args()
    auditar(args.desde_id)