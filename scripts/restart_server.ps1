<#
.SYNOPSIS
    一键重启「反算法个人情报站」本地后台服务并验证全链路健康状态。

.EXAMPLE
    .\scripts\restart_server.ps1
    .\scripts\restart_server.ps1 -RebuildWeb -OpenBrowser
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$TaskName = "AntiAlgoHub_Server",
    [switch]$RebuildWeb,
    [switch]$OpenBrowser,
    [int]$TimeoutSeconds = 15
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonwExe  = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$ServerLog   = Join-Path $ProjectRoot "data\server.log"
$BaseUrl     = "http://127.0.0.1:$Port"

# --- 工具函数：绕过系统代理探测本地端口与 JSON 接口 ---
function Test-LocalPortOpen {
    param([int]$TargetPort)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(200, $false) -and $client.Connected) {
            $client.EndConnect($async)
            return $true
        }
        return $false
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Invoke-LocalJson {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutMs = 3000
    )
    $req = [System.Net.HttpWebRequest]([System.Net.WebRequest]::Create($Url))
    $req.Method  = "GET"
    $req.Timeout = $TimeoutMs
    $req.Proxy   = $null  # 强制绕过系统代理，避免 127.0.0.1 超时

    $resp = $null
    try {
        $resp   = [System.Net.HttpWebResponse]$req.GetResponse()
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
        $body   = $reader.ReadToEnd()
        $reader.Close()
        return [PSCustomObject]@{
            Ok         = $true
            StatusCode = [int]$resp.StatusCode
            Data       = ($body | ConvertFrom-Json)
            Error      = $null
        }
    }
    catch {
        return [PSCustomObject]@{
            Ok         = $false
            StatusCode = 0
            Data       = $null
            Error      = $_.Exception.Message
        }
    }
    finally {
        if ($resp) { $resp.Close() }
    }
}

function Get-ServerProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*pipeline.server*" }
}

Write-Host "`n[Anti-Algo Hub] 开始重启本地服务 ($BaseUrl)" -ForegroundColor Cyan

# --- 1. 可选：重新构建前端静态资源 ---
if ($RebuildWeb) {
    Write-Host "[1/4] 正在重新构建前端 (npm --prefix web run build)..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        & npm --prefix web run build
        if ($LASTEXITCODE -ne 0) {
            throw "前端构建失败（退出码 $LASTEXITCODE），已中止重启。"
        }
    }
    finally {
        Pop-Location
    }
}

# --- 2. 停止旧服务并等待端口彻底释放 ---
Write-Host "[2/4] 正在停止旧版后台进程..." -ForegroundColor Yellow
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

$oldProcs = @(Get-ServerProcesses)
if ($oldProcs.Count -gt 0) {
    foreach ($proc in $oldProcs) {
        Write-Host "      结束旧进程 PID=$($proc.ProcessId)" -ForegroundColor DarkGray
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

# 等待端口释放（最多 5 秒，避免 SO_EXCLUSIVEADDRUSE 冲突）
$releaseDeadline = (Get-Date).AddSeconds(5)
while ((Get-Date) -lt $releaseDeadline) {
    if (-not (Test-LocalPortOpen -TargetPort $Port) -and @(Get-ServerProcesses).Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 250
}

if (Test-LocalPortOpen -TargetPort $Port) {
    Write-Host "[ERROR] 端口 $Port 仍被其他进程占用，请检查后重试。" -ForegroundColor Red
    exit 1
}

# --- 3. 静默拉起新版服务 ---
Write-Host "[3/4] 正在启动新版后台服务..." -ForegroundColor Yellow
if ($task -and $Port -eq 8765) {
    Start-ScheduledTask -TaskName $TaskName
    $startMode = "计划任务 ($TaskName)"
}
else {
    if (-not (Test-Path $PythonwExe)) {
        Write-Host "[ERROR] 未找到虚拟环境解释器：$PythonwExe" -ForegroundColor Red
        exit 1
    }
    Start-Process -FilePath $PythonwExe `
        -ArgumentList "-m", "pipeline.server", "--port", "$Port" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden
    $startMode = "直接静默拉起 (pythonw.exe)"
}
Write-Host "      启动方式：$startMode" -ForegroundColor DarkGray

# --- 4. 轮询并验证全部核心端点健康状态 ---
Write-Host "[4/4] 正在验证服务健康状态..." -ForegroundColor Yellow
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$statusRes = $null
while ((Get-Date) -lt $deadline) {
    $probe = Invoke-LocalJson -Url "$BaseUrl/api/status" -TimeoutMs 1500
    if ($probe.Ok) {
        $statusRes = $probe
        break
    }
    Start-Sleep -Milliseconds 400
}

if (-not $statusRes -or -not $statusRes.Ok) {
    Write-Host "`n[FAIL] 服务在 ${TimeoutSeconds} 秒内未就绪！" -ForegroundColor Red
    if (Test-Path $ServerLog) {
        Write-Host "`n--- data/server.log 最近 15 行 ---" -ForegroundColor DarkYellow
        Get-Content $ServerLog -Tail 15 -Encoding UTF8
    }
    exit 1
}

$digestRes = Invoke-LocalJson -Url "$BaseUrl/digest.json"
$notifyRes = Invoke-LocalJson -Url "$BaseUrl/api/notify/status"
$termsRes  = Invoke-LocalJson -Url "$BaseUrl/api/terms"

$newPids = (@(Get-ServerProcesses) | ForEach-Object { $_.ProcessId }) -join ", "
Write-Host "`n[OK] 本地服务已重启并通过健康检查！" -ForegroundColor Green
Write-Host "  服务地址 : $BaseUrl  (PID: $newPids)"

if ($digestRes.Ok) {
    $itemCount     = @($digestRes.Data.items).Count
    $upcomingCount = @($digestRes.Data.upcoming).Count
    $termName      = if ($digestRes.Data.daily_term) { $digestRes.Data.daily_term.term_en } else { "无" }
    Write-Host "  情报数据 : HTTP 200 | 卡片 $itemCount 条 | 日程 $upcomingCount 条 | 今日术语: $termName"
}
else {
    Write-Host "  情报数据 : [WARN] /digest.json 异常 ($($digestRes.Error))" -ForegroundColor DarkYellow
}

if ($notifyRes.Ok) {
    $notifyState = if ($notifyRes.Data.enabled) { "已开启" } else { "已暂停" }
    Write-Host "  桌面提醒 : HTTP 200 | 状态: $notifyState"
}
else {
    Write-Host "  桌面提醒 : [WARN] /api/notify/status 异常 ($($notifyRes.Error))" -ForegroundColor DarkYellow
}

if ($termsRes.Ok) {
    $totalTerms    = $termsRes.Data.stats.total
    $masteredTerms = $termsRes.Data.stats.mastered
    Write-Host "  历史术语 : HTTP 200 | 已掌握 $masteredTerms / $totalTerms"
}
else {
    Write-Host "  历史术语 : [WARN] /api/terms 异常 ($($termsRes.Error))" -ForegroundColor DarkYellow
}

if ($OpenBrowser) {
    Start-Process $BaseUrl
}

Write-Host ""