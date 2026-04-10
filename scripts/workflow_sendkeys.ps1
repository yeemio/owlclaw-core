param(
    [string]$WindowTitle,
    [int]$ProcessId = 0,
    [UInt64]$WindowHandle = 0,
    [Parameter(Mandatory = $true)][string]$Message
)

Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class WorkflowSendKeysNative
{
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@

$wshell = New-Object -ComObject WScript.Shell
$activated = $false
if ($WindowHandle -gt 0) {
    $handle = [IntPtr]::new([Int64]$WindowHandle)
    [WorkflowSendKeysNative]::ShowWindow($handle, 9) | Out-Null
    Start-Sleep -Milliseconds 150
    $activated = [WorkflowSendKeysNative]::SetForegroundWindow($handle)
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
    $activated = $wshell.AppActivate($WindowTitle)
}

if (-not $activated) {
    if ($WindowHandle -gt 0) {
        Write-Error "Window not found: hwnd=$WindowHandle title=$WindowTitle"
    }
    elseif ($ProcessId -gt 0) {
        Write-Error "Window not found: pid=$ProcessId title=$WindowTitle"
    }
    else {
        Write-Error "Window not found: $WindowTitle"
    }
    exit 1
}

Start-Sleep -Milliseconds 300
Set-Clipboard -Value $Message
Start-Sleep -Milliseconds 100
$wshell.SendKeys('^v')
Start-Sleep -Milliseconds 100
$wshell.SendKeys('~')

if ($WindowHandle -gt 0) {
    Write-Output "sent:hwnd=${WindowHandle}:$Message"
}
elseif ($ProcessId -gt 0) {
    Write-Output "sent:pid=${ProcessId}:$Message"
}
else {
    Write-Output "sent:${WindowTitle}:$Message"
}
