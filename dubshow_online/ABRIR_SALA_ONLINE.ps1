$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root ".runtime"
$PythonExe = Join-Path $Runtime "python\python.exe"
$BinDir = Join-Path $Runtime "bin"
$Cloudflared = Join-Path $BinDir "cloudflared.exe"
$Marker = Join-Path $Runtime "instalado-v3.ok"
$Port = 8765
$Tunnel = $null
$Server = $null
$PublicUrl = $null

function Install-DubShow {
    Write-Host "Preparando o DubShow Online..." -ForegroundColor Cyan
    $InstallerScript = Join-Path $Root "INSTALAR_ONLINE.ps1"
    & $InstallerScript
    if (-not (Test-Path $Marker)) {
        throw "A instalacao terminou sem criar o arquivo de confirmacao."
    }
}

function Get-CombinedLog([string]$OutFile, [string]$ErrFile) {
    $Text = ""
    if (Test-Path $OutFile) { $Text += (Get-Content $OutFile -Raw -ErrorAction SilentlyContinue) }
    if (Test-Path $ErrFile) { $Text += "`r`n" + (Get-Content $ErrFile -Raw -ErrorAction SilentlyContinue) }
    return $Text
}

function Test-DnsPublished([string]$HostName) {
    try {
        $Addresses = [System.Net.Dns]::GetHostAddresses($HostName)
        if ($Addresses.Count -gt 0) { return $true }
    }
    catch {}

    try {
        $ResolveCommand = Get-Command Resolve-DnsName -ErrorAction SilentlyContinue
        if ($ResolveCommand) {
            $Answer = Resolve-DnsName -Name $HostName -Type A -Server "1.1.1.1" -DnsOnly -ErrorAction Stop
            if ($null -ne ($Answer | Where-Object { $_.IPAddress } | Select-Object -First 1)) {
                & ipconfig.exe /flushdns | Out-Null
                return $true
            }
        }
    }
    catch {}
    return $false
}

function Wait-PublicTunnel(
    [string]$Url,
    [System.Diagnostics.Process]$TunnelProcess,
    [int]$MaximumSeconds = 120
) {
    $Uri = [System.Uri]$Url
    $HealthUrl = "$Url/health?startup=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"

    for ($Second = 1; $Second -le $MaximumSeconds; $Second++) {
        $TunnelProcess.Refresh()
        if ($TunnelProcess.HasExited) { return $false }

        if (Test-DnsPublished $Uri.DnsSafeHost) {
            try {
                $Response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 6 `
                    -Headers @{ "Cache-Control" = "no-cache"; "User-Agent" = "DubShow-Online/1.0.2" }
                if ($Response.StatusCode -eq 200) { return $true }
            }
            catch {
                # DNS can be ready a few seconds before the tunnel accepts HTTP.
            }
        }

        if (($Second % 10) -eq 0) {
            Write-Host "  Aguardando o link ficar acessivel... ${Second}s" -ForegroundColor DarkGray
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Start-TunnelAttempt(
    [string]$Protocol,
    [int]$Attempt,
    [string]$OutFile,
    [string]$ErrFile
) {
    Remove-Item $OutFile, $ErrFile -Force -ErrorAction SilentlyContinue

    # Isolate cloudflared from any config.yml in the user's profile. Quick
    # Tunnels can fail when a named-tunnel config exists in ~/.cloudflared.
    $CloudflaredHome = Join-Path $Runtime "cloudflared-home"
    New-Item -ItemType Directory -Force -Path $CloudflaredHome | Out-Null
    $OldHome = $env:HOME
    $OldUserProfile = $env:USERPROFILE
    $env:HOME = $CloudflaredHome
    $env:USERPROFILE = $CloudflaredHome

    try {
        Write-Host "Tentativa $Attempt/3 usando protocolo $Protocol..." -ForegroundColor Cyan
        return Start-Process -FilePath $Cloudflared `
            -ArgumentList @(
                "tunnel",
                "--url", "http://127.0.0.1:$Port",
                "--no-autoupdate",
                "--protocol", $Protocol,
                "--loglevel", "info"
            ) `
            -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $OutFile -RedirectStandardError $ErrFile
    }
    finally {
        $env:HOME = $OldHome
        $env:USERPROFILE = $OldUserProfile
    }
}

if (-not (Test-Path $Marker) -or -not (Test-Path $PythonExe) -or -not (Test-Path $Cloudflared)) {
    Install-DubShow
}

if (-not (Test-Path $PythonExe)) { throw "python.exe nao foi encontrado apos a instalacao." }
if (-not (Test-Path $Cloudflared)) { throw "cloudflared.exe nao foi encontrado apos a instalacao." }

$env:PATH = "$BinDir;$env:PATH"
$env:DUBSHOW_RUNTIME_DIR = Join-Path $Runtime "salas"
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
New-Item -ItemType Directory -Force -Path $env:DUBSHOW_RUNTIME_DIR | Out-Null

$ServerOut = Join-Path $Runtime "servidor.log"
$ServerErr = Join-Path $Runtime "servidor-erros.log"
$TunnelOut = Join-Path $Runtime "tunnel.log"
$TunnelErr = Join-Path $Runtime "tunnel-erros.log"
$LinkFile = Join-Path $Root "LINK_DA_SALA.txt"
Remove-Item $ServerOut, $ServerErr, $TunnelOut, $TunnelErr, $LinkFile -Force -ErrorAction SilentlyContinue

Write-Host "Iniciando o servidor do DubShow..." -ForegroundColor Cyan
$Server = Start-Process -FilePath $PythonExe `
    -ArgumentList @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "$Port", "--workers", "1") `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $ServerOut -RedirectStandardError $ServerErr

try {
    $Ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Milliseconds 500
        $Server.Refresh()
        if ($Server.HasExited) {
            $Details = if (Test-Path $ServerErr) { Get-Content $ServerErr -Raw } else { "Sem detalhes." }
            throw "O servidor nao iniciou. $Details"
        }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { $Ready = $true; break }
        }
        catch {}
    }
    if (-not $Ready) { throw "O servidor demorou demais para iniciar. Consulte servidor-erros.log." }

    Write-Host "Criando e validando um link publico gratuito..." -ForegroundColor Cyan
    Write-Host "O navegador so sera aberto depois que o DNS e o servidor responderem." -ForegroundColor DarkGray

    $Protocols = @("auto", "http2", "quic")
    for ($Attempt = 1; $Attempt -le $Protocols.Count; $Attempt++) {
        $Protocol = $Protocols[$Attempt - 1]
        $AttemptOut = Join-Path $Runtime "tunnel-$Attempt.log"
        $AttemptErr = Join-Path $Runtime "tunnel-$Attempt-erros.log"
        $Tunnel = Start-TunnelAttempt $Protocol $Attempt $AttemptOut $AttemptErr

        $CandidateUrl = $null
        for ($i = 0; $i -lt 180; $i++) {
            Start-Sleep -Milliseconds 500
            $Tunnel.Refresh()
            $Text = Get-CombinedLog $AttemptOut $AttemptErr
            $Match = [regex]::Match($Text, "https://[a-z0-9-]+\.trycloudflare\.com")
            if ($Match.Success) {
                $CandidateUrl = $Match.Value.TrimEnd('/')
                break
            }
            if ($Tunnel.HasExited) { break }
        }

        if ($CandidateUrl) {
            Write-Host "  Dominio criado. Confirmando publicacao e acesso HTTP..." -ForegroundColor DarkGray
            if (Wait-PublicTunnel $CandidateUrl $Tunnel 120) {
                $PublicUrl = $CandidateUrl
                Copy-Item $AttemptOut $TunnelOut -Force -ErrorAction SilentlyContinue
                Copy-Item $AttemptErr $TunnelErr -Force -ErrorAction SilentlyContinue
                break
            }
            Write-Host "  O dominio nao ficou acessivel. Tentando uma nova rota..." -ForegroundColor Yellow
        }
        else {
            Write-Host "  O Cloudflare nao retornou um dominio valido nessa tentativa." -ForegroundColor Yellow
        }

        Copy-Item $AttemptOut $TunnelOut -Force -ErrorAction SilentlyContinue
        Copy-Item $AttemptErr $TunnelErr -Force -ErrorAction SilentlyContinue
        if ($Tunnel -and -not $Tunnel.HasExited) {
            Stop-Process -Id $Tunnel.Id -Force -ErrorAction SilentlyContinue
            $Tunnel.WaitForExit(5000) | Out-Null
        }
        $Tunnel = $null
        Start-Sleep -Seconds 2
    }

    if (-not $PublicUrl) {
        Write-Host "" 
        Write-Host "Nao foi possivel publicar a sala pela rede atual." -ForegroundColor Red
        Write-Host "O jogo local sera aberto para confirmar que o servidor funciona." -ForegroundColor Yellow
        Write-Host "Execute DIAGNOSTICAR_TUNEL.bat e consulte tunnel-erros.log." -ForegroundColor Yellow
        Write-Host "Cloudflare Tunnel precisa de DNS e saida TCP ou UDP na porta 7844." -ForegroundColor Yellow
        Start-Process "http://127.0.0.1:$Port"
        Read-Host "Pressione ENTER para encerrar"
        return
    }

    # Avoid keeping a negative DNS answer cached from an earlier failed attempt.
    & ipconfig.exe /flushdns | Out-Null
    $PublicUrl | Set-Content -Path $LinkFile -Encoding ASCII
    if (Get-Command Set-Clipboard -ErrorAction SilentlyContinue) {
        try { Set-Clipboard -Value $PublicUrl } catch {}
    }

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Magenta
    Write-Host "LINK ONLINE VALIDADO:" -ForegroundColor Green
    Write-Host $PublicUrl -ForegroundColor Green
    Write-Host "" 
    Write-Host "O link foi copiado e salvo em LINK_DA_SALA.txt." -ForegroundColor White
    Write-Host "Envie aos amigos e mantenha esta janela aberta." -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Magenta
    Write-Host ""

    $BrowserUrl = "$PublicUrl/?startup=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
    Start-Process $BrowserUrl

    while (-not $Server.HasExited -and $Tunnel -and -not $Tunnel.HasExited) {
        Start-Sleep -Seconds 2
        $Server.Refresh()
        $Tunnel.Refresh()
    }

    if ($Tunnel -and $Tunnel.HasExited -and -not $Server.HasExited) {
        Write-Host "" 
        Write-Host "O link online foi encerrado pelo Cloudflare ou pela rede." -ForegroundColor Red
        Write-Host "Feche esta janela e execute ABRIR_SALA_ONLINE.bat para gerar outro link." -ForegroundColor Yellow
        Read-Host "Pressione ENTER para encerrar"
    }
}
finally {
    if ($Tunnel) {
        $Tunnel.Refresh()
        if (-not $Tunnel.HasExited) { Stop-Process -Id $Tunnel.Id -Force -ErrorAction SilentlyContinue }
    }
    if ($Server) {
        $Server.Refresh()
        if (-not $Server.HasExited) { Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue }
    }
}
