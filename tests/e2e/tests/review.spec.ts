import type { Response } from "@playwright/test";

import { expect, test } from "./stack-fixture.js";

function isReviewEventResponse(response: Response) {
  const url = new URL(response.url());
  return (
    response.request().method() === "POST" &&
    url.pathname === "/api/v1/admin/review-events" &&
    url.search === ""
  );
}

test("owner records Again and Good, sees progress, and gets a safe duplicate conflict", async ({
  context,
  page,
}, testInfo) => {
  if (process.env.E2E_RETRY_ISOLATION_PROBE === "1" && testInfo.retry === 0) {
    await page.goto("/review");
    await expect(page.getByRole("heading", { name: "Review this again" })).toBeVisible();
    const mutationResponsePromise = page.waitForResponse(isReviewEventResponse);
    await page.getByRole("button", { name: "Again" }).click();
    expect((await mutationResponsePromise).status()).toBe(201);
    throw new Error("Controlled retry-isolation probe after a committed review mutation.");
  }

  const todayInSeoul = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Asia/Seoul",
  }).format(new Date());
  await page.goto("/review");
  await expect(page.getByRole("heading", { name: "Practice one expression at a time" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review this again" })).toBeVisible();
  await expect(page.getByText("1 of 2 due", { exact: true })).toBeVisible();
  await expect(page.getByText("7 days", { exact: true })).toBeVisible();
  await expect(page.getByText(/^0 reviews from/)).toBeVisible();
  const progressTable = page.getByRole("table", { name: "Thirty-day review progress" });
  await expect(
    progressTable.getByRole("row", { name: `${todayInSeoul} 0 0 0`, exact: true }),
  ).toBeVisible();

  const stalePage = await context.newPage();
  await stalePage.goto("/review");
  await expect(stalePage.getByRole("heading", { name: "Review this again" })).toBeVisible();

  await page.getByRole("button", { name: "Again" }).click();
  await expect(page.getByRole("status")).toHaveText("Again recorded. The queue has advanced.");
  await expect(page.getByRole("heading", { name: "Review this as good" })).toBeVisible();
  await expect(page.getByText("1 of 1 due", { exact: true })).toBeVisible();
  await expect(page.getByText("3 days", { exact: true })).toBeVisible();
  await expect(page.getByText(/^1 review from/)).toBeVisible();
  await expect(
    progressTable.getByRole("row", { name: `${todayInSeoul} 1 0 1`, exact: true }),
  ).toBeVisible();

  const duplicateResponsePromise = stalePage.waitForResponse(isReviewEventResponse);
  await stalePage.getByRole("button", { name: "Good" }).click();
  const duplicateResponse = await duplicateResponsePromise;
  expect(duplicateResponse.status()).toBe(409);
  await expect(stalePage.getByRole("alert")).toContainText("Already reviewed today");
  await expect(stalePage.getByRole("heading", { name: "Review this again" })).toBeVisible();

  await page.getByRole("button", { name: "Good" }).click();
  await expect(page.getByRole("status")).toHaveText("Good recorded. The queue has advanced.");
  await expect(page.getByRole("heading", { name: "Nothing is due today." })).toBeVisible();
  await expect(page.getByText(/^2 reviews from/)).toBeVisible();
  await expect(
    progressTable.getByRole("row", { name: `${todayInSeoul} 1 1 2`, exact: true }),
  ).toBeVisible();
});
