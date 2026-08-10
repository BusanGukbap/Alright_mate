import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { ApiResult, PublicExpression } from "../api/contracts";
import { createApiStub, deferred } from "../test/apiStub";

const expressionId = "11111111-1111-4111-8111-111111111111";

const detail = {
  id: expressionId,
  expression: "Fancy a cuppa?",
  meaningKo: "차 한잔할래요?",
  note: "A warm, informal way to offer somebody a cup of tea.",
  tags: ["invitation", "tea"],
  sourceUrl: "https://dictionary.cambridge.org/example",
  publishedAt: "2026-08-08T08:30:00Z",
} satisfies PublicExpression;

function renderDetail(api: ReturnType<typeof createApiStub>) {
  return render(
    <MemoryRouter initialEntries={[`/expressions/${expressionId}`]}>
      <App api={api} />
    </MemoryRouter>,
  );
}

describe("public expression detail route", () => {
  it("announces while the source-checked note is loading", () => {
    const pending = deferred<ApiResult<PublicExpression>>();
    const api = createApiStub({ getPublicExpression: () => pending.promise });

    renderDetail(api);

    expect(within(screen.getByRole("main")).getByRole("status")).toHaveTextContent(
      "Opening the journal note…",
    );
  });

  it("shows the public note, British date and a safe cited-source link", async () => {
    const getPublicExpression = vi.fn().mockResolvedValue({ status: 200, data: detail });
    const api = createApiStub({ getPublicExpression });

    renderDetail(api);

    expect(await screen.findByRole("heading", { level: 1, name: "Fancy a cuppa?" })).toBeInTheDocument();
    expect(screen.getByText("차 한잔할래요?")).toHaveAttribute("lang", "ko");
    expect(screen.getByText(detail.note)).toBeInTheDocument();
    expect(screen.getByText("Published 8 August 2026")).toBeInTheDocument();
    expect(screen.getByText("invitation")).toBeInTheDocument();
    const sourceLink = screen.getByRole("link", { name: "Open cited source (new tab)" });
    expect(sourceLink).toHaveAttribute("href", detail.sourceUrl);
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(sourceLink).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(getPublicExpression).toHaveBeenCalledWith(expressionId);
  });

  it("treats a missing public expression separately from a network failure", async () => {
    const api = createApiStub({
      getPublicExpression: async () => ({
        status: 404,
        problem: {
          type: "urn:alright-mate:problem:not-found",
          title: "Expression not found",
          status: 404,
          detail: "The expression was not found.",
        },
      }),
    });

    renderDetail(api);

    expect(
      await screen.findByRole("heading", {
        name: "That expression isn’t in the public journal.",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Return to the expression index" })).toHaveAttribute(
      "href",
      "/expressions",
    );
    expect(screen.queryByRole("button", { name: /try/i })).not.toBeInTheDocument();
  });

  it("retries a temporary detail failure", async () => {
    const user = userEvent.setup();
    const getPublicExpression = vi
      .fn()
      .mockResolvedValueOnce({
        status: 503,
        problem: {
          type: "urn:alright-mate:problem:service-unavailable",
          title: "Journal unavailable",
          status: 503,
          detail: "This note cannot be reached just now.",
        },
      })
      .mockResolvedValueOnce({ status: 200, data: detail });
    const api = createApiStub({ getPublicExpression });

    renderDetail(api);

    expect(await screen.findByRole("alert")).toHaveTextContent("This note cannot be reached just now.");
    await user.click(screen.getByRole("button", { name: "Try this expression again" }));

    expect(await screen.findByRole("heading", { name: "Fancy a cuppa?" })).toBeInTheDocument();
    expect(getPublicExpression).toHaveBeenCalledTimes(2);
  });
});
