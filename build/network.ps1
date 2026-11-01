param([ValidateSet('list','apply','restore')][string]$Action='list', [int]$InterfaceIndex=0,
      [string]$Address='', [string]$Board='')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$root=Split-Path $PSScriptRoot -Parent
$state=Join-Path $root ".runtime\network_$InterfaceIndex.json"
try {
    if ($Action -eq 'list') {
        $items=@(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and $_.MediaType -eq '802.3' } | ForEach-Object {
            $adapter=$_
            [pscustomobject]@{index=$adapter.ifIndex;name=$adapter.Name;description=$adapter.InterfaceDescription;
                addresses=@(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress)}
        })
        ConvertTo-Json -InputObject $items -Depth 4 -Compress
        exit 0
    }
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    if (!(New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator authorization required.' }
    $adapter=Get-NetAdapter -Physical | Where-Object { $_.ifIndex -eq $InterfaceIndex }
    if (!$adapter) { throw 'Physical Ethernet adapter not found.' }
    if ($adapter.MediaType -ne '802.3') { throw 'Only a physical Ethernet adapter may be configured.' }
    if ($Action -eq 'restore') {
        $saved=Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
        if ($saved.mac -ne $adapter.MacAddress) { throw 'Adapter identity changed; restore cancelled.' }
        if ($saved.added) { Get-NetIPAddress -InterfaceIndex $InterfaceIndex -IPAddress $saved.added -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false }
        if ($saved.dhcp -eq 'Enabled') { Set-NetIPInterface -InterfaceIndex $InterfaceIndex -AddressFamily IPv4 -Dhcp Enabled }
        Write-Host 'Previous Ethernet configuration restored.'
        exit 0
    }
    $parsed=$null; $peer=$null
    if (![Net.IPAddress]::TryParse($Address,[ref]$parsed) -or $parsed.AddressFamily -ne 'InterNetwork' -or
        ![Net.IPAddress]::TryParse($Board,[ref]$peer) -or $peer.AddressFamily -ne 'InterNetwork') { throw 'Invalid IPv4 address.' }
    if (($Address.Split('.')[0..2] -join '.') -ne ($Board.Split('.')[0..2] -join '.') -or $Address -eq $Board) { throw 'Addresses must be distinct and in the same /24 subnet.' }
    if (Get-NetRoute -InterfaceIndex $InterfaceIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue) { throw 'This Ethernet adapter has a default gateway. Use a dedicated board Ethernet adapter; existing Internet connections are not changed.' }
    if (Test-Connection -ComputerName $Address -Count 1 -Quiet) { throw 'Candidate address responds; retry with another address.' }
    $dhcp=(Get-NetIPInterface -InterfaceIndex $InterfaceIndex -AddressFamily IPv4).Dhcp.ToString()
    if (Test-Path -LiteralPath $state) { throw 'A previous network change exists. Restore it before configuring again.' }
    [pscustomobject]@{mac=$adapter.MacAddress;dhcp=$dhcp;added=$Address;previous=@(Get-NetIPAddress -InterfaceIndex $InterfaceIndex -AddressFamily IPv4 | Select-Object IPAddress,PrefixLength)} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $state -Encoding UTF8
    try {
        New-NetIPAddress -InterfaceIndex $InterfaceIndex -IPAddress $Address -PrefixLength 24 | Out-Null
        Start-Sleep -Seconds 3
        $entry=Get-NetIPAddress -InterfaceIndex $InterfaceIndex -IPAddress $Address
        if ($entry.AddressState -ne 'Preferred') { throw "Address unavailable: $($entry.AddressState)" }
    } catch {
        Get-NetIPAddress -InterfaceIndex $InterfaceIndex -IPAddress $Address -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false
        if ($dhcp -eq 'Enabled') { Set-NetIPInterface -InterfaceIndex $InterfaceIndex -AddressFamily IPv4 -Dhcp Enabled }
        Remove-Item -LiteralPath $state -Force
        throw
    }
    Write-Host 'Ethernet address configured.'
} catch {
    Write-Host $_ -ForegroundColor Red
    if ($Action -ne 'list') { $_.ToString() | Set-Content -LiteralPath (Join-Path $root '.runtime/network_error.txt') -Encoding UTF8 }
    exit 1
}
