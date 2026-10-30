# Laboratório de ETL — Mercatto Varejo

Projeto prático de Mineração de Dados — IFMT, 2026/2.

O repositório integra quatro fontes heterogêneas (ERP MySQL/latin1, e-commerce PostgreSQL/UTF-8, SAC em CSV e avaliações em NDJSON), constrói um Data Warehouse e duas ABTs para estudar vazamento de dados na previsão de recompra.

## 1. Requisitos

- Docker Desktop com Docker Compose v2;
- cerca de 8 GB de RAM e espaço livre para gerar/carregar os dados;
- Orange Data Mining instalado no computador (fora do Docker);
- Windows PowerShell 5.1 ou outro terminal capaz de executar os comandos Docker.

O arquivo `.env.example` define `ESCALA=1.0`, que corresponde aos volumes usados nos resultados entregues. Alterar a escala reduz a quantidade de registros e invalida as contagens fixas da auditoria adicional.

## 2. Configuração das portas

**As portas que precisam estar livres no computador são as do lado esquerdo de `:` no `docker-compose.yml`.** A porta à direita pertence ao contêiner e normalmente não precisa ser alterada.

| Serviço | Entrada em `docker-compose.yml` | Porta externa atual | Finalidade |
|---|---|---:|---|
| `mysql_legado` | `"3307:3306"` | 3307 | MySQL da origem legado |
| `pg_ecommerce` | `"15432:5432"` | 15432 | PostgreSQL da origem e-commerce |
| `pg_dw` | `"15433:5432"` | 15433 | PostgreSQL de destino |
| `adminer` | `"8080:8080"` | 8080 | Interface web para consultar os bancos |

**Exemplo:** se a porta `15432` estiver ocupada, altere somente `"15432:5432"` para `"25432:5432"` em `docker-compose.yml`. A nova porta externa será 25432. O mesmo se aplica às demais portas. Depois, execute `docker compose up -d --build` para recriar o serviço com a nova publicação de portas.

As conexões Python dos serviços usam os nomes internos `mysql_legado`, `pg_ecommerce` e `pg_dw` e **continuam nas portas internas 3306 e 5432**; não altere os DSNs em `etl/config.py` por causa de uma colisão de portas do Windows. Acesse o Adminer em `http://localhost:8080` ou na porta externa escolhida para ele.

## 3. Iniciar o ambiente pela primeira vez

No PowerShell, na raiz do repositório:

```powershell
Copy-Item .env.example .env
# Se necessário, ajuste ESCALA em .env ANTES de gerar os dados.
docker compose up -d --build
docker compose ps -a
docker compose logs seed
```

A inicialização inclui um serviço `seed` que gera os dados determinísticos, cria esquemas e carrega as fontes. Aguarde a mensagem `ambiente pronto.` no log do `seed` e verifique se o serviço `etl` está disponível antes de rodar a extração.

Não coloque arquivos SQL de dump no lugar dos scripts de carga: o pipeline normal constrói tudo a partir das fontes geradas.

## 4. Executar o ETL

### Forma oficial: seis comandos na ordem do professor

```powershell
docker compose exec -T etl python 01_extracao.py
docker compose exec -T etl python 02_transformacao.py
docker compose exec -T etl python 03_carga_dw.py
docker compose exec -T etl python 04_abt.py --correta
docker compose exec -T etl python 04_abt.py --vazamento
docker compose exec -T etl python 05_exportar_orange.py
```

### Execução com auditoria automatizada (sem exclusão)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1
```

Esse comando preserva volumes e as fontes geradas; executa as seis etapas, confere as asserções novas, quantidades de registros e assinaturas SHA-256 dos CSVs. Salva o log em `validacao_pipeline/execucao-*.log`, a referência em `validacao_pipeline/referencia_pipeline.json` e a auditoria da última execução em `saida/auditoria_pipeline.json`.

### Reconstrução do zero — DESTRUTIVA, somente com autorização explícita

**CUIDADO:** a opção abaixo remove os volumes dos bancos do projeto e exclui as pastas geradas `dados/` e `saida/`, incluindo os CSVs. Faça backup antes. Não execute em conjunto com outras instâncias que usem o mesmo projeto Docker Compose.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1 -ReconstruirDoZero -ConfirmarExclusao
```

A reconstrução executa `docker compose down -v --remove-orphans`, remove apenas `dados/` e `saida/`, sobe tudo com `docker compose up -d --build`, executa os seis comandos oficiais e confere as mesmas auditorias. A referência em `validacao_pipeline/` é preservada para permitir comparar o resultado de uma geração limpa com o resultado anterior. **Nunca use a flag destrutiva sem cópia do banco/dump quando precisar preservar dados.**

Se for demonstrar isso ao professor, prefira fazê-lo numa cópia isolada do ambiente. Apenas copiar a pasta não isola volumes quando o projeto Compose tem o mesmo nome; certifique-se de que não esteja apontando para a instalação original.

## 5. Saídas e experimentos no Orange

Depois da execução das etapas, o exportador gera os arquivos abaixo na pasta `saida/` (no contêiner: `/saida`). **Esses arquivos são gerados, e por isso não são incluídos no Git por padrão.**

| Fluxo `.ows` (em `etl/orange/`) | Arquivo(s) de entrada (em `saida/`) | Como gerar |
|---|---|---|
| `mercatto_abt_correta.ows` | `abt_recompra_correta.csv` | Rodar as seis etapas ou `05_exportar_orange.py` após a ABT correta |
| `mercatto_abt_vazamento.ows` | `abt_recompra_vazamento.csv` | Rodar as seis etapas ou `05_exportar_orange.py` após a ABT contaminada |
| `mercatto_normalizacao_segura.ows` | `abt_norm_segura_treino.csv` **e** `abt_norm_segura_teste.csv` | Após gerar `abt_recompra_correta.csv`, rodar `experimento_normalizacao.py` |
| `mercatto_normalizacao_global.ows` | `abt_norm_global_treino.csv` **e** `abt_norm_global_teste.csv` | Após gerar `abt_recompra_correta.csv`, rodar `experimento_normalizacao.py` |

Para gerar os quatro CSVs do experimento de normalização:

```powershell
docker compose exec -T etl python experimento_normalizacao.py
```

Abra cada `.ows` no Orange. Se o widget **File** indicar arquivo ausente, use **Browse** e selecione o CSV correspondente na pasta `saida/` da máquina local. Os caminhos salvos dentro dos `.ows` podem apontar para o computador da equipe e precisar ser redefinidos ao abrir em outro PC.

Nos dois experimentos principais, confirme que `Data Sampler` envia **Data Sample** (70%) para `Data` do `Test & Score` e **Remaining Data** (30%) para `Test Data`, com amostragem estratificada e mesma configuração/partição nas duas versões. Nos experimentos de normalização, já existem arquivos de treino e teste separados: use dois widgets File diretamente no Test & Score e selecione **Test on test data**. Os modelos são Naive Bayes, Logistic Regression e Tree, com visualizações Confusion Matrix e ROC Analysis.

As capturas de resultados estão em `etl/dados_orange/`.

## 6. Saídas esperadas (ESCALA=1.0)

- `dw.fato_pedido`: **410.000** pedidos;
- `dw.fato_item_pedido`: **1.371.876** itens vinculados;
- `dw.fato_atendimento`: **95.000** protocolos únicos;
- `dw.fato_avaliacao`: **160.000** avaliações;
- `abt.recompra_90d` e `abt.recompra_90d_vazamento`: **108.064** clientes, **18** colunas (15 atributos, 2 metadados, 1 classe);
- recompras positivas: **24.043** em cada ABT.

As asserções são persistidas em `qa.execucao_assercao`. Os scripts Python não substituem os registros antigos automaticamente, por isso o número total de linhas no QA cresce entre execuções; a auditoria adicional seleciona asserções a partir do último ID antes da execução.

## 7. Artefatos de entrega

- `docs/RDT - 1 a 15.md`: texto das 15 fichas, tabela-resumo e reflexão;
- `docs/Relatorio_Final_Experimento_Vazamento_Mercatto.md`: versão editável do relatório experimental;
- os PDFs correspondentes são enviados diretamente ao professor;
- `dump_sql.zip`: exportação do DW e do histórico de asserções, entregue em formato compactado;
- `etl/orange/*.ows`: fluxos do Orange;
- `etl/dados_orange/`: capturas de Test & Score, matrizes de confusão e ROC;
- relatório diagnóstico da Semana 1: entregue diretamente ao professor.

Os arquivos fornecidos originalmente pelo professor, incluindo `docs/PROFESSOR.md`, são preservados sem alterações conforme orientação recebida.

## 8. Observações e problemas comuns

- **Porta em uso:** altere apenas a porta externa no `docker-compose.yml`, na seção `ports:` do serviço correspondente.
- **Dados `latin1`:** a origem MySQL e o CSV do SAC não devem ser lidos como UTF-8; as conexões e o script já tratam isso.
- **Fonte não gerada:** confira `docker compose logs seed` e o status do contêiner `lab_seed`.
- **CSV ausente no Orange:** rode a exportação após as duas ABTs; depois selecione os arquivos corretos no widget File.
- **Acentos corrompidos no console clássico:** utilize PowerShell com saída UTF-8; o script configura a codificação. Se a janela parar ao selecionar texto, desative o QuickEdit nas propriedades do console.
- **Volumes existentes:** a execução normal não limpa o ambiente. A reconstrução do zero exige as duas flags explícitas.
