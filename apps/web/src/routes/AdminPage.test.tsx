import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { AdminExpression, ApiResult } from "../api/contracts";
import { createApiStub, deferred } from "../test/apiStub";

const expressionId = "11111111-1111-4111-8111-111111111111";

const draft = {
  id: expressionId,
  expression: "Fancy a cuppa?",
  meaningKo: "차 한잔할래요?",
  note: null,
  tags: ["invitation", "tea"],
  sourceUrl: null,
  status: "draft",
  createdAt: "2026-08-08T08:30:00Z",
  updatedAt: "2026-08-08T08:30:00Z",
} satisfies AdminExpression;

const published = {
  ...draft,
  note: "A warm, informal invitation.",
  sourceUrl: "https://example.com/fancy-a-cuppa",
  status: "published",
  updatedAt: "2026-08-08T09:30:00Z",
  publishedAt: "2026-08-08T09:30:00Z",
  reviewState: {
    intervalDays: 1,
    dueAt: "2026-08-09T09:30:00Z",
    lastReviewedAt: null,
    updatedAt: "2026-08-08T09:30:00Z",
  },
} satisfies AdminExpression;

const archived = {
  ...published,
  status: "archived",
  updatedAt: "2026-08-08T10:30:00Z",
  archivedAt: "2026-08-08T10:30:00Z",
} satisfies AdminExpression;

const populatedDraft = {
  ...draft,
  note: "A warm, informal invitation.",
  sourceUrl: "https://example.com/fancy-a-cuppa",
} satisfies AdminExpression;

const clearedDraft = {
  ...populatedDraft,
  note: null,
  tags: [],
  sourceUrl: null,
  updatedAt: "2026-08-08T10:45:00Z",
} satisfies AdminExpression;

function renderAdmin(api: ReturnType<typeof createApiStub>) {
  return render(
    <MemoryRouter initialEntries={["/admin"]}>
      <App api={api} />
    </MemoryRouter>,
  );
}

async function enterRequiredCreateFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: "Expression" }), "Fancy a cuppa?");
  await user.type(screen.getByRole("textbox", { name: "Korean meaning" }), "차 한잔할래요?");
}

describe("owner journal route", () => {
  it("associates missing create fields with their client-side feedback", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn();
    const api = createApiStub({ createExpression });
    renderAdmin(api);

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Complete the required fields");
    const expression = screen.getByRole("textbox", { name: "Expression" });
    const meaning = screen.getByRole("textbox", { name: "Korean meaning" });
    expect(expression).toHaveAttribute("aria-invalid", "true");
    expect(expression).toHaveAccessibleDescription("Enter an expression.");
    expect(meaning).toHaveAttribute("aria-invalid", "true");
    expect(meaning).toHaveAccessibleDescription("Enter the Korean meaning.");
    expect(expression).toHaveFocus();
    expect(createExpression).not.toHaveBeenCalled();
  });

  it("offers gateway sign-in after an unauthorised owner response", async () => {
    const user = userEvent.setup();
    const api = createApiStub({
      createExpression: async () => ({
        status: 401,
        problem: {
          type: "urn:alright-mate:problem:unauthorised",
          title: "Owner sign-in required",
          status: 401,
          detail: "Owner authentication is required.",
        },
      }),
    });
    renderAdmin(api);
    await enterRequiredCreateFields(user);

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Owner authentication is required.");
    expect(screen.getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute(
      "href",
      "/oauth2/start?rd=%2Fadmin",
    );
  });

  it("creates a normalised draft and keeps its representation as the working copy", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn().mockResolvedValue({ status: 201, data: draft });
    const api = createApiStub({ createExpression });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    await user.type(screen.getByRole("textbox", { name: "Tags, separated by commas" }), "tea, invitation, tea");

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Draft saved.");
    expect(createExpression).toHaveBeenCalledWith({
      expression: "Fancy a cuppa?",
      meaningKo: "차 한잔할래요?",
      tags: ["tea", "invitation"],
      status: "draft",
    });
    expect(screen.getByRole("region", { name: "Current expression" })).toHaveTextContent(
      "Status: draft",
    );
    expect(screen.getByRole("textbox", { name: "Expression UUID (for amendments)" })).toHaveValue(
      expressionId,
    );
    expect(screen.getByRole("button", { name: "Publish draft" })).toBeInTheDocument();
  });

  it("locks editor inputs during a mutation and restores them after the response", async () => {
    const user = userEvent.setup();
    const pending = deferred<ApiResult<AdminExpression>>();
    const api = createApiStub({ createExpression: () => pending.promise });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    const expression = screen.getByRole("textbox", { name: "Expression" });
    const meaning = screen.getByRole("textbox", { name: "Korean meaning" });
    const sourceUrl = screen.getByRole("textbox", { name: "Source URL" });

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(expression).toBeDisabled();
    expect(meaning).toBeDisabled();
    expect(sourceUrl).toBeDisabled();
    await user.type(expression, " changed after submit");
    expect(expression).toHaveValue("Fancy a cuppa?");

    pending.resolve({ status: 201, data: draft });

    expect(await screen.findByRole("status")).toHaveTextContent("Draft saved.");
    expect(expression).toBeEnabled();
    expect(meaning).toBeEnabled();
    expect(sourceUrl).toBeEnabled();
  });

  it("requires a source before direct publication and then submits the generated union shape", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn().mockResolvedValue({ status: 201, data: published });
    const api = createApiStub({ createExpression });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    await user.click(screen.getByRole("radio", { name: "Publish directly" }));

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A source URL is required for direct publication.",
    );
    expect(screen.getByRole("link", { name: "Check source URL" })).toHaveAttribute(
      "href",
      "#source-url",
    );
    const sourceUrl = screen.getByRole("textbox", { name: "Source URL" });
    expect(sourceUrl).toHaveAttribute("aria-invalid", "true");
    expect(sourceUrl).toHaveAccessibleDescription("Enter a source URL before publishing.");
    expect(sourceUrl).toHaveFocus();
    expect(createExpression).not.toHaveBeenCalled();

    await user.type(sourceUrl, "https://example.com/fancy-a-cuppa");
    expect(sourceUrl).not.toHaveAttribute("aria-invalid", "true");
    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Published expression created.");
    expect(createExpression).toHaveBeenCalledWith({
      expression: "Fancy a cuppa?",
      meaningKo: "차 한잔할래요?",
      sourceUrl: "https://example.com/fancy-a-cuppa",
      status: "published",
    });
  });

  it("associates native URL validity with the source control", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn().mockResolvedValue({ status: 201, data: draft });
    const api = createApiStub({ createExpression });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    const sourceUrl = screen.getByRole("textbox", { name: "Source URL" });
    await user.type(sourceUrl, "not a URL");

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Check the source URL");
    expect(sourceUrl).toHaveAttribute("aria-invalid", "true");
    expect(sourceUrl).toHaveAccessibleDescription("Enter a valid absolute URL.");
    expect(sourceUrl).toHaveFocus();
    expect(createExpression).not.toHaveBeenCalled();
  });

  it("amends an entered expression UUID without inventing an owner read endpoint", async () => {
    const user = userEvent.setup();
    const updateExpression = vi.fn().mockResolvedValue({ status: 200, data: published });
    const api = createApiStub({ updateExpression });
    renderAdmin(api);

    await user.type(
      screen.getByRole("textbox", { name: "Expression UUID (for amendments)" }),
      expressionId,
    );
    await user.type(
      screen.getByRole("textbox", { name: "Public note" }),
      "A warm, informal invitation.",
    );
    await user.click(screen.getByRole("button", { name: "Amend expression" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Expression amended.");
    expect(updateExpression).toHaveBeenCalledWith(expressionId, {
      note: "A warm, informal invitation.",
    });
    expect(screen.getByText(/The API does not provide an owner list or read view/i)).toBeInTheDocument();
  });

  it("clears dirty optional values without patching untouched retained fields", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn().mockResolvedValue({ status: 201, data: populatedDraft });
    const updateExpression = vi.fn().mockResolvedValue({ status: 200, data: clearedDraft });
    const api = createApiStub({ createExpression, updateExpression });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    await user.type(
      screen.getByRole("textbox", { name: "Public note" }),
      "A warm, informal invitation.",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Tags, separated by commas" }),
      "invitation, tea",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Source URL" }),
      "https://example.com/fancy-a-cuppa",
    );
    await user.click(screen.getByRole("button", { name: "Save new expression" }));
    await screen.findByText("Draft saved.");

    await user.clear(screen.getByRole("textbox", { name: "Public note" }));
    await user.clear(screen.getByRole("textbox", { name: "Tags, separated by commas" }));
    await user.clear(screen.getByRole("textbox", { name: "Source URL" }));
    await user.click(screen.getByRole("button", { name: "Amend expression" }));

    expect(await screen.findByText("Expression amended.")).toBeInTheDocument();
    expect(updateExpression).toHaveBeenCalledWith(expressionId, {
      note: null,
      tags: [],
      sourceUrl: null,
    });
  });

  it("rejects clearing dirty required amendment fields with associated feedback", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn().mockResolvedValue({ status: 201, data: draft });
    const updateExpression = vi.fn();
    const api = createApiStub({ createExpression, updateExpression });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    await user.click(screen.getByRole("button", { name: "Save new expression" }));
    await screen.findByText("Draft saved.");
    const expression = screen.getByRole("textbox", { name: "Expression" });
    const meaning = screen.getByRole("textbox", { name: "Korean meaning" });

    await user.clear(expression);
    await user.click(screen.getByRole("button", { name: "Amend expression" }));

    expect(expression).toHaveAttribute("aria-invalid", "true");
    expect(expression).toHaveAccessibleDescription("Enter an expression.");
    expect(expression).toHaveFocus();
    expect(updateExpression).not.toHaveBeenCalled();

    await user.type(expression, "Fancy a cuppa?");
    await user.clear(meaning);
    await user.click(screen.getByRole("button", { name: "Amend expression" }));

    expect(meaning).toHaveAttribute("aria-invalid", "true");
    expect(meaning).toHaveAccessibleDescription("Enter the Korean meaning.");
    expect(meaning).toHaveFocus();
    expect(updateExpression).not.toHaveBeenCalled();
  });

  it("publishes the retained draft and then archives the retained publication", async () => {
    const user = userEvent.setup();
    const createExpression = vi.fn().mockResolvedValue({ status: 201, data: draft });
    const updateExpression = vi
      .fn()
      .mockResolvedValueOnce({ status: 200, data: published })
      .mockResolvedValueOnce({ status: 200, data: archived });
    const api = createApiStub({ createExpression, updateExpression });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    await user.click(screen.getByRole("button", { name: "Save new expression" }));
    await screen.findByRole("button", { name: "Publish draft" });

    await user.click(screen.getByRole("button", { name: "Publish draft" }));
    expect(await screen.findByRole("button", { name: "Archive expression" })).toBeInTheDocument();
    expect(updateExpression).toHaveBeenNthCalledWith(1, expressionId, { status: "published" });

    await user.click(screen.getByRole("button", { name: "Archive expression" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Expression archived.");
    expect(updateExpression).toHaveBeenNthCalledWith(2, expressionId, { status: "archived" });
    expect(screen.getByRole("region", { name: "Current expression" })).toHaveTextContent(
      "Status: archived",
    );
  });

  it("surfaces RFC 9457 business feedback as text in an accessible alert", async () => {
    const user = userEvent.setup();
    const api = createApiStub({
      createExpression: async () => ({
        status: 422,
        problem: {
          type: "urn:alright-mate:problem:invalid-source-url",
          title: "Source URL is invalid",
          status: 422,
          detail: "Use an absolute HTTP or HTTPS source URL without credentials.",
        },
      }),
    });
    renderAdmin(api);
    await enterRequiredCreateFields(user);
    await user.type(screen.getByRole("textbox", { name: "Source URL" }), "https://user@example.com");

    await user.click(screen.getByRole("button", { name: "Save new expression" }));

    const sourceUrl = screen.getByRole("textbox", { name: "Source URL" });
    const problemAlert = await screen.findByRole("alert");
    expect(problemAlert).toHaveTextContent("Source URL is invalid");
    expect(problemAlert).toHaveTextContent(
      "Use an absolute HTTP or HTTPS source URL without credentials.",
    );
    expect(problemAlert).toHaveAttribute("tabindex", "-1");
    expect(problemAlert).toHaveFocus();
    expect(sourceUrl).not.toHaveAttribute("aria-invalid", "true");
    expect(sourceUrl).not.toHaveAttribute("aria-describedby");
  });
});
