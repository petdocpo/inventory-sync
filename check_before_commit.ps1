# check_before_commit.ps1
# 커밋 전 안전 점검: main.py 문법 오류가 커밋/푸시되지 않도록 사전 검사
# 사용법: powershell -ExecutionPolicy Bypass -File check_before_commit.ps1
# 종료코드: 0 = 모두 통과, 1 = 하나 이상 실패

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$results = @()

function Add-Result($name, $status, $detail) {
    $script:results += [pscustomobject]@{ Name = $name; Status = $status; Detail = $detail }
}

# (a) 작업본 main.py 컴파일 검사
python -c "import sys;compile(open('main.py','rb').read().decode('utf-8'),'main.py','exec')" 2>$null
if ($LASTEXITCODE -eq 0) {
    Add-Result '작업본 main.py 문법' 'PASS' '컴파일 성공'
} else {
    Add-Result '작업본 main.py 문법' 'FAIL' '컴파일 실패 (python 오류 출력 참조)'
}

# (b) 스테이징된 내용(git show :main.py) 컴파일 검사 - git 출력을 python이 바이트로 받아 인코딩 깨짐 방지
$stagedCheck = @'
import subprocess, sys
r = subprocess.run(["git", "show", ":main.py"], capture_output=True)
if r.returncode != 0:
    print("NO_STAGED")
    sys.exit(0)
try:
    compile(r.stdout.decode("utf-8"), "main.py", "exec")
    print("OK")
except SyntaxError as e:
    print("FAIL line %s: %s" % (e.lineno, e.msg))
    sys.exit(1)
'@
$stagedOut = $stagedCheck | python - 2>&1
if ($LASTEXITCODE -eq 0 -and ($stagedOut | Select-Object -Last 1) -eq 'NO_STAGED') {
    Add-Result '스테이징 main.py 문법' 'PASS' '스테이징된 main.py 없음 (작업본만 검사됨)'
} elseif ($LASTEXITCODE -eq 0) {
    Add-Result '스테이징 main.py 문법' 'PASS' '인덱스 내용 컴파일 성공'
} else {
    $lastLine = ($stagedOut | Select-Object -Last 1)
    Add-Result '스테이징 main.py 문법' 'FAIL' "인덱스 내용 컴파일 실패: $lastLine"
}

# (c) render_page <script> 추출 -> node --check (렌더링 가능할 때만)
$renderCode = @'
import re, os, subprocess, sys
try:
    import main
except Exception as e:
    print("SKIP import failed: %s" % e)
    sys.exit(3)
d = os.path.join(os.environ.get("TEMP", "."), "precommit_rcheck")
os.makedirs(d, exist_ok=True)
html = main.render_page("<p>check</p>", user={"role": "master", "branch_code": "HQ"}, active="")
scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
bad = []
for i, s in enumerate(scripts):
    p = os.path.join(d, "s%d.js" % i)
    open(p, "w", encoding="utf-8").write(s)
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    if r.returncode != 0:
        bad.append((i, r.stderr[:300]))
if bad:
    for i, e in bad:
        print("script s%d.js FAIL: %s" % (i, e))
    sys.exit(1)
print("render scripts: %d, all OK" % len(scripts))
'@
try {
    $renderOut = $renderCode | python - 2>&1
    if ($LASTEXITCODE -eq 3) {
        $lastLine = ($renderOut | Select-Object -Last 1)
        Add-Result '렌더링 스크립트 문법' 'SKIP' "$lastLine"
    } elseif ($LASTEXITCODE -eq 0) {
        $lastLine = ($renderOut | Select-Object -Last 1)
        Add-Result '렌더링 스크립트 문법' 'PASS' "$lastLine"
    } else {
        $renderOut | ForEach-Object { Write-Host "  $_" }
        Add-Result '렌더링 스크립트 문법' 'FAIL' 'node --check 실패'
    }
} catch {
    Add-Result '렌더링 스크립트 문법' 'SKIP' "렌더링 점검 불가(의존성/환경): $($_.Exception.Message)"
}

# (d) git diff --check
git diff --check 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Add-Result 'git diff --check' 'PASS' '문제 없음'
} else {
    Add-Result 'git diff --check' 'FAIL' 'git diff --check 오류 있음'
}

# 요약 출력
Write-Host ""
Write-Host "===== 커밋 전 점검 결과 ====="
$results | Format-Table -AutoSize | Out-String | Write-Host
$failCount = @($results | Where-Object { $_.Status -eq 'FAIL' }).Count
if ($failCount -gt 0) {
    Write-Host "결과: FAIL ($failCount 건) - 커밋/푸시 전에 위 항목을 수정하세요."
    exit 1
} else {
    Write-Host "결과: PASS - 커밋해도 안전합니다."
    exit 0
}
