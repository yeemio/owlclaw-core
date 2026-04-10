import { render, screen } from "@testing-library/react";
import { MetricCard } from "@/components/charts/MetricCard";

describe("MetricCard", () => {
  it("renders metric value and trend", () => {
    render(<MetricCard label="Success Rate" value="98.2%" trend={1.5} />);
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
    expect(screen.getByText("98.2%")).toBeInTheDocument();
    expect(screen.getByText("+1.5%")).toBeInTheDocument();
  });
});
