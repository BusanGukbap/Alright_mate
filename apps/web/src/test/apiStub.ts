import type { JournalApi } from "../api/contracts";

const unexpectedCall = async (): Promise<never> => {
  throw new Error("Unexpected API call in test");
};

export function createApiStub(overrides: Partial<JournalApi> = {}): JournalApi {
  return {
    listPublicExpressions: unexpectedCall,
    getPublicExpression: unexpectedCall,
    createExpression: unexpectedCall,
    updateExpression: unexpectedCall,
    listDueReviews: unexpectedCall,
    recordReviewEvent: unexpectedCall,
    getReviewProgress: unexpectedCall,
    ...overrides,
  };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, resolve, reject };
}
