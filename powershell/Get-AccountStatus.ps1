# Liest den Status eines Kontos aus dem Active Directory und gibt JSON aus.
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,100}$')][string]$Upn
)
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory
$u = Get-ADUser -Filter "UserPrincipalName -eq '$Upn'" -Properties LockedOut, Enabled, BadLogonCount, PasswordLastSet, DisplayName, Department
if (-not $u) { throw "Benutzer nicht gefunden" }
[pscustomobject]@{
    upn = $Upn; display_name = $u.DisplayName; department = $u.Department
    enabled = [bool]$u.Enabled; locked_out = [bool]$u.LockedOut; failed_logons = [int]$u.BadLogonCount
    mfa_registered = $null; password_last_set = "$($u.PasswordLastSet)"
} | ConvertTo-Json -Compress
