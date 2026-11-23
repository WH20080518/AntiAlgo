$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TaskNames = @("AntiAlgoHub_Server", "AntiAlgoHub_DailyRefresh")

foreach ($Name in $TaskNames) {
    $Existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($Existing) {
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Output "[OK] 已卸载计划任务：$Name"
    } else {
        Write-Output "[跳过] 未发现计划任务：$Name"
    }
}

$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "反算法情报站.lnk"
if (Test-Path $ShortcutPath) {
    Remove-Item -Path $ShortcutPath -Force
    Write-Output "[OK] 已移除桌面快捷方式：$ShortcutPath"
} else {
    Write-Output "[跳过] 桌面未发现快捷方式：$ShortcutPath"
}

$ServerProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'pipeline\.server' -and $_.CommandLine -like "*$ProjectRoot*" -or
    ($_.CommandLine -and $_.CommandLine -match 'pipeline\.server' -and $_.ExecutablePath -like "$ProjectRoot*")
}
foreach ($Proc in $ServerProcs) {
    Stop-Process -Id $Proc.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output "[OK] 已停止后台本地服务进程 (PID=$($Proc.ProcessId))"
}

Write-Output "`n卸载清理完成。"
