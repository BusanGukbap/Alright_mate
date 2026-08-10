import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { ApiResult, PublicExpressionPage } from "../api/contracts";
import { createApiStub, deferred } from "../test/apiStub";

type ListItem = PublicExpressionPage["items"][number];

function expression(overrides: Partial<ListItem> = {}): ListItem {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    expression: "Fancy a cuppa?",
    meaningKo: "차 한잔할래요?",
    tags: ["invitation", "tea"],
    sourceUrl: "https://example.com/fancy-a-cuppa",
    publishedAt: "2026-08-08T08:30:00Z",
    ...overrides,
  };
}

function page(items: ListItem[], currentPage = 1, total = items.length): PublicExpressionPage {
  return { items, page: currentPage, pageSize: 12, total };
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="Current location">{`${location.pathname}${location.search}`}</output>;
}

function renderBrowse(api: ReturnType<typeof createApiStub>, entry = "/expressions") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <App api={api} />
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe("public expression browse route", () => {
  it("announces while published expressions are loading", () => {
    const pending = deferred<ApiResult<PublicExpressionPage>>();
    const api = createApiStub({ listPublicExpressions: () => pending.promise });

    renderBrowse(api);

    expect(within(screen.getByRole("main")).getByRole("status")).toHaveTextContent(
      "Looking through the journal…",
    );
  });

  it("loads URL search, exact tag and page state as one AND request", async () => {
    const listPublicExpressions = vi.fn().mockResolvedValue({
      status: 200,
      data: page(
        [
          expression(),
          expression({
            id: "22222222-2222-4222-8222-222222222222",
            expression: "Not my cup of tea",
            meaningKo: "내 취향이 아니에요",
            tags: ["idiom", "tea"],
            sourceUrl: "https://example.com/not-my-cup-of-tea",
          }),
        ],
        2,
        25,
      ),
    });
    const api = createApiStub({ listPublicExpressions });

    renderBrowse(api, "/expressions?q=tea&tag=idiom&page=2");

    expect(await screen.findByRole("heading", { level: 1, name: "The expression index" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search expression or Korean meaning" })).toHaveValue(
      "tea",
    );
    expect(screen.getByRole("combobox", { name: "Exact tag" })).toHaveValue("idiom");
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(listPublicExpressions).toHaveBeenCalledWith({
      q: "tea",
      tag: "idiom",
      page: 2,
      pageSize: 12,
    });
    const tagFilter = screen.getByRole("combobox", { name: "Exact tag" });
    expect(within(tagFilter).getByRole("option", { name: "tea" })).toBeInTheDocument();
    expect(within(tagFilter).getByRole("option", { name: "invitation" })).toBeInTheDocument();
  });

  it("synchronises search, tag, clearing and pagination with the URL", async () => {
    const user = userEvent.setup();
    const listPublicExpressions = vi.fn().mockResolvedValue({
      status: 200,
      data: page([expression()], 1, 25),
    });
    const api = createApiStub({ listPublicExpressions });
    renderBrowse(api);
    await screen.findByRole("heading", { name: "Fancy a cuppa?" });

    await user.type(
      screen.getByRole("searchbox", { name: "Search expression or Korean meaning" }),
      "cuppa",
    );
    await user.click(screen.getByRole("button", { name: "Search journal" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Exact tag" }), "tea");

    expect(listPublicExpressions).toHaveBeenLastCalledWith({
      q: "cuppa",
      tag: "tea",
      page: 1,
      pageSize: 12,
    });
    expect(screen.getByLabelText("Current location")).toHaveTextContent(
      "/expressions?q=cuppa&tag=tea",
    );

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(listPublicExpressions).toHaveBeenLastCalledWith({
      q: "cuppa",
      tag: "tea",
      page: 2,
      pageSize: 12,
    });
    expect(screen.getByLabelText("Current location")).toHaveTextContent(
      "/expressions?q=cuppa&tag=tea&page=2",
    );

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(listPublicExpressions).toHaveBeenLastCalledWith({ page: 1, pageSize: 12 });
    expect(screen.getByLabelText("Current location")).toHaveTextContent("/expressions");
  });

  it("offers clear recovery for empty and failed result sets", async () => {
    const user = userEvent.setup();
    const listPublicExpressions = vi
      .fn()
      .mockResolvedValueOnce({
        status: 503,
        problem: {
          type: "urn:alright-mate:problem:service-unavailable",
          title: "Browse unavailable",
          status: 503,
          detail: "Published expressions cannot be reached just now.",
        },
      })
      .mockResolvedValueOnce({ status: 200, data: page([]) });
    const api = createApiStub({ listPublicExpressions });
    renderBrowse(api, "/expressions?q=lost");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Published expressions cannot be reached just now.",
    );
    await user.click(screen.getByRole("button", { name: "Try this search again" }));

    expect(await screen.findByText("No published expressions match these filters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("never renders owner review state from a public result", async () => {
    const itemWithPrivateField = {
      ...expression(),
      reviewState: {
        intervalDays: 3,
        dueAt: "2026-08-09T08:30:00Z",
        lastReviewedAt: null,
        updatedAt: "2026-08-08T08:30:00Z",
      },
    } as ListItem;
    const api = createApiStub({
      listPublicExpressions: async () => ({ status: 200, data: page([itemWithPrivateField]) }),
    });

    renderBrowse(api);
    await screen.findByRole("heading", { name: "Fancy a cuppa?" });

    expect(screen.queryByText(/due at/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/interval/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/last reviewed/i)).not.toBeInTheDocument();
  });
});
