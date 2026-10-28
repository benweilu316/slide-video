$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\PC\Desktop\claude输出\slide-video-repo'
$logDir = Join-Path $repo 'maintain\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$prompt = Get-Content -Raw -Encoding UTF8 (Join-Path $repo 'maintain\prompt.md')
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) { "claude 不在 PATH" | Out-File $log -Encoding utf8; exit 1 }
& $claude.Source -p $prompt --permission-mode acceptEdits --add-dir $repo --output-format text 2>&1 | Out-File $log -Encoding utf8
"exit=$LASTEXITCODE" | Out-File $log -Append -Encoding utf8
exit $LASTEXITCODE
