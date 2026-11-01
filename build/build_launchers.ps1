$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$root = Split-Path $PSScriptRoot -Parent
foreach ($name in @('Start','Prepare')) {
    & $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll "/out:$root\$name.exe" "$PSScriptRoot\launcher.cs"
    if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
}
