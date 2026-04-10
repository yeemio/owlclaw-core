import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/system/Toast";
import { OverviewPage } from "@/pages/Overview";

vi.mock("@/hooks/useApi", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useApi")>("@/hooks/useApi");
  return {
    ...actual,
    useOverview: () => ({
      data: {
        total_cost_today: 12.5,
        total_executions_today: 42,
        success_rate_today: 0.9,
        active_agents: 3,
        health_checks: [{ component: "Runtime", healthy: true }],
        alerts: [{ level: "warning", message: "Rate limit at 80%" }],
      },
      isLoading: false,
      isError: false,
      error: null,
    }),
  };
});

vi.mock("@/hooks/useWebSocket", () => ({
  useConsoleWebSocket: () => undefined,
}));

describe("OverviewPage", () => {
  it("renders overview data from hook", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <OverviewPage />
        </ToastProvider>
      </QueryClientProvider>
    );
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Rate limit at 80%")).toBeInTheDocument();
    expect(screen.getByText("Active Agents")).toBeInTheDocument();
  });
});
