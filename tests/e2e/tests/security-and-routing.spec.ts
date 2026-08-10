import { expect, test } from "./stack-fixture.js";

const publishedId = "11111111-1111-4111-8111-111111111111";

test("public API needs no identity and forged owner headers cannot select an identity", async ({
  request,
}) => {
  const publicResponse = await request.get("/api/v1/expressions");
  expect(publicResponse.status()).toBe(200);
  const publicBody = (await publicResponse.json()) as { items: Array<Record<string, unknown>> };
  expect(publicBody.items.length).toBeGreaterThan(0);
  expect(publicBody.items[0]).not.toHaveProperty("reviewState");
  expect(publicBody.items[0]).not.toHaveProperty("status");

  const forgedResponse = await request.post("/api/v1/admin/expressions", {
    headers: {
      Authorization: "Bearer forged-token",
      Cookie: "_oauth2_proxy=forged-session",
      "X-Alright-Mate-Owner": "arbitrary-attacker",
      "X-Auth-Request-User": "arbitrary-attacker",
      "X-Forwarded-User": "arbitrary-attacker",
    },
    data: {
      expression: "Header boundary proof",
      meaningKo: "헤더 경계 증명",
      status: "draft",
    },
  });
  expect(forgedResponse.status()).toBe(201);
  const ownerBody = (await forgedResponse.json()) as { status: string };
  expect(ownerBody.status).toBe("draft");
});

test("gateway serves SPA deep links and reloads instead of Nginx 404 responses", async ({ page }) => {
  const detailResponse = await page.goto(`/expressions/${publishedId}`);
  expect(detailResponse?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Fancy a cuppa?" })).toBeVisible();
  const detailReload = await page.reload();
  expect(detailReload?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Fancy a cuppa?" })).toBeVisible();

  const adminResponse = await page.goto("/admin");
  expect(adminResponse?.status()).toBe(200);
  const adminReload = await page.reload();
  expect(adminReload?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Owner journal" })).toBeVisible();

  const reviewResponse = await page.goto("/review");
  expect(reviewResponse?.status()).toBe(200);
  const reviewReload = await page.reload();
  expect(reviewReload?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Practice one expression at a time" })).toBeVisible();
});
