$Text = ($args -join ' ').Trim()
if (-not $Text) { Write-Error 'Provide announcement text.'; exit 1 }
$payload = @{ text = $Text; source = 'codex' } | ConvertTo-Json -Compress
$tmp = New-TemporaryFile
Set-Content -LiteralPath $tmp -Value $payload -Encoding UTF8
try {
  curl.exe -k -s -X POST https://localhost:8340/api/announce -H "Content-Type: application/json" --data-binary "@$tmp"
} finally {
  Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
}
