#requires -Version 5.1
<#
Uso normal (preserva os volumes, mas recarrega o DW e sobrescreve os CSVs):
  powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1

Uso com a flag destrutiva (reconstroi volumes, fontes e bancos do zero):
  powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1 -ReconstruirDoZero

ATENÇÃO: -ReconstruirDoZero executa docker compose down -v e exclui ./dados e ./saida.
A operação é destrutiva. Faça backup antes de utilizá-la.
Não remove documentos, código, dump_sql.zip, validacao_pipeline/ ou execucoes_pipeline/.
Não execute enquanto outra cópia deste projeto Docker estiver em uso.

Arquivos de auditoria:
  validacao_pipeline/referencia_pipeline.json      referência persistente
  execucoes_pipeline/execucao-*.log                histórico de logs
  saida/auditoria_pipeline.json                    auditoria da última execução
#>


# Parâmetro único: a presença da flag autoriza a reconstrução destrutiva.
param(
    [switch]$ReconstruirDoZero
)


# Configurações de execução e formatação
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$utf8SemBOM = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8SemBOM
$OutputEncoding = $utf8SemBOM
Set-Location $PSScriptRoot


# Arquivos obrigatórios (somente verificação de existência)
foreach ($arquivo in @(
    'docker-compose.yml',
    'etl/01_extracao.py',
    'etl/02_transformacao.py',
    'etl/03_carga_dw.py',
    'etl/04_abt.py',
    'etl/05_exportar_orange.py',
    'etl/auditar_pipeline.py'
)) {
    # Verificação dos arquivos a serem executados
    if (-not (Test-Path -LiteralPath $arquivo -PathType Leaf)) {
        throw "Arquivo obrigatorio nao encontrado: $arquivo"
    }
}


# Garante que o comando Docker esteja disponível
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker nao encontrado no PATH.'
}


# Define os caminhos das pastas
$pastaValidacao = Join-Path $PSScriptRoot 'validacao_pipeline'
$pastaExecucao = Join-Path $PSScriptRoot 'execucoes_pipeline'
New-Item -ItemType Directory -Path $pastaValidacao -Force | Out-Null
New-Item -ItemType Directory -Path $pastaExecucao -Force | Out-Null


# Define o caminho do log
$instante = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $pastaExecucao "execucao-$instante.log"


# Mede o tempo total
$cronometroTotal = [Diagnostics.Stopwatch]::StartNew()


# Salva cada linha em UTF-8 e a exibe no terminal
function GravarLinha([string]$linha) {
    [System.IO.File]::AppendAllText($script:log, $linha + [Environment]::NewLine, $script:utf8SemBOM)
    Write-Output $linha
}


# Executa comandos Docker, captura saída/erros e exige código de saída zero
function ExecutarDocker([string[]]$argumentos) {
    $preferenciaAnterior = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker @argumentos 2>&1 | ForEach-Object {
            GravarLinha ([string]$_)
        }
        $codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $preferenciaAnterior
    }
    if ($codigo -ne 0) {
        throw "Docker falhou (exit $codigo): docker $($argumentos -join ' ')"
    }
}


# Erros em qualquer etapa seguem para o bloco catch e encerram com código 1
try {
    # A reconstrução destrutiva ocorre somente quando a flag é informada
    if ($ReconstruirDoZero) {
        GravarLinha '[ATENCAO] RECONSTRUCAO DO ZERO AUTORIZADA: volumes Docker, ./dados e ./saida serao excluidos.'
        GravarLinha '[INFO] Mantenha backup externo do DW, do dump SQL e dos CSVs, caso necessite preserva-los.'
        # Remove contêineres, redes do Compose e volumes nomeados do projeto
        ExecutarDocker @('compose', 'down', '-v', '--remove-orphans')
        # Remove os dados gerados e as saídas
        foreach ($pastaGerada in @('dados', 'saida')) {
            $caminho = Join-Path $PSScriptRoot $pastaGerada
            if (Test-Path -LiteralPath $caminho) {
                GravarLinha "[LIMPEZA] Removendo ./$pastaGerada"
                Remove-Item -LiteralPath $caminho -Recurse -Force -ErrorAction Stop
            }
            # Recria os dois diretórios vazios para o gerador e o ETL
            New-Item -ItemType Directory -Path $caminho -Force | Out-Null
        }
        GravarLinha '[DOCKER] Recriando imagens, fontes e bancos de dados...'
        # Recria os serviços. O Compose aguarda o término bem-sucedido do seed
        ExecutarDocker @('compose', 'up', '-d', '--build')
        GravarLinha '[DOCKER] Ambiente reconstruido; iniciando as seis etapas do professor.'
    # Reexecução comum: preserva volumes e diretórios existentes
    } else {
        GravarLinha '[INICIO] Auditoria de reexecucao do pipeline, SEM apagar volumes'
    }

    # Registra o estado dos serviços no log
    ExecutarDocker @('compose', 'ps')

    # Memoriza o último ID de QA antes da execução, para auditar apenas asserções novas.
    $consultaQa = "from config import eng_dw; from sqlalchemy import text; e=eng_dw(); c=e.connect(); print(c.execute(text('SELECT COALESCE(MAX(id),0) FROM qa.execucao_assercao')).scalar_one()); c.close()"
    $preferenciaAnterior = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $idTexto = & docker compose exec -T etl python -c $consultaQa 2>&1
        $codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $preferenciaAnterior
    }
    # Confere se a consulta retornou um único ID e terminou com sucesso
    if ($codigo -ne 0 -or @($idTexto).Count -ne 1) {
        throw 'Nao foi possivel determinar o ID inicial de qa.execucao_assercao. Confira docker compose logs seed.'
    }
    $idAntes = [int]($idTexto | Select-Object -Last 1)
    GravarLinha "[INFO] Ultimo ID QA anterior: $idAntes"

    # Lista ordenada de seis execuções (a etapa 4 é executada nas duas variantes)
    $etapas = @(
        @{Nome = '01 Extracao';         Args = @('compose','exec','-T','etl','python','-u','01_extracao.py')},
        @{Nome = '02 Transformacao';    Args = @('compose','exec','-T','etl','python','-u','02_transformacao.py')},
        @{Nome = '03 Carga DW';         Args = @('compose','exec','-T','etl','python','-u','03_carga_dw.py')},
        @{Nome = '04 ABT correta';      Args = @('compose','exec','-T','etl','python','-u','04_abt.py','--correta')},
        @{Nome = '04 ABT vazamento';    Args = @('compose','exec','-T','etl','python','-u','04_abt.py','--vazamento')},
        @{Nome = '05 Exportar Orange';  Args = @('compose','exec','-T','etl','python','-u','05_exportar_orange.py')}
    )
    # Executa cada etapa na ordem do professor
    foreach ($etapa in $etapas) {
        GravarLinha "`n>>> EXECUTANDO: $($etapa.Nome)"
        # Inicia o cronômetro desta etapa
        $relogio = [Diagnostics.Stopwatch]::StartNew()
        # Executa o script dentro do contêiner etl
        ExecutarDocker $etapa.Args
        # Finaliza o cronômetro e grava a duração
        $relogio.Stop()
        GravarLinha ("[TEMPO] {0}: {1:N1}s" -f $etapa.Nome, $relogio.Elapsed.TotalSeconds)
    }

    GravarLinha "`n>>> AUDITORIA POS-CARGA (somente leitura)"
    # Executa o arquivo da auditoria
    ExecutarDocker @('compose','exec','-T','etl','python','-u','auditar_pipeline.py','--desde-id',"$idAntes")

    # Lê o JSON da auditoria gerado pela última execução
    $atualPath = Join-Path $PSScriptRoot 'saida/auditoria_pipeline.json'
    # Garante que o JSON foi realmente produzido
    if (-not (Test-Path -LiteralPath $atualPath)) {
        throw 'Auditoria nao gerou saida/auditoria_pipeline.json.'
    }
    $atual = Get-Content $atualPath -Raw -Encoding UTF8 | ConvertFrom-Json

    # A referência fica em uma pasta preservada mesmo após down -v
    $referenciaPath = Join-Path $pastaValidacao 'referencia_pipeline.json'
    # Se a referência não existe, a primeira execução torna-se a referência
    if (-not (Test-Path -LiteralPath $referenciaPath)) {
        # Copia o resultado atual como referência; execuções futuras não a substituem
        Copy-Item -LiteralPath $atualPath -Destination $referenciaPath
        GravarLinha '[REFERENCIA] Primeira execucao auditada. Referencia registrada.'
    # Havendo referência, compara os hashes de conteúdo das duas ABTs
    } else {
        # Lê as assinaturas esperadas
        $antiga = Get-Content $referenciaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        # Compara somente os SHA-256 ordenados por cliente dos dois CSVs
        foreach ($arquivo in @('abt_recompra_correta.csv', 'abt_recompra_vazamento.csv')) {
            $antes = $antiga.csv.$arquivo.sha256_ordenado_cliente_sk
            $depois = $atual.csv.$arquivo.sha256_ordenado_cliente_sk
            # Qualquer divergência indica resultado diferente da referência
            if ($antes -ne $depois) {
                throw "HASH DIFERENTE: $arquivo. Investigue antes de entregar."
            }
            GravarLinha "[OK] CSV IDENTICO A REFERENCIA: $arquivo"
        }
    }

    # Finaliza e registra o tempo total
    $cronometroTotal.Stop()
    GravarLinha ("[SUCESSO] Pipeline reexecutado e auditado em {0:N1}s" -f $cronometroTotal.Elapsed.TotalSeconds)
    GravarLinha "[LOG] $log"
    exit 0
# Em caso de erro, grava a falha no log persistente e retorna exit code 1
} catch {
    $cronometroTotal.Stop()
    GravarLinha "[FALHA] $($_.Exception.Message)"
    GravarLinha "[LOG] $log"
    exit 1
}