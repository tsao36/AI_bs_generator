$envFile = '.\\.env'
$kv = @{}
Get-Content $envFile | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith('#')) { return }
  $parts = $line -split '=',2
  if ($parts.Count -eq 2) { $kv[$parts[0].Trim()] = $parts[1].Trim() }
}
$tenant=$kv['AZURE_TENANT_ID']
$client=$kv['AZURE_CLIENT_ID']
$secret=$kv['GRAPH_CLIENT_SECRET']
$user=$kv['DEFAULT_TO']
if (-not $user) { $user='frank.lee@intel.com' }
$tokenResp = Invoke-RestMethod -Method Post -Uri "https://login.microsoftonline.com/$tenant/oauth2/v2.0/token" -ContentType 'application/x-www-form-urlencoded' -Body @{client_id=$client; client_secret=$secret; scope='https://graph.microsoft.com/.default'; grant_type='client_credentials'}
$token=$tokenResp.access_token
$uri = "https://graph.microsoft.com/v1.0/users/$user/messages?`$top=1&`$select=id,subject,receivedDateTime"
try {
  $resp = Invoke-WebRequest -Method Get -Uri $uri -Headers @{Authorization="Bearer $token"}
  Write-Output "MAIL_READ_CHECK_HTTP $($resp.StatusCode)"
  Write-Output "MAIL_READ_CHECK_OK"
} catch {
  $code = $_.Exception.Response.StatusCode.value__
  $body=''
  try {
    $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
    $body = $sr.ReadToEnd()
  } catch {}
  Write-Output "MAIL_READ_CHECK_HTTP $code"
  if ($body) { Write-Output $body }
  exit 1
}
