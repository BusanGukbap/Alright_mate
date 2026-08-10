import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("application shell", () => {
  it("offers landmark-led keyboard navigation to every main journey", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Skip to journal content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse expressions" })).toHaveAttribute(
      "href",
      "/expressions",
    );
    expect(screen.getByRole("link", { name: "Owner journal" })).toHaveAttribute(
      "href",
      "/admin",
    );
    expect(screen.getByRole("link", { name: "Review practice" })).toHaveAttribute(
      "href",
      "/review",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(
      screen.getByRole("heading", { level: 1, name: "A British English journal, kept with care." }),
    ).toBeInTheDocument();
  });

  it("navigates between the public journal routes without a page reload", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("link", { name: "Browse expressions" }));

    expect(
      screen.getByRole("heading", { level: 1, name: "The expression index" }),
    ).toBeInTheDocument();
  });
});
