# Entsperrt ein gesperrtes Konto. Ein deaktiviertes Konto wird nicht angefasst.
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,100}$')][string]$Upn
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory
$u = Get-ADUser -Filter "UserPrincipalName -eq '$Upn'" -Properties Enabled
if (-not $u) { throw "Benutzer nicht gefunden" }
if (-not $u.Enabled) { throw "Das Konto ist deaktiviert, nicht gesperrt" }
Unlock-ADAccount -Identity $u
[pscustomobject]@{ upn = $Upn; locked_out = $false } | ConvertTo-Json -Compress
