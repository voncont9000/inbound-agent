import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { APP_URL } from "../config";
import { HeroForm } from "./Hero";

describe("HeroForm", () => {
  it("navigates to signup with the entered website encoded", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<HeroForm onNavigate={onNavigate} />);

    await user.type(screen.getByRole("textbox", { name: "Your website" }), "https://my-shop.example/?q=1");
    await user.click(screen.getByRole("button", { name: "Get started" }));

    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith(
      `${APP_URL}/signup?website=${encodeURIComponent("https://my-shop.example/?q=1")}`,
    );
  });

  it("navigates to the plain signup URL when the website is empty", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<HeroForm onNavigate={onNavigate} />);

    await user.click(screen.getByRole("button", { name: "Get started" }));

    expect(onNavigate).toHaveBeenCalledWith(`${APP_URL}/signup`);
  });
});
