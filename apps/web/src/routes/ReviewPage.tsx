import { useEffect, useState } from "react";

import type { DueReviewPage, JournalApi, Problem, ReviewProgress } from "../api/contracts";

interface ReviewPageProps {
  api?: JournalApi;
}

type LoadState<T> =
  | { kind: "loading" }
  | { kind: "success"; data: T }
  | { kind: "error"; title: string; message: string; unauthorised?: boolean };

type ReviewFeedback =
  | { kind: "success"; message: string }
  | { kind: "error"; title: string; message: string };

function problemText(problem: Problem, status: number) {
  return {
    title: problem.title?.trim().slice(0, 160) || "Review data is unavailable",
    message: problem.detail?.trim().slice(0, 500) || "Please try again shortly.",
    ...(status === 401 ? { unauthorised: true } : {}),
  };
}

function formatDate(value: string, includeYear = true) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    ...(includeYear ? { year: "numeric" } : {}),
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function ProgressTable({ progress }: { progress: ReviewProgress }) {
  const reviewWord = progress.total === 1 ? "review" : "reviews";
  return (
    <>
      <p>
        {progress.total} {reviewWord} from {formatDate(progress.startDate, false)} to{" "}
        {formatDate(progress.endDate)}.
      </p>
      <div className="table-scroll" tabIndex={0} aria-label="Scrollable progress table">
        <table aria-label="Thirty-day review progress">
          <caption>Thirty-day review progress</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Again</th>
              <th scope="col">Good</th>
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            {progress.entries.map((entry) => (
              <tr key={entry.date}>
                <th scope="row">{formatDate(entry.date)}</th>
                <td>{entry.again}</td>
                <td>{entry.good}</td>
                <td>{entry.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function ReviewPage({ api }: ReviewPageProps) {
  const [dueState, setDueState] = useState<LoadState<DueReviewPage>>({ kind: "loading" });
  const [progressState, setProgressState] = useState<LoadState<ReviewProgress>>({ kind: "loading" });
  const [dueRefresh, setDueRefresh] = useState(0);
  const [progressRefresh, setProgressRefresh] = useState(0);
  const [feedback, setFeedback] = useState<ReviewFeedback | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!api) {
      return;
    }
    let active = true;
    void api
      .listDueReviews(20)
      .then((result) => {
        if (!active) {
          return;
        }
        if (result.data) {
          setDueState({ kind: "success", data: result.data });
        } else {
          setDueState({ kind: "error", ...problemText(result.problem, result.status) });
        }
      })
      .catch(() => {
        if (active) {
          setDueState({
            kind: "error",
            title: "Review queue unavailable",
            message: "The due queue cannot be reached just now.",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [api, dueRefresh]);

  useEffect(() => {
    if (!api) {
      return;
    }
    let active = true;
    void api
      .getReviewProgress(30)
      .then((result) => {
        if (!active) {
          return;
        }
        if (result.data) {
          setProgressState({ kind: "success", data: result.data });
        } else {
          setProgressState({ kind: "error", ...problemText(result.problem, result.status) });
        }
      })
      .catch(() => {
        if (active) {
          setProgressState({
            kind: "error",
            title: "Progress unavailable",
            message: "Thirty-day progress cannot be reached just now.",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [api, progressRefresh]);

  const rate = async (rating: "again" | "good") => {
    if (!api || dueState.kind !== "success" || !dueState.data.items[0]) {
      return;
    }
    const current = dueState.data.items[0];
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await api.recordReviewEvent({ expressionId: current.expression.id, rating });
      if (result.data) {
        setDueState({ kind: "success", data: { items: dueState.data.items.slice(1) } });
        setFeedback({
          kind: "success",
          message: `${rating === "again" ? "Again" : "Good"} recorded. The queue has advanced.`,
        });
        setProgressState({ kind: "loading" });
        setProgressRefresh((value) => value + 1);
      } else if (
        result.status === 409 &&
        result.problem.type === "urn:alright-mate:problem:review-already-recorded"
      ) {
        setFeedback({
          kind: "error",
          title: "Already reviewed today",
          message:
            result.problem.detail?.trim().slice(0, 500) ||
            "A review is already recorded for this expression today in Asia/Seoul.",
        });
      } else {
        const problem = problemText(result.problem, result.status);
        setFeedback({ kind: "error", title: problem.title, message: problem.message });
      }
    } catch {
      setFeedback({
        kind: "error",
        title: "The review was not recorded",
        message: "The journal could not be reached. Please try again.",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const currentDue = dueState.kind === "success" ? dueState.data.items[0] : undefined;
  const fullyLoading = dueState.kind === "loading" && progressState.kind === "loading";

  return (
    <div className="page-stack review-page">
      <header className="page-intro">
        <p className="eyebrow">Spaced practice</p>
        <h1>Today’s review desk</h1>
        <p>Choose Again when the expression needs another look, or Good when it came back readily.</p>
      </header>

      {fullyLoading ? (
        <p className="status-note" role="status" aria-live="polite">
          Preparing today’s review…
        </p>
      ) : null}

      {feedback?.kind === "success" ? (
        <p className="notice notice-success" role="status" aria-live="polite">
          {feedback.message}
        </p>
      ) : null}
      {feedback?.kind === "error" ? (
        <div className="notice notice-error" role="alert">
          <h2>{feedback.title}</h2>
          <p>{feedback.message}</p>
        </div>
      ) : null}

      <section className="review-queue" aria-labelledby="queue-heading">
        <div className="section-heading">
          <p className="eyebrow">Due queue</p>
          <h2 id="queue-heading">Practice one expression at a time</h2>
        </div>

        {dueState.kind === "loading" && !fullyLoading ? <p>Refreshing the due queue…</p> : null}
        {dueState.kind === "error" ? (
          <div className="notice notice-error" role="alert" aria-label={dueState.title}>
            <h3>{dueState.title}</h3>
            <p>{dueState.message}</p>
            {dueState.unauthorised ? (
              <a className="button-link" href="/oauth2/start?rd=%2Freview">
                Sign in with GitHub
              </a>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setDueState({ kind: "loading" });
                  setDueRefresh((value) => value + 1);
                }}
              >
                Try the due queue again
              </button>
            )}
          </div>
        ) : null}
        {dueState.kind === "success" && !currentDue ? (
          <div className="notice empty-queue">
            <h3>Nothing is due today.</h3>
            <p>The next expression will appear here when its interval is complete.</p>
          </div>
        ) : null}
        {currentDue ? (
          <article className="review-card">
            <p className="queue-count">
              1 of {dueState.kind === "success" ? dueState.data.items.length : 1} due
            </p>
            <h3>{currentDue.expression.expression}</h3>
            <p className="meaning" lang="ko">
              {currentDue.expression.meaningKo}
            </p>
            {currentDue.expression.note ? <p>{currentDue.expression.note}</p> : null}
            <dl className="review-meta">
              <div>
                <dt>Current interval</dt>
                <dd>{currentDue.reviewState.intervalDays} days</dd>
              </div>
              <div>
                <dt>Due</dt>
                <dd>{formatDate(currentDue.reviewState.dueAt.slice(0, 10))}</dd>
              </div>
            </dl>
            <div className="rating-actions" aria-label="Record this review">
              <button type="button" disabled={submitting} onClick={() => void rate("again")}>
                Again
              </button>
              <button type="button" disabled={submitting} onClick={() => void rate("good")}>
                Good
              </button>
            </div>
          </article>
        ) : null}
      </section>

      <section className="progress-section" aria-labelledby="progress-heading">
        <div className="section-heading">
          <p className="eyebrow">Asia/Seoul dates</p>
          <h2 id="progress-heading">Thirty-day progress</h2>
        </div>
        {progressState.kind === "loading" && !fullyLoading ? <p>Refreshing progress…</p> : null}
        {progressState.kind === "error" ? (
          <div className="notice notice-error" role="alert" aria-label={progressState.title}>
            <h3>{progressState.title}</h3>
            <p>{progressState.message}</p>
            {progressState.unauthorised ? (
              <a className="button-link" href="/oauth2/start?rd=%2Freview">
                Sign in with GitHub
              </a>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setProgressState({ kind: "loading" });
                  setProgressRefresh((value) => value + 1);
                }}
              >
                Try progress again
              </button>
            )}
          </div>
        ) : null}
        {progressState.kind === "success" ? <ProgressTable progress={progressState.data} /> : null}
      </section>
    </div>
  );
}
