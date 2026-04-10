param(
    [Parameter(Mandatory = $true)]
    [string]$Agent,
    [string]$RepoRoot = ".",
    [int]$IntervalSeconds = 2
)

$repoRootPath = (Resolve-Path $RepoRoot).Path
$runtimeRoot = Join-Path $repoRootPath ".kiro\runtime"

function Read-JsonFile {
    param(
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    try {
        return Get-Content $Path -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Read-TextFile {
    param(
        [string]$Path,
        [int]$MaxLines = 40
    )

    if (-not (Test-Path $Path)) {
        return @()
    }

    try {
        return @(Get-Content $Path -Tail $MaxLines)
    }
    catch {
        return @()
    }
}

function Get-AgentLabel {
    param(
        [string]$Agent
    )

    switch ($Agent) {
        "main" { return "orchestrator" }
        "review" { return "review-gate" }
        "codex" { return "coding-primary" }
        "codex-gpt" { return "coding-secondary" }
        "audit-a" { return "deep-audit" }
        "audit-b" { return "audit-review" }
        default { return "agent" }
    }
}

function Get-RecentAssistantMessage {
    param(
        [string]$Agent
    )

    $path = Join-Path $runtimeRoot ("executions\{0}\last_message.txt" -f $Agent)
    if (-not (Test-Path $path)) {
        return @()
    }

    try {
        $content = (Get-Content $path -Raw).Trim()
        if ([string]::IsNullOrWhiteSpace($content)) {
            return @()
        }

        $lines = @()
        foreach ($line in ($content -split "(`r`n|`n|`r)")) {
            $trimmed = $line.TrimEnd()
            if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
                $lines += $trimmed
            }
        }
        return $lines | Select-Object -First 12
    }
    catch {
        return @()
    }
}

function Get-ExecutionLogTail {
    param(
        [string]$Agent
    )

    $result = Read-JsonFile (Join-Path $runtimeRoot ("executions\{0}\result.json" -f $Agent))
    if ($null -eq $result) {
        return @()
    }

    $logPath = [string]$result.log_path
    if ([string]::IsNullOrWhiteSpace($logPath) -or -not (Test-Path $logPath)) {
        return @()
    }

    return Read-TextFile -Path $logPath -MaxLines 16
}

function Test-WorkflowObjectRelevant {
    param(
        [string]$Agent,
        $Item
    )

    if ($null -eq $Item) {
        return $false
    }

    $owner = [string]$Item.owner
    $source = [string]$Item.source
    $targetAgent = [string]$Item.target_agent
    $targetBranch = [string]$Item.target_branch
    $worktree = [string]$Item.target_worktree

    switch ($Agent) {
        "main" {
            return $true
        }
        "review" {
            return $owner -eq "review" -or $targetAgent -eq "review" -or $targetBranch -eq "review-work"
        }
        "codex" {
            return $targetAgent -eq "codex" -or $targetBranch -eq "codex-work" -or $owner -eq "codex"
        }
        "codex-gpt" {
            return $targetAgent -eq "codex-gpt" -or $targetBranch -eq "codex-gpt-work" -or $owner -eq "codex-gpt"
        }
        "audit-a" {
            return $source -eq "audit-a" -or $owner -eq "audit-a" -or $worktree -eq "audit-a"
        }
        "audit-b" {
            return $source -eq "audit-b" -or $owner -eq "audit-b" -or $worktree -eq "audit-b"
        }
        default {
            return $false
        }
    }
}

function Format-WorkflowObjectLine {
    param(
        [string]$Category,
        $Item
    )

    $objectType = [string]$Item.object_type
    $status = [string]$Item.status
    $shortId = [string]$Item.id
    if ($shortId.Length -gt 24) {
        $shortId = $shortId.Substring(0, 24)
    }

    $headline = ""
    switch ($objectType) {
        "finding" {
            $headline = [string]$Item.title
        }
        "assignment" {
            $spec = [string]$Item.spec
            $taskRefs = @($Item.task_refs) -join ","
            $headline = "{0} {1}" -f $spec, $taskRefs
        }
        "triage_decision" {
            $headline = "{0} | {1}" -f [string]$Item.decision, [string]$Item.reason
        }
        "review_verdict" {
            $headline = "{0} {1}" -f [string]$Item.verdict, [string]$Item.target_branch
        }
        "delivery" {
            $headline = "{0} {1}" -f [string]$Item.source_branch, [string]$Item.summary
        }
        "merge_decision" {
            $headline = "{0} -> {1}" -f [string]$Item.source_branch, [string]$Item.target_branch
        }
        "blocker" {
            $headline = [string]$Item.summary
        }
        default {
            $headline = [string]$Item.summary
        }
    }

    if ([string]::IsNullOrWhiteSpace($headline)) {
        $headline = [string]$Item.reason
    }
    if ([string]::IsNullOrWhiteSpace($headline)) {
        $headline = [string]$Item.title
    }
    if ([string]::IsNullOrWhiteSpace($headline)) {
        $headline = "<no summary>"
    }

    return ("[{0}] {1} {2} {3}" -f $Category, $shortId, $status, $headline)
}

function Get-RecentWorkflowObjects {
    param(
        [string]$Agent,
        [int]$MaxItems = 6
    )

    $sources = @(
        @{ Category = "finding"; Path = Join-Path $runtimeRoot "findings\open" },
        @{ Category = "finding"; Path = Join-Path $runtimeRoot "findings\assigned" },
        @{ Category = "triage"; Path = Join-Path $runtimeRoot "triage\pending" },
        @{ Category = "triage"; Path = Join-Path $runtimeRoot "triage\completed" },
        @{ Category = "assignment"; Path = Join-Path $runtimeRoot "assignments\pending" },
        @{ Category = "assignment"; Path = Join-Path $runtimeRoot "assignments\active" },
        @{ Category = "assignment"; Path = Join-Path $runtimeRoot "assignments\reviewed" },
        @{ Category = "delivery"; Path = Join-Path $runtimeRoot "deliveries\pending_review" },
        @{ Category = "delivery"; Path = Join-Path $runtimeRoot "deliveries\reviewed" },
        @{ Category = "verdict"; Path = Join-Path $runtimeRoot "verdicts\pending_main" },
        @{ Category = "verdict"; Path = Join-Path $runtimeRoot "verdicts\applied" },
        @{ Category = "merge"; Path = Join-Path $runtimeRoot "merges\pending" },
        @{ Category = "merge"; Path = Join-Path $runtimeRoot "merges\completed" },
        @{ Category = "blocker"; Path = Join-Path $runtimeRoot "blockers\open" }
    )

    $candidates = @()
    foreach ($source in $sources) {
        if (-not (Test-Path $source.Path)) {
            continue
        }

        $files = @(Get-ChildItem $source.Path -Filter *.json -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 6)
        foreach ($file in $files) {
            $item = Read-JsonFile $file.FullName
            if (-not (Test-WorkflowObjectRelevant -Agent $Agent -Item $item)) {
                continue
            }
            $candidates += [PSCustomObject]@{
                Category = [string]$source.Category
                UpdatedAt = $file.LastWriteTime
                Line = Format-WorkflowObjectLine -Category ([string]$source.Category) -Item $item
            }
        }
    }

    return @($candidates | Sort-Object UpdatedAt -Descending | Select-Object -First $MaxItems | ForEach-Object { $_.Line })
}

function Show-Observer {
    Clear-Host
    $mailbox = Read-JsonFile (Join-Path $runtimeRoot ("mailboxes\{0}.json" -f $Agent))
    $heartbeat = Read-JsonFile (Join-Path $runtimeRoot ("heartbeats\{0}.json" -f $Agent))
    $ack = Read-JsonFile (Join-Path $runtimeRoot ("acks\{0}.json" -f $Agent))
    $executorState = Read-JsonFile (Join-Path $runtimeRoot ("executor-state\{0}.json" -f $Agent))
    $result = Read-JsonFile (Join-Path $runtimeRoot ("executions\{0}\result.json" -f $Agent))
    $launchState = Read-JsonFile (Join-Path $runtimeRoot ("launch-state\{0}.json" -f $Agent))
    $dispatchLines = Read-TextFile (Join-Path $runtimeRoot ("dispatch\{0}.md" -f $Agent)) 24
    $observeState = Read-JsonFile (Join-Path $runtimeRoot ("terminal-observe\{0}.json" -f $Agent))
    $auditState = Read-JsonFile (Join-Path $runtimeRoot ("audit-state\{0}.json" -f $Agent))
    $agentLog = Read-TextFile (Join-Path $runtimeRoot ("supervisor\logs\{0}-agent.log" -f $Agent)) 20
    $mailboxLog = Read-TextFile (Join-Path $runtimeRoot ("supervisor\logs\{0}-mailbox-agent.log" -f $Agent)) 12
    $assistantMessage = Get-RecentAssistantMessage -Agent $Agent
    $executionLog = Get-ExecutionLogTail -Agent $Agent
    $recentObjects = Get-RecentWorkflowObjects -Agent $Agent

    Write-Host ("# OwlClaw Observer: {0}" -f $Agent)
    Write-Host ("role: {0}" -f (Get-AgentLabel -Agent $Agent))
    Write-Host ("updated: {0}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"))
    Write-Host ""

    if ($null -ne $launchState) {
        Write-Host ("launch         : {0} pid={1} note={2}" -f [string]$launchState.status, [string]$launchState.pid, [string]$launchState.note)
    }
    else {
        Write-Host "launch         : <missing>"
    }

    if ($null -ne $mailbox) {
        Write-Host ("mailbox.action : {0}" -f [string]$mailbox.action)
        Write-Host ("mailbox.stage  : {0}" -f [string]$mailbox.stage)
        Write-Host ("mailbox.object : {0}/{1}" -f [string]$mailbox.object_type, [string]$mailbox.object_id)
        Write-Host ("mailbox.summary: {0}" -f [string]$mailbox.summary)
    }
    else {
        Write-Host "mailbox.action : <missing>"
    }

    if ($null -ne $heartbeat) {
        Write-Host ("heartbeat      : {0}" -f [string]$heartbeat.polled_at)
    }
    else {
        Write-Host "heartbeat      : <missing>"
    }

    if ($null -ne $ack) {
        Write-Host ("ack            : {0} @ {1}" -f [string]$ack.status, [string]$ack.acked_at)
    }
    else {
        Write-Host "ack            : <missing>"
    }

    if ($null -ne $executorState) {
        Write-Host ("executor       : {0} action={1} updated={2}" -f [string]$executorState.status, [string]$executorState.action, [string]$executorState.updated_at)
    }
    else {
        Write-Host "executor       : <missing>"
    }

    if ($null -ne $result) {
        Write-Host ("result         : returncode={0} error={1} runner={2}" -f [string]$result.returncode, [string]$result.error_kind, [string]$result.runner)
        Write-Host ("last exec      : {0}" -f [string]$result.executed_at)
        Write-Host ("workdir        : {0}" -f [string]$result.workdir)
    }
    else {
        Write-Host "result         : <missing>"
    }

    if ($null -ne $observeState) {
        Write-Host ("observe        : transport={0} updated={1}" -f [string]$observeState.transport, [string]$observeState.updated_at)
    }

    if ($null -ne $auditState) {
        Write-Host ("audit          : status={0} updated={1}" -f [string]$auditState.status, [string]$auditState.updated_at)
    }

    Write-Host ""
    Write-Host "== Last Assistant Message =="
    if ($assistantMessage.Count -gt 0) {
        $assistantMessage | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "<no captured assistant message>"
    }

    Write-Host ""
    Write-Host "== Recent Workflow Objects =="
    if ($recentObjects.Count -gt 0) {
        $recentObjects | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "<no recent workflow objects>"
    }

    Write-Host ""
    Write-Host "== Dispatch =="
    if ($dispatchLines.Count -gt 0) {
        $dispatchLines | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "<no dispatch>"
    }

    Write-Host ""
    Write-Host "== Agent Log =="
    if ($agentLog.Count -gt 0) {
        $agentLog | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "<no agent log>"
    }

    Write-Host ""
    Write-Host "== Execution Log =="
    if ($executionLog.Count -gt 0) {
        $executionLog | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "<no execution log>"
    }

    if ($mailboxLog.Count -gt 0) {
        Write-Host ""
        Write-Host "== Mailbox Log =="
        $mailboxLog | ForEach-Object { Write-Host $_ }
    }

    Write-Host ""
    Write-Host "Press Ctrl+C to close this observer window."
}

while ($true) {
    Show-Observer
    Start-Sleep -Seconds ([Math]::Max(1, $IntervalSeconds))
}
