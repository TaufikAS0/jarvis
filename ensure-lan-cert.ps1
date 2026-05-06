$ErrorActionPreference = 'Stop'

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$certPath = Join-Path $projectDir 'cert.pem'
$keyPath = Join-Path $projectDir 'key.pem'
$metaPath = Join-Path $projectDir 'cert.lan.meta.json'
$crtPath = Join-Path $projectDir 'jarvis-lan.crt'
$cerPath = Join-Path $projectDir 'jarvis-lan.cer'
$desktopCrtPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'JARVIS LAN Certificate.crt'

function Get-ActiveLanIps {
    $addresses = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        $_.IPAddress -ne '127.0.0.1' -and
        $_.IPAddress -notlike '169.254*' -and
        $_.PrefixOrigin -ne 'WellKnown'
    } | Select-Object -ExpandProperty IPAddress -Unique

    return @($addresses | Sort-Object -Unique)
}

function Get-CurrentMetaKey([string[]]$ips) {
    return (($ips + @('127.0.0.1')) | Sort-Object -Unique) -join ','
}

function Get-StoredMetaKey {
    if (-not (Test-Path $metaPath)) { return '' }
    try {
        $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
        if ($null -eq $meta -or $null -eq $meta.ips) { return '' }
        return (@($meta.ips) | Sort-Object -Unique) -join ','
    } catch {
        return ''
    }
}

function Write-LanMeta([string[]]$ips) {
    $payload = [ordered]@{
        ips = @($ips | Sort-Object -Unique)
        generated_at = (Get-Date).ToString('o')
    }
    $payload | ConvertTo-Json -Depth 3 | Set-Content -Path $metaPath -Encoding UTF8
}

function Export-LanCertArtifacts {
    $openssl = Get-Command openssl -ErrorAction SilentlyContinue
    if (-not $openssl) {
        throw 'OpenSSL tidak ditemukan di PATH.'
    }

    & openssl x509 -in $certPath -out $crtPath -outform PEM | Out-Null
    & openssl x509 -in $certPath -out $cerPath -outform DER | Out-Null
    Copy-Item -Force $crtPath $desktopCrtPath
}

function New-LanCert([string[]]$ips) {
    $openssl = Get-Command openssl -ErrorAction SilentlyContinue
    if (-not $openssl) {
        throw 'OpenSSL tidak ditemukan di PATH.'
    }

    $sanEntries = @('DNS:localhost', 'IP:127.0.0.1') + ($ips | ForEach-Object { "IP:$($_)" })
    $sanValue = ($sanEntries | Sort-Object -Unique) -join ','

    $tempDir = Join-Path $env:TEMP ("jarvis-cert-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempDir | Out-Null
    $tempKey = Join-Path $tempDir 'key.pem'
    $tempCert = Join-Path $tempDir 'cert.pem'
    $tempCnf = Join-Path $tempDir 'openssl.cnf'

    @"
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = localhost

[v3_req]
subjectAltName = $sanValue
"@ | Set-Content -Path $tempCnf -Encoding ASCII

    & openssl req -x509 -nodes -newkey rsa:2048 -days 3650 -keyout $tempKey -out $tempCert -config $tempCnf | Out-Null

    Move-Item -Force $tempKey $keyPath
    Move-Item -Force $tempCert $certPath
    Write-LanMeta $ips
    Export-LanCertArtifacts

    Remove-Item -Recurse -Force $tempDir
    Write-Host "LAN certificate siap: $sanValue"
}

$currentIps = Get-ActiveLanIps
if (-not $currentIps -or $currentIps.Count -eq 0) {
    throw 'Tidak ada IP LAN aktif yang ditemukan.'
}

$currentKey = Get-CurrentMetaKey $currentIps
$storedKey = Get-StoredMetaKey

if ((Test-Path $certPath) -and (Test-Path $keyPath) -and ($currentKey -eq $storedKey)) {
    Export-LanCertArtifacts
    Write-Host "LAN certificate masih cocok untuk IP aktif: $currentKey"
    exit 0
}

New-LanCert $currentIps
