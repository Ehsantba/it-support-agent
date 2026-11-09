# Postfachgröße, Auslastung und Weiterleitung (Exchange Online).
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,100}$')][string]$Upn
)
$ErrorActionPreference = 'Stop'
$mb = Get-Mailbox -Identity $Upn
$stats = Get-MailboxStatistics -Identity $Upn
$quotaMb = [int]($mb.ProhibitSendQuota.ToString() -replace '.*\(([\d,\.]+) bytes\).*', '$1' -replace '[,\.]', '') / 1MB
$usedMb = [int]($stats.TotalItemSize.Value.ToMB())
[pscustomobject]@{
    upn = $Upn; quota_mb = [int]$quotaMb; used_mb = $usedMb
    used_percent = [int](100 * $usedMb / [math]::Max($quotaMb, 1)); forwarding_address = "$($mb.ForwardingSmtpAddress)"
} | ConvertTo-Json -Compress
