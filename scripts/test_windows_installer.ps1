[CmdletBinding()]
param(
    [string]$InstallerPath = (Join-Path $PSScriptRoot '..\dist\Shakti-Setup.exe'),
    [switch]$AllowLocalTest
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
if ($env:GITHUB_ACTIONS -ne 'true' -and -not $AllowLocalTest) { throw 'Use a disposable GitHub Actions runner, or explicitly pass -AllowLocalTest on a machine without Shakti installed.' }
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$root = Join-Path $env:LOCALAPPDATA 'Shakti'
if (Test-Path -LiteralPath $root) { throw "A Shakti installation already exists at $root. Refusing to modify it for a test." }
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E0CE54FD-4BAC-4FC7-8F65-7F9B01EAC928}_is1'
$startLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Shakti.lnk'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Shakti.lnk'
foreach ($existingPath in @($registryKey, $startLink, $desktopLink)) {
    if (Test-Path -LiteralPath $existingPath) { throw "Existing Shakti registration or shortcut found. Refusing to change it: $existingPath" }
}
$data = Join-Path $root 'data'
$config = Join-Path $data 'config.yaml'
$sentinel = Join-Path $data 'uploads\keep-original.txt'
New-Item -ItemType Directory -Path (Split-Path -Parent $sentinel) -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'rag_pipeline\config.example.yaml') -Destination $config
Add-Content -LiteralPath $config -Value "`ninstaller_test_marker: preserve"
Set-Content -LiteralPath $sentinel -Value 'A user document that must survive installation, upgrade and uninstall.'
$configHash = (Get-FileHash -LiteralPath $config).Hash
$documentHash = (Get-FileHash -LiteralPath $sentinel).Hash

function Assert-UserData {
    if ((Get-FileHash -LiteralPath $config).Hash -ne $configHash) { throw 'Setup changed the existing configuration.' }
    if ((Get-FileHash -LiteralPath $sentinel).Hash -ne $documentHash) { throw 'Setup changed an existing document.' }
}

function Invoke-InstallerTest {
    param([string]$Executable, [string]$Label, [string[]]$ExtraArguments = @())

    $log = Join-Path $repoRoot "dist\installer-test-$Label.log"
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/LOG="' + $log + '"')) + $ExtraArguments
    $process = Start-Process -FilePath $Executable -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$Label failed with exit code $($process.ExitCode). See $log." }
    Assert-UserData
}

Invoke-InstallerTest -Executable $InstallerPath -Label 'install' -ExtraArguments @('/TASKS=desktopicon')
Write-Output 'Fresh installation passed; checking registration, shortcuts and launch.'
if (-not (Test-Path -LiteralPath $registryKey)) { throw 'Shakti was not registered in Settings > Apps.' }
foreach ($shortcutPath in @($startLink, $desktopLink)) {
    if (-not (Test-Path -LiteralPath $shortcutPath)) { throw "Shortcut is missing: $shortcutPath" }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if ($shortcut.TargetPath -ne (Join-Path $root 'venv\Scripts\pythonw.exe') -or $shortcut.WorkingDirectory -ne (Join-Path $root 'app') -or $shortcut.Arguments -ne "-m rag_pipeline --data-dir `"$data`"") {
        throw 'A shortcut does not launch the private runtime with the preserved data folder.'
    }
}

$python = Join-Path $root 'venv\Scripts\python.exe'
$server = Start-Process -FilePath $python -ArgumentList @('-m', 'rag_pipeline', '--data-dir', ('"' + $data + '"'), '--port', '18765', '--no-browser') -WorkingDirectory (Join-Path $root 'app') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $repoRoot 'dist\installer-test-companion.out.log') -RedirectStandardError (Join-Path $repoRoot 'dist\installer-test-companion.err.log') -PassThru
$ready = $false
$deadline = [DateTime]::UtcNow.AddMinutes(5)
while ([DateTime]::UtcNow -lt $deadline -and -not $server.HasExited) {
    try {
        $version = Invoke-RestMethod 'http://127.0.0.1:18765/version' -TimeoutSec 2
        if ($version.name -eq 'shakti-companion') { $ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'The installed companion did not start. See data\shakti.log.' }
$page = Invoke-WebRequest 'http://127.0.0.1:18765/' -UseBasicParsing
if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'id="root"') { throw 'The installed web UI was not served.' }
Write-Output 'Installed companion and web UI are responding; testing upgrade while it is running.'

Invoke-InstallerTest -Executable $InstallerPath -Label 'upgrade' -ExtraArguments @('/TASKS=desktopicon')
$server.Refresh()
if (-not $server.HasExited) { throw 'Upgrade did not stop the installed companion.' }
Write-Output 'Upgrade preserved the data and stopped its companion; testing uninstall.'
$setupLog = Join-Path $root 'installer\setup.log'
Copy-Item -LiteralPath $setupLog -Destination (Join-Path $repoRoot 'dist\installer-test-prerequisites.log')
Invoke-InstallerTest -Executable (Join-Path $root 'unins000.exe') -Label 'uninstall'
foreach ($path in @($registryKey, $startLink, $desktopLink, (Join-Path $root 'app'), (Join-Path $root 'venv'), (Join-Path $root 'runtime'))) {
    if (Test-Path -LiteralPath $path) { throw "Uninstall left program files or registration behind: $path" }
}
Write-Output 'Installer smoke test passed: install, shortcuts, Apps registration, launch, upgrade, uninstall and data preservation.'
