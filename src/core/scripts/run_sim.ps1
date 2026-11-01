param([ValidateSet('array','post','layer','memory','network','gap','linear','control')][string]$Test='network')
$ErrorActionPreference='Stop'
$entry=Join-Path $PSScriptRoot '../../../manage.py'
& python $entry rtl sim --tests $Test
exit $LASTEXITCODE