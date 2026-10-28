$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\PC\Desktop\claude输出\slide-video-repo'
$logDir = Join-Path $repo 'maintain\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$prompt = Get-Content -Raw -Encoding UTF8 (Join-Path $repo 'maintain\prompt.md')
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) { "claude 不在 PATH" | Out-File $log -Encoding utf8; exit 1 }
$out = Join-Path $logDir 'last-run.txt'
$arg = "-p " + ('"' + ($prompt -replace '"','\"') + '"') + " --permission-mode acceptEdits --add-dir `"$repo`" --output-format text"
$p = Start-Process -FilePath $claude.Source -ArgumentList $arg -WorkingDirectory $repo -Wait -PassThru -NoNewWindow -RedirectStandardOutput $out -RedirectStandardError (Join-Path $logDir 'last-run.err')
Get-Content -Raw -Encoding UTF8 $out | Out-File $log -Encoding utf8
if (Test-Path (Join-Path $logDir 'last-run.err')) { Get-Content -Raw -Encoding UTF8 (Join-Path $logDir 'last-run.err') | Out-File $log -Append -Encoding utf8 }
"exit=$($p.ExitCode)" | Out-File $log -Append -Encoding utf8
exit $p.ExitCode
