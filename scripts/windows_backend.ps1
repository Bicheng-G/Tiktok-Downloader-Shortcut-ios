param(
    [ValidateSet('menu', 'start', 'stop', 'restart', 'status', 'enable-autostart', 'disable-autostart', 'logs')]
    [string]$Action = 'menu'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$manager = Join-Path $PSScriptRoot 'windows_backend.py'
$log = Join-Path $projectRoot 'artifacts\backend.log'
$startup = [Environment]::GetFolderPath('Startup')
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $identity = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 12)
} finally { $sha.Dispose() }
$shortcutPath = Join-Path $startup "Video Resolver $identity.lnk"

function Invoke-Action([string]$Choice) {
    switch ($Choice) {
        'enable-autostart' {
            if (-not (Test-Path -LiteralPath $python)) { throw "Missing Python environment: $python" }
            if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) { throw 'Configure .env first.' }
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut($shortcutPath)
            $shortcut.WorkingDirectory = $projectRoot
            $shortcut.WindowStyle = 7
            # pythonw avoids even a console flash at login; errors go to the log.
            $pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
            if (-not (Test-Path -LiteralPath $pythonw)) { throw "Missing Python runtime: $pythonw" }
            $shortcut.TargetPath = $pythonw
            $shortcut.Arguments = '"' + $manager + '" run'
            $shortcut.Save()
            Write-Host 'Autostart enabled for this Windows user (after sign-in).'
        }
        'disable-autostart' {
            if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath }
            Write-Host 'Autostart disabled. A running backend is not stopped.'
        }
        'logs' {
            if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 40 }
            else { Write-Host 'No log yet.' }
        }
        default {
            if (-not (Test-Path -LiteralPath $python)) { throw "Missing Python environment: $python" }
            & $python $manager $Choice
            if ($LASTEXITCODE -ne 0) { throw "Action failed: $Choice" }
            if ($Choice -eq 'status') {
                Write-Host ('Autostart after sign-in: ' + (Test-Path -LiteralPath $shortcutPath))
            }
        }
    }
}

if ($Action -ne 'menu') { Invoke-Action $Action; exit }
while ($true) {
    Write-Host "`nVideo Resolver"
    Write-Host '1 Start   2 Stop   3 Restart   4 Status'
    Write-Host '5 Enable autostart after sign-in   6 Disable autostart'
    Write-Host '7 Logs    0 Exit'
    $choice = Read-Host 'Select'
    if ($choice -eq '0') { break }
    $actions = @{ '1'='start'; '2'='stop'; '3'='restart'; '4'='status'; '5'='enable-autostart'; '6'='disable-autostart'; '7'='logs' }
    if ($actions.ContainsKey($choice)) {
        try { Invoke-Action $actions[$choice] } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
    }
}
