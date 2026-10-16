$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SecretDir = Join-Path $env:APPDATA 'AIStockIntelligence'
$SecretFile = Join-Path $SecretDir 'database_url.dpapi.txt'
$LogFile = Join-Path $SecretDir 'windows-worker.log'

if (-not (Test-Path $SecretFile)) {
    Add-Content -Path $LogFile -Value "$(Get-Date -Format o) [WINDOWS_AUTOSTART] BLOCKED secret file missing"
    exit 2
}

try {
    $Encrypted = Get-Content -Path $SecretFile -Raw
    $Secure = ConvertTo-SecureString $Encrypted
    $Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        $DatabaseUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    }

    if ([string]::IsNullOrWhiteSpace($DatabaseUrl) -or -not $DatabaseUrl.StartsWith('postgres')) {
        Add-Content -Path $LogFile -Value "$(Get-Date -Format o) [WINDOWS_AUTOSTART] BLOCKED invalid DATABASE_URL"
        exit 3
    }

    $env:DATABASE_URL = $DatabaseUrl
    $env:ASI_HYBRID_WORKER = '1'
    $env:ASI_WORKER_DEVICE = 'laptop'

    Set-Location $RepoRoot
    Add-Content -Path $LogFile -Value "$(Get-Date -Format o) [WINDOWS_AUTOSTART] START repo=$RepoRoot"

    $Py = Get-Command py -ErrorAction SilentlyContinue
    if ($Py) {
        & $Py.Source 'hybrid_worker.py' '--device' 'laptop' '--daemon' *>> $LogFile
    }
    else {
        $Python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $Python) {
            Add-Content -Path $LogFile -Value "$(Get-Date -Format o) [WINDOWS_AUTOSTART] BLOCKED Python launcher not found"
            exit 4
        }
        & $Python.Source 'hybrid_worker.py' '--device' 'laptop' '--daemon' *>> $LogFile
    }
}
catch {
    Add-Content -Path $LogFile -Value "$(Get-Date -Format o) [WINDOWS_AUTOSTART] ERROR $($_.Exception.Message)"
    exit 1
}
finally {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
