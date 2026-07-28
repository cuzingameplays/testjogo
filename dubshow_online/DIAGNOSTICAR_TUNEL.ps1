$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root ".runtime"
$Cloudflared = Join-Path $Runtime "bin\cloudflared.exe"
$Report = Join-Path $Root "DIAGNOSTICO_TUNEL.txt"

"DubShow Online - diagnostico de rede - $(Get-Date -Format o)" | Set-Content $Report -Encoding UTF8

function Add-Report([string]$Text) {
    Write-Host $Text
    $Text | Add-Content $Report -Encoding UTF8
}

Add-Report ""
Add-Report "=== DNS ==="
foreach ($Name in @("api.trycloudflare.com", "region1.v2.argotunnel.com", "region2.v2.argotunnel.com")) {
    try {
        $Result = Resolve-DnsName -Name $Name -ErrorAction Stop | Where-Object { $_.IPAddress } | Select-Object -First 2
        Add-Report "$Name : OK"
        foreach ($Item in $Result) { Add-Report "  $($Item.IPAddress)" }
    }
    catch { Add-Report "$Name : FALHOU - $($_.Exception.Message)" }
}

Add-Report ""
Add-Report "=== PORTA 7844 ==="
if (Get-Command Test-NetConnection -ErrorAction SilentlyContinue) {
    foreach ($Name in @("region1.v2.argotunnel.com", "region2.v2.argotunnel.com")) {
        $Test = Test-NetConnection -ComputerName $Name -Port 7844 -InformationLevel Detailed -WarningAction SilentlyContinue
        Add-Report "$Name TCP/7844 : $($Test.TcpTestSucceeded)"
    }
}
else { Add-Report "Test-NetConnection nao esta disponivel neste Windows." }

Add-Report ""
Add-Report "=== CLOUDFLARED ==="
if (Test-Path $Cloudflared) {
    & $Cloudflared --version 2>&1 | ForEach-Object { Add-Report "$_" }
    Add-Report ""
    Add-Report "Executando verificacao nativa do Cloudflare..."
    & $Cloudflared tunnel diag 2>&1 | ForEach-Object { Add-Report "$_" }
}
else { Add-Report "cloudflared.exe nao encontrado. Execute ABRIR_SALA_ONLINE.bat primeiro." }

Add-Report ""
Add-Report "Relatorio salvo em: $Report"
Read-Host "Pressione ENTER para fechar"
