#requires -Version 5.1
<#
Uso normal (preserva volumes e dados):
  powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1

Reconstrução destrutiva, explicitamente autorizada:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\executar_pipeline.ps1 -ReconstruirDoZero -ConfirmarExclusao

ATENÇÃO: a segunda opção executa docker compose down -v e apaga ./dados e ./saida.
Não remove documentos, arquivos de código, dump_sql.zip nem validacao_pipeline/.
Fazer backup antes. Não executar enquanto outra cópia do projeto estiver em uso.
#>
param(
    [switch]$ReconstruirDoZero,
    [switch]$ConfirmarExclusao
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$utf8SemBOM = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8SemBOM
$OutputEncoding = $utf8SemBOM
Set-Location $PSScriptRoot

if ($ConfirmarExclusao -and -not $ReconstruirDoZero) {
    throw 'A flag -ConfirmarExclusao so pode ser usada com -ReconstruirDoZero.'
}
if ($ReconstruirDoZero -and -not $ConfirmarExclusao) {
    throw 'RECONSTRUCAO CANCELADA. Para autorizar a exclusao de volumes e dados gerados, use -ReconstruirDoZero -ConfirmarExclusao.'
}

foreach ($arquivo in @('docker-compose.yml', 'etl/01_extracao.py', 'etl/02_transformacao.py',
                      'etl/03_carga_dw.py', 'etl/04_abt.py', 'etl/05_exportar_orange.py',
                      'etl/auditar_pipeline.py')) {
    if (-not (Test-Path -LiteralPath $arquivo -PathType Leaf)) {
        throw "Arquivo obrigatorio nao encontrado: $arquivo"
    }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker nao encontrado no PATH.'
}

$pastaValidacao = Join-Path $PSScriptRoot 'validacao_pipeline'
New-Item -ItemType Directory -Path $pastaValidacao -Force | Out-Null
$instante = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $pastaValidacao "execucao-$instante.log"
$cronometroTotal = [Diagnostics.Stopwatch]::StartNew()

function Gravar-Linha([string]$linha) {
    [System.IO.File]::AppendAllText($script:log, $linha + [Environment]::NewLine, $script:utf8SemBOM)
    Write-Output $linha
}
function Escrever-Log([string]$mensagem) { Gravar-Linha $mensagem }
function Docker-Exec([string[]]$argumentos) {
    & docker @argumentos 2>&1 | ForEach-Object { Gravar-Linha ([string]$_) }
    $codigo = $LASTEXITCODE
    if ($codigo -ne 0) { throw "Docker falhou (exit $codigo): docker $($argumentos -join ' ')" }
}

try {
    if ($ReconstruirDoZero) {
        Escrever-Log '[ATENCAO] RECONSTRUCAO DO ZERO AUTORIZADA: volumes Docker, ./dados e ./saida serao excluidos.'
        Escrever-Log '[INFO] Mantenha backup externo do DW, do dump SQL e dos CSVs, caso necessite preserva-los.'
        Docker-Exec @('compose', 'down', '-v', '--remove-orphans')
        foreach ($pastaGerada in @('dados', 'saida')) {
            $caminho = Join-Path $PSScriptRoot $pastaGerada
            if (Test-Path -LiteralPath $caminho) {
                Escrever-Log "[LIMPEZA] Removendo ./$pastaGerada"
                Remove-Item -LiteralPath $caminho -Recurse -Force -ErrorAction Stop
            }
            New-Item -ItemType Directory -Path $caminho -Force | Out-Null
        }
        Escrever-Log '[DOCKER] Recriando imagens, fontes e bancos de dados...'
        Docker-Exec @('compose', 'up', '-d', '--build')
        Escrever-Log '[DOCKER] Ambiente reconstruido; iniciando as seis etapas do professor.'
    } else {
        Escrever-Log '[INICIO] Auditoria de reexecucao do pipeline, SEM apagar volumes'
    }

    Docker-Exec @('compose', 'ps')

    # O gerador prepara o esquema qa antes da primeira etapa; registrar ID evita
    # confundir assercoes de execucoes anteriores com a execucao atual.
    $idTexto = & docker compose exec -T etl python -c "from config import eng_dw; from sqlalchemy import text; e=eng_dw(); c=e.connect(); print(c.execute(text('SELECT COALESCE(MAX(id),0) FROM qa.execucao_assercao')).scalar_one()); c.close()"
    if ($LASTEXITCODE -ne 0 -or @($idTexto).Count -ne 1) {
        throw 'Nao foi possivel determinar o ID inicial de qa.execucao_assercao. Confira docker compose logs seed.'
    }
    $idAntes = [int]($idTexto | Select-Object -Last 1)
    Escrever-Log "[INFO] Ultimo ID QA anterior: $idAntes"

    $etapas = @(
        @{Nome = '01 Extracao';         Args = @('compose','exec','-T','etl','python','-u','01_extracao.py')},
        @{Nome = '02 Transformacao';     Args = @('compose','exec','-T','etl','python','-u','02_transformacao.py')},
        @{Nome = '03 Carga DW';          Args = @('compose','exec','-T','etl','python','-u','03_carga_dw.py')},
        @{Nome = '04 ABT correta';       Args = @('compose','exec','-T','etl','python','-u','04_abt.py','--correta')},
        @{Nome = '04 ABT vazamento';     Args = @('compose','exec','-T','etl','python','-u','04_abt.py','--vazamento')},
        @{Nome = '05 Exportar Orange';  Args = @('compose','exec','-T','etl','python','-u','05_exportar_orange.py')}
    )
    foreach ($etapa in $etapas) {
        Escrever-Log "`n>>> EXECUTANDO: $($etapa.Nome)"
        $relogio = [Diagnostics.Stopwatch]::StartNew()
        Docker-Exec $etapa.Args
        $relogio.Stop()
        Escrever-Log ("[TEMPO] {0}: {1:N1}s" -f $etapa.Nome, $relogio.Elapsed.TotalSeconds)
    }

    Escrever-Log "`n>>> AUDITORIA POS-CARGA (somente leitura)"
    Docker-Exec @('compose','exec','-T','etl','python','-u','auditar_pipeline.py','--desde-id',"$idAntes")

    $atualPath = Join-Path $PSScriptRoot 'saida/auditoria_pipeline.json'
    if (-not (Test-Path -LiteralPath $atualPath)) { throw 'Auditoria nao gerou saida/auditoria_pipeline.json.' }
    $atual = Get-Content $atualPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $referenciaPath = Join-Path $pastaValidacao 'referencia_pipeline.json'
    if (-not (Test-Path -LiteralPath $referenciaPath)) {
        Copy-Item -LiteralPath $atualPath -Destination $referenciaPath
        Escrever-Log '[REFERENCIA] Primeira execucao auditada. Referencia registrada.'
    } else {
        $antiga = Get-Content $referenciaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($arquivo in @('abt_recompra_correta.csv', 'abt_recompra_vazamento.csv')) {
            $antes = $antiga.csv.$arquivo.sha256_ordenado_cliente_sk
            $depois = $atual.csv.$arquivo.sha256_ordenado_cliente_sk
            if ($antes -ne $depois) { throw "HASH DIFERENTE: $arquivo. Investigue antes de entregar." }
            Escrever-Log "[OK] CSV IDENTICO A REFERENCIA: $arquivo"
        }
    }

    $cronometroTotal.Stop()
    Escrever-Log ("[SUCESSO] Pipeline reexecutado e auditado em {0:N1}s" -f $cronometroTotal.Elapsed.TotalSeconds)
    Escrever-Log "[LOG] $log"
    exit 0
} catch {
    $cronometroTotal.Stop()
    Escrever-Log "[FALHA] $($_.Exception.Message)"
    Escrever-Log "[LOG] $log"
    exit 1
}
