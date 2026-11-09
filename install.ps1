<#
.SYNOPSIS
  Installs (or updates) the Shakti companion for the current Windows user.

.DESCRIPTION
  One-line install, no admin rights needed:

    powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/ArnavChoudhary-007/Shakti/main/install.ps1 | iex"

  What it does:
    1. Installs uv (Astral's Python manager) for this user if it's missing.
    2. Downloads the latest Shakti release from GitHub.
    3. Creates a private Python 3.12 environment (uv downloads Python itself)
       and installs Shakti's dependencies into it.
    4. Adds "Shakti" to the Start menu and the desktop, then launches it.

  Everything goes under %LOCALAPPDATA%\Shakti:
    app\   program files (replaced on update)
    data\  your documents, indexes and settings (kept on update and uninstall
           unless you delete it)

  Ollama is NOT installed here — Shakti's setup page offers to do that.

  Re-run the same command to update. To uninstall:

    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/ArnavChoudhary-007/Shakti/main/install.ps1))) -Uninstall
#>
[CmdletBinding()]
param(
  [switch]$Uninstall,
  # Install from a local release zip instead of downloading (for testing).
  [string]$SourceZip,
  [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Shakti"),
  [switch]$NoShortcuts,
  [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar

$Repo = "ArnavChoudhary-007/Shakti"
$AssetName = "shakti-companion.zip"
$PythonVersion = "3.12"
$AppDir = Join-Path $InstallRoot "app"
$DataDir = Join-Path $InstallRoot "data"
$VenvDir = Join-Path $InstallRoot "venv"
$StartMenuLink = Join-Path ([Environment]::GetFolderPath("Programs")) "Shakti.lnk"
$DesktopLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "Shakti.lnk"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }

function Stop-Shakti {
  # Stop a running companion so its files can be replaced.
  Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($VenvDir, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
  Step "Uninstalling Shakti"
  Stop-Shakti
  Remove-Item $StartMenuLink, $DesktopLink -ErrorAction SilentlyContinue
  Remove-Item $AppDir, $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
  Info "Removed the app. Your documents and settings are still in:"
  Info "  $DataDir"
  Info "Delete that folder too if you want to remove everything. Ollama is uninstalled separately (Settings > Apps)."
  return
}

Write-Host ""
Write-Host "  Shakti - private document AI" -ForegroundColor Magenta
Write-Host "  Installing to $InstallRoot"

# ── 1. uv ──────────────────────────────────────────────────────
Step "Checking for uv (Python manager)"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
  $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (Test-Path $candidate) { $uv = $candidate }
}
if (-not $uv) {
  Info "Installing uv from astral.sh ..."
  $env:UV_NO_MODIFY_PATH = "1"
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (-not (Test-Path $uv)) { throw "uv installation failed - see https://docs.astral.sh/uv/ to install it manually, then re-run." }
}
Info "Using $uv"

# ── 2. Download the release ────────────────────────────────────
Step "Downloading Shakti"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("shakti-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  if ($SourceZip) {
    $zip = (Resolve-Path $SourceZip).Path
    Info "Using local package $zip"
  } else {
    $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "shakti-installer" }
    $asset = $release.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
    if (-not $asset) { throw "The latest release ($($release.tag_name)) has no $AssetName." }
    $zip = Join-Path $tmp $AssetName
    Info "Release $($release.tag_name)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
  }

  $staging = Join-Path $tmp "app"
  Expand-Archive -Path $zip -DestinationPath $staging -Force
  if (-not (Test-Path (Join-Path $staging "rag_pipeline\__main__.py"))) { throw "The package doesn't look like a Shakti release." }

  Stop-Shakti
  if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
  New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
  Move-Item $staging $AppDir
} finally {
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ── 3. Python environment ──────────────────────────────────────
Step "Setting up Python $PythonVersion and dependencies (first install: a few minutes, ~1-2 GB)"
$python = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $python)) {
  & $uv venv $VenvDir --python $PythonVersion --quiet
  if ($LASTEXITCODE -ne 0) { throw "Couldn't create the Python environment." }
}
& $uv pip install --python $python --quiet -r (Join-Path $AppDir "rag_pipeline\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing dependencies failed. Check your internet connection and re-run the installer." }

New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
$userConfig = Join-Path $DataDir "config.yaml"
if (-not (Test-Path $userConfig)) {
  Copy-Item (Join-Path $AppDir "rag_pipeline\config.example.yaml") $userConfig
}

# ── 4. Shortcuts and launch ────────────────────────────────────
$pythonw = Join-Path $VenvDir "Scripts\pythonw.exe"
$launchArgs = "-m rag_pipeline --data-dir `"$DataDir`""
if (-not $NoShortcuts) {
  Step "Adding Shakti to the Start menu and desktop"
  $shell = New-Object -ComObject WScript.Shell
  foreach ($path in @($StartMenuLink, $DesktopLink)) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = $launchArgs
    $lnk.WorkingDirectory = $AppDir
    $lnk.Description = "Shakti - private document AI"
    $icon = Join-Path $AppDir "rag_pipeline\data\shakti.ico"
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Save()
  }
}

Write-Host ""
Write-Host "  Shakti is installed." -ForegroundColor Green
Info "Start it any time from the Start menu. It opens in your browser at http://127.0.0.1:8000"
if (-not $NoLaunch) {
  Info "Starting Shakti now..."
  Start-Process -FilePath $pythonw -ArgumentList $launchArgs -WorkingDirectory $AppDir
}
