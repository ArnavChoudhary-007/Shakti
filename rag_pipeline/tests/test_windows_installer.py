import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest
import psutil


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'installer' / 'windows' / 'ShaktiInstaller.psm1'


def powershell(script, tmp_path):
    if sys.platform != 'win32':
        pytest.skip('Windows installer helpers require Windows PowerShell')
    environment = dict(os.environ, LOCALAPPDATA=str(tmp_path), SHAKTI_INSTALLER_MODULE=str(MODULE))
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command',
         "$ErrorActionPreference = 'Stop'; Import-Module $env:SHAKTI_INSTALLER_MODULE -Force; " + script],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_installer_refuses_sibling_root_and_traversal(tmp_path):
    powershell("""
        $expected = Join-Path $env:LOCALAPPDATA 'Shakti'
        $paths = Get-ShaktiInstallPaths -InstallRoot $expected
        if ($paths.data -ne (Join-Path $expected 'data')) { throw 'Wrong data directory' }
        foreach ($candidate in @($env:LOCALAPPDATA, ($expected + '-other'), (Join-Path $expected '..\\private'))) {
            $refused = $false
            try { Get-ShaktiInstallPaths -InstallRoot $candidate | Out-Null } catch { $refused = $true }
            if (-not $refused) { throw "Accepted unsafe root: $candidate" }
        }
    """, tmp_path)


def test_installer_rejects_junction_before_recursive_cleanup(tmp_path):
    powershell("""
        $root = Join-Path $env:LOCALAPPDATA 'Shakti'
        $outside = Join-Path $env:LOCALAPPDATA 'outside'
        $app = Join-Path $root 'app'
        New-Item -ItemType Directory -Path $app, $outside -Force | Out-Null
        $private = Join-Path $outside 'private.txt'
        Set-Content -LiteralPath $private -Value 'preserve me'
        New-Item -ItemType Junction -Path (Join-Path $app 'escape') -Target $outside | Out-Null
        $paths = Get-ShaktiInstallPaths -InstallRoot $root
        $refused = $false
        try { Assert-ShaktiManagedTrees -Paths $paths } catch { $refused = $true }
        if (-not $refused -or -not (Test-Path -LiteralPath $private)) { throw 'Unsafe junction handling' }
    """, tmp_path)


def test_installer_only_matches_its_own_companion_process(tmp_path):
    powershell("""
        $paths = Get-ShaktiInstallPaths -InstallRoot (Join-Path $env:LOCALAPPDATA 'Shakti')
        $python = Join-Path $paths.venv 'Scripts\\pythonw.exe'
        $companion = [pscustomobject]@{ ExecutablePath = $python; CommandLine = 'pythonw.exe -m rag_pipeline --no-browser' }
        if (-not (Test-ShaktiCompanionProcess -Process $companion -Paths $paths)) { throw 'Missed companion' }
        $companion.CommandLine = 'pythonw.exe other_app.py'
        if (Test-ShaktiCompanionProcess -Process $companion -Paths $paths) { throw 'Matched another application' }
        $companion.CommandLine = 'pythonw.exe -m rag_pipeline'
        $companion.ExecutablePath = Join-Path ($paths.venv + '-other') 'Scripts\\pythonw.exe'
        if (Test-ShaktiCompanionProcess -Process $companion -Paths $paths) { throw 'Matched a sibling runtime' }
        $companion.ExecutablePath = $null
        if (Test-ShaktiCompanionProcess -Process $companion -Paths $paths) { throw 'Matched an unknown runtime' }
    """, tmp_path)


@pytest.mark.parametrize('status,subject,expected', [
    ('Valid', 'CN=Ollama Inc., O=Ollama Inc., C=US', True),
    ('Valid', 'O=Ollama Inc., CN=Ollama Inc., C=US', True),
    ('NotSigned', 'CN=Ollama Inc.', False),
    ('HashMismatch', 'CN=Ollama Inc.', False),
    ('Valid', 'CN=Ollama Inc. Impostor, C=US', False),
    ('Valid', 'CN=Another Company', False),
])
def test_only_valid_ollama_signer_is_accepted(tmp_path, status, subject, expected):
    result = powershell(f"""
        $signature = [pscustomobject]@{{ Status = '{status}'; SignerCertificate = [pscustomobject]@{{ Subject = '{subject}' }} }}
        Test-ShaktiOllamaSignature -Signature $signature | ConvertTo-Json
    """, tmp_path)
    assert json.loads(result) is expected


def test_shutdown_ignores_an_exited_process_with_an_open_handle(tmp_path):
    powershell("""
        & (Get-Module ShaktiInstaller) {
            $script:paths = Get-ShaktiInstallPaths -InstallRoot (Join-Path $env:LOCALAPPDATA 'Shakti')
            function script:Get-CimInstance {
                param($ClassName, $Filter, $ErrorAction)
                return [pscustomobject]@{ ProcessId = 12345; ParentProcessId = 1; ExecutablePath = (Join-Path $script:paths.venv 'Scripts\\python.exe'); CommandLine = 'python.exe -m rag_pipeline' }
            }
            function script:Get-Process {
                param($Id, $ErrorAction)
                $exited = [pscustomobject]@{ HasExited = $true }
                $exited | Add-Member -MemberType ScriptMethod -Name Dispose -Value { }
                return $exited
            }
            function script:Stop-Process { throw 'Attempted to stop an exited process' }
            Stop-ShaktiCompanion -Paths $script:paths
        }
    """, tmp_path)


@pytest.mark.parametrize('valid_signature,exit_code,should_fail', [(True, 0, False), (False, 0, True), (True, 5, True), (True, 3010, False)])
def test_ollama_installer_checks_signature_silent_flags_and_exit_code(tmp_path, valid_signature, exit_code, should_fail):
    script = """
        & (Get-Module ShaktiInstaller) {
            $script:started = $false
            $script:waited = $false
            $script:downloaded = $null
            function script:Get-Command { param($Name, $ErrorAction) return $null }
            function script:Invoke-WebRequest {
                param($Uri, $OutFile, [switch]$UseBasicParsing)
                if ($Uri -ne 'https://ollama.com/download/OllamaSetup.exe') { throw 'Wrong download source' }
                $script:downloaded = $OutFile
                [IO.File]::WriteAllText($OutFile, 'test fixture')
            }
            function script:Get-AuthenticodeSignature {
                param($LiteralPath)
                return [pscustomobject]@{ Status = '__STATUS__'; SignerCertificate = [pscustomobject]@{ Subject = 'CN=Ollama Inc.' } }
            }
            function script:Start-Process {
                param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
                if ($ArgumentList -notcontains '/VERYSILENT' -or $ArgumentList -notcontains '/NORESTART' -or $WindowStyle -ne 'Hidden' -or $Wait) { throw 'Unsafe installer invocation or waiting on the Ollama daemon' }
                $script:started = $true
                $directory = Join-Path $env:LOCALAPPDATA 'Programs\\Ollama'
                New-Item -ItemType Directory -Path $directory -Force | Out-Null
                [IO.File]::WriteAllText((Join-Path $directory 'ollama.exe'), 'test fixture')
                $process = [pscustomobject]@{ ExitCode = __EXIT__ }
                $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { $script:waited = $true }
                return $process
            }
            $failed = $false
            try { $result = Install-ShaktiOllama } catch { $failed = $true }
            if ($failed -ne __FAIL__) { throw 'Incorrect failure handling' }
            if ($script:started -ne __STARTED__) { throw 'Signature did not gate execution' }
            if ($script:waited -ne __STARTED__) { throw 'Did not wait for the installer process' }
            if (-not $failed -and $result -ne __EXIT__) { throw 'Lost the installer exit code' }
            if (Test-Path -LiteralPath $script:downloaded) { throw 'Downloaded installer was not cleaned up' }
        }
    """
    script = script.replace('__STATUS__', 'Valid' if valid_signature else 'NotSigned').replace('__EXIT__', str(exit_code))
    script = script.replace('__FAIL__', '$true' if should_fail else '$false').replace('__STARTED__', '$true' if valid_signature else '$false')
    powershell(script, tmp_path)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows virtual environments use a redirector process')
def test_upgrade_stops_companion_redirector_children_but_not_other_python(tmp_path):
    venv = tmp_path / 'Shakti' / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True, timeout=60)
    package = tmp_path / 'rag_pipeline'
    package.mkdir()
    (package / '__main__.py').write_text("import os, pathlib, time\npathlib.Path('companion.pid').write_text(str(os.getpid()))\ntime.sleep(120)\n")
    python = str(venv / 'Scripts' / 'python.exe')
    other_code = "import os, pathlib, time; pathlib.Path('other.pid').write_text(str(os.getpid())); time.sleep(120)"
    companion = subprocess.Popen([python, '-m', 'rag_pipeline'], cwd=tmp_path)
    other = subprocess.Popen([python, '-c', other_code], cwd=tmp_path)
    try:
        deadline = time.monotonic() + 15
        while not all((tmp_path / name).exists() for name in ('companion.pid', 'other.pid')) and time.monotonic() < deadline:
            time.sleep(0.05)
        companion_pid = int((tmp_path / 'companion.pid').read_text())
        other_pid = int((tmp_path / 'other.pid').read_text())
        powershell("$paths = Get-ShaktiInstallPaths -InstallRoot (Join-Path $env:LOCALAPPDATA 'Shakti'); Stop-ShaktiCompanion -Paths $paths", tmp_path)
        companion.wait(timeout=10)
        assert not psutil.pid_exists(companion_pid)
        assert other.poll() is None and psutil.pid_exists(other_pid)
    finally:
        for name in ('companion.pid', 'other.pid'):
            path = tmp_path / name
            if path.exists():
                try:
                    process = psutil.Process(int(path.read_text()))
                    process.terminate()
                    process.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
        for process in (companion, other):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_package_stamps_version_without_shipping_user_data(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('package_release', ROOT / 'scripts' / 'package_release.py')
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    for relative, content in {
        'rag_pipeline/__init__.py': '__version__ = "0.2.0"\n',
        'rag_pipeline/webui/index.html': '<div id="root"></div>',
        'rag_pipeline/config.yaml': 'private configuration',
        'rag_pipeline/tests/test_private.py': 'not shipped',
        'uploads/private.txt': 'private document',
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    monkeypatch.setattr(package, 'OUT', tmp_path / 'dist' / 'shakti-companion.zip')
    monkeypatch.setattr(package.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='rag_pipeline/__init__.py\nrag_pipeline/tests/test_private.py\n'))
    assert package.main(['--version', '0.3.0-rc.1']) == 0
    with zipfile.ZipFile(package.OUT) as archive:
        assert set(archive.namelist()) == {'rag_pipeline/__init__.py', 'rag_pipeline/webui/index.html'}
        assert '__version__ = "0.3.0-rc.1"' in archive.read('rag_pipeline/__init__.py').decode()
    assert '__version__ = "0.2.0"' in (tmp_path / 'rag_pipeline/__init__.py').read_text()
    with pytest.raises(SystemExit):
        package.main(['--version', 'bad/version'])
