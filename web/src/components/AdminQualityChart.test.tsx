import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AdminOverview } from "../api/adminApi";
import { AdminQualityChart } from "./AdminQualityChart";

describe("admin quality chart", () => {
  it("renders one bar per day and describes totals accessibly", () => {
    const series: AdminOverview["by_day"] = [
      { date: "2026-05-01", conversations: 3, avg_overall: 4, needs_review: 1 },
      { date: "2026-05-02", conversations: 0, avg_overall: null, needs_review: 0 },
      { date: "2026-05-03", conversations: 2, avg_overall: 3, needs_review: 1 },
    ];
    render(<AdminQualityChart series={series} />);
    expect(screen.getByRole("img", { name: "Quality over time: 5 conversations, 2 need review, across 3 days" })).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-bar")).toHaveLength(3);
    expect(screen.getByText("Average score")).toBeInTheDocument();
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    expect(document.querySelector("path[d^='M']")).toBeInTheDocument();
  });

  it("uses the four-count minimum scale and stacks review volume above the remainder", () => {
    const series: AdminOverview["by_day"] = [
      { date: "2026-05-01", conversations: 4, avg_overall: 4, needs_review: 1 },
      { date: "2026-05-02", conversations: 1, avg_overall: 3, needs_review: 0 },
    ];
    render(<AdminQualityChart series={series} />);
    const chart = screen.getByRole("img", { name: /Quality over time/ });
    expect(chart).toHaveAttribute("viewBox", "0 0 720 240");
    const bars = chart.querySelectorAll<SVGGElement>('[data-testid="chart-bar"]');
    const [remainder, review] = bars[0].querySelectorAll("rect");
    expect(remainder).toHaveAttribute("fill", "var(--border-control)");
    expect(review).toHaveAttribute("fill", "var(--accent)");
    expect(Number(review.getAttribute("y"))).toBeLessThan(Number(remainder.getAttribute("y")));
    expect(Number(review.getAttribute("y")) + Number(review.getAttribute("height"))).toBeCloseTo(Number(remainder.getAttribute("y")));
    expect(Number(bars[1].querySelector("rect")?.getAttribute("height"))).toBeLessThan(60);
  });
});
