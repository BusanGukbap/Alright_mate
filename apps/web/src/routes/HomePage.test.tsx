import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { ApiResult, PublicExpressionPage } from "../api/contracts";
import { createApiStub, deferred } from "../test/apiStub";

describe("home route", () => {
  it("announces while the newest expressions are loading", () => {
    const pending = deferred<ApiResult<PublicExpressionPage>>();
    const api = createApiStub({
      listPublicExpressions: () => pending.promise,
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App api={api} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Gathering the newest expressions…");
  });

  it("introduces source-checked study and shows the newest published expressions", async () => {
    const listPublicExpressions = vi.fn().mockResolvedValue({
      status: 200,
      data: {
        items: [
          {
            id: "11111111-1111-4111-8111-111111111111",
            expression: "Fancy a cuppa?",
            meaningKo: "차 한잔할래요?",
            tags: ["tea", "invitation"],
            sourceUrl: "https://example.com/fancy-a-cuppa",
            publishedAt: "2026-08-08T08:30:00Z",
          },
          {
            id: "22222222-2222-4222-8222-222222222222",
            expression: "Not my cup of tea",
            meaningKo: "내 취향이 아니에요",
            tags: ["idiom"],
            sourceUrl: "https://example.com/not-my-cup-of-tea",
            publishedAt: "2026-08-07T08:30:00Z",
          },
        ],
        page: 1,
        pageSize: 6,
        total: 2,
      } satisfies PublicExpressionPage,
    });
    const api = createApiStub({ listPublicExpressions });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App api={api} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { level: 2, name: "Fresh from the journal" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Every published example is checked against its cited source/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Fancy a cuppa?" })).toBeInTheDocument();
    expect(screen.getByText("차 한잔할래요?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read Fancy a cuppa?" })).toHaveAttribute(
      "href",
      "/expressions/11111111-1111-4111-8111-111111111111",
    );
    expect(screen.getByRole("link", { name: "Browse the full journal" })).toHaveAttribute(
      "href",
      "/expressions",
    );
    expect(screen.getByRole("link", { name: "Start a review" })).toHaveAttribute(
      "href",
      "/review",
    );
    expect(listPublicExpressions).toHaveBeenCalledWith({ page: 1, pageSize: 6 });
  });

  it("offers a useful starting point when nothing has been published", async () => {
    const api = createApiStub({
      listPublicExpressions: async () => ({
        status: 200,
        data: { items: [], page: 1, pageSize: 6, total: 0 },
      }),
    });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App api={api} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("The journal is ready for its first published expression."),
    ).toBeInTheDocument();
  });

  it("recovers from a failed newest-expression request", async () => {
    const user = userEvent.setup();
    const listPublicExpressions = vi
      .fn()
      .mockResolvedValueOnce({
        status: 503,
        problem: {
          type: "urn:alright-mate:problem:service-unavailable",
          title: "Journal unavailable",
          status: 503,
          detail: "The journal cannot be reached just now.",
        },
      })
      .mockResolvedValueOnce({
        status: 200,
        data: {
          items: [
            {
              id: "33333333-3333-4333-8333-333333333333",
              expression: "Bob's your uncle",
              meaningKo: "이제 다 됐어요",
              tags: ["idiom"],
              sourceUrl: "https://example.com/bobs-your-uncle",
              publishedAt: "2026-08-06T08:30:00Z",
            },
          ],
          page: 1,
          pageSize: 6,
          total: 1,
        } satisfies PublicExpressionPage,
      });
    const api = createApiStub({ listPublicExpressions });

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App api={api} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("The journal cannot be reached just now.");
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Bob's your uncle" })).toBeInTheDocument();
    expect(listPublicExpressions).toHaveBeenCalledTimes(2);
  });
});
