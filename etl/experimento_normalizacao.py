#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Experimento auxiliar da Semana 4: isolar vazamento por normalização.

Entrada: /saida/abt_recompra_correta.csv (cabeçalho de três linhas do Orange).

Gera QUATRO arquivos (treino e teste para duas abordagens):
    abt_norm_segura_treino.csv
    abt_norm_segura_teste.csv
    abt_norm_global_treino.csv
    abt_norm_global_teste.csv

O particionamento é 70/30 estratificado, determinístico, e igual nas duas
abordagens. Mantém todas as outras 14 features e o alvo sem alterações.
Não altera os CSVs originais, o 04_abt.py nem o banco de dados.
"""

from pathlib import Path
from time import perf_counter
import numpy as np
import pandas as pd

from config import DIR_SAIDA


def secao(titulo):
    """Cabeçalho visual do experimento auxiliar."""
    print("\n" + "=" * 72)
    print(titulo)
    print("=" * 72)


def info(mensagem):
    """Mensagem descritiva padronizada."""
    print(f"[INFO] {mensagem}")


def ok(mensagem):
    """Mensagem de sucesso padronizada."""
    print(f"[OK] {mensagem}")

ORIGEM = Path(DIR_SAIDA) / 'abt_recompra_correta.csv'
ALVO = 'alvo_recompra_90d'
ATRIBUTO = 'valor_monetario'
SEMENTE = 2026
PROPORCAO_TREINO = 0.70


def grava_orange(destino, tabela, cabecalho):
    """Preserva exatamente as 3 linhas descritivas do formato Orange."""
    with open(destino, 'w', encoding='utf-8', newline='') as saida:
        saida.writelines(cabecalho)
    tabela.to_csv(destino, mode='a', header=False, index=False, encoding='utf-8')
    ok(f'{destino.name}: {len(tabela)} linhas, {len(tabela.columns)} colunas')


def main():
    inicio = perf_counter()
    secao("EXPERIMENTO AUXILIAR — NORMALIZAÇÃO")
    info("Comparando normalização segura (treino) com normalização global (vazamento).")
    if not ORIGEM.is_file():
        raise FileNotFoundError(f'Não encontrei {ORIGEM}; execute antes 05_exportar_orange.py')

    with open(ORIGEM, 'r', encoding='utf-8', newline='') as arquivo:
        cabecalho = [arquivo.readline() for _ in range(3)]
    if not all(linha.endswith('\n') for linha in cabecalho):
        raise ValueError('CSV de origem sem cabeçalho Orange de três linhas')

    base = pd.read_csv(ORIGEM, skiprows=[1, 2], low_memory=False)
    for coluna in [ALVO, ATRIBUTO, 'cliente_sk']:
        if coluna not in base.columns:
            raise ValueError(f'Coluna ausente no arquivo: {coluna}')
    if base['cliente_sk'].duplicated().any() or base[ALVO].isna().any():
        raise ValueError('IDs duplicados ou alvo nulo; interrompido')
    base[ATRIBUTO] = pd.to_numeric(base[ATRIBUTO], errors='raise')
    if base[ATRIBUTO].isna().any():
        raise ValueError('valor_monetario possui nulos; decidir tratamento antes')

    # Partição ESTRATIFICADA, reproduzível, sem reposição e independente dos atributos.
    rng = np.random.default_rng(SEMENTE)
    indices_treino = []
    for classe in sorted(base[ALVO].unique()):
        indices_classe = base.index[base[ALVO] == classe].to_numpy()
        qtd = round(len(indices_classe) * PROPORCAO_TREINO)
        indices_treino.extend(rng.choice(indices_classe, size=qtd, replace=False))
    treino = base.loc[sorted(indices_treino)].copy()
    teste = base.drop(index=indices_treino).copy()
    assert len(treino) + len(teste) == len(base)
    assert not set(treino['cliente_sk']) & set(teste['cliente_sk'])

    # NORMALIZAÇÃO LEGÍTIMA: aprende média e desvio SOMENTE no treinamento.
    media_treino = float(treino[ATRIBUTO].mean())
    dp_treino = float(treino[ATRIBUTO].std(ddof=0))
    # NORMALIZAÇÃO COM VAZAMENTO: aprende média e desvio em TODA a população,
    # inclusive as observações reservadas para teste.
    media_global = float(base[ATRIBUTO].mean())
    dp_global = float(base[ATRIBUTO].std(ddof=0))
    if dp_treino == 0 or dp_global == 0 or not np.isfinite([dp_treino, dp_global]).all():
        raise ValueError('Desvio padrão inválido para normalização')

    variantes = {
        'segura': (media_treino, dp_treino),
        'global': (media_global, dp_global),
    }
    for nome, (media, dp) in variantes.items():
        for papel, parte in [('treino', treino), ('teste', teste)]:
            resultado = parte.copy()
            resultado[ATRIBUTO] = (resultado[ATRIBUTO] - media) / dp
            destino = Path(DIR_SAIDA) / f'abt_norm_{nome}_{papel}.csv'
            grava_orange(destino, resultado, cabecalho)

    secao('RESUMO DO EXPERIMENTO')
    info(f'Atributo alterado nas duas versões: {ATRIBUTO}')
    info('Todas as outras features e o alvo permaneceram inalterados')
    info(f'Treino: {len(treino)} clientes | Teste: {len(teste)} clientes')
    info('Taxas de recompra: ' + f'treino {treino[ALVO].mean():.4%} | teste {teste[ALVO].mean():.4%}')
    info(f'Média / DP apenas do treino: {media_treino:.6f} / {dp_treino:.6f}')
    info(f'Média / DP da base inteira: {media_global:.6f} / {dp_global:.6f}')
    ok('Experimento concluído.')
    print(f'[tempo] total experimento | {perf_counter() - inicio:.1f}s')


if __name__ == '__main__':
    main()
