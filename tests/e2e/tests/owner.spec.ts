import { expect, test } from "./stack-fixture.js";

test("owner carries one returned draft through amend, publish and archive", async ({
  context,
  page,
}) => {
  const ownerListRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/admin/expressions") {
      ownerListRequests.push(request.url());
    }
  });

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Owner journal" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "A deliberate MVP boundary" })).toBeVisible();

  await page.getByRole("textbox", { name: "Expression", exact: true }).fill("A decent brew");
  await page.getByRole("textbox", { name: "Korean meaning" }).fill("괜찮은 차 한 잔");
  await page.getByRole("textbox", { name: "Public note" }).fill("A small test note.");
  await page.getByRole("textbox", { name: "Tags, separated by commas" }).fill("tea, casual");
  await page
    .getByRole("textbox", { name: "Source URL" })
    .fill("https://example.test/sources/decent-brew");
  await page.getByRole("button", { name: "Save new expression" }).click();

  await expect(page.getByRole("status")).toHaveText("Draft saved.");
  const workingCopy = page.getByRole("region", { name: "Current expression" });
  await expect(workingCopy.getByRole("heading", { name: "A decent brew" })).toBeVisible();
  await expect(workingCopy.getByText("draft", { exact: true })).toBeVisible();
  const idField = page.getByRole("textbox", { name: "Expression UUID (for amendments)" });
  await expect(idField).toHaveValue(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  const expressionId = await idField.inputValue();

  await page.getByRole("textbox", { name: "Expression", exact: true }).fill("A proper brew");
  await page.getByRole("textbox", { name: "Public note" }).fill("An amended test note.");
  await page.getByRole("button", { name: "Amend expression" }).click();
  await expect(page.getByRole("status")).toHaveText("Expression amended.");
  await expect(workingCopy.getByRole("heading", { name: "A proper brew" })).toBeVisible();

  await page.getByRole("button", { name: "Publish draft" }).click();
  await expect(page.getByRole("status")).toHaveText("Draft published.");
  await expect(workingCopy.getByText("published", { exact: true })).toBeVisible();

  const publicPage = await context.newPage();
  await publicPage.goto("/expressions");
  await publicPage
    .getByRole("searchbox", { name: "Search expression or Korean meaning" })
    .fill("proper brew");
  await publicPage.getByRole("button", { name: "Search journal" }).click();
  await expect(publicPage.getByRole("heading", { name: "A proper brew" })).toBeVisible();

  await page.getByRole("button", { name: "Archive expression" }).click();
  await expect(page.getByRole("status")).toHaveText("Expression archived.");
  await expect(workingCopy.getByText("archived", { exact: true })).toBeVisible();

  await publicPage.reload();
  await expect(
    publicPage.getByRole("heading", { name: "No published expressions match these filters." }),
  ).toBeVisible();
  await publicPage.goto(`/expressions/${expressionId}`);
  await expect(
    publicPage.getByRole("heading", { name: "That expression isn’t in the public journal." }),
  ).toBeVisible();
  expect(ownerListRequests).toEqual([]);
});
