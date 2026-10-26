import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { UseCaseTabs } from "./UseCases";

describe("UseCaseTabs", () => {
  it("renders the five tabs with the first tab selected and labelled panel", () => {
    render(<UseCaseTabs />);

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(5);
    expect(tabs.map((tab) => tab.textContent?.trim())).toEqual([
      "Front desk",
      "Customer service",
      "Appointments",
      "Order intake",
      "After hours",
    ]);

    const firstTab = screen.getByRole("tab", { name: "Front desk" });
    const panel = screen.getByRole("tabpanel");
    expect(firstTab).toHaveAttribute("aria-selected", "true");
    expect(panel).toHaveAttribute("aria-labelledby", firstTab.id);
  });

  it("selects the clicked tab and displays its panel content", async () => {
    const user = userEvent.setup();
    render(<UseCaseTabs />);

    const appointmentsTab = screen.getByRole("tab", { name: "Appointments" });
    await user.click(appointmentsTab);

    expect(appointmentsTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", appointmentsTab.id);
    expect(screen.getByRole("heading", { name: "Bookings made during the conversation" })).toBeInTheDocument();
  });

  it("moves focus and selection with arrow keys, wrapping, Home, and End", async () => {
    const user = userEvent.setup();
    render(<UseCaseTabs />);

    const tabList = screen.getByRole("tablist");
    const tabs = within(tabList).getAllByRole("tab");
    const firstTab = screen.getByRole("tab", { name: "Front desk" });
    const secondTab = screen.getByRole("tab", { name: "Customer service" });
    const lastTab = screen.getByRole("tab", { name: "After hours" });

    firstTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(secondTab).toHaveFocus();
    expect(secondTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}");
    expect(firstTab).toHaveFocus();
    expect(firstTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}");
    expect(lastTab).toHaveFocus();
    expect(lastTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(firstTab).toHaveFocus();
    expect(firstTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(lastTab).toHaveFocus();
    expect(lastTab).toHaveAttribute("aria-selected", "true");

    expect(tabs.filter((tab) => tab.tabIndex === 0)).toEqual([lastTab]);
  });
});
