import { expect, test } from "./stack-fixture.js";

const publishedId = "11111111-1111-4111-8111-111111111111";
const draftId = "33333333-3333-4333-8333-333333333333";
const archivedId = "44444444-4444-4444-8444-444444444444";
const queryOnlyExpression = "Cuppa on the go";
const tagOnlyExpression = "Tea before the train";

test("product routes remain usable without page overflow at 320 pixels", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/");

  await expect(page.getByRole("link", { name: "Alright, Mate? home" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse expressions" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Owner journal" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review practice" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(0);

  await page.getByRole("link", { name: "Browse expressions" }).click();
  await expect(page.getByRole("heading", { name: "The expression index" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Search journal" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(0);

  await page.getByRole("link", { name: "Owner journal" }).click();
  await expect(page.getByRole("heading", { name: "Owner journal" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(0);

  await page.getByRole("link", { name: "Review practice" }).click();
  await expect(page.getByRole("heading", { name: "Today’s review desk" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(0);
  const progressScroller = page.locator(".table-scroll");
  await expect(progressScroller).toBeVisible();
  expect(
    await progressScroller.evaluate((element) => element.scrollWidth > element.clientWidth),
  ).toBe(true);
});

test("public visitor browses the published CI fixture through the gateway", async ({
  context,
  page,
}) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "A British English journal, kept with care." }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Browse the full journal" }).click();

  await expect(page).toHaveURL(/\/expressions$/);
  await expect(page.getByRole("heading", { name: "The expression index" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Fancy a cuppa?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: queryOnlyExpression })).toBeVisible();
  await expect(page.getByRole("heading", { name: tagOnlyExpression })).toBeVisible();
  await expect(page.getByText("Private draft phrase", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Archived phrase", { exact: true })).toHaveCount(0);

  await page.getByRole("combobox", { name: "Exact tag" }).selectOption("tea");
  await page.getByRole("searchbox", { name: "Search expression or Korean meaning" }).fill("cuppa");
  await page.getByRole("button", { name: "Search journal" }).click();
  await expect(page).toHaveURL((url) => {
    return (
      url.pathname === "/expressions" &&
      url.searchParams.get("q") === "cuppa" &&
      url.searchParams.get("tag") === "tea"
    );
  });
  await expect(page.getByRole("heading", { name: "Fancy a cuppa?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: queryOnlyExpression })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: tagOnlyExpression })).toHaveCount(0);

  await page.getByRole("link", { name: "Read Fancy a cuppa?" }).click();
  await expect(page).toHaveURL(`/expressions/${publishedId}`);
  await expect(page.getByRole("heading", { name: "Fancy a cuppa?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Usage note" })).toBeVisible();
  await expect(page.getByText("차 한 잔 할래요?", { exact: true })).toBeVisible();

  const sourceUrl = "https://example.test/sources/fancy-a-cuppa";
  const sourceLink = page.getByRole("link", { name: "Open cited source (new tab)" });
  await expect(sourceLink).toHaveAttribute("href", sourceUrl);
  await expect(sourceLink).toHaveAttribute("target", "_blank");
  await expect(sourceLink).toHaveAttribute("rel", "noopener noreferrer");
  const sourceRequestPromise = context.waitForEvent("request", {
    predicate: (request) => request.url() === sourceUrl,
  });
  const sourcePagePromise = page.waitForEvent("popup");
  await sourceLink.click();
  const sourceRequest = await sourceRequestPromise;
  const sourcePage = await sourcePagePromise;
  expect(sourceRequest.url()).toBe(sourceUrl);
  await sourcePage.close();

  await page.goto(`/expressions/${draftId}`);
  await expect(
    page.getByRole("heading", { name: "That expression isn’t in the public journal." }),
  ).toBeVisible();

  await page.goto(`/expressions/${archivedId}`);
  await expect(
    page.getByRole("heading", { name: "That expression isn’t in the public journal." }),
  ).toBeVisible();
});
