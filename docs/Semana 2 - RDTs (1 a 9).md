# MERCATTO VAREJO — Registo de Decisões de Transformação (RDT)

**Entrega 2 — Semana 2: Extrair e conformar**  
**Fichas:** RDT-01 a RDT-09  
**Equipe:** Gabriel Siqueira Matias (2023278440020), Krystyan Douglas Santos Costa (2023278440026) e Pedro Alexandre Souza de Campos (2023278440034)  
**Disciplina / turma:** Mineração de Dados / IFMT  
**Data de consolidação:** 16/10/2026  
**Repositório github:** https\://github.com/Gabriel-Siqueira-Matias/Atividade-2-Mineracao-de-Dados  
**Versão do pipeline / commit Git:** a1b37a3 \- Primeiro commit (Gabriel\_Siqueira\_Matias)  
**Ambiente de referência:** Docker Compose, `ESCALA=1` (ERP MySQL; e-commerce PostgreSQL; SAC CSV; avaliações NDJSON; DW PostgreSQL).

---

## 1\. Comprovação da Execução do Pipeline

### 1.1 Extração — `01_extracao.py`

| Fonte | Tabelas / Coleções Origem | Tabela Destino (`stg`) | Registros Extraídos |
| :---- | :---- | :---- | ----: |
| ERP legado (MySQL) | `CLIENTES`, `PRODUTOS`, `PEDIDOS`, `ITENS_PEDIDO` | `stg.legado_*` | 577.336 |
| E-commerce (PostgreSQL) | `customer`, `product`, `orders`, `order_item`, `category` | `stg.ecommerce_*` | 1.372.388 |
| SAC | `atendimentos_sac.csv` | `stg.atendimentos_sac` | 96.140 |
| Marketplace | `avaliacoes_marketplace.json` (NDJSON) | `stg.avaliacoes_marketplace` | 160.000 |
| **Total** | **11 fontes / coleções** | **Esquema `stg`** | **2.205.864** |

- Leitura em blocos (`chunksize = 50.000`) aplicada nas conexões relacionais e gravação em lote (`chunksize = 5.000`) no DW.  
- Inclusão dos metadados `_fonte`, `_extraido_em` e `_linha_origem`.  
- **Resultado do QA:** Asserções de igualdade de linhas com a origem, ausência de nulos no protocolo do SAC e unicidade do `review_id` aprovadas.

### 1.2 Transformação e Carga Analítica — `02_transformacao.py`

| Tabela Persistida | Esquema | Registros Carregados |
| :---- | :---- | ----: |
| `dim_cliente` | `dw` | \~119.480 |
| `dim_status` | `dw` | 9 |
| `dim_tempo` | `dw` | *Gerado a partir das datas únicas de pedido/entrega* |
| `dim_canal` | `dw` | 3 |
| `dim_produto` | `dw` | 15.000 |
| `fato_pedido` | `dw` | 410.000 (130.000 Legado \+ 280.000 E-com) |
| `fato_avaliacao` | `dw` | 160.000 |

---

## RDT-01 — Carga Completa versus Incremental

**Etapa:** `01_extracao.py` (`extrai_mysql`, `extrai_postgres`, `extrai_sac`, `extrai_avaliacoes`).

* **a) Evidência observada:** Foram extraídos 2.205.864 registros distribuídos em quatro fontes heterogêneas. As bases de origem não possuem mecanismos padronizados de captura de dados alterados (CDC) ou colunas confiáveis de modificação recente em todas as tabelas.  
* **b) Alternativas consideradas:**  
  1. *Carga Incremental por data:* Reduziria tráfego de rede, mas exigiria triggers/CDC indisponíveis nas origens somente leitura.  
  2. *Carga Completa (Full Dump):* Recria a camada de staging a cada execução utilizando `if_exists='replace'` no primeiro bloco e `append` nos blocos seguintes.  
* **c) Decisão e justificativa:** Adotar **Carga Completa**. Prioriza a reprodutibilidade integral do laboratório e a garantia de consistência da camada `stg`, lendo em lotes (`LOTE = 50_000`) para não sobrecarregar a memória RAM.  
* **d) O que se perde:** Perde-se a cópia histórica do estado anterior da `stg` a cada reexecução e incorre-se no custo computacional de releitura de dados inalterados.  
* **e) Registros afetados:** **2.205.864 / 2.205.864 (100%)** dos registros reextraídos na execução. Perda de dados: **0 / 2.205.864 (0%)**.  
* **f) Asserções associadas:** Validações de igualdade de contagem (`COUNT(*)`) entre tabelas de origem e tabelas staging.

---

## RDT-02 — Identidade do Cliente entre ERP e E-Commerce

**Etapa:** `02_transformacao.py` → `unifica_clientes()`.

* **a) Evidência observada:** O ERP possui 47.250 clientes e o E-commerce possui 85.000 clientes (total de 132.250 cadastros). Existem CPFs ausentes/nulos e CPFs com dígitos verificadores inválidos em ambos os sistemas.  
* **b) Alternativas consideradas:**  
  1. *Pareamento estrito apenas por CPF bruto:* Uniria cadastros mesmo com CPFs inválidos ou mascarados.  
  2. *Pareamento em Duas Fases (CPF Válido \+ Nome/Localidade 1-para-1):* Valida dígitos verificadores antes do merge por CPF e utiliza aproximação por `Nome Normalizado + Cidade Normalizada + UF Normalizada` sem duplicatas para as sobras.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. A função `cpf_valido()` filtra registros confiáveis. Em seguida, aplica-se merge por `cpf_limpo`. Para as sobras sem CPF/com CPF inválido, aplica-se pareamento estrito de 1-para-1 em `nome_normalizado`, `cidade_normalizado` e `uf_normalizado`. Os excedentes entram como exclusivos de cada base (`LEGADO` ou `ECOM`).  
* **d) O que se perde:** Podem ocorrer falsos negativos (mesmo cliente com grafia de nome ou cidade ligeiramente diferente não é unido) e potencial risco de falsos positivos no pareamento por nome/localidade se existirem homónimos exatos na mesma cidade.  
* **e) Registros afetados:** Todos os **132.250 cadastros de origem** são processados, resultando em uma dimensão unificada `dw.dim_cliente` deduplicada por `cpf_normalizado` válido.  
* **f) Asserções associadas:** `qa.unico("cpf_normalizado em dim_cliente")` e `qa.igual("origens unificacao conhecidas")`.

---

## RDT-03 — Deduplicação Interna de Clientes no Legado

**Etapa:** `02_transformacao.py` → `unifica_clientes()`.

* **a) Evidência observada:** O sistema legado contém cadastros duplicados do mesmo cliente sob IDs distintos com o mesmo CPF.  
* **b) Alternativas consideradas:**  
  1. *Manter todas as duplicatas:* Preserva IDs originais, mas fragmenta o histórico analítico do cliente.  
  2. *Deduplicação Interna Prévia (`drop_duplicates` mantendo o primeiro ID):* Agrupa clientes por CPF válido antes do cruzamento entre sistemas, atribuindo um `cliente_sk` único no DW.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. Aplica-se `.sort_values('id_legado').drop_duplicates('cpf_limpo', keep='first')` na base do legado com CPF válido antes da unificação com o E-commerce.  
* **d) O que se perde:** Perde-se a referência secundária dos IDs do legado que foram descartados na deduplicação interna da dimensão.  
* **e) Registros afetados:** Aproximadamente **2.604 cadastros do legado** agrupados em 1.302 CPFs duplicados (\~5,5% do legado).  
* **f) Asserções associadas:** Garantia de unicidade de `cpf_normalizado` não nulo na `dw.dim_cliente`.

---

## RDT-04 — Conformação e Hierarquia de Status dos Pedidos

**Etapa:** `02_transformacao.py` → `conforma_status()`.

* **a) Evidência observada:** O Legado possui 4 status (`ENTREGUE`, `CANCELADO`, `EM TRANSITO`, `SEPARACAO`). O E-commerce possui 5 status (`delivered`, `canceled`, `payment_declined`, `shipped`, `processing`).  
* **b) Alternativas consideradas:**  
  1. *Mapeamento direto simples:* Renomear strings sem criar estrutura categórica.  
  2. *Modelagem Dimensional Hierárquica em `dw.dim_status`:* Mapear cada par `(status_origem, sistema)` para um `status_conformado` unificado (`CONCLUIDO`, `CANCELADO`, `EM_ANDAMENTO`, `EM_PROCESSAMENTO`) e detalhar `motivo_cancelamento` quando disponível.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. A dimensão `dw.dim_status` padroniza os status de negócio mantendo a rastreabilidade do motivo (ex.:  
  `payment_declined` → `status_conformado: CANCELADO`, `motivo_cancelamento: RECUSA_PAGAMENTO`

  `canceled` → `status_conformado: CANCELADO`, `motivo_cancelamento: CANCELADO_CLIENTE`

  `CANCELADO` →  `status_conformado: CANCELADO, motivo_cancelamento: NAO_INFORMADO`)  
* **d) O que se perde:** A granularidade operacional específica dos sistemas de origem ao consultar apenas a coluna `status_conformado`.  
* **e) Registros afetados:** **410.000 pedidos (100%)** classificados em 9 regras na `dw.dim_status`.  
* **f) Asserções associadas:** Integridade referencial entre `dw.fato_pedido.status_sk` e `dw.dim_status.status_sk`.

---

## RDT-05 — Semântica dos Valores Monetários, Fretes e Descontos

**Etapa:** `02_transformacao.py` → `conforma_valores()`.

* **a) Evidência observada:** No Legado, o valor do frete já estava contido no total e havia descontos-sentinela (`-1`). No E-commerce, o frete não estava somado ao `total_amount`. Adicionalmente, existem fretes gravados com valores negativos nas tabelas de staging.  
* **b) Alternativas consideradas:**  
  1. *Manter totais brutos originais:* Geraria métricas financeiras incomparáveis entre canais.  
  2. *Recomposição Matemática Explícita:* Padronizar na `dw.fato_pedido` as colunas `valor_mercadoria` (`qtd * vl_unitario`), `valor_frete` (módulo `abs()`), `valor_desconto` e `valor_total = valor_mercadoria + valor_frete - valor_desconto`.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. Aplica-se `.abs()` aos valores de frete para corrigir anomalias de sinal e zera-se o desconto caso seja negativo (`vl_desconto < 0` para tratar a sentinela `-1`). Em seguida, calcula-se o `valor_total` de forma idêntica e homogénea para todos os pedidos.  
* **d) O que se perde:** O valor bruto original do frete com sinal negativo é sobrescrito na camada DW pela sua magnitude positiva.  
* **e) Registros afetados:** **410.000 pedidos (100%)** padronizados. Correção do valor do frete afetando 1.174 pedidos com frete negativo (\~0,29%) e tratamento da sentinela `-1` em 4.056 itens do legado (\~1,02%).  
* **f) Asserções associadas:** Preservação da cardinalidade total dos pedidos em `dw.fato_pedido`.

---

## RDT-06 — Construção da Dimensão Tempo e Canal

**Etapa:** `02_transformacao.py` → `conforma_valores()`.

* **a) Evidência observada:** Pedidos de ambas as fontes contêm datas de realização e entrega que precisam alimentar análises temporais e por canal de vendas.  
* **b) Alternativas consideradas:**  
  1. *Usar colunas de data brutas na fato:* Dificulta agregações por trimestre, dia da semana ou fim de semana.  
  2. *Modelagem Conforme de `dw.dim_tempo` e `dw.dim_canal`:* Extrair o universo de datas válidas de ambas as fontes, filtrar limites lógicos (`>= 2000-01-01`) e popular dimensões dedicadas.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. A `dw.dim_tempo` é carregada com atributos derivados (`ano`, `trimestre`, `mes`, `dia`, `dia_semana`, `eh_fim_semana`). A `dw.dim_canal` mapeia `LOJA_FISICA` → `FISICO`, `WEBSITE` → `ONLINE_PROPRIO` e `MARKETPLACE` → `MARKETPLACE`.  
* **d) O que se perde:** Datas anteriores a `2000-01-01` são excluídas da dimensão de tempo por serem consideradas ruídos/sentinelas.  
* **e) Registros afetados:** Todas as datas únicas de pedidos e entregas válidas do universo de 410.000 pedidos.  
* **f) Asserções associadas:** População sem nulos das tabelas `dw.dim_tempo` e `dw.dim_canal`.

---

## RDT-07 — Taxonomia e Padronização de Categorias de Produtos

**Etapa:** `02_transformacao.py` → `conforma_categorias()`.

* **a) Evidência observada:** O Legado possui 3.000 produtos com categorias em texto livre. O E-commerce possui 12.000 produtos associados a uma tabela de categorias.  
* **b) Alternativas consideradas:**  
  1. *Manter categorias originais:* Resulta em dezenas de categorias redundantes e não comparáveis.  
  2. *Mapeamento por Regras de Negócio (`categoria_nivel1`):* Agrupar termos equivalentes via Expressões Regulares/substrings para uma taxonomia conformada na `dw.dim_produto` (`ELETRODOMESTICOS`, `INFORMATICA`, `JOGOS`, `CASA_E_DECORACAO`, `FERRAMENTAS`, `ESPORTE_E_LAZER`, `AUDIO_E_VIDEO`, `TELEFONIA`, `OUTROS`).  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. A função `mapeia_categoria()` varre as descrições/nomes de categorias originais e atribui uma classe padronizada, garantindo que 100% dos produtos tenham uma categoria analítica utilizável.  
* **d) O que se perde:** Perde-se a subcategoria detalhada original do produto na visão agregada de nível 1\.  
* **e) Registros afetados:** **15.000 produtos (100%)** (3.000 do Legado \+ 12.000 do E-commerce) unificados na `dw.dim_produto`.  
* **f) Asserções associadas:** Carga sem falhas e integridade de `produto_sk` na `dw.dim_produto`.

---

## RDT-08 — Tratamento de Datas Incoerentes, Ausências e Sentinelas

**Etapa:** `02_transformacao.py` → `trata_datas_e_ausencias()`.

* **a) Evidência observada:** O banco de dados contém registros de datas de entrega nulas, datas-sentinela (ex.: menores que `2000-01-01`) ou datas de entrega registradas como anteriores à data do pedido.  
* **b) Alternativas consideradas:**  
  1. *Manter datas incoerentes ou substituir por datas fictícias:* Distorce o cálculo de prazos de entrega e métricas logísticas.  
  2. *Invalidação Seletiva (`NULL`):* Converter para `NULL` qualquer data de entrega menor que `2000-01-01` ou menor que a própria data do pedido (`data_entrega_sk < data_pedido_sk`). O cálculo de `dias_entrega` é realizado exclusivamente para registros válidos.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. Executa-se um comando SQL `UPDATE` direto no DW que limpa as datas de entrega inválidas e calcula `dias_entrega = (data_entrega_sk - data_pedido_sk)` apenas para o conjunto coerente.  
* **d) O que se perde:** Fica impossibilitado o cálculo do tempo de entrega para pedidos afetados por erros de digitação de data na origem.  
* **e) Registros afetados:** 1.025 pedidos com data-sentinela `1900-01-01` no Legado (\~0,79%) e 2.458 pedidos com datas de entrega invertidas em relação ao pedido (\~0,60% do total).  
* **f) Asserções associadas:** Nulos mantidos corretamente como `None` no banco sem falhas de conversão de tipo.

---

## RDT-09 — Rastreabilidade entre SAC, Avaliações e Fato Pedido

**Etapa:** `02_transformacao.py` → `liga_sac_e_avaliacoes()`.

* **a) Evidência observada:** Os atendimentos do SAC e as avaliações de marketplace fazem referência aos pedidos originais através de identificadores compostos/formatados (ex.: `LEG-1234`, `ECOM-5678`).  
* **b) Alternativas consideradas:**  
  1. *Descartar avaliações/SAC sem correspondência exata:* Perderia dados de satisfação do cliente.  
  2. *Cruzamento por Chave Composta/Prefixo de Origem:* Formatar a coluna `id_origem` na `dw.fato_pedido` com o prefixo do sistema e relacioná-la às tabelas `stg.sac_atendimentos` e `stg.avaliacoes_marketplace`, populando a `dw.fato_avaliacao`.  
* **c) Decisão e justificativa:** Adotada a **Alternativa 2**. O código extrai a origem do pedido a partir da referência (`order_ref`), mapeia `LEG` → `LEGADO` e realiza o join com a `dw.fato_pedido` para obter o `pedido_sk` correto.  
* **d) O que se perde:** Avaliações ou atendimentos que refiram pedidos inexistentes nas bases principais não são vinculados à fato de pedidos.  
* **e) Registros afetados:** **160.000 avaliações de marketplace** e **96.140 atendimentos de SAC** processados.  
* **f) Asserções associadas:** Carga bem-sucedida de `dw.fato_avaliacao` e preservação da cardinalidade de pedidos.

---

## 2\. Quadro-Resumo das Decisões de Transformação

| Ficha | Decisão Principal | Tabela Destino DW | Impacto Absoluto e Percentual | Informação Sacrificada |
| :---- | :---- | :---- | :---- | :---- |
| **RDT-01** | Carga Completa via lotes | `stg.*` | 2.205.864 registros (100%) | Histórico da versão anterior do staging |
| **RDT-02** | Pareamento CPF Válido \+ Nome/Localidade | `dw.dim_cliente` | 132.250 cadastros processados | Risco de falsos negativos/positivos por localidade |
| **RDT-03** | Deduplicação Interna Legado | `dw.dim_cliente` | \~2.604 cadastros (\~5,5% do legado) | IDs secundários descartados da dimensão |
| **RDT-04** | Status Conformado \+ Motivo | `dw.dim_status` | 410.000 pedidos (100%) | Granularidade exata do status bruto na visão agregada |
| **RDT-05** | Recomposição Monetária e Frete Absoluto | `dw.fato_pedido` | 410.000 pedidos / 1.174 fretes negativos | Sinal negativo original do frete |
| **RDT-06** | Dimensão Tempo e Canal Conforme | `dw.dim_tempo`, `dw.dim_canal` | Todas as datas válidas de 410k pedidos | Datas anteriores a 2000 descartadas |
| **RDT-07** | Padronização de Categorias de Produtos | `dw.dim_produto` | 15.000 produtos (100%) | Subcategorias detalhadas na visão de nível 1 |
| **RDT-08** | Invalidação de Datas Incoerentes | `dw.fato_pedido` | \~3.483 pedidos com datas inválidas (\~0,85%) | Prazos de entrega de registros corrompidos |
| **RDT-09** | Vínculo SAC/Avaliações por Chave Composta | `dw.fato_avaliacao` | 160.000 avaliações / 96.140 SACs | Registros órfãos não vinculados |

---

## 3\. Reflexão Final

* **Qual decisão mais afetaria o modelo preditivo?** A regra de unificação de clientes (RDT-02/03). Agrupar incorretamente dois clientes distintos unifica os seus históricos de compra, alterando métricas de recorrência e valor de vida do cliente (LTV). Por outro lado, não unir um mesmo cliente fragmenta o histórico.  
* **Qual decisão técnica afetaria pessoas reais?** O tratamento de ausências e notas de atendimento/avaliação. Converter a ausência de resposta do SAC em nota zero atribuiria um julgamento negativo não realizado pelo cliente, afetando avaliações de desempenho do suporte.  
* **Que informação fez falta?** Faltou um identificador único universal de cliente entre as empresas e uma documentação sobre a causa raiz dos fretes negativos e dos descontos-sentinela `-1`. Estas incertezas foram tratadas via regras numéricas transparentes e rastreáveis no pipeline.