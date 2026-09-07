param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$localSource = 'C:\langfuse-main'
$runtime = if (Test-Path -LiteralPath $localSource) { $localSource } else { Join-Path $root 'data\langfuse-runtime' }
$envFile = Join-Path $root 'docker\langfuse.env'
$overrideFile = Join-Path $root 'docker\langfuse-compose.override.yml'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop was not found. Start Docker Desktop and retry."
}

if (-not (Test-Path -LiteralPath $runtime)) {
    git clone --depth 1 https://github.com/langfuse/langfuse.git $runtime
}

if (-not (Test-Path -LiteralPath $envFile)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $secret = ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
    $contents = "NEXTAUTH_URL=http://localhost:3000`nTELEMETRY_ENABLED=false`nSALT=$secret`nENCRYPTION_KEY=$secret`nNEXTAUTH_SECRET=$secret`nPOSTGRES_PASSWORD=$secret`nDATABASE_URL=postgresql://postgres:${secret}@postgres:5432/postgres`nCLICKHOUSE_PASSWORD=$secret`nMINIO_ROOT_PASSWORD=$secret`nLANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=$secret`nLANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=$secret`nLANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=$secret`nREDIS_AUTH=$secret"
    Set-Content -LiteralPath $envFile -Value $contents -Encoding utf8
    Write-Host "Generated local secret file: $envFile"
}
elseif (-not (Select-String -LiteralPath $envFile -Pattern '^DATABASE_URL=' -Quiet)) {
    $passwordLine = Select-String -LiteralPath $envFile -Pattern '^POSTGRES_PASSWORD=' | Select-Object -First 1
    if (-not $passwordLine) {
        throw "The local Langfuse secret file is missing POSTGRES_PASSWORD. Delete it and run this script again."
    }
    $password = $passwordLine.Line.Substring('POSTGRES_PASSWORD='.Length)
    Add-Content -LiteralPath $envFile -Value "DATABASE_URL=postgresql://postgres:${password}@postgres:5432/postgres" -Encoding utf8
    Write-Host "Added the missing DATABASE_URL to the local Langfuse secret file."
}

Push-Location $runtime
try {
    # Use Langfuse's published v4 images. This avoids compiling the full
    # Langfuse monorepo locally and is the recommended path for a demo host.
    $composeFile = 'docker-compose.yml'
    docker compose --env-file $envFile -f $composeFile -f $overrideFile -p shopcross-langfuse up -d
    if ($LASTEXITCODE -ne 0) {
        throw "Langfuse Docker Compose failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

Write-Host 'Langfuse is starting: http://localhost:3000'
Write-Host 'First boot usually takes 2-3 minutes. Create a project and add pk-lf/sk-lf to the root .env.'
