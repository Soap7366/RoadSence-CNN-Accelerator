$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$root = Split-Path $PSScriptRoot -Parent
foreach ($name in @('Prepare','RoadSense')) {
    & $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll "/win32icon:$PSScriptRoot\roadsense.ico" "/out:$root\$name.exe" "$PSScriptRoot\launcher.cs"
    if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed' }
}
