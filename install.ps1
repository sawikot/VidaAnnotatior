# Installs VidaAnnotator with Docker Desktop on Windows. Run once from the cloned folder:
#     powershell -ExecutionPolicy Bypass -File install.ps1
# Afterwards, versions are installed and switched from the app: menu -> Version & updates.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker Desktop is not installed. Install it first: https://www.docker.com/products/docker-desktop/"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "Docker Desktop is not running. Start it and run this again." }

function Set-EnvValue($key, $value) {
    $lines = @(Get-Content .env)
    $found = $false
    $lines = $lines | ForEach-Object { if ($_ -match "^$key=") { $found = $true; "$key=$value" } else { $_ } }
    if (-not $found) { $lines += "$key=$value" }
    [IO.File]::WriteAllLines("$PWD\.env", [string[]]$lines)
}
function Get-EnvValue($key) {
    $line = Get-Content .env | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if ($line) { return $line.Substring($key.Length + 1) } else { return "" }
}

if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force data\watch | Out-Null

if (-not (Get-EnvValue "GITHUB_TOKEN")) {
    Write-Host "If the GitHub repository is private, paste a GitHub token (classic, scopes: repo + read:packages)."
    $secure = Read-Host "Token (press Enter if the repository is public)" -AsSecureString
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    Set-EnvValue "GITHUB_TOKEN" $token
}
$token = Get-EnvValue "GITHUB_TOKEN"
$repo = Get-EnvValue "GITHUB_REPO"
if (-not $repo) { $repo = "sawikot/VidaAnnotatior" }
if ($token) { $token | docker login ghcr.io -u $repo.Split("/")[0] --password-stdin }

docker compose pull
if ($LASTEXITCODE -ne 0) { Write-Error "Could not download the app. Check the token, and that a version has been released." }
docker compose up -d

$port = Get-EnvValue "PORT"
if (-not $port) { $port = "8088" }
Write-Host ""
Write-Host "VidaAnnotator is running: http://localhost:$port"
Write-Host "The first visit asks you to create the administrator account."
