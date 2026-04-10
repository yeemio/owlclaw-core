import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { act } from "react";
import { type ReactNode } from "react";
import { useConsoleWebSocket } from "@/hooks/useWebSocket";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() {}
}

function createWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useWebSocket contract mapping", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("handles overview/triggers/ledger message types", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = createWrapper(queryClient);

    renderHook(() => useConsoleWebSocket(), { wrapper });
    const ws = MockWebSocket.instances[0];
    expect(ws).toBeDefined();

    const overviewPayload = {
      total_cost_today: 2.1,
      total_executions_today: 9,
      success_rate_today: 0.9,
      active_agents: 2,
      health_checks: [],
      alerts: [],
    };

    act(() => {
      ws.onmessage?.({ data: JSON.stringify({ type: "overview", payload: overviewPayload }) });
      ws.onmessage?.({ data: JSON.stringify({ type: "ledger" }) });
      ws.onmessage?.({ data: JSON.stringify({ type: "triggers" }) });
    });

    expect(queryClient.getQueryData(["overview"])).toEqual(overviewPayload);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ledger"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["triggers"] });
  });
});
