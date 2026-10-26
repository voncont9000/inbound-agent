import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Accordion } from "./Accordion";

const items = [
  { question: "First question?", answer: "First answer." },
  { question: "Second question?", answer: "Second answer." },
  { question: "Third question?", answer: "Third answer." },
];

describe("Accordion", () => {
  it("exposes collapsed regions and allows only one item to be open", async () => {
    const user = userEvent.setup();
    render(<Accordion items={items} />);

    const first = screen.getByRole("button", { name: "First question?" });
    const second = screen.getByRole("button", { name: "Second question?" });
    const firstRegion = document.getElementById(first.getAttribute("aria-controls")!);
    const secondRegion = document.getElementById(second.getAttribute("aria-controls")!);

    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(second).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Third question?" })).toHaveAttribute("aria-expanded", "false");
    expect(firstRegion).toHaveAttribute("role", "region");
    expect(firstRegion).toHaveAttribute("aria-hidden", "true");

    await user.click(first);
    expect(first).toHaveAttribute("aria-expanded", "true");
    expect(firstRegion).toHaveAttribute("aria-hidden", "false");

    await user.click(first);
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(firstRegion).toHaveAttribute("aria-hidden", "true");

    await user.click(second);
    expect(second).toHaveAttribute("aria-expanded", "true");
    expect(secondRegion).toHaveAttribute("aria-hidden", "false");
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(firstRegion).toHaveAttribute("aria-hidden", "true");
  });

  it("moves focus to the next trigger with ArrowDown", async () => {
    const user = userEvent.setup();
    render(<Accordion items={items} />);

    const first = screen.getByRole("button", { name: "First question?" });
    const second = screen.getByRole("button", { name: "Second question?" });

    await user.click(first);
    await user.keyboard("{ArrowDown}");

    expect(second).toHaveFocus();
  });
});
