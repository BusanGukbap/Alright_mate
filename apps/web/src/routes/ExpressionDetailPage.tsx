import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import type { JournalApi, PublicExpression } from "../api/contracts";

interface ExpressionDetailPageProps {
  api?: JournalApi;
}

type DetailState =
  | { kind: "loading"; requestKey: string }
  | { kind: "success"; requestKey: string; expression: PublicExpression }
  | { kind: "not-found"; requestKey: string }
  | { kind: "error"; requestKey: string; message: string };

function safeDetail(detail?: string) {
  return detail?.trim().slice(0, 400) || "This journal note could not be loaded. Please try again.";
}

function publishedDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

export function ExpressionDetailPage({ api }: ExpressionDetailPageProps) {
  const { id = "" } = useParams();
  const [refreshKey, setRefreshKey] = useState(0);
  const requestKey = `${id}:${refreshKey}`;
  const [storedState, setState] = useState<DetailState>({ kind: "loading", requestKey });
  const state: DetailState =
    storedState.requestKey === requestKey ? storedState : { kind: "loading", requestKey };

  useEffect(() => {
    if (!api) {
      return;
    }

    let active = true;
    void api
      .getPublicExpression(id)
      .then((result) => {
        if (!active) {
          return;
        }
        if (result.data) {
          setState({ kind: "success", requestKey, expression: result.data });
          return;
        }
        if (result.status === 404) {
          setState({ kind: "not-found", requestKey });
          return;
        }
        setState({ kind: "error", requestKey, message: safeDetail(result.problem.detail) });
      })
      .catch(() => {
        if (active) {
          setState({ kind: "error", requestKey, message: safeDetail() });
        }
      });

    return () => {
      active = false;
    };
  }, [api, id, requestKey]);

  if (state.kind === "loading") {
    return (
      <p className="status-note" role="status" aria-live="polite">
        Opening the journal note…
      </p>
    );
  }

  if (state.kind === "not-found") {
    return (
      <section className="notice not-found">
        <p className="eyebrow">Nothing filed here</p>
        <h1>That expression isn’t in the public journal.</h1>
        <p>It may be a draft, archived, or simply the wrong address.</p>
        <Link to="/expressions">Return to the expression index</Link>
      </section>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="notice notice-error" role="alert">
        <h1>This note is out of reach.</h1>
        <p>{state.message}</p>
        <button type="button" onClick={() => setRefreshKey((value) => value + 1)}>
          Try this expression again
        </button>
      </div>
    );
  }

  const item = state.expression;
  return (
    <article className="detail-sheet">
      <Link className="back-link" to="/expressions">
        Back to the expression index
      </Link>
      <header>
        <p className="eyebrow">Journal note</p>
        <h1>{item.expression}</h1>
        <p className="meaning" lang="ko">
          {item.meaningKo}
        </p>
      </header>
      <ul className="tag-list" aria-label={`Tags for ${item.expression}`}>
        {item.tags.map((tag) => (
          <li key={tag}>{tag}</li>
        ))}
      </ul>
      {item.note ? (
        <section className="note-body" aria-labelledby="note-heading">
          <h2 id="note-heading">Usage note</h2>
          <p>{item.note}</p>
        </section>
      ) : null}
      <footer className="source-block">
        <p>
          <time dateTime={item.publishedAt}>Published {publishedDate(item.publishedAt)}</time>
        </p>
        <a href={item.sourceUrl} target="_blank" rel="noopener noreferrer" aria-label="Open cited source (new tab)">
          Open cited source ↗
        </a>
      </footer>
    </article>
  );
}
