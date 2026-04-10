import { render, screen } from "@testing-library/react";
import { HealthIndicator } from "@/components/data/HealthIndicator";

describe("HealthIndicator", () => {
  it("renders system health checks", () => {
    render(
      <HealthIndicator
        status="healthy"
        checks={[
          { component: "Runtime", healthy: true, latency_ms: 12 },
          { component: "DB", healthy: false, message: "connection timeout" },
        ]}
      />
    );
    expect(screen.getByText("System Health")).toBeInTheDocument();
    expect(screen.getByText("Runtime")).toBeInTheDocument();
    expect(screen.getByText("DB")).toBeInTheDocument();
  });
});
