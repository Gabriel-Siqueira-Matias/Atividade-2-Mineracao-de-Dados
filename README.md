# Laboratório de ETL — Mercatto Varejo

Projeto prático de **Mineração de Dados — IFMT, 2026/2**.

O projeto integra quatro fontes de dados (ERP MySQL, e-commerce PostgreSQL, SAC CSV e avaliações NDJSON), implementa um Data Warehouse em esquema estrela e produz duas ABTs (uma correta e outra intencionalmente contaminada por vazamento de dados) para estudar a previsão de recompra. Inclui controle de qualidade, auditoria automatizada, testes de reprodutibilidade e fluxos do Orange Data Mining.

## 1. Pré-requisitos

- Docker Desktop iniciado e Docker Compose v2 disponível.
- Windows PowerShell 5.1 para usar `executar_pipeline.ps1`; os comandos Docker individuais funcionam também em outros terminais.
- Orange Data Mining instalado localmente para abrir os fluxos `.ows`.
- Memória RAM e espaço em disco suficientes para a criação e a carga inicial dos bancos. Aproximadamente 8 GB de RAM ou mais é uma referência prática, dependente da máquina.
- Acesso às imagens Docker, especialmente na primeira inicialização.

Para conferir o ambiente:

```powershell
docker --version
docker compose version
docker compose config --quiet
```

## 2. Estrutura do projeto

```text
lab-etl-varejo/
├── .env.example                    # Escala dos dados gerados
├── docker-compose.yml              # Serviços e healthchecks
├── executar_pipeline.ps1           # Reexecução, reconstrução e auditoria
├── gerador/                        # Gerador determinístico e carga inicial
├── sql/                            # Esquemas SQL
├── etl/
│   ├── 00_perfilamento.py
│   ├── 01_extracao.py
│   ├── 02_transformacao.py
│   ├── 03_carga_dw.py
│   ├── 04_abt.py
│   ├── 05_exportar_orange.py
│   ├── auditar_pipeline.py
│   ├── experimento_normalizacao.py
│   ├── qualidade/                  # Asserções
│   ├── orange/                     # Workflows .ows
│   └── dados_orange/               # Capturas de resultados
├── docs/                           # RDT e relatório em Markdown
├── dump_sql.zip                    # Dump compactado fornecido no repositório
├── validacao_pipeline/
│   └── referencia_pipeline.json    # Referência persistente
├── execucoes_pipeline/             # Logs locais, ignorados pelo Git
├── dados/                          # Dados gerados, ignorados pelo Git
└── saida/                          # CSVs e auditoria, ignorados pelo Git
```

**Importante:** `dados/`, `saida/` e `execucoes_pipeline/` podem não existir em um clone novo. São criadas durante as execuções. A pasta `validacao_pipeline/` contém a referência utilizada para comparar os resultados. Os **logs** ficam em `execucoes_pipeline/`.

## 3. Fontes e arquitetura

| Origem | Tecnologia e codificação | Dados |
|---|---|---|
| ERP legado | MySQL 8, `latin1` | Clientes, produtos, pedidos e itens |
| E-commerce | PostgreSQL 16, UTF-8 | Clientes, produtos, pedidos, itens e categorias |
| SAC | CSV `latin-1`, delimitador `;` | Atendimentos |
| Marketplace | NDJSON UTF-8 | Avaliações |

Fluxo: **fontes → staging (`stg`) → transformação e conformação → dimensões e fatos (`dw`) → ABTs (`abt`) → CSVs → Orange**.

O Compose inclui os serviços `mysql_legado`, `pg_ecommerce`, `pg_dw`, `seed`, `etl` e `adminer`. O `seed` gera e carrega os dados, criando os esquemas e finalizando a tarefa. O `etl` executa os scripts Python, enquanto o Adminer permite consultar os bancos pelo navegador.

## 4. Configuração inicial

### 4.1 Escala

Na raiz do repositório, se o arquivo `.env` ainda não existir:

```powershell
Copy-Item .env.example .env
```

A configuração validada é:

```dotenv
ESCALA=1.0
```

**Mantenha `ESCALA=1.0` para reproduzir as contagens e os hashes de referência.** Outras escalas alteram a quantidade de registros gerados e podem invalidar os valores fixos de `etl/auditar_pipeline.py`.

### 4.2 Portas externas

As portas do **lado esquerdo** da expressão `HOST:CONTAINER` são publicadas no computador local; as do lado direito são usadas dentro dos contêineres.

| Serviço | Configuração atual | Aplicação |
|---|---|---|
| `mysql_legado` | `3307:3306` | ERP legado |
| `pg_ecommerce` | `15432:5432` | E-commerce |
| `pg_dw` | `15433:5432` | DW |
| `adminer` | `8080:8080` | Interface web |

Se `15432` estiver ocupada, por exemplo, troque apenas `"15432:5432"` por `"25432:5432"` em `docker-compose.yml`. Depois use `docker compose up -d`. As conexões Python entre contêineres continuam usando `mysql_legado:3306`, `pg_ecommerce:5432` e `pg_dw:5432`; **não altere os DSNs internos de `etl/config.py` só por causa de uma colisão de porta no Windows**.

O Adminer fica em `http://localhost:8080`, ou na porta externa escolhida para o serviço.

## 5. Iniciando o ambiente pela primeira vez

Na raiz do repositório:

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=60 seed
```

Verifique se os três bancos estão marcados como `healthy`, se `seed` terminou com **`Exited (0)`** e a mensagem **`ambiente pronto.`**, e se `etl` está `Up`. O `seed` é uma tarefa de inicialização: **`Exited (0)` indica sucesso, não falha**. O Compose só inicia o `seed` depois que os três bancos estiverem saudáveis e só libera o `etl` depois de o `seed` terminar com sucesso.

Não é necessário restaurar manualmente o `dump_sql.zip` para realizar o procedimento normal: o ambiente é gerado e carregado pelas rotinas do projeto.

## 6. Executando as seis etapas oficiais

Os comandos são executados nesta ordem:

```powershell
docker compose exec -T etl python -u 01_extracao.py
docker compose exec -T etl python -u 02_transformacao.py
docker compose exec -T etl python -u 03_carga_dw.py
docker compose exec -T etl python -u 04_abt.py --correta
docker compose exec -T etl python -u 04_abt.py --vazamento
docker compose exec -T etl python -u 05_exportar_orange.py
```

| Etapa | Objetivo |
|---|---|
| `01_extracao.py` | Ler as quatro fontes e preencher a camada `stg` |
| `02_transformacao.py` | Conformar clientes, status, valores, datas, categorias e relacionamentos |
| `03_carga_dw.py` | Carregar e validar tabelas dimensionais e fatos |
| `04_abt.py --correta` | Gerar atributos históricos até a data de corte |
| `04_abt.py --vazamento` | Gerar o cenário contaminado intencionalmente |
| `05_exportar_orange.py` | Exportar as duas ABTs no formato Orange |

`-T` desativa a alocação de terminal interativo; `-u` faz o Python exibir a saída sem buffer. O perfilamento pode ser executado separadamente, se necessário, com `docker compose exec -T etl python -u 00_perfilamento.py`. Ele não integra os seis comandos do automatizador.

## 7. Automatizador `executar_pipeline.ps1`

O script possui a flag opcional: `-ReconstruirDoZero`.

### 7.1 Modo normal (sem apagar volumes)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1
```

Nesse modo, o script **preserva os volumes e as pastas `dados/` e `saida/`**, mas **reprocessa as etapas**: os dados de destino e CSVs podem ser sobrescritos pelos scripts Python. É necessário que os contêineres e o `seed` estejam disponíveis e prontos.

### 7.2 Modo destrutivo: reconstrução completa

> **ATENÇÃO: operação destrutiva.** A presença da flag autoriza a exclusão dos volumes Docker vinculados ao projeto, de `dados/` e de `saida/`. Faça backup dos bancos e dos arquivos que precisar preservar.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1 -ReconstruirDoZero
```

Sequência do script (O modo normal só executa a 4 até a 6, já o modo destrutivo executa as 6):

1. Executa `docker compose down -v --remove-orphans`, removendo os contêineres, a rede e os volumes nomeados do projeto.
2. Exclui as pastas locais `dados/` e `saida/` e as recria vazias.
3. Executa `docker compose up -d --build` e acompanha a geração das imagens, a saúde dos bancos e a conclusão do `seed`.
4. Executa os seis scripts Python na ordem prevista.
5. Executa `etl/auditar_pipeline.py` e gera `saida/auditoria_pipeline.json`.
6. Compara os hashes SHA-256 dos dois CSVs com a referência persistente e registra `[SUCESSO]` ou `[FALHA]`.

O script **não apaga** `docs/`, `etl/`, `sql/`, `dump_sql.zip`, `validacao_pipeline/` ou `execucoes_pipeline/`. O tempo total inclui também a reconstrução do Docker.

**Clones paralelos:** o Compose usa o nome fixo de projeto `lab-etl-varejo` e nomes de contêineres fixos. Copiar a pasta do projeto não isola automaticamente os volumes/contêineres. Não execute a reconstrução destrutiva quando outra cópia estiver usando os mesmos recursos Docker.

### 7.3 Logs e tratamento de erros

O PowerShell usa `GravarLinha()` para registrar mensagens no arquivo e exibi-las no terminal. A função `ExecutarDocker()` captura `stdout` e `stderr` e confere o código de saída nativo do Docker. Assim, mensagens de progresso como `Image ... Building` e `Container ... Stopping` não são confundidas automaticamente com falhas no Windows PowerShell 5.1. Se o comando realmente terminar com código diferente de zero, o automatizador registra `[FALHA]` e encerra com código 1.

## 8. Referência, histórico e auditoria

| Caminho | Finalidade | Preservado após reconstrução? |
|---|---|---|
| `validacao_pipeline/referencia_pipeline.json` | Auditoria de referência persistente | **Sim** |
| `execucoes_pipeline/execucao-AAAAMMDD-HHMMSS.log` | Log local de cada execução | **Sim** |
| `saida/auditoria_pipeline.json` | Resultado da auditoria atual | Não: regenerado |
| `saida/abt_recompra_correta.csv` | ABT correta exportada | Não: regenerado |
| `saida/abt_recompra_vazamento.csv` | ABT experimental exportada | Não: regenerado |

O script registra o último identificador em `qa.execucao_assercao` **antes** da execução. Esse valor é passado à auditoria por `--desde-id`, para considerar somente as novas asserções. Em um banco completamente novo, o ID anterior é `0`. As asserções permanecem no histórico do banco enquanto os volumes forem preservados.

A auditoria Python confere contagens, integridade de vínculos, atributos, alvo e cabeçalhos Orange (três linhas). Os arquivos CSV são ordenados logicamente por `cliente_sk` antes da produção do hash, permitindo comparar conteúdo independentemente da ordem original das linhas.

**Primeira execução:** caso `validacao_pipeline/referencia_pipeline.json` ainda não exista, o script copia o JSON atual para criá-la. Nesse primeiro caso, **não existe comparação com execução anterior** e a referência deve ser revisada. **Execuções seguintes:** se a referência existir, o script compara o SHA-256 dos dois CSVs; se qualquer hash divergir, falha, sem sobrescrever automaticamente a referência.

O arquivo `validacao_pipeline/referencia_pipeline.json` é versionado. Os novos logs, os CSVs e o JSON de auditoria atual ficam fora do Git, conforme `.gitignore`.

## 9. Saídas esperadas com `ESCALA=1.0`

| Tabela / indicador | Quantidade |
|---|---:|
| `dw.dim_cliente` | 115.674 |
| `dw.dim_produto` | 15.000 |
| `dw.dim_status` | 9 |
| `dw.dim_canal` | 3 |
| `dw.fato_pedido` | 410.000 |
| `dw.fato_item_pedido` | 1.371.876 |
| `dw.fato_atendimento` | 95.000 |
| `dw.fato_avaliacao` | 160.000 |
| Cada ABT (`abt.recompra_90d` / `abt.recompra_90d_vazamento`) | 108.064 linhas |
| Colunas de cada ABT / CSV | 18 (15 atributos, 2 metadados, 1 alvo) |
| Clientes com recompra positiva | 24.043 (aprox. 22,25%) |

A carga identifica 5.868 itens órfãos na origem legada e 14.709 na origem e-commerce. Os itens permanecem no staging e não são associados artificialmente a pedidos no DW.

A auditoria do pipeline espera **28 asserções aprovadas**, distribuídas em 11 (extração), 4 (transformação), 4 (DW), 5 (ABT correta) e 4 (ABT com vazamento). Com `ESCALA=1.0`, também confere as contagens esperadas de 21 tabelas.

## 10. Orange Data Mining

**Os CSVs devem ser gerados localmente antes de abrir os workflows `.ows` em outro computador.**

| Workflow em `etl/orange/` | Arquivo(s) em `saida/` | Como gerar |
|---|---|---|
| `mercatto_abt_correta.ows` | `abt_recompra_correta.csv` | Executar as seis etapas ou exportador após a ABT correta |
| `mercatto_abt_vazamento.ows` | `abt_recompra_vazamento.csv` | Executar as seis etapas ou exportador após a ABT com vazamento |
| `mercatto_normalizacao_segura.ows` | `abt_norm_segura_treino.csv`, `abt_norm_segura_teste.csv` | Executar `experimento_normalizacao.py` |
| `mercatto_normalizacao_global.ows` | `abt_norm_global_treino.csv`, `abt_norm_global_teste.csv` | Executar `experimento_normalizacao.py` |

Para gerar os quatro arquivos adicionais de normalização **depois** de ter gerado `abt_recompra_correta.csv`:

```powershell
docker compose exec -T etl python -u experimento_normalizacao.py
```

**Esse experimento auxiliar não faz parte das seis etapas automatizadas.**

### 10.1 Abrir o `.ows` em outra máquina

1. Abra o arquivo `.ows` no Orange.
2. Entre em cada widget **File**; se o caminho estiver inválido, escolha **Browse** e localize os CSVs correspondentes dentro da pasta local `saida/`.
3. Confirme `alvo_recompra_90d` como classe, e `cliente_sk` e `data_corte` como metadados (não como atributos preditivos).
4. Abra **Test & Score**, **Confusion Matrix** e **ROC Analysis**.

Os `.ows` podem conter caminhos absolutos utilizados no computador em que foram salvos; portanto, será necessário selecionar novamente os arquivos em outros computadores.

### 10.2 Fluxos principais (correta e vazamento)

Os dois workflows usam **File → Data Sampler**, com a saída **Data Sample** para a entrada **Data** de **Test & Score** e a saída **Remaining Data** para a entrada **Test Data**. Os três modelos **Naive Bayes**, **Logistic Regression** e **Tree** são ligados como *Learners* independentes ao Test & Score. As saídas do Test & Score seguem para a matriz de confusão e o gráfico ROC.

Utilize amostragem **70/30 estratificada** e configurações equivalentes para uma comparação válida. A diferença intencional entre os fluxos principais é a ABT usada como entrada.

### 10.3 Fluxos auxiliares de normalização

Nesses workflows, são usados **dois widgets File**: um para treino e outro para teste. Eles se conectam diretamente às entradas **Data** e **Test Data** do **Test & Score**, respectivamente. **Não é preciso dividir esses arquivos novamente**, pois a separação estratificada e determinística já foi feita pelo script `experimento_normalizacao.py`.

As capturas dos resultados, incluindo Test & Score, Confusion Matrix e ROC, estão em `etl/dados_orange/`.

## 11. Experimento temporal e vazamento de dados

A questão analítica é: **com o histórico de compras até 30/06/2025, o cliente fará uma nova compra nos 90 dias seguintes?**

- **Data de corte:** `2025-06-30`.
- **Janela de previsão:** `2025-07-01` a `2025-09-28`.
- **ABT correta:** os atributos preditivos usam dados disponíveis até o corte; o intervalo futuro serve apenas à definição do alvo.
- **ABT com vazamento:** inclui intencionalmente atributos calculados com dados após o corte e normalização global anterior à separação treino/teste.

O experimento auxiliar de normalização avalia especificamente a diferença entre calcular o z-score com **treino apenas** (seguro) e com **a base inteira** antes da divisão (vazamento), mantendo os mesmos registros e alvos.

O objetivo é analisar o efeito do vazamento sobre as métricas de avaliação, não apenas alcançar AUCs maiores.

## 12. Artefatos de entrega

| Arquivo ou pasta | Finalidade |
|---|---|
| `docs/RDT - 1 a 15.md` | 15 fichas, tabela-resumo e reflexão |
| `docs/Relatorio_Final_Experimento_Vazamento_Mercatto.md` | Texto editável do relatório experimental |
| `dump_sql.zip` | Dump compactado publicado no repositório |
| `etl/orange/*.ows` | Workflows do Orange |
| `etl/dados_orange/` | Imagens dos resultados |
| `validacao_pipeline/referencia_pipeline.json` | Referência persistente para comparação |

## 13. Diagnóstico e soluções comuns

### 13.1 MySQL `unhealthy` durante a primeira criação

```powershell
docker compose ps -a
docker compose logs --tail=100 mysql_legado
docker inspect lab_mysql_legado --format '{{json .State.Health}}'
```

Confirme o `start_period: 240s`. Se o MySQL estiver `healthy` depois de uma interrupção, tente **retomar sem destruir volumes novamente**:

```powershell
docker compose up -d
docker compose ps -a
docker compose logs --tail=60 seed
```

Quando `seed` terminar com código zero e `etl` ficar `Up`, use o **modo normal** do `executar_pipeline.ps1` para completar as etapas.

### 13.2 `seed` não terminou

Verifique `docker compose logs --tail=100 seed` e `docker compose ps -a`. O `seed` só roda quando os bancos estão saudáveis; o `etl` depende de sua conclusão com êxito.

### 13.3 Porta externa ocupada

Altere **somente a porta externa** em `docker-compose.yml` e valide com `docker compose config --quiet`. Não mude a porta interna de conexão de outro contêiner.

### 13.4 `HASH DIFERENTE` ou auditoria reprovada

Confira `ESCALA=1.0`, a referência em `validacao_pipeline/`, o JSON atual em `saida/` e o log em `execucoes_pipeline/`. **Não substitua o JSON de referência apenas para ocultar divergências.** Para executar o auditor isoladamente é necessário passar o ID registrado antes do conjunto de asserções que se deseja validar.

### 13.5 CSV ausente no Orange

Execute as ABTs e a exportação. Reabra os widgets **File** e escolha os CSVs corretos da pasta `saida/`.

### 13.6 Terminal com acentos ou saída travada

O PowerShell automatizado configura a saída em UTF-8. Em versões clássicas do console do Windows, desative o **QuickEdit / Edição Rápida** se selecionar texto estiver interrompendo a execução.