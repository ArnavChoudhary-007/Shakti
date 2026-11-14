[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare', 'Stop')][string]$Action,
    [Parameter(Mandatory)][string]$InstallRoot,
    [string]$UvPath,
    [string]$RequirementsPath,
    [switch]$InstallOllama
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'ShaktiInstaller.psm1') -Force
$transcribing = $false
$exitCode = 0
$paths = $null

try {
    $paths = Get-ShaktiInstallPaths -InstallRoot $InstallRoot
    Assert-ShaktiManagedTrees -Paths $paths
    if ($Action -eq 'Prepare') {
        if (-not $UvPath -or -not (Test-Path -LiteralPath $UvPath -PathType Leaf)) { throw 'The bundled Python manager is missing.' }
        if (-not $RequirementsPath -or -not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf)) { throw 'The dependency list is missing.' }
    }
    New-Item -ItemType Directory -Path $paths.installer -Force | Out-Null
    $errorFile = Join-Path $paths.installer 'setup-error.txt'
    if (Test-Path -LiteralPath $errorFile) { Remove-Item -LiteralPath $errorFile -Force }
    Start-Transcript -LiteralPath (Join-Path $paths.installer 'setup.log') -Append | Out-Null
    $transcribing = $true
    Stop-ShaktiCompanion -Paths $paths

    if ($Action -eq 'Prepare') {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $paths.runtime 'python'
        $env:UV_CACHE_DIR = Join-Path $paths.runtime 'cache'
        $env:UV_LINK_MODE = 'copy'
        $env:UV_NO_PROGRESS = '1'
        $python = Join-Path $paths.venv 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) {
            Write-Output 'Installing a private Python 3.12 environment.'
            Invoke-ShaktiCommand -Executable $UvPath -Arguments @('--no-config', 'venv', $paths.venv, '--python', '3.12', '--managed-python')
        }
        Invoke-ShaktiCommand -Executable $python -Arguments @('-c', 'import sys; assert sys.version_info[:2] == (3, 12), "Reinstall Shakti to repair its Python environment; your data will be kept."')
        Write-Output 'Installing Shakti libraries. The first installation downloads several gigabytes and can take a few minutes.'
        Invoke-ShaktiCommand -Executable $UvPath -Arguments @('--no-config', 'pip', 'install', '--compile-bytecode', '--python', $python, '-r', $RequirementsPath)
        Invoke-ShaktiCommand -Executable $UvPath -Arguments @('--no-config', 'pip', 'check', '--python', $python)
        Write-Output 'Checking the AI runtime before the first launch.'
        Invoke-ShaktiCommand -Executable $python -Arguments @('-c', 'import fastapi, uvicorn, yaml, torch, sentence_transformers, chromadb, pandas, fitz, faiss, langchain_text_splitters')
        if ($InstallOllama) {
            $exitCode = Install-ShaktiOllama
        }
        Write-Output 'Prerequisites are ready. Setup will now copy Shakti and create its shortcuts.'
    }
} catch {
    $failure = "Setup failed: $($_.Exception.Message)"
    Write-Output $failure
    if ($paths -and (Test-Path -LiteralPath $paths.installer)) {
        [IO.File]::WriteAllText((Join-Path $paths.installer 'setup-error.txt'), $failure)
    }
    $exitCode = 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
}
exit $exitCode
