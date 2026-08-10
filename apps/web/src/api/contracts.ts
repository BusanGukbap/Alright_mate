import type { components, operations } from "./schema";

export type Problem = components["schemas"]["Problem"];
export type PublicExpression = components["schemas"]["PublicExpression"];
export type PublicExpressionPage = components["schemas"]["PublicExpressionPage"];
export type AdminExpression = components["schemas"]["AdminExpression"];
export type DueReviewPage = components["schemas"]["DueReviewPage"];
export type ReviewEventResult = components["schemas"]["ReviewEventResult"];
export type ReviewProgress = components["schemas"]["ReviewProgress"];

export type PublicExpressionQuery = NonNullable<
  operations["listPublicExpressions"]["parameters"]["query"]
>;
export type CreateExpressionBody =
  operations["createExpression"]["requestBody"]["content"]["application/json"];
export type UpdateExpressionBody =
  operations["updateExpression"]["requestBody"]["content"]["application/json"];
export type CreateReviewEventBody =
  operations["recordReviewEvent"]["requestBody"]["content"]["application/json"];

export type ApiResult<T> =
  | { data: T; status: number; problem?: never }
  | { data?: never; status: number; problem: Problem };

export interface JournalApi {
  listPublicExpressions(query: PublicExpressionQuery): Promise<ApiResult<PublicExpressionPage>>;
  getPublicExpression(id: string): Promise<ApiResult<PublicExpression>>;
  createExpression(body: CreateExpressionBody): Promise<ApiResult<AdminExpression>>;
  updateExpression(id: string, body: UpdateExpressionBody): Promise<ApiResult<AdminExpression>>;
  listDueReviews(limit: number): Promise<ApiResult<DueReviewPage>>;
  recordReviewEvent(body: CreateReviewEventBody): Promise<ApiResult<ReviewEventResult>>;
  getReviewProgress(days: number): Promise<ApiResult<ReviewProgress>>;
}
