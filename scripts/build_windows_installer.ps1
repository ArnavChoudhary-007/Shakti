[CmdletBinding()]
param(
    [string]$Version,
    [string]$CompilerPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = Join-Path $repoRoot 'dist'
$toolchain = Join-Path $outputRoot 'windows-toolchain'
New-Item -ItemType Directory -Path $toolchain -Force | Out-Null

if (-not $Version) {
    $packageSource = Get-Content -LiteralPath (Join-Path $repoRoot 'rag_pipeline\__init__.py') -Raw
    $Version = [regex]::Match($packageSource, '(?m)^__version__ = "([^"]+)"').Groups[1].Value
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw 'Version must look like 0.2.1 or 0.2.1-rc.1 (without a leading v).'
}
$fileVersion = [regex]::Match($Version, '^\d+\.\d+\.\d+').Value + '.0'

function Get-VerifiedDownload {
    param([string]$Url, [string]$Destination, [string]$Sha256)

    if (-not (Test-Path -LiteralPath $Destination)) {
        Invoke-WebRequest $Url -OutFile $Destination -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Checksum mismatch for $Destination. Remove that cached download and retry."
    }
}

if (-not $CompilerPath) {
    $compilerInstaller = Join-Path $toolchain 'innosetup-6.6.1.exe'
    Get-VerifiedDownload -Url 'https://github.com/jrsoftware/issrc/releases/download/is-6_6_1/innosetup-6.6.1.exe' -Destination $compilerInstaller -Sha256 'd243ce440c02705530699554fb9612b9b2bd7a2a90629cdb7f41e66f5faeb91f'
    $compilerDirectory = Join-Path $toolchain 'inno-6.6.1'
    $CompilerPath = Join-Path $compilerDirectory 'ISCC.exe'
    if (-not (Test-Path -LiteralPath $CompilerPath)) {
        $compilerSetup = Start-Process -FilePath $compilerInstaller -ArgumentList @('/PORTABLE=1', '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS', ('/DIR="' + $compilerDirectory + '"')) -WindowStyle Hidden -Wait -PassThru
        if ($compilerSetup.ExitCode -ne 0) { throw "Inno Setup compiler preparation failed: $($compilerSetup.ExitCode)" }
    }
}
if (-not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) { throw 'ISCC.exe was not found.' }

$uvArchive = Join-Path $toolchain 'uv-0.9.5-windows-x64.zip'
Get-VerifiedDownload -Url 'https://github.com/astral-sh/uv/releases/download/0.9.5/uv-x86_64-pc-windows-msvc.zip' -Destination $uvArchive -Sha256 '515dc53d7553f1357d0abc1f70acd921fbb9e30230b1d9a08737236daa6ee920'
$uvDirectory = Join-Path $toolchain 'uv-0.9.5'
Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvDirectory -Force
$uvExecutable = Join-Path $uvDirectory 'uv.exe'
if (-not (Test-Path -LiteralPath $uvExecutable)) { throw 'uv.exe is missing from the verified archive.' }

& python (Join-Path $repoRoot 'scripts\package_release.py') --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Companion packaging failed. Build the web UI first.' }
$payload = Join-Path $outputRoot ('windows-payload-' + [Guid]::NewGuid())
Expand-Archive -LiteralPath (Join-Path $outputRoot 'shakti-companion.zip') -DestinationPath $payload
& $CompilerPath "/DAppVersion=$Version" "/DFileVersion=$fileVersion" "/DPayloadDir=$payload" "/DUvExe=$uvExecutable" "/DOutputDir=$outputRoot" (Join-Path $repoRoot 'installer\windows\shakti.iss')
if ($LASTEXITCODE -ne 0) { throw 'Compiling Shakti-Setup.exe failed.' }

$checksums = foreach ($asset in @('Shakti-Setup.exe', 'shakti-companion.zip')) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $outputRoot $asset) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $asset"
}
$checksums | Set-Content -LiteralPath (Join-Path $outputRoot 'SHA256SUMS.txt') -Encoding ASCII
Write-Output "Built $(Join-Path $outputRoot 'Shakti-Setup.exe') (version $Version)."
