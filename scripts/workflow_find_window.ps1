param(
    [string[]]$WindowTitles = @(),
    [int]$ProcessId = 0
)

Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class WorkflowWindowFinder
{
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}
"@

if (($null -eq $WindowTitles -or $WindowTitles.Count -eq 0) -and $ProcessId -le 0) {
    Write-Error "Either WindowTitles or ProcessId is required"
    exit 1
}

$targets = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($title in $WindowTitles) {
    if ($title) {
        [void]$targets.Add($title)
    }
}

$match = $null
[WorkflowWindowFinder]::EnumWindows({
    param($hWnd, $lParam)

    if (-not [WorkflowWindowFinder]::IsWindowVisible($hWnd)) {
        return $true
    }

    [uint32]$windowPid = 0
    [void][WorkflowWindowFinder]::GetWindowThreadProcessId($hWnd, [ref]$windowPid)
    if ($ProcessId -gt 0 -and [int]$windowPid -eq $ProcessId) {
        $length = [WorkflowWindowFinder]::GetWindowTextLength($hWnd)
        $builder = New-Object System.Text.StringBuilder ([Math]::Max($length + 1, 1))
        [void][WorkflowWindowFinder]::GetWindowText($hWnd, $builder, $builder.Capacity)
        $script:match = [pscustomobject]@{
            title = $builder.ToString()
            hwnd = [int64]$hWnd
            pid = [int]$windowPid
        }
        return $false
    }

    $length = [WorkflowWindowFinder]::GetWindowTextLength($hWnd)
    if ($length -le 0) {
        return $true
    }

    $builder = New-Object System.Text.StringBuilder ($length + 1)
    [void][WorkflowWindowFinder]::GetWindowText($hWnd, $builder, $builder.Capacity)
    $title = $builder.ToString()
    if (-not $targets.Contains($title)) {
        return $true
    }

    $script:match = [pscustomobject]@{
        title = $title
        hwnd = [int64]$hWnd
        pid = [int]$windowPid
    }
    return $false
}, [IntPtr]::Zero) | Out-Null

if ($null -eq $match) {
    [pscustomobject]@{
        found = $false
        title = ""
        hwnd = 0
        pid = 0
    } | ConvertTo-Json -Depth 3
    exit 1
}

[pscustomobject]@{
    found = $true
    title = $match.title
    hwnd = $match.hwnd
    pid = $match.pid
} | ConvertTo-Json -Depth 3
