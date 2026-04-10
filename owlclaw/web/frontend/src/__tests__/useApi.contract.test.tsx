import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { apiFetch } from "@/api/client";
import { useGovernance, useLedger } from "@/hooks/useApi";

vi.mock("@/api/client", () => ({
  apiFetch: vi.fn(),
}));

function createWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useApi contract mapping", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("maps governance endpoint split and normalizes merged payload", async () => {
    const mockedFetch = vi.mocked(apiFetch);
    mockedFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/governance/budget")) {
        return {
          items: [{ period_start: "2026-03-01", total_cost: "1.5" }],
          migration_weight: 0.3,
          skills_quality_rank: [{ skill: "entry-monitor", score: 92 }],
        };
      }
      if (path === "/governance/circuit-breakers") {
        return { items: [{ capability_name: "llm-budget", state: "closed" }] };
      }
      if (path === "/governance/visibility-matrix") {
        return {
          items: [
            { agent_id: "agent-a", capability_name: "entry", visible: true },
            { agent_id: "agent-a", capability_name: "exit", visible: false },
            { agent_id: "agent-b", capability_name: "entry", visible: true },
          ],
        };
      }
      return {};
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useGovernance("week"), { wrapper: createWrapper(queryClient) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedFetch).toHaveBeenCalledWith("/governance/budget?granularity=week");
    expect(mockedFetch).toHaveBeenCalledWith("/governance/circuit-breakers");
    expect(mockedFetch).toHaveBeenCalledWith("/governance/visibility-matrix");
    expect(result.current.data?.budget_trend[0]).toEqual({ date: "2026-03-01", cost: 1.5 });
    expect(result.current.data?.circuit_breakers[0]).toEqual({ name: "llm-budget", state: "closed" });
    expect(result.current.data?.visibility).toContainEqual({
      agent: "agent-a",
      capabilities: { entry: true, exit: false },
    });
    expect(result.current.data?.visibility).toContainEqual({
      agent: "agent-b",
      capabilities: { entry: true },
    });
  });

  it("maps ledger query parameters and parses paginated items", async () => {
    const mockedFetch = vi.mocked(apiFetch);
    mockedFetch.mockResolvedValue({
      items: [
        {
          id: "r-1",
          created_at: "2026-03-01T00:00:00Z",
          agent_id: "agent-a",
          capability_name: "entry",
          status: "success",
          estimated_cost: "0.02",
          execution_time_ms: 123,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(
      () =>
        useLedger(
          {
            agent: "agent-a",
            capability: "entry",
            start_time: "2026-03-01",
            end_time: "2026-03-02",
          },
          20,
          0
        ),
      { wrapper: createWrapper(queryClient) }
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const calledPath = mockedFetch.mock.calls[0]?.[0] ?? "";
    expect(calledPath).toContain("/ledger?");
    expect(calledPath).toContain("agent_id=agent-a");
    expect(calledPath).toContain("capability_name=entry");
    expect(calledPath).toContain("start_date=2026-03-01");
    expect(calledPath).toContain("end_date=2026-03-02");
    expect(result.current.data?.items[0]).toMatchObject({
      id: "r-1",
      agent: "agent-a",
      capability: "entry",
      latency_ms: 123,
      cost_usd: 0.02,
    });
  });
});
