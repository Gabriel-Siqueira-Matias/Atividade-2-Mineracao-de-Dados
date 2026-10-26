# Mercatto Varejo — validação de reexecução do pipeline

## O que verifica

O professor exige que o ETL consiga ser executado do zero por uma semente fixa, na sequência: `01_extracao.py`, `02_transformacao.py`, `03_carga_dw.py`, `04_abt.py --correta`, `04_abt.py --vazamento`, `05_exportar_orange.py`.

O script `validar_pipeline_completo.ps1` **não exclui bancos nem volumes**. Ele executa as mesmas seis etapas sobre o ambiente disponível e, ao final, roda a auditoria de somente leitura `auditar_pipeline.py`.

Essa reexecução **não substitui** a prova de reconstrução *do zero* exigida pelo professor. Para um teste totalmente limpo, utilize um ambiente isolado (máquina virtual ou outra máquina); não basta duplicar a pasta enquanto o Compose original está ativo, porque o projeto possui `name:` e `container_name:` fixos.

## Instalação

- Salve `validar_pipeline_completo.ps1` **na raiz** do projeto, junto ao `docker-compose.yml`.
- Salve `auditar_pipeline.py` dentro da pasta **`etl`**, ao lado de `01_extracao.py` etc.
- Inicie Docker Desktop e verifique que os serviços estejam ativos (`docker compose ps`).
- Preserve um backup dos bancos antes do teste integrado; as etapas 1–4 refazem staging, dimensões, fatos e ABTs conforme o código atual.

## Execução no PowerShell

```powershell
cd 'H:\Mineração\lab-etl-varejo\lab-etl-varejo'
powershell -NoProfile -ExecutionPolicy Bypass -File .\validar_pipeline_completo.ps1
```

Não utiliza `docker compose down -v`, não exclui pastas `dados` ou `saida` e não altera o código atual do ETL.

## Resultados

- `validacao_pipeline/execucao-AAAAMMDD-HHMMSS.log`: saída e duração de cada etapa.
- `saida/auditoria_pipeline.json`: contagens de staging, DW e ABT; QA das **novas** execuções; verificação dos cabeçalhos e hashes SHA-256 dos CSVs ordenados por `cliente_sk`.
- `validacao_pipeline/referencia_pipeline.json`: referência armazenada **após a primeira execução auditada**. Na segunda execução, os hashes dos dois CSVs são comparados automaticamente contra ela. A auditoria não atualiza a referência automaticamente.

A auditoria pressupõe a semente padrão do laboratório, `ESCALA=1.0`, 108.064 clientes nas ABTs, 24.043 recompras e **15 features** (18 colunas no CSV), como nos resultados verificados pela equipe. Se estes parâmetros forem alterados intencionalmente, é preciso ajustar as referências.

**Importante:** hash idêntico é uma prova forte de identidade dos CSVs, mas não comprova por si só que o gerador reiniciou do zero. Para essa prova, use o procedimento oficial somente após backup e em ambiente isolado.

## Reconstrução do zero para o professor

O roteiro descreve remover os volumes e as pastas `dados` e `saida`, subir Docker e executar as seis etapas. Isso elimina os bancos atuais e os arquivos gerados. Não execute essa limpeza sobre a única cópia dos dados.

O projeto usa nomes de containers e portas fixos; para um ambiente isolado, prefira outra máquina/VM ou ajuste cuidadosamente `name`, `container_name` e portas de um segundo Compose. Somente depois de conferir que o ambiente de teste **não compartilha volumes/containers com o original**, aplique a limpeza indicada no roteiro oficial. Preserve os logs, os CSVs e as capturas do Orange para comparação final.
