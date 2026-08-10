import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import type { JournalApi, PublicExpressionPage } from "../api/contracts";

interface HomePageProps {
  api?: JournalApi;
}

type HomeState =
  | { kind: "idle" | "loading" }
  | { kind: "success"; page: PublicExpressionPage }
  | { kind: "error"; message: string };

function problemMessage(detail?: string) {
  const safeText = detail?.trim().slice(0, 400);
  return safeText || "The journal could not be loaded. Please try again.";
}

export function HomePage({ api }: HomePageProps) {
  const [state, setState] = useState<HomeState>(api ? { kind: "loading" } : { kind: "idle" });

  const loadNewest = useCallback(async () => {
    if (!api) {
      return;
    }

    try {
      const result = await api.listPublicExpressions({ page: 1, pageSize: 6 });
      if (result.data) {
        setState({ kind: "success", page: result.data });
        return;
      }
      setState({ kind: "error", message: problemMessage(result.problem.detail) });
    } catch {
      setState({ kind: "error", message: problemMessage() });
    }
  }, [api]);

  useEffect(() => {
    if (!api) {
      return;
    }

    let active = true;
    void api
      .listPublicExpressions({ page: 1, pageSize: 6 })
      .then((result) => {
        if (!active) {
          return;
        }
        if (result.data) {
          setState({ kind: "success", page: result.data });
          return;
        }
        setState({ kind: "error", message: problemMessage(result.problem.detail) });
      })
      .catch(() => {
        if (active) {
          setState({ kind: "error", message: problemMessage() });
        }
      });

    return () => {
      active = false;
    };
  }, [api]);

  return (
    <>
      <section className="hero" aria-labelledby="home-title">
        <p className="eyebrow">Notes from everyday Britain</p>
        <h1 id="home-title">A British English journal, kept with care.</h1>
        <p className="lede">
          Every published example is checked against its cited source, paired with a Korean
          meaning, and saved for thoughtful practice.
        </p>
        <div className="hero-actions">
          <Link className="button-link" to="/expressions">
            Browse the full journal
          </Link>
          <Link className="text-link" to="/review">
            Start a review
          </Link>
        </div>
      </section>

      <section className="journal-section" aria-labelledby="fresh-heading">
        <div className="section-heading">
          <p className="eyebrow">Recently filed</p>
          <h2 id="fresh-heading">Fresh from the journal</h2>
        </div>

        {state.kind === "loading" ? (
          <p className="status-note" role="status" aria-live="polite">
            Gathering the newest expressions…
          </p>
        ) : null}

        {state.kind === "error" ? (
          <div className="notice notice-error" role="alert">
            <h3>We couldn’t open the journal.</h3>
            <p>{state.message}</p>
            <button
              type="button"
              onClick={() => {
                setState({ kind: "loading" });
                void loadNewest();
              }}
            >
              Try again
            </button>
          </div>
        ) : null}

        {state.kind === "success" && state.page.items.length === 0 ? (
          <div className="notice">
            <p>The journal is ready for its first published expression.</p>
            <Link to="/admin">Open the owner journal</Link>
          </div>
        ) : null}

        {state.kind === "success" && state.page.items.length > 0 ? (
          <ol className="expression-grid">
            {state.page.items.map((item) => (
              <li key={item.id}>
                <article className="expression-card">
                  <p className="card-number" aria-hidden="true">
                    No.
                  </p>
                  <h3>{item.expression}</h3>
                  <p lang="ko">{item.meaningKo}</p>
                  <ul className="tag-list" aria-label={`Tags for ${item.expression}`}>
                    {item.tags.map((tag) => (
                      <li key={tag}>{tag}</li>
                    ))}
                  </ul>
                  <Link to={`/expressions/${item.id}`} aria-label={`Read ${item.expression}`}>
                    Open note
                  </Link>
                </article>
              </li>
            ))}
          </ol>
        ) : null}
      </section>
    </>
  );
}
