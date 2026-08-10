import { Link, NavLink, Route, Routes } from "react-router";

import type { JournalApi } from "./api/contracts";
import { AdminPage } from "./routes/AdminPage";
import { ExpressionDetailPage } from "./routes/ExpressionDetailPage";
import { ExpressionsPage } from "./routes/ExpressionsPage";
import { HomePage } from "./routes/HomePage";
import { ReviewPage } from "./routes/ReviewPage";

interface AppProps {
  api?: JournalApi;
}

export function App({ api }: AppProps) {
  const apiProps: { api?: JournalApi } = api ? { api } : {};

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to journal content
      </a>
      <header className="site-header">
        <Link className="wordmark" to="/" aria-label="Alright, Mate? home">
          Alright, Mate?
        </Link>
        <nav aria-label="Primary">
          <NavLink to="/expressions">Browse expressions</NavLink>
          <NavLink to="/admin">Owner journal</NavLink>
          <NavLink to="/review">Review practice</NavLink>
        </nav>
      </header>
      <main id="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<HomePage {...apiProps} />} />
          <Route path="/expressions" element={<ExpressionsPage {...apiProps} />} />
          <Route path="/expressions/:id" element={<ExpressionDetailPage {...apiProps} />} />
          <Route path="/admin" element={<AdminPage {...apiProps} />} />
          <Route path="/review" element={<ReviewPage {...apiProps} />} />
        </Routes>
      </main>
    </>
  );
}
