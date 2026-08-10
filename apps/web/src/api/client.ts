import createClient from "openapi-fetch";

import type {
  AdminExpression,
  ApiResult,
  DueReviewPage,
  JournalApi,
  Problem,
  PublicExpression,
  PublicExpressionPage,
  ReviewEventResult,
  ReviewProgress,
} from "./contracts";
import type { paths } from "./schema";

interface JournalApiOptions {
  baseUrl: string;
  fetch?: (input: Request) => Promise<Response>;
}

interface TransportResponse<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

const networkProblem: Problem = {
  type: "urn:alright-mate:problem:network-unavailable",
  title: "Journal unavailable",
  status: 0,
  detail: "The journal could not be reached. Check your connection and try again.",
};

function textField(value: unknown, limit: number) {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

function normaliseProblem(value: unknown, status: number): Problem {
  const input = typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  const type = textField(input.type, 240) || "urn:alright-mate:problem:request-failed";
  const title = textField(input.title, 160) || "Journal request failed";
  const detail = textField(input.detail, 500) || "Please check the request and try again.";
  const instance = textField(input.instance, 240);
  return {
    type,
    title,
    status: typeof input.status === "number" ? input.status : status,
    detail,
    ...(instance ? { instance } : {}),
  };
}

async function request<T>(operation: () => Promise<TransportResponse<T>>): Promise<ApiResult<T>> {
  try {
    const result = await operation();
    if (result.data !== undefined) {
      return { status: result.response.status, data: result.data };
    }
    return {
      status: result.response.status,
      problem: normaliseProblem(result.error, result.response.status),
    };
  } catch {
    return { status: 0, problem: networkProblem };
  }
}

export function createJournalApi(options: JournalApiOptions): JournalApi {
  const client = createClient<paths>({
    baseUrl: options.baseUrl,
    credentials: "include",
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });

  return {
    listPublicExpressions: (query) =>
      request<PublicExpressionPage>(() =>
        client.GET("/api/v1/expressions", { params: { query } }),
      ),
    getPublicExpression: (id) =>
      request<PublicExpression>(() =>
        client.GET("/api/v1/expressions/{id}", { params: { path: { id } } }),
      ),
    createExpression: (body) =>
      request<AdminExpression>(() => client.POST("/api/v1/admin/expressions", { body })),
    updateExpression: (id, body) =>
      request<AdminExpression>(() =>
        client.PATCH("/api/v1/admin/expressions/{id}", { params: { path: { id } }, body }),
      ),
    listDueReviews: (limit) =>
      request<DueReviewPage>(() =>
        client.GET("/api/v1/admin/reviews/due", { params: { query: { limit } } }),
      ),
    recordReviewEvent: (body) =>
      request<ReviewEventResult>(() => client.POST("/api/v1/admin/review-events", { body })),
    getReviewProgress: (days) =>
      request<ReviewProgress>(() =>
        client.GET("/api/v1/admin/progress", { params: { query: { days } } }),
      ),
  };
}

const sameOrigin = typeof window === "undefined" ? "" : window.location.origin;

export const journalApi = createJournalApi({ baseUrl: sameOrigin });
