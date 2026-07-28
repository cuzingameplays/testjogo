$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Force TLS 1.2 on older Windows PowerShell versions.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root ".runtime"
$PythonDir = Join-Path $Runtime "python"
$PythonExe = Join-Path $PythonDir "python.exe"
$BinDir = Join-Path $Runtime "bin"
$Marker = Join-Path $Runtime "instalado-v3.ok"

New-Item -ItemType Directory -Force -Path $Runtime, $BinDir | Out-Null

function Download-File([string]$Url, [string]$Destination) {
    $Attempts = 3
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        try {
            Remove-Item $Destination -Force -ErrorAction SilentlyContinue
            Write-Host "Baixando arquivo ($Attempt/$Attempts)..." -ForegroundColor Cyan
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination -Headers @{"User-Agent"="DubShow-Online/1.0.1"}
            if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 0)) {
                return
            }
            throw "O arquivo baixado esta vazio."
        }
        catch {
            if ($Attempt -eq $Attempts) { throw }
            Write-Host "Falha temporaria no download. Tentando novamente..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
        }
    }
}

function Assert-LastExitCode([string]$Message) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Message Codigo de saida: $LASTEXITCODE"
    }
}

function Test-LocalPython {
    if (-not (Test-Path $PythonExe)) { return $false }
    try {
        & $PythonExe -c "import sys; print(sys.version)" | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

if (-not (Test-LocalPython)) {
    Write-Host "[1/5] Preparando o Python portatil..." -ForegroundColor Yellow

    # Remove any partial installation left by version 1.0.
    Remove-Item $PythonDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null

    $PythonZip = Join-Path $Runtime "python-embed.zip"
    Download-File "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip" $PythonZip
    Expand-Archive -Path $PythonZip -DestinationPath $PythonDir -Force
    Remove-Item $PythonZip -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $PythonExe)) {
        throw "O Python portatil foi extraido, mas python.exe nao foi encontrado."
    }

    # Enable site-packages in the official embeddable Python distribution.
    $PthFile = Get-ChildItem $PythonDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $PthFile) { throw "O arquivo de configuracao do Python portatil nao foi encontrado." }
    $PthContent = Get-Content $PthFile.FullName
    $PthContent = $PthContent -replace '^#import site$', 'import site'
    Set-Content -Path $PthFile.FullName -Value $PthContent -Encoding ASCII

    Write-Host "Instalando o gerenciador de pacotes..." -ForegroundColor Cyan
    $GetPip = Join-Path $Runtime "get-pip.py"
    Download-File "https://bootstrap.pypa.io/get-pip.py" $GetPip
    & $PythonExe $GetPip --disable-pip-version-check
    Assert-LastExitCode "Falha ao preparar o pip."
    Remove-Item $GetPip -Force -ErrorAction SilentlyContinue
}

Write-Host "[2/5] Instalando os componentes do jogo..." -ForegroundColor Yellow
& $PythonExe -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
Assert-LastExitCode "Falha ao atualizar o pip."
& $PythonExe -m pip install --disable-pip-version-check --prefer-binary -r (Join-Path $Root "requirements.txt")
Assert-LastExitCode "Falha ao instalar as dependencias do jogo."

# Verify the main imports before continuing.
& $PythonExe -c "import fastapi, uvicorn, numpy, scipy, yt_dlp; print('Dependencias OK')"
Assert-LastExitCode "As dependencias foram instaladas, mas a verificacao falhou."

$FfmpegExe = Join-Path $BinDir "ffmpeg.exe"
$FfprobeExe = Join-Path $BinDir "ffprobe.exe"
if (-not (Test-Path $FfmpegExe) -or -not (Test-Path $FfprobeExe)) {
    Write-Host "[3/5] Baixando FFmpeg..." -ForegroundColor Yellow
    $FfmpegZip = Join-Path $Runtime "ffmpeg.zip"
    $FfmpegTemp = Join-Path $Runtime "ffmpeg-temp"
    Remove-Item $FfmpegTemp -Recurse -Force -ErrorAction SilentlyContinue
    Download-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $FfmpegZip
    Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegTemp -Force
    $FoundFfmpeg = Get-ChildItem $FfmpegTemp -Filter "ffmpeg.exe" -Recurse | Select-Object -First 1
    $FoundFfprobe = Get-ChildItem $FfmpegTemp -Filter "ffprobe.exe" -Recurse | Select-Object -First 1
    if (-not $FoundFfmpeg -or -not $FoundFfprobe) { throw "FFmpeg nao foi encontrado no arquivo baixado." }
    Copy-Item $FoundFfmpeg.FullName $FfmpegExe -Force
    Copy-Item $FoundFfprobe.FullName $FfprobeExe -Force
    Remove-Item $FfmpegZip -Force -ErrorAction SilentlyContinue
    Remove-Item $FfmpegTemp -Recurse -Force -ErrorAction SilentlyContinue
}

$DenoExe = Join-Path $BinDir "deno.exe"
if (-not (Test-Path $DenoExe)) {
    Write-Host "[4/5] Baixando o componente auxiliar do YouTube..." -ForegroundColor Yellow
    $DenoZip = Join-Path $Runtime "deno.zip"
    $DenoTemp = Join-Path $Runtime "deno-temp"
    Remove-Item $DenoTemp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $DenoTemp | Out-Null
    Download-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $DenoZip
    Expand-Archive -Path $DenoZip -DestinationPath $DenoTemp -Force
    $FoundDeno = Get-ChildItem $DenoTemp -Filter "deno.exe" -Recurse | Select-Object -First 1
    if (-not $FoundDeno) { throw "Deno nao foi encontrado no arquivo baixado." }
    Copy-Item $FoundDeno.FullName $DenoExe -Force
    Remove-Item $DenoZip -Force -ErrorAction SilentlyContinue
    Remove-Item $DenoTemp -Recurse -Force -ErrorAction SilentlyContinue
}

$Cloudflared = Join-Path $BinDir "cloudflared.exe"
Write-Host "[5/5] Atualizando o criador do link publico..." -ForegroundColor Yellow
$CloudflaredNew = Join-Path $Runtime "cloudflared-novo.exe"
Download-File "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" $CloudflaredNew
& $CloudflaredNew --version
Assert-LastExitCode "O cloudflared baixado nao passou na verificacao."
Move-Item $CloudflaredNew $Cloudflared -Force

# Final verification prevents a false success marker.
$RequiredFiles = @($PythonExe, $FfmpegExe, $FfprobeExe, $DenoExe, $Cloudflared)
foreach ($File in $RequiredFiles) {
    if (-not (Test-Path $File)) { throw "Componente ausente ao final da instalacao: $File" }
}

"DubShow Online installer v3 - $(Get-Date -Format o)" | Set-Content -Path $Marker -Encoding ASCII
Write-Host "Instalacao concluida com sucesso." -ForegroundColor Green
