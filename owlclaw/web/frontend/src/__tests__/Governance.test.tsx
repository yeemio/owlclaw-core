import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GovernancePage } from "@/pages/Governance";

vi.mock("@/hooks/useApi", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useApi")>("@/hooks/useApi");
  return {
    ...actual,
    useGovernance: () => ({
      data: {
        budget_trend: [{ date: "2026-03-01", cost: 1.2 }],
        circuit_breakers: [{ name: "llm", state: "closed" }],
        visibility: [{ agent: "a1", capabilities: { cap1: true } }],
        migration_weight: 0.4,
        skills_quality_rank: [{ skill: "entry-monitor", score: 93 }],
      },
      isLoading: false,
      isError: false,
      error: null,
    }),
  };
});

describe("GovernancePage", () => {
  it("renders governance widgets", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <GovernancePage />
      </QueryClientProvider>
    );
    expect(screen.getByText("Governance")).toBeInTheDocument();
    expect(screen.getByText("Migration Weight")).toBeInTheDocument();
    expect(screen.getByText("Skills Quality Ranking")).toBeInTheDocument();
  });
});
