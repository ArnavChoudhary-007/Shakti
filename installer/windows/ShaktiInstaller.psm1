Set-StrictMode -Version Latest

function Get-ShaktiInstallPaths {
    param([Parameter(Mandatory)][string]$InstallRoot)

    $expected = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Shakti')).TrimEnd('\')
    $resolved = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    if (-not $resolved.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The installer only manages $expected. Refusing another install directory."
    }
    $paths = [ordered]@{ Root = $resolved }
    foreach ($name in @('app', 'venv', 'runtime', 'data', 'installer')) {
        $paths[$name] = Join-Path $resolved $name
    }
    foreach ($directory in $paths.Values) {
        if (Test-Path -LiteralPath $directory) {
            $entry = Get-Item -LiteralPath $directory -Force
            if (-not $entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "The install directory must be a regular directory: $directory"
            }
        }
    }
    return [pscustomobject]$paths
}

function Assert-ShaktiManagedTrees {
    param([Parameter(Mandatory)]$Paths)

    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    foreach ($directory in @($Paths.app, $Paths.venv, $Paths.runtime, $Paths.installer)) {
        if (Test-Path -LiteralPath $directory) { $pending.Push($directory) }
    }
    while ($pending.Count -gt 0) {
        foreach ($entry in Get-ChildItem -LiteralPath $pending.Pop() -Force -ErrorAction Stop) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "A linked path was found inside the program files. Refusing to modify it: $($entry.FullName)"
            }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
        }
    }
}

function Test-ShaktiCompanionProcess {
    param([Parameter(Mandatory)]$Process, [Parameter(Mandatory)]$Paths)

    if (-not $Process.ExecutablePath -or -not $Process.CommandLine) { return $false }
    $executables = @(
        (Join-Path $Paths.venv 'Scripts\python.exe'),
        (Join-Path $Paths.venv 'Scripts\pythonw.exe')
    )
    $privatePython = (Join-Path $Paths.runtime 'python') + '\'
    $managedPython = $Process.ExecutablePath.StartsWith($privatePython, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($Process.ExecutablePath) -in @('python.exe', 'pythonw.exe')
    return (($Process.ExecutablePath -in $executables -or $managedPython) -and $Process.CommandLine -match '\s-m\s+rag_pipeline(?:\s|$)')
}

function Stop-ShaktiCompanion {
    param([Parameter(Mandatory)]$Paths)

    $processes = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction Stop
    $selected = @($processes | Where-Object { Test-ShaktiCompanionProcess -Process $_ -Paths $Paths })
    do {
        $selectedIds = @($selected | ForEach-Object { $_.ProcessId })
        $children = @($processes | Where-Object { $_.ParentProcessId -in $selectedIds -and $_.ProcessId -notin $selectedIds })
        $selected += $children
    } while ($children.Count -gt 0)
    [array]::Reverse($selected)
    foreach ($process in $selected) {
        $running = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        if (-not $running) { continue }
        try {
            if ($running.HasExited) { continue }
            Write-Output "Stopping the installed Shakti companion (PID $($process.ProcessId))."
            try { Stop-Process -InputObject $running -Force -ErrorAction Stop } catch {
                if (-not $running.HasExited) { throw }
            }
            if (-not $running.WaitForExit(60000)) {
                throw 'Shakti is still shutting down. Wait a moment, then retry setup.'
            }
        } finally {
            $running.Dispose()
        }
    }
}

function Invoke-ShaktiCommand {
    param([Parameter(Mandatory)][string]$Executable, [Parameter(Mandatory)][string[]]$Arguments)

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$([IO.Path]::GetFileName($Executable)) exited with code $LASTEXITCODE. See the preceding output."
    }
}

function Test-ShaktiOllamaSignature {
    param([Parameter(Mandatory)]$Signature)

    return ($Signature.Status -eq 'Valid' -and $null -ne $Signature.SignerCertificate -and
        $Signature.SignerCertificate.Subject -match '(?:^|,\s*)CN=Ollama Inc\.(?:,|$)')
}

function Install-ShaktiOllama {
    $installed = Get-Command ollama.exe -ErrorAction SilentlyContinue
    $usualPath = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if ($installed -or (Test-Path -LiteralPath $usualPath)) {
        Write-Host 'Ollama is already installed; keeping the existing installation and models.'
        return 0
    }

    $download = Join-Path ([IO.Path]::GetTempPath()) ('Shakti-Ollama-' + [Guid]::NewGuid() + '.exe')
    try {
        Write-Host 'Downloading the official Ollama Windows installer.'
        Invoke-WebRequest 'https://ollama.com/download/OllamaSetup.exe' -OutFile $download -UseBasicParsing
        $signature = Get-AuthenticodeSignature -LiteralPath $download
        if (-not (Test-ShaktiOllamaSignature -Signature $signature)) {
            throw 'The Ollama installer does not have a valid Ollama Inc. signature. It was not run.'
        }
        Write-Host 'Installing Ollama silently. No models are downloaded during this step.'
        $process = Start-Process -FilePath $download -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-' -WindowStyle Hidden -PassThru
        $process.WaitForExit()
        if ($process.ExitCode -notin @(0, 3010)) {
            throw "Ollama setup failed with code $($process.ExitCode). Retry, or deselect Ollama and install it from Shakti later."
        }
        if ($process.ExitCode -eq 0 -and -not (Test-Path -LiteralPath $usualPath) -and -not (Get-Command ollama.exe -ErrorAction SilentlyContinue)) {
            throw 'Ollama setup finished but its executable was not found. Retry or install Ollama separately.'
        }
        return $process.ExitCode
    } finally {
        if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download -Force }
    }
}

Export-ModuleMember -Function Get-ShaktiInstallPaths, Assert-ShaktiManagedTrees, Test-ShaktiCompanionProcess, Stop-ShaktiCompanion, Invoke-ShaktiCommand, Test-ShaktiOllamaSignature, Install-ShaktiOllama
