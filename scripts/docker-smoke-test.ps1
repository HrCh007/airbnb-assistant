# Docker smoke test for Windows PowerShell. Same checks as docker-smoke-test.sh.
#   powershell -ExecutionPolicy Bypass -File scripts\docker-smoke-test.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Front = "http://localhost:3000"
function Pass($m) { Write-Host "  PASS  $m" -ForegroundColor Green }
function Fail($m) { Write-Host "  FAIL  $m" -ForegroundColor Red; docker compose ps; exit 1 }

function Wait-Healthy {
  Write-Host "Waiting for services to become healthy..."
  for ($i = 0; $i -lt 90; $i++) {
    $rows = docker compose ps --format "{{.Service}}={{.Health}}" | Where-Object { $_ -match "^(ollama|chromadb|backend|frontend)=" }
    if (($rows | Where-Object { $_ -match "=healthy$" }).Count -eq 4) { $rows | ForEach-Object { "  $_" }; return }
    Start-Sleep -Seconds 4
  }
  Fail "services did not become healthy"
}

function Ask($sid, $tok, $msg) {
  $body = @{ session_id = $sid; message = $msg } | ConvertTo-Json
  $raw = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Front/api/chat/message" -Headers @{ "X-Session-Token" = $tok } -ContentType "application/json" -Body $body
  $text = ""; $places = 0; $ev = $null
  foreach ($line in ($raw.Content -split "`n")) {
    if ($line.StartsWith("event: ")) { $ev = $line.Substring(7).Trim() }
    elseif ($line.StartsWith("data: ")) {
      $d = $line.Substring(6) | ConvertFrom-Json
      if ($ev -eq "token") { $text += $d } elseif ($ev -eq "replace") { $text = $d } elseif ($ev -eq "places") { $places = $d.items.Count }
    }
  }
  return @{ text = $text; places = $places }
}

Write-Host "== 1. Build and start"
docker compose config -q; if ($LASTEXITCODE) { Fail "invalid compose config" }
docker compose up -d --build; if ($LASTEXITCODE) { Fail "docker compose up failed" }
Wait-Healthy; Pass "ollama, chromadb, backend, frontend healthy"

Write-Host "== 2. Connectivity"
$status = Invoke-RestMethod "$Front/api/status"
if ($status.paid_apis -ne "none") { Fail "paid APIs configured" } else { Pass "no paid APIs" }
if (-not $status.vector_store.reachable) { Fail "chromadb unreachable" } else { Pass "backend -> chromadb" }
if ($status.llm.state -eq "unreachable") { Fail "ollama unreachable" } else { Pass "backend -> ollama ($($status.llm.state))" }

Write-Host "== 3. Acceptance conversation"
$s = Invoke-RestMethod -Method Post "$Front/api/chat/sessions" -ContentType "application/json" -Body "{}"
Invoke-RestMethod -Method Put "$Front/api/chat/sessions/$($s.session_id)/selection" -Headers @{ "X-Session-Token" = $s.session_token } -ContentType "application/json" -Body '{"location_slug":"saket"}' | Out-Null
$r = Ask $s.session_id $s.session_token "What's the Wi-Fi password?";            if ($r.text -notmatch "saket-demo-2468") { Fail "wifi" } else { Pass "Wi-Fi password" }
$r = Ask $s.session_id $s.session_token "Suggest some nice places for dinner nearby."; if ($r.places -lt 1) { Fail "dinner" } else { Pass "dinner ($($r.places) places)" }
$r = Ask $s.session_id $s.session_token "Anything more budget-friendly?";          if ($r.places -lt 1) { Fail "cheaper" } else { Pass "cheaper follow-up" }
$r = Ask $s.session_id $s.session_token "What about nightlife? Somewhere for drinks"; if ($r.places -lt 1) { Fail "nightlife" } else { Pass "nightlife" }
$r = Ask $s.session_id $s.session_token "What's the checkout time tomorrow?";     if ($r.text -notmatch "11:00 AM") { Fail "checkout" } else { Pass "checkout time" }

Write-Host "== 4. Persistence"
$h = @{ "X-Session-Token" = $s.session_token }
$before = (Invoke-RestMethod "$Front/api/chat/sessions/$($s.session_id)/messages" -Headers $h).Count
docker compose down; docker compose up -d --no-build; Wait-Healthy
$after = (Invoke-RestMethod "$Front/api/chat/sessions/$($s.session_id)/messages" -Headers $h).Count
if ($before -ne $after) { Fail "messages $before -> $after" } else { Pass "data survived restart ($after messages)" }
Write-Host "All smoke tests passed." -ForegroundColor Green
