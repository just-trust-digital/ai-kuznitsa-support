param(
    [ValidateRange(1, 65535)][int]$Port = 8766,
    [ValidateSet('buyer', 'personal', 'manager1', 'manager2', 'jury', 'developer')][string]$CopyToken,
    [switch]$ConfigureTelegram
)
$ErrorActionPreference = 'Stop'
$entry = Join-Path $PSScriptRoot 'server.py'
$runtimeDir = Join-Path $PSScriptRoot 'private/runtime'
$telegramFile = Join-Path $runtimeDir 'telegram-token.dpapi'
if ($ConfigureTelegram) {
    $Host.UI.RawUI.WindowTitle = 'Softline | Telegram @jtd_test_bot'
    $secret = Read-Host 'Paste the @jtd_test_bot token and press Enter (input is hidden)' -AsSecureString
    if (-not $secret.Length) { throw 'No token entered.' }
    try {
        $tokenValue = [Net.NetworkCredential]::new('', $secret).Password
        if ($tokenValue -cnotmatch '^[0-9]+:[A-Za-z0-9_-]{20,}$') { throw 'Invalid Telegram token format.' }
        New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        $pendingFile = Join-Path $runtimeDir ('tg-' + [guid]::NewGuid().ToString('N') + '.tmp')
        $secret | ConvertFrom-SecureString | Set-Content -LiteralPath $pendingFile -Encoding ascii
        Move-Item -LiteralPath $pendingFile -Destination $telegramFile -Force
        Write-Host 'Saved for this Windows account.'
    } finally {
        $tokenValue = $null
        $secret.Dispose()
    }
    return
}
if ($CopyToken) {
    & python $entry --init-access
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $filename = if ($CopyToken -eq 'personal') { 'personal-access.json' } else { 'access.json' }
    $access = Get-Content -LiteralPath (Join-Path $runtimeDir $filename) -Raw | ConvertFrom-Json
    Set-Clipboard -Value $access.$CopyToken
    Write-Output "Token copied for $CopyToken"
    return
}
$modelDir = Join-Path $PSScriptRoot 'private/runtime/nimble-isolated/models'
$nimbleManifest = Join-Path $modelDir 'manifests/registry.ollama.ai/library/nimble/latest'
if (Test-Path -LiteralPath $nimbleManifest) {
    try {
        $models = Invoke-RestMethod 'http://127.0.0.1:11439/api/tags' -TimeoutSec 2
    } catch {
        $models = $null
    }
    if (-not $models) {
        $ollama = (Get-Command ollama.exe -ErrorAction Stop).Source
        $previousHost = $env:OLLAMA_HOST
        $previousModels = $env:OLLAMA_MODELS
        try {
            $env:OLLAMA_HOST = '127.0.0.1:11439'
            $env:OLLAMA_MODELS = $modelDir
            Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
        } finally {
            $env:OLLAMA_HOST = $previousHost
            $env:OLLAMA_MODELS = $previousModels
        }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            try {
                $models = Invoke-RestMethod 'http://127.0.0.1:11439/api/tags' -TimeoutSec 1
                break
            } catch {
                Start-Sleep -Milliseconds 300
            }
        }
    }
    if ('nimble:latest' -notin @($models.models.name)) { throw 'Local Nimble did not become ready on port 11439.' }
}
$originalBotToken = $env:SUPPORT_TELEGRAM_BOT_TOKEN
try {
    if ((-not $originalBotToken) -and (Test-Path -LiteralPath $telegramFile)) {
        $savedToken = Get-Content -LiteralPath $telegramFile -Raw | ForEach-Object { $_.Trim() } | ConvertTo-SecureString
        try { $env:SUPPORT_TELEGRAM_BOT_TOKEN = [Net.NetworkCredential]::new('', $savedToken).Password }
        finally { $savedToken.Dispose() }
    }
    & python $entry --port $Port
    $serverExitCode = $LASTEXITCODE
} finally {
    $env:SUPPORT_TELEGRAM_BOT_TOKEN = $originalBotToken
}
exit $serverExitCode
