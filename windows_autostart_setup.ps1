$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $RepoRoot 'windows_autostart_run.ps1'
$SecretDir = Join-Path $env:APPDATA 'AIStockIntelligence'
$SecretFile = Join-Path $SecretDir 'database_url.dpapi.txt'
$LogFile = Join-Path $SecretDir 'windows-worker.log'
$TaskName = 'AI Stock Intelligence Hybrid Worker'

if (-not (Test-Path $Runner)) {
    throw "windows_autostart_run.ps1 tidak ditemukan di $RepoRoot"
}

New-Item -ItemType Directory -Path $SecretDir -Force | Out-Null

Write-Host ''
Write-Host 'AI Stock Intelligence - Windows Auto Start Setup' -ForegroundColor Cyan
Write-Host 'DATABASE_URL akan disimpan terenkripsi dengan Windows DPAPI untuk user Windows ini.' -ForegroundColor DarkGray
Write-Host 'Jangan paste URL ke chat. Paste hanya pada prompt rahasia di bawah.' -ForegroundColor Yellow
Write-Host ''

$SecureUrl = Read-Host 'Paste Neon DATABASE_URL lalu Enter' -AsSecureString
$Encrypted = ConvertFrom-SecureString $SecureUrl
Set-Content -Path $SecretFile -Value $Encrypted -Encoding UTF8

# Verify decryptability without printing the secret.
$VerifySecure = ConvertTo-SecureString (Get-Content -Path $SecretFile -Raw)
$VerifyBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($VerifySecure)
try {
    $VerifyPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($VerifyBstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($VerifyBstr)
}
if ([string]::IsNullOrWhiteSpace($VerifyPlain) -or -not $VerifyPlain.StartsWith('postgres')) {
    Remove-Item $SecretFile -Force -ErrorAction SilentlyContinue
    throw 'DATABASE_URL tidak valid. Setup dibatalkan.'
}
$VerifyPlain = $null

$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$ActionArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Runner`""
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $ActionArgs -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'Auto start AI Stock Intelligence laptop hybrid worker after Windows login.' -Force | Out-Null

Write-Host ''
Write-Host 'WINDOWS AUTOSTART TERPASANG' -ForegroundColor Green
Write-Host "Task      : $TaskName"
Write-Host "Repo      : $RepoRoot"
Write-Host "Secret    : terenkripsi DPAPI di $SecretFile"
Write-Host "Log       : $LogFile"
Write-Host ''
Write-Host 'Tes sekarang tanpa restart:' -ForegroundColor Cyan
Write-Host "Start-ScheduledTask -TaskName '$TaskName'"
Write-Host 'Lalu tunggu 5-10 detik dan cek log:' -ForegroundColor Cyan
Write-Host "Get-Content '$LogFile' -Tail 30"
