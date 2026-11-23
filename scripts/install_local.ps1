$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Launcher = Join-Path $ProjectRoot "scripts\launch_hub.pyw"
$DistIndex = Join-Path $ProjectRoot "web\dist\index.html"

if (-not (Test-Path $PythonW)) {
    throw "未找到虚拟环境解释器：$PythonW，请先在项目根目录执行 uv sync。"
}
if (-not (Test-Path $Launcher)) {
    throw "未找到无窗启动脚本：$Launcher"
}
if (-not (Test-Path $DistIndex)) {
    Write-Warning "尚未检测到前端构建产物 ($DistIndex)，请确认已执行 npm --prefix web run build。"
}

# 1. 创建桌面快捷方式（反算法情报站.lnk，无黑框启动）
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "反算法情报站.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PythonW
$Shortcut.Arguments = "`"$Launcher`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.WindowStyle = 7
$Shortcut.Description = "反算法个人情报站（本地一键无窗启动）"
$Shortcut.Save()
Write-Output "[OK] 已生成桌面快捷方式：$ShortcutPath"

# 2. 注册当前用户级计划任务（无需管理员权限）
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited

# 任务 A：登录时静默后台起本地服务
$ServerTaskName = "AntiAlgoHub_Server"
$ServerAction = New-ScheduledTaskAction `
    -Execute $PythonW `
    -Argument "-m pipeline.server --host 127.0.0.1 --port 8765" `
    -WorkingDirectory $ProjectRoot
$ServerTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$ServerSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $ServerTaskName `
    -Action $ServerAction `
    -Trigger $ServerTrigger `
    -Principal $Principal `
    -Settings $ServerSettings `
    -Description "登录时静默启动反算法个人情报站本地服务 (127.0.0.1:8765)" `
    -Force | Out-Null
Write-Output "[OK] 已注册计划任务：$ServerTaskName（开机/登录静默起服务）"

# 任务 B：每日 08:30 与 20:30 静默刷新情报，错过时间开机自动补跑
$RefreshTaskName = "AntiAlgoHub_DailyRefresh"
$RefreshAction = New-ScheduledTaskAction `
    -Execute $PythonW `
    -Argument "-m pipeline.run" `
    -WorkingDirectory $ProjectRoot
$TriggerMorning = New-ScheduledTaskTrigger -Daily -At "08:30"
$TriggerEvening = New-ScheduledTaskTrigger -Daily -At "20:30"
$RefreshSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $RefreshTaskName `
    -Action $RefreshAction `
    -Trigger @($TriggerMorning, $TriggerEvening) `
    -Principal $Principal `
    -Settings $RefreshSettings `
    -Description "每天 08:30 与 20:30 静默抓取并清洗反算法个人情报（含开机补跑）" `
    -Force | Out-Null
Write-Output "[OK] 已注册计划任务：$RefreshTaskName（08:30 / 20:30 定时更新 + StartWhenAvailable）"

Write-Output "`n安装完成！你现在可以直接双击桌面「反算法情报站」打开 http://127.0.0.1:8765 。"
