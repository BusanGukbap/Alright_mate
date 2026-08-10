import { describe, expect, it, vi } from "vitest";

import { createJournalApi } from "./client";
import type { PublicExpressionPage } from "./contracts";

describe("generated OpenAPI client facade", () => {
  it("sends the AND browse query through one credentialled same-origin client", async () => {
    const responsePage = {
      items: [],
      page: 2,
      pageSize: 12,
      total: 0,
    } satisfies PublicExpressionPage;
    const fetchMock = vi.fn(async (request: Request) => {
      void request;
      return new Response(JSON.stringify(responsePage), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const api = createJournalApi({
      baseUrl: "https://journal.example",
      fetch: fetchMock,
    });

    const result = await api.listPublicExpressions({
      q: "cuppa",
      tag: "tea",
      page: 2,
      pageSize: 12,
    });

    expect(result).toEqual({ status: 200, data: responsePage });
    const request = fetchMock.mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    if (!request) {
      throw new Error("Expected the OpenAPI client to issue a request");
    }
    const url = new URL(request.url);
    expect(url.origin).toBe("https://journal.example");
    expect(url.pathname).toBe("/api/v1/expressions");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      q: "cuppa",
      tag: "tea",
      page: "2",
      pageSize: "12",
    });
    expect(request.credentials).toBe("include");
  });

  it("returns plain RFC 9457 details without interpreting them as HTML", async () => {
    const problem = {
      type: "urn:alright-mate:problem:not-found",
      title: "Expression not found",
      status: 404,
      detail: "The expression <strong>was not found</strong>.",
      instance: "/api/v1/expressions/11111111-1111-4111-8111-111111111111",
    };
    const fetchMock = vi.fn(async (request: Request) => {
      void request;
      return new Response(JSON.stringify(problem), {
        status: 404,
        headers: { "Content-Type": "application/problem+json" },
      });
    });
    const api = createJournalApi({ baseUrl: "https://journal.example", fetch: fetchMock });

    const result = await api.getPublicExpression("11111111-1111-4111-8111-111111111111");

    expect(result).toEqual({ status: 404, problem });
  });

  it("converts a transport failure into a safe British English problem", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      void request;
      throw new TypeError("failed to fetch internal-host.example");
    });
    const api = createJournalApi({ baseUrl: "https://journal.example", fetch: fetchMock });

    const result = await api.getReviewProgress(30);

    expect(result).toEqual({
      status: 0,
      problem: {
        type: "urn:alright-mate:problem:network-unavailable",
        title: "Journal unavailable",
        status: 0,
        detail: "The journal could not be reached. Check your connection and try again.",
      },
    });
    expect(JSON.stringify(result)).not.toContain("internal-host.example");
  });
});
