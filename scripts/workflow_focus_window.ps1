param(
    [string]$WindowTitle,
    [int]$ProcessId = 0,
    [UInt64]$WindowHandle = 0
)

if (-not $WindowTitle -and $ProcessId -le 0 -and $WindowHandle -le 0) {
    Write-Error "Either WindowTitle, ProcessId, or WindowHandle is required"
    exit 1
}

Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class WorkflowFocusNative
{
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@

$activated = $false
if ($WindowHandle -gt 0) {
    $handle = [IntPtr]::new([Int64]$WindowHandle)
    [WorkflowFocusNative]::ShowWindow($handle, 9) | Out-Null
    Start-Sleep -Milliseconds 150
    $activated = [WorkflowFocusNative]::SetForegroundWindow($handle)
}
elseif ($ProcessId -gt 0) {
    Add-Type -AssemblyName Microsoft.VisualBasic
    try {
        [Microsoft.VisualBasic.Interaction]::AppActivate($ProcessId)
        $activated = $true
    }
    catch {
        $activated = $false
    }
}

if (-not $activated -and $WindowTitle) {
    $wshell = New-Object -ComObject WScript.Shell
    $activated = $wshell.AppActivate($WindowTitle)
}

if (-not $activated) {
    Write-Error "Window not found"
    exit 1
}

if ($WindowHandle -gt 0) {
    Write-Output "focused:hwnd=$WindowHandle"
}
elseif ($ProcessId -gt 0) {
    Write-Output "focused:pid=$ProcessId"
}
else {
    Write-Output "focused:$WindowTitle"
}
