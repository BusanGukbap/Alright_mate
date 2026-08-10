import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type {
  ApiResult,
  DueReviewPage,
  ReviewEventResult,
  ReviewProgress,
} from "../api/contracts";
import { createApiStub, deferred } from "../test/apiStub";

const firstId = "11111111-1111-4111-8111-111111111111";
const secondId = "22222222-2222-4222-8222-222222222222";

const firstDue: DueReviewPage["items"][number] = {
  expression: {
    id: firstId,
    expression: "Fancy a cuppa?",
    meaningKo: "차 한잔할래요?",
    note: "A warm, informal invitation.",
    tags: ["invitation", "tea"],
    sourceUrl: "https://example.com/fancy-a-cuppa",
    status: "published",
    publishedAt: "2026-08-01T08:30:00Z",
  },
  reviewState: {
    intervalDays: 1,
    dueAt: "2026-08-08T08:30:00Z",
    lastReviewedAt: null,
    updatedAt: "2026-08-01T08:30:00Z",
  },
};

const secondDue: DueReviewPage["items"][number] = {
  expression: {
    id: secondId,
    expression: "Bob's your uncle",
    meaningKo: "이제 다 됐어요",
    note: null,
    tags: ["idiom"],
    sourceUrl: "https://example.com/bobs-your-uncle",
    status: "published",
    publishedAt: "2026-08-02T08:30:00Z",
  },
  reviewState: {
    intervalDays: 3,
    dueAt: "2026-08-08T09:30:00Z",
    lastReviewedAt: "2026-08-05T09:30:00Z",
    updatedAt: "2026-08-05T09:30:00Z",
  },
};

function progressFixture(): ReviewProgress {
  const entries = Array.from({ length: 30 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 6, 10 + index)).toISOString().slice(0, 10);
    const good = index === 29 ? 1 : 0;
    return { date, total: good, again: 0, good };
  });
  return {
    days: 30,
    startDate: "2026-07-10",
    endDate: "2026-08-08",
    total: 1,
    entries,
  };
}

function eventResult(rating: "again" | "good"): ReviewEventResult {
  return {
    eventId: "33333333-3333-4333-8333-333333333333",
    expressionId: firstId,
    rating,
    reviewedAt: "2026-08-08T10:00:00Z",
    reviewDateSeoul: "2026-08-08",
    previousIntervalDays: 1,
    nextIntervalDays: rating === "again" ? 1 : 3,
    reviewState: {
      intervalDays: rating === "again" ? 1 : 3,
      dueAt: rating === "again" ? "2026-08-09T10:00:00Z" : "2026-08-11T10:00:00Z",
      lastReviewedAt: "2026-08-08T10:00:00Z",
      updatedAt: "2026-08-08T10:00:00Z",
    },
  };
}

function renderReview(api: ReturnType<typeof createApiStub>) {
  return render(
    <MemoryRouter initialEntries={["/review"]}>
      <App api={api} />
    </MemoryRouter>,
  );
}

describe("owner review route", () => {
  it("announces while the due queue and progress are loading", () => {
    const pendingDue = deferred<ApiResult<DueReviewPage>>();
    const pendingProgress = deferred<ApiResult<ReviewProgress>>();
    const api = createApiStub({
      listDueReviews: () => pendingDue.promise,
      getReviewProgress: () => pendingProgress.promise,
    });

    renderReview(api);

    expect(within(screen.getByRole("main")).getByRole("status")).toHaveTextContent(
      "Preparing today’s review…",
    );
  });

  it("shows an empty due state and a complete text-table summary including zero days", async () => {
    const listDueReviews = vi.fn().mockResolvedValue({ status: 200, data: { items: [] } });
    const getReviewProgress = vi.fn().mockResolvedValue({ status: 200, data: progressFixture() });
    const api = createApiStub({ listDueReviews, getReviewProgress });
    renderReview(api);

    expect(await screen.findByRole("heading", { name: "Nothing is due today." })).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Thirty-day review progress" });
    expect(within(table).getAllByRole("row")).toHaveLength(31);
    expect(within(table).getByRole("row", { name: "10 July 2026 0 0 0" })).toBeInTheDocument();
    expect(screen.getByText("1 review from 10 July to 8 August 2026.")).toBeInTheDocument();
    expect(listDueReviews).toHaveBeenCalledWith(20);
    expect(getReviewProgress).toHaveBeenCalledWith(30);
  });

  it("records Again and advances to the next due expression", async () => {
    const user = userEvent.setup();
    const getReviewProgress = vi.fn().mockResolvedValue({ status: 200, data: progressFixture() });
    const recordReviewEvent = vi.fn().mockResolvedValue({ status: 201, data: eventResult("again") });
    const api = createApiStub({
      listDueReviews: async () => ({ status: 200, data: { items: [firstDue, secondDue] } }),
      recordReviewEvent,
      getReviewProgress,
    });
    renderReview(api);
    await screen.findByRole("heading", { name: "Fancy a cuppa?" });

    await user.click(screen.getByRole("button", { name: "Again" }));

    expect(await screen.findByRole("heading", { name: "Bob's your uncle" })).toBeInTheDocument();
    expect(recordReviewEvent).toHaveBeenCalledWith({ expressionId: firstId, rating: "again" });
    expect(getReviewProgress).toHaveBeenCalledTimes(2);
  });

  it("records Good and leaves an empty queue after the successful event", async () => {
    const user = userEvent.setup();
    const api = createApiStub({
      listDueReviews: async () => ({ status: 200, data: { items: [firstDue] } }),
      recordReviewEvent: async () => ({ status: 201, data: eventResult("good") }),
      getReviewProgress: async () => ({ status: 200, data: progressFixture() }),
    });
    renderReview(api);
    await screen.findByRole("heading", { name: "Fancy a cuppa?" });

    await user.click(screen.getByRole("button", { name: "Good" }));

    expect(await screen.findByRole("heading", { name: "Nothing is due today." })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Good recorded. The queue has advanced.");
  });

  it("distinguishes a duplicate daily review and keeps the current card", async () => {
    const user = userEvent.setup();
    const api = createApiStub({
      listDueReviews: async () => ({ status: 200, data: { items: [firstDue] } }),
      recordReviewEvent: async () => ({
        status: 409,
        problem: {
          type: "urn:alright-mate:problem:review-already-recorded",
          title: "Review already recorded",
          status: 409,
          detail: "A review already exists for this expression today in Asia/Seoul.",
        },
      }),
      getReviewProgress: async () => ({ status: 200, data: progressFixture() }),
    });
    renderReview(api);
    await screen.findByRole("heading", { name: "Fancy a cuppa?" });

    await user.click(screen.getByRole("button", { name: "Good" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already reviewed today");
    expect(screen.getByRole("alert")).toHaveTextContent("Asia/Seoul");
    expect(screen.getByRole("heading", { name: "Fancy a cuppa?" })).toBeInTheDocument();
  });

  it("retries a failed due queue and reports progress failure independently", async () => {
    const user = userEvent.setup();
    const listDueReviews = vi
      .fn()
      .mockResolvedValueOnce({
        status: 503,
        problem: {
          type: "urn:alright-mate:problem:service-unavailable",
          title: "Review queue unavailable",
          status: 503,
          detail: "The due queue cannot be reached just now.",
        },
      })
      .mockResolvedValueOnce({ status: 200, data: { items: [] } });
    const api = createApiStub({
      listDueReviews,
      getReviewProgress: async () => ({
        status: 503,
        problem: {
          type: "urn:alright-mate:problem:service-unavailable",
          title: "Progress unavailable",
          status: 503,
          detail: "Thirty-day progress cannot be reached just now.",
        },
      }),
    });
    renderReview(api);

    expect(await screen.findByRole("alert", { name: "Review queue unavailable" })).toHaveTextContent(
      "The due queue cannot be reached just now.",
    );
    expect(screen.getByRole("alert", { name: "Progress unavailable" })).toHaveTextContent(
      "Thirty-day progress cannot be reached just now.",
    );
    await user.click(screen.getByRole("button", { name: "Try the due queue again" }));

    expect(await screen.findByRole("heading", { name: "Nothing is due today." })).toBeInTheDocument();
  });
});
