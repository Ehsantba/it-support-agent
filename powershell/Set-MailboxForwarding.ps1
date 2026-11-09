# Setzt oder löscht die Weiterleitung eines Postfachs.
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,100}$')][string]$Upn,
    [ValidatePattern('^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,100}\.[A-Za-z]{2,}$')][string]$Address,
    [ValidateSet('yes')][string]$Clear
)
$ErrorActionPreference = 'Stop'
if ($Clear -eq 'yes') {
    Set-Mailbox -Identity $Upn -ForwardingSmtpAddress $null -DeliverToMailboxAndForward $false
    [pscustomobject]@{ upn = $Upn; forwarding_address = $null } | ConvertTo-Json -Compress
} else {
    if (-not $Address) { throw "Zieladresse fehlt" }
    Set-Mailbox -Identity $Upn -ForwardingSmtpAddress "smtp:$Address" -DeliverToMailboxAndForward $true
    [pscustomobject]@{ upn = $Upn; forwarding_address = $Address } | ConvertTo-Json -Compress
}
