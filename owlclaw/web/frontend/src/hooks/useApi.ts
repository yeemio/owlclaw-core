import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";

export type HealthStatus = "healthy" | "degraded" | "unhealthy";

export type HealthCheck = {
  component: string;
  healthy: boolean;
  latency_ms?: number | null;
  message?: string | null;
};

export type OverviewAlert = {
  level: "info" | "warning" | "critical";
  message: string;
};

export type OverviewSnapshot = {
  total_cost_today: number;
  total_executions_today: number;
  success_rate_today: number;
  active_agents: number;
  health_checks: HealthCheck[];
  alerts: OverviewAlert[];
};

export type BudgetTrendPoint = {
  date: string;
  cost: number;
};

type BudgetTrendInput = {
  date?: string;
  cost?: number | string;
  period_start?: string;
  total_cost?: number | string;
};

export type CircuitBreakerState = {
  name: string;
  state: "open" | "closed" | "half_open";
};

export type VisibilityRow = {
  agent: string;
  capabilities: Record<string, boolean>;
};

export type SkillQualityItem = {
  skill: string;
  score: number;
};

export type GovernanceSnapshot = {
  budget_trend: BudgetTrendPoint[];
  circuit_breakers: CircuitBreakerState[];
  visibility: VisibilityRow[];
  migration_weight: number;
  skills_quality_rank: SkillQualityItem[];
};

export type LedgerStatus = "success" | "failed" | "running";

export type LedgerRecord = {
  id: string;
  timestamp: string;
  agent: string;
  capability: string;
  status: LedgerStatus;
  cost_usd: number;
  model: string;
  latency_ms: number;
  input: string;
  output: string;
  reasoning: string;
};

export type LedgerFilters = {
  agent?: string;
  capability?: string;
  status?: LedgerStatus | "";
  start_time?: string;
  end_time?: string;
  min_cost?: number;
  max_cost?: number;
  order_by?: "created_at_desc" | "created_at_asc" | "cost_desc" | "cost_asc" | "";
};

export type PaginatedLedger = {
  items: LedgerRecord[];
  total: number;
  limit: number;
  offset: number;
};

export type AgentStatus = "active" | "idle" | "error";

export type AgentSummary = {
  id: string;
  name: string;
  role: string;
  status: AgentStatus;
  identity_summary: string;
};

export type AgentRunRecord = {
  timestamp: string;
  capability: string;
  status: LedgerStatus;
  cost_usd: number;
};

export type AgentDetail = {
  id: string;
  name: string;
  role: string;
  status: AgentStatus;
  identity: Record<string, string>;
  memory: {
    short_term_count: number;
    long_term_count: number;
  };
  knowledge: {
    skills: string[];
    references_count: number;
  };
  recent_runs: AgentRunRecord[];
};

export type CapabilityCategory = "handler" | "skill" | "binding";

export type CapabilityItem = {
  name: string;
  category: CapabilityCategory;
  schema: Record<string, unknown>;
  stats: {
    executions: number;
    success_rate: number;
    avg_latency_ms: number;
  };
};

export type ScanCandidate = {
  module: string;
  function: string;
  signature: string;
};

export type MigrationItem = {
  capability: string;
  status: "migrated" | "pending" | "binding_ready";
};

export type CapabilitiesSnapshot = {
  capabilities: CapabilityItem[];
  scan_candidates: ScanCandidate[];
  migration_progress: MigrationItem[];
};

export type TriggerItem = {
  id: string;
  type: string;
  name: string;
  next_run: string;
  last_status: "success" | "failed" | "running";
};

export type TriggersSnapshot = {
  triggers: TriggerItem[];
};

export type SettingsSnapshot = {
  config: Record<string, unknown>;
  mcp_status: { connected_clients: number; server_up: boolean };
  db_status: { migration_version: string; healthy: boolean };
  version: { app_version: string; build_time: string; commit_hash: string; provenance: string };
  docs: { title: string; url: string }[];
};

type CircuitBreakerInput = Partial<CircuitBreakerState> & {
  capability_name?: string;
};

type VisibilityItemInput = Partial<VisibilityRow> & {
  agent_id?: string;
  capability_name?: string;
  visible?: boolean;
};

function toNumber(value: unknown): number {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function normalizeOverviewSnapshot(raw: unknown): OverviewSnapshot {
  const input = (raw as Partial<OverviewSnapshot>) ?? {};
  return {
    total_cost_today: toNumber(input.total_cost_today),
    total_executions_today: toNumber(input.total_executions_today),
    success_rate_today: toNumber(input.success_rate_today),
    active_agents: toNumber(input.active_agents),
    health_checks: Array.isArray(input.health_checks) ? input.health_checks : [],
    alerts: Array.isArray(input.alerts) ? input.alerts : [],
  };
}

function normalizeGovernanceSnapshot(raw: unknown): GovernanceSnapshot {
  const input = (raw as Partial<GovernanceSnapshot>) ?? {};
  const rawCircuitBreakers = Array.isArray(input.circuit_breakers) ? input.circuit_breakers : [];
  const rawVisibility = Array.isArray(input.visibility) ? input.visibility : [];
  const groupedVisibility = new Map<string, Record<string, boolean>>();
  const normalizedVisibilityRows: VisibilityRow[] = [];

  for (const item of rawVisibility) {
    const row = item as VisibilityItemInput;
    if (typeof row.capabilities === "object" && row.capabilities) {
      normalizedVisibilityRows.push({
        agent: String(row.agent ?? row.agent_id ?? "unknown"),
        capabilities: row.capabilities,
      });
      continue;
    }
    const agentId = String(row.agent_id ?? row.agent ?? "unknown");
    const capabilityName = String(row.capability_name ?? "");
    if (!capabilityName) {
      continue;
    }
    if (!groupedVisibility.has(agentId)) {
      groupedVisibility.set(agentId, {});
    }
    groupedVisibility.get(agentId)![capabilityName] = Boolean(row.visible);
  }

  for (const [agent, capabilities] of groupedVisibility.entries()) {
    normalizedVisibilityRows.push({ agent, capabilities });
  }

  return {
    budget_trend: Array.isArray(input.budget_trend)
      ? input.budget_trend.map((item) => ({
          date: String((item as BudgetTrendInput).date ?? (item as BudgetTrendInput).period_start ?? ""),
          cost: toNumber((item as BudgetTrendInput).cost ?? (item as BudgetTrendInput).total_cost),
        }))
      : [],
    circuit_breakers: rawCircuitBreakers.map((item) => ({
      name: String((item as CircuitBreakerInput).name ?? (item as CircuitBreakerInput).capability_name ?? "unknown"),
      state: ((item as CircuitBreakerInput).state ?? "closed") as CircuitBreakerState["state"],
    })),
    visibility: normalizedVisibilityRows,
    migration_weight: toNumber(input.migration_weight),
    skills_quality_rank: Array.isArray(input.skills_quality_rank)
      ? input.skills_quality_rank.map((item) => ({
          skill: String((item as SkillQualityItem).skill ?? "unknown"),
          score: toNumber((item as SkillQualityItem).score),
        }))
      : [],
  };
}

function normalizeLedgerRecord(raw: unknown): LedgerRecord {
  const item = (raw as Partial<LedgerRecord> & {
    created_at?: string;
    agent_id?: string;
    capability_name?: string;
    estimated_cost?: number | string;
    execution_time_ms?: number | string;
  }) ?? {};
  return {
    id: String(item.id ?? ""),
    timestamp: String(item.timestamp ?? item.created_at ?? ""),
    agent: String(item.agent ?? item.agent_id ?? "unknown"),
    capability: String(item.capability ?? item.capability_name ?? "unknown"),
    status: ((item.status ?? "success") as LedgerStatus),
    cost_usd: toNumber(item.cost_usd ?? item.estimated_cost),
    model: String(item.model ?? "unknown"),
    latency_ms: toNumber(item.latency_ms ?? item.execution_time_ms),
    input: String(item.input ?? ""),
    output: String(item.output ?? ""),
    reasoning: String(item.reasoning ?? ""),
  };
}

function normalizePaginatedLedger(raw: unknown, limit: number, offset: number): PaginatedLedger {
  const input = (raw as Partial<PaginatedLedger>) ?? {};
  const rawList =
    (raw as { items?: unknown[]; records?: unknown[] })?.items ??
    (raw as { items?: unknown[]; records?: unknown[] })?.records ??
    input.items ??
    [];
  return {
    items: Array.isArray(rawList) ? rawList.map((record) => normalizeLedgerRecord(record)) : [],
    total: toNumber(input.total),
    limit: toNumber(input.limit) || limit,
    offset: toNumber(input.offset) || offset,
  };
}

function normalizeAgentSummary(raw: unknown): AgentSummary {
  const item = (raw as Partial<AgentSummary>) ?? {};
  return {
    id: String(item.id ?? ""),
    name: String(item.name ?? "Unnamed Agent"),
    role: String(item.role ?? "agent"),
    status: (item.status ?? "idle") as AgentStatus,
    identity_summary: String(item.identity_summary ?? ""),
  };
}

function extractItems(raw: unknown): unknown[] {
  if (Array.isArray(raw)) {
    return raw;
  }
  if (typeof raw !== "object" || raw === null) {
    return [];
  }
  const input = raw as {
    items?: unknown[];
    records?: unknown[];
    capabilities?: unknown[];
    triggers?: unknown[];
  };
  if (Array.isArray(input.items)) {
    return input.items;
  }
  if (Array.isArray(input.records)) {
    return input.records;
  }
  if (Array.isArray(input.capabilities)) {
    return input.capabilities;
  }
  if (Array.isArray(input.triggers)) {
    return input.triggers;
  }
  return [];
}

function normalizeAgentDetail(raw: unknown): AgentDetail {
  const item = (raw as Partial<AgentDetail>) ?? {};
  const memory = item.memory ?? { short_term_count: 0, long_term_count: 0 };
  const knowledge = item.knowledge ?? { skills: [], references_count: 0 };

  return {
    id: String(item.id ?? ""),
    name: String(item.name ?? "Unnamed Agent"),
    role: String(item.role ?? "agent"),
    status: (item.status ?? "idle") as AgentStatus,
    identity:
      typeof item.identity === "object" && item.identity
        ? Object.fromEntries(Object.entries(item.identity).map(([key, value]) => [key, String(value)]))
        : {},
    memory: {
      short_term_count: toNumber(memory.short_term_count),
      long_term_count: toNumber(memory.long_term_count),
    },
    knowledge: {
      skills: Array.isArray(knowledge.skills) ? knowledge.skills.map((skill) => String(skill)) : [],
      references_count: toNumber(knowledge.references_count),
    },
    recent_runs: Array.isArray(item.recent_runs)
      ? item.recent_runs.map((run) => ({
          timestamp: String((run as AgentRunRecord).timestamp ?? ""),
          capability: String((run as AgentRunRecord).capability ?? "unknown"),
          status: ((run as AgentRunRecord).status ?? "success") as LedgerStatus,
          cost_usd: toNumber((run as AgentRunRecord).cost_usd),
        }))
      : [],
  };
}

function normalizeCapabilitiesSnapshot(raw: unknown): CapabilitiesSnapshot {
  const input = (raw as Partial<CapabilitiesSnapshot>) ?? {};
  return {
    capabilities: Array.isArray(input.capabilities)
      ? input.capabilities.map((item) => {
          const cap = (item as Partial<CapabilityItem>) ?? {};
          return {
            name: String(cap.name ?? "unknown"),
            category: (cap.category ?? "handler") as CapabilityCategory,
            schema: (cap.schema as Record<string, unknown>) ?? {},
            stats: {
              executions: toNumber(cap.stats?.executions),
              success_rate: toNumber(cap.stats?.success_rate),
              avg_latency_ms: toNumber(cap.stats?.avg_latency_ms),
            },
          };
        })
      : [],
    scan_candidates: Array.isArray(input.scan_candidates)
      ? input.scan_candidates.map((item) => ({
          module: String((item as ScanCandidate).module ?? ""),
          function: String((item as ScanCandidate).function ?? ""),
          signature: String((item as ScanCandidate).signature ?? ""),
        }))
      : [],
    migration_progress: Array.isArray(input.migration_progress)
      ? input.migration_progress.map((item) => ({
          capability: String((item as MigrationItem).capability ?? ""),
          status: ((item as MigrationItem).status ?? "pending") as MigrationItem["status"],
        }))
      : [],
  };
}

function normalizeTriggersSnapshot(raw: unknown): TriggersSnapshot {
  const input = (raw as Partial<TriggersSnapshot>) ?? {};
  return {
    triggers: Array.isArray(input.triggers)
      ? input.triggers.map((item) => ({
          id: String((item as TriggerItem).id ?? ""),
          type: String((item as TriggerItem).type ?? ""),
          name: String((item as TriggerItem).name ?? ""),
          next_run: String((item as TriggerItem).next_run ?? ""),
          last_status: ((item as TriggerItem).last_status ?? "success") as TriggerItem["last_status"],
        }))
      : [],
  };
}

function normalizeSettingsSnapshot(raw: unknown): SettingsSnapshot {
  const input = (raw as Partial<SettingsSnapshot>) ?? {};
  return {
    config: (input.config as Record<string, unknown>) ?? {},
    mcp_status: {
      connected_clients: toNumber(input.mcp_status?.connected_clients),
      server_up: Boolean(input.mcp_status?.server_up),
    },
    db_status: {
      migration_version: String(input.db_status?.migration_version ?? "unknown"),
      healthy: Boolean(input.db_status?.healthy),
    },
    version: {
      app_version: String(input.version?.app_version ?? "unknown"),
      build_time: String(input.version?.build_time ?? "unknown"),
      commit_hash: String(input.version?.commit_hash ?? "unknown"),
      provenance: String(input.version?.provenance ?? "unknown"),
    },
    docs: Array.isArray(input.docs)
      ? input.docs.map((item) => ({
          title: String((item as { title: string }).title ?? ""),
          url: String((item as { url: string }).url ?? ""),
        }))
      : [],
  };
}

function buildLedgerQuery(filters: LedgerFilters, limit: number, offset: number): string {
  const params = new URLSearchParams();
  if (filters.agent) {
    params.set("agent_id", filters.agent);
  }
  if (filters.capability) {
    params.set("capability_name", filters.capability);
  }
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.start_time) {
    params.set("start_date", filters.start_time);
  }
  if (filters.end_time) {
    params.set("end_date", filters.end_time);
  }
  if (typeof filters.min_cost === "number") {
    params.set("min_cost", String(filters.min_cost));
  }
  if (typeof filters.max_cost === "number") {
    params.set("max_cost", String(filters.max_cost));
  }
  if (filters.order_by) {
    params.set("order_by", filters.order_by);
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return params.toString();
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: async () => normalizeOverviewSnapshot(await apiFetch<unknown>("/overview")),
    refetchInterval: 30_000,
  });
}

export function useGovernance(granularity: "day" | "week" | "month" = "day") {
  return useQuery({
    queryKey: ["governance", granularity],
    queryFn: async () => {
      const [budgetRaw, circuitRaw, visibilityRaw] = await Promise.all([
        apiFetch<unknown>(`/governance/budget?granularity=${granularity}`),
        apiFetch<unknown>("/governance/circuit-breakers"),
        apiFetch<unknown>("/governance/visibility-matrix"),
      ]);
      const budgetObj = (budgetRaw as Record<string, unknown>) ?? {};
      return normalizeGovernanceSnapshot({
        budget_trend: extractItems(budgetRaw),
        circuit_breakers: extractItems(circuitRaw),
        visibility: extractItems(visibilityRaw),
        migration_weight: budgetObj.migration_weight,
        skills_quality_rank:
          budgetObj.skills_quality_rank ??
          budgetObj.skill_quality_rank ??
          budgetObj.skills_quality ??
          [],
      });
    },
    refetchInterval: 30_000,
  });
}

export function useLedger(filters: LedgerFilters, limit = 20, offset = 0) {
  return useQuery({
    queryKey: ["ledger", filters, limit, offset],
    queryFn: async () =>
      normalizePaginatedLedger(
        await apiFetch<unknown>(`/ledger?${buildLedgerQuery(filters, limit, offset)}`),
        limit,
        offset
      ),
    refetchInterval: 30_000,
  });
}

export function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: async () => extractItems(await apiFetch<unknown>("/agents")).map((item) => normalizeAgentSummary(item)),
    refetchInterval: 30_000,
  });
}

export function useAgentDetail(agentId: string | null) {
  return useQuery({
    queryKey: ["agent", agentId],
    queryFn: async () => normalizeAgentDetail(await apiFetch<unknown>(`/agents/${agentId}`)),
    enabled: Boolean(agentId),
    refetchInterval: 30_000,
  });
}

export function useCapabilities() {
  return useQuery({
    queryKey: ["capabilities"],
    queryFn: async () => {
      const raw = await apiFetch<unknown>("/capabilities");
      const obj = (raw as Record<string, unknown>) ?? {};
      return normalizeCapabilitiesSnapshot({
        capabilities: extractItems(raw),
        scan_candidates: obj.scan_candidates ?? obj.scan_results ?? [],
        migration_progress: obj.migration_progress ?? obj.migrations ?? [],
      });
    },
    refetchInterval: 30_000,
  });
}

export function useTriggers() {
  return useQuery({
    queryKey: ["triggers"],
    queryFn: async () => {
      const raw = await apiFetch<unknown>("/triggers");
      return normalizeTriggersSnapshot({ triggers: extractItems(raw) });
    },
    refetchInterval: 30_000,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: async () => normalizeSettingsSnapshot(await apiFetch<unknown>("/settings")),
    refetchInterval: 30_000,
  });
}
