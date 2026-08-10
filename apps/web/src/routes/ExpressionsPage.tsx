import { type FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import type { JournalApi, PublicExpressionPage } from "../api/contracts";

const PAGE_SIZE = 12;

interface ExpressionsPageProps {
  api?: JournalApi;
}

type BrowseState =
  | { kind: "loading"; requestKey: string }
  | { kind: "success"; requestKey: string; page: PublicExpressionPage }
  | { kind: "error"; requestKey: string; message: string };

function readPage(value: string | null) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
}

function safeDetail(detail?: string) {
  return detail?.trim().slice(0, 400) || "The expression index could not be loaded. Please try again.";
}

function nextSearchParams(query: string, tag: string, page: number) {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  if (tag) {
    params.set("tag", tag);
  }
  if (page > 1) {
    params.set("page", String(page));
  }
  return params;
}

export function ExpressionsPage({ api }: ExpressionsPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q")?.trim() ?? "";
  const tag = searchParams.get("tag")?.trim() ?? "";
  const currentPage = readPage(searchParams.get("page"));
  const [searchDraft, setSearchDraft] = useState({ source: query, value: query });
  const [refreshKey, setRefreshKey] = useState(0);
  const requestKey = JSON.stringify([query, tag, currentPage, refreshKey]);
  const [storedState, setState] = useState<BrowseState>({ kind: "loading", requestKey });
  const state: BrowseState =
    storedState.requestKey === requestKey ? storedState : { kind: "loading", requestKey };
  const searchValue = searchDraft.source === query ? searchDraft.value : query;

  useEffect(() => {
    if (!api) {
      return;
    }

    let active = true;
    const request = {
      ...(query ? { q: query } : {}),
      ...(tag ? { tag } : {}),
      page: currentPage,
      pageSize: PAGE_SIZE,
    };

    void api
      .listPublicExpressions(request)
      .then((result) => {
        if (!active) {
          return;
        }
        if (result.data) {
          setState({ kind: "success", requestKey, page: result.data });
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
  }, [api, currentPage, query, requestKey, tag]);

  const availableTags = (() => {
    const values = new Set<string>();
    if (tag) {
      values.add(tag);
    }
    if (state.kind === "success") {
      for (const item of state.page.items) {
        for (const itemTag of item.tags) {
          values.add(itemTag);
        }
      }
    }
    return [...values].sort((left, right) => left.localeCompare(right, "en-GB"));
  })();

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSearchParams(nextSearchParams(searchValue.trim(), tag, 1));
  };

  const clearFilters = () => {
    setSearchDraft({ source: "", value: "" });
    setSearchParams(new URLSearchParams());
  };

  const totalPages =
    state.kind === "success" ? Math.max(1, Math.ceil(state.page.total / PAGE_SIZE)) : 1;

  return (
    <div className="page-stack">
      <header className="page-intro">
        <p className="eyebrow">Published notes</p>
        <h1>The expression index</h1>
        <p>Search the phrasing or Korean meaning, then narrow the current results by one exact tag.</p>
      </header>

      <form className="filter-panel" role="search" aria-label="Expression filters" onSubmit={submitSearch}>
        <div className="field-group field-grow">
          <label htmlFor="expression-search">Search expression or Korean meaning</label>
          <input
            id="expression-search"
            type="search"
            value={searchValue}
            onChange={(event) => setSearchDraft({ source: query, value: event.target.value })}
          />
        </div>
        <div className="field-group">
          <label htmlFor="tag-filter">Exact tag</label>
          <select
            id="tag-filter"
            value={tag}
            onChange={(event) => {
              setSearchParams(nextSearchParams(query, event.target.value, 1));
            }}
          >
            <option value="">All current tags</option>
            {availableTags.map((availableTag) => (
              <option key={availableTag} value={availableTag}>
                {availableTag}
              </option>
            ))}
          </select>
        </div>
        <div className="filter-actions">
          <button type="submit">Search journal</button>
          <button className="quiet-button" type="button" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      </form>

      <div aria-live="polite">
        {state.kind === "loading" ? (
          <p className="status-note" role="status">
            Looking through the journal…
          </p>
        ) : null}

        {state.kind === "error" ? (
          <div className="notice notice-error" role="alert">
            <h2>The index is out of reach.</h2>
            <p>{state.message}</p>
            <button type="button" onClick={() => setRefreshKey((value) => value + 1)}>
              Try this search again
            </button>
          </div>
        ) : null}

        {state.kind === "success" && state.page.items.length === 0 ? (
          <div className="notice">
            <h2>No published expressions match these filters.</h2>
            <p>Clear the filters or try a broader phrase.</p>
          </div>
        ) : null}

        {state.kind === "success" && state.page.items.length > 0 ? (
          <ol className="expression-list">
            {state.page.items.map((item) => (
              <li key={item.id}>
                <article className="expression-row">
                  <div>
                    <h2>{item.expression}</h2>
                    <p lang="ko">{item.meaningKo}</p>
                    <ul className="tag-list" aria-label={`Tags for ${item.expression}`}>
                      {item.tags.map((itemTag) => (
                        <li key={itemTag}>{itemTag}</li>
                      ))}
                    </ul>
                  </div>
                  <Link to={`/expressions/${item.id}`} aria-label={`Read ${item.expression}`}>
                    Read note
                  </Link>
                </article>
              </li>
            ))}
          </ol>
        ) : null}
      </div>

      {state.kind === "success" && totalPages > 1 ? (
        <nav className="pagination" aria-label="Expression pages">
          <button
            type="button"
            disabled={currentPage <= 1}
            onClick={() => setSearchParams(nextSearchParams(query, tag, currentPage - 1))}
          >
            Previous page
          </button>
          <p>
            Page {currentPage} of {totalPages}
          </p>
          <button
            type="button"
            disabled={currentPage >= totalPages}
            onClick={() => setSearchParams(nextSearchParams(query, tag, currentPage + 1))}
          >
            Next page
          </button>
        </nav>
      ) : null}
    </div>
  );
}
