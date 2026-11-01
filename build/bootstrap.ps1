param([ValidateSet('prepare','start')][string]$Mode = 'prepare')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $root '.runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
try {
    Set-Location -LiteralPath $root
    $venv = Join-Path $runtime 'pc_env\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) {
        $healthy=$false
        try { & $venv -I -c 'import tkinter,cv2,paramiko,numpy,PIL; tkinter.Tcl()' 2>$null; $healthy=($LASTEXITCODE -eq 0) } catch { }
        if ($healthy) {
            if ($Mode -eq 'start') { & $venv -B manage.py start }
            else { & $venv -B build/prepare.py }
            exit $LASTEXITCODE
        }
    }
    if ($Mode -eq 'start') {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show('Please run Prepare.exe or Prepare.cmd first.', 'PYNQ CNN') | Out-Null
        exit 1
    }
    $candidates = @()
    foreach ($minor in @(12,11,10,9)) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            try {
                $found = & py.exe "-3.$minor" -I -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $candidates += $found }
            } catch { }
        }
    }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    if (Get-Command python.exe -ErrorAction SilentlyContinue) { $candidates += (Get-Command python.exe).Source }
    $basePython = $null
    foreach ($candidate in $candidates) {
        if (!(Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate -I -c 'import sys,tkinter,venv; assert (3,9)<=sys.version_info[:2]<=(3,12); tkinter.Tcl()' 2>$null
            if ($LASTEXITCODE -eq 0) { $basePython = $candidate; break }
        } catch { }
    }
    if (!$basePython) {
        Write-Host 'Installing Python 3.12 with Tcl/Tk for the current user...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $installer = Join-Path $runtime 'python-3.12.10-amd64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python installer signature verification failed.' }
        $process = Start-Process -FilePath $installer -ArgumentList @('/quiet','InstallAllUsers=0','Include_tcltk=1','Include_pip=1','Include_test=0','PrependPath=0') -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw "Python installer failed: $($process.ExitCode)" }
        $basePython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    }
    # Preserve a copied/broken machine-specific environment before recreating it.
    $envFolder = Join-Path $runtime 'pc_env'
    if (Test-Path -LiteralPath $envFolder) {
        Move-Item -LiteralPath $envFolder -Destination (Join-Path $runtime ('pc_env_previous_' + [guid]::NewGuid().ToString('N')))
    }
    & $basePython -B manage.py setup --python $basePython
    if ($LASTEXITCODE -ne 0) { throw 'PC environment setup failed. Check Internet access and retry Prepare.cmd.' }
    & $venv -B build/prepare.py
    exit $LASTEXITCODE
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
