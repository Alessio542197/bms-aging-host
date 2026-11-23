param([string]$Url)
if (-not $Url) { $Url = Read-Host "Manifest URL" }
Write-Host ""
Write-Host ("Manifest: " + $Url)
try {
    $resp = Invoke-WebRequest -Uri $Url -TimeoutSec 15 -UseBasicParsing
    $bytes = $resp.RawContentStream.ToArray()
    $text = [System.Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)
    $m = $text | ConvertFrom-Json
    Write-Host ("version : " + $m.version)
    Write-Host ("exe_url : " + $m.exe_url)
    Write-Host ("sha256  : " + $m.exe_sha256)
    $ok = $false
    try {
        $r = Invoke-WebRequest -Uri $m.exe_url -Method Head -TimeoutSec 20 -UseBasicParsing
        Write-Host ("exe_url HEAD: " + [int]$r.StatusCode + " " + $r.StatusDescription)
        $ok = $true
    } catch {
        $hr = $_.Exception.Response
        if ($hr) {
            Write-Host ("exe_url HEAD: " + [int]$hr.StatusCode + " " + $hr.StatusDescription)
        } else {
            Write-Host ("exe_url HEAD ERR: " + $_.Exception.Message)
        }
    }
} catch {
    Write-Host ("Manifest ERR: " + $_.Exception.Message)
}
Write-Host ""
