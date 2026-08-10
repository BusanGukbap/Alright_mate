import { type FormEvent, useEffect, useRef, useState } from "react";

import type {
  AdminExpression,
  CreateExpressionBody,
  JournalApi,
  Problem,
  UpdateExpressionBody,
} from "../api/contracts";

interface AdminPageProps {
  api?: JournalApi;
}

interface EditorFields {
  id: string;
  expression: string;
  meaningKo: string;
  note: string;
  tags: string;
  sourceUrl: string;
}

type EditableField = Exclude<keyof EditorFields, "id">;
type ValidatedField = "expression" | "meaningKo" | "sourceUrl";
type FieldErrors = Partial<Record<ValidatedField, string>>;

type Feedback =
  | { kind: "success"; message: string }
  | {
      kind: "error";
      title: string;
      message: string;
      unauthorised?: boolean;
      sourceField?: boolean;
      focusAlert?: boolean;
    };

const emptyFields: EditorFields = {
  id: "",
  expression: "",
  meaningKo: "",
  note: "",
  tags: "",
  sourceUrl: "",
};

function normaliseTags(value: string) {
  return [...new Set(value.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean))];
}

function safeProblem(problem: Problem, status: number): Feedback {
  const title = problem.title?.trim().slice(0, 160) || "The owner request could not be completed";
  const message = problem.detail?.trim().slice(0, 500) || "Please check the entry and try again.";
  return {
    kind: "error",
    title,
    message,
    focusAlert: true,
    ...(status === 401 ? { unauthorised: true } : {}),
  };
}

function isValidatedField(name: keyof EditorFields): name is ValidatedField {
  return name === "expression" || name === "meaningKo" || name === "sourceUrl";
}

function fieldsFromExpression(item: AdminExpression): EditorFields {
  return {
    id: item.id,
    expression: item.expression,
    meaningKo: item.meaningKo,
    note: item.note ?? "",
    tags: item.tags.join(", "),
    sourceUrl: item.sourceUrl ?? "",
  };
}

export function AdminPage({ api }: AdminPageProps) {
  const [fields, setFields] = useState<EditorFields>(emptyFields);
  const [creationStatus, setCreationStatus] = useState<"draft" | "published">("draft");
  const [workingCopy, setWorkingCopy] = useState<AdminExpression | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirtyFields, setDirtyFields] = useState<ReadonlySet<EditableField>>(() => new Set());
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const alertRef = useRef<HTMLDivElement>(null);
  const expressionRef = useRef<HTMLInputElement>(null);
  const meaningRef = useRef<HTMLTextAreaElement>(null);
  const sourceUrlRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (feedback?.kind === "error" && feedback.focusAlert) {
      alertRef.current?.focus();
    }
  }, [feedback]);

  const setField = (name: keyof EditorFields, value: string) => {
    setFields((current) => ({ ...current, [name]: value }));
    if (name !== "id") {
      setDirtyFields((current) => new Set(current).add(name));
    }
    if (isValidatedField(name)) {
      setFieldErrors((current) => {
        if (!current[name]) {
          return current;
        }
        const next = { ...current };
        delete next[name];
        return next;
      });
    }
  };

  const retain = (item: AdminExpression) => {
    setWorkingCopy(item);
    setFields(fieldsFromExpression(item));
    setDirtyFields(new Set());
    setFieldErrors({});
  };

  const submitEditor = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!api) {
      setFeedback({
        kind: "error",
        title: "The owner service is unavailable",
        message: "Please reload the page and try again.",
      });
      return;
    }

    const id = fields.id.trim();
    const expression = fields.expression.trim();
    const meaningKo = fields.meaningKo.trim();
    const note = fields.note.trim();
    const sourceUrl = fields.sourceUrl.trim();
    const tags = normaliseTags(fields.tags);
    const missingPublicationSource =
      !id && creationStatus === "published" && !sourceUrl;

    const validationErrors: FieldErrors = {};
    if ((!id || dirtyFields.has("expression")) && !expression) {
      validationErrors.expression = "Enter an expression.";
    }
    if ((!id || dirtyFields.has("meaningKo")) && !meaningKo) {
      validationErrors.meaningKo = "Enter the Korean meaning.";
    }
    if (missingPublicationSource) {
      validationErrors.sourceUrl = "Enter a source URL before publishing.";
    } else if (sourceUrl && sourceUrlRef.current?.validity.typeMismatch) {
      validationErrors.sourceUrl = "Enter a valid absolute URL.";
    }

    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors);
      const sourceError = validationErrors.sourceUrl;
      setFeedback({
        kind: "error",
        title: missingPublicationSource
          ? "Add a publication source"
          : sourceError
            ? "Check the source URL"
            : "Complete the required fields",
        message:
          (missingPublicationSource
            ? "A source URL is required for direct publication."
            : sourceError) || "Expression and Korean meaning are required for a new entry.",
        ...(sourceError ? { sourceField: true } : {}),
      });
      if (validationErrors.expression) {
        expressionRef.current?.focus();
      } else if (validationErrors.meaningKo) {
        meaningRef.current?.focus();
      } else {
        sourceUrlRef.current?.focus();
      }
      return;
    }

    setFieldErrors({});
    setBusy(true);
    setFeedback(null);
    try {
      if (id) {
        const body: UpdateExpressionBody = {
          ...(dirtyFields.has("expression") && expression ? { expression } : {}),
          ...(dirtyFields.has("meaningKo") && meaningKo ? { meaningKo } : {}),
          ...(dirtyFields.has("note") ? { note: note || null } : {}),
          ...(dirtyFields.has("tags") ? { tags } : {}),
          ...(dirtyFields.has("sourceUrl") ? { sourceUrl: sourceUrl || null } : {}),
        };
        if (Object.keys(body).length === 0) {
          setFeedback({
            kind: "error",
            title: "Nothing to amend",
            message: "Enter at least one field alongside the expression UUID.",
          });
          return;
        }
        const result = await api.updateExpression(id, body);
        if (result.data) {
          retain(result.data);
          setFeedback({ kind: "success", message: "Expression amended." });
        } else {
          setFeedback(safeProblem(result.problem, result.status));
        }
        return;
      }

      const body: CreateExpressionBody =
        creationStatus === "published"
          ? {
              expression,
              meaningKo,
              ...(note ? { note } : {}),
              ...(tags.length ? { tags } : {}),
              sourceUrl,
              status: "published",
            }
          : {
              expression,
              meaningKo,
              ...(note ? { note } : {}),
              ...(tags.length ? { tags } : {}),
              ...(sourceUrl ? { sourceUrl } : {}),
              status: "draft",
            };
      const result = await api.createExpression(body);
      if (result.data) {
        retain(result.data);
        setFeedback({
          kind: "success",
          message: result.data.status === "draft" ? "Draft saved." : "Published expression created.",
        });
      } else {
        setFeedback(safeProblem(result.problem, result.status));
      }
    } catch {
      setFeedback({
        kind: "error",
        title: "The owner request could not be completed",
        message: "The journal could not be reached. Please try again.",
        focusAlert: true,
      });
    } finally {
      setBusy(false);
    }
  };

  const transition = async (status: "published" | "archived") => {
    if (!api || !workingCopy) {
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      const result = await api.updateExpression(workingCopy.id, { status });
      if (result.data) {
        retain(result.data);
        setFeedback({
          kind: "success",
          message: status === "published" ? "Draft published." : "Expression archived.",
        });
      } else {
        setFeedback(safeProblem(result.problem, result.status));
      }
    } catch {
      setFeedback({
        kind: "error",
        title: "The status could not be changed",
        message: "The journal could not be reached. Please try again.",
        focusAlert: true,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack admin-page">
      <header className="page-intro">
        <p className="eyebrow">Private desk</p>
        <h1>Owner journal</h1>
        <p>Create a reviewed note, amend one by UUID, then move it through the approved lifecycle.</p>
      </header>

      <aside className="constraint-note" aria-labelledby="mvp-constraint-heading">
        <h2 id="mvp-constraint-heading">A deliberate MVP boundary</h2>
        <p>
          The API does not provide an owner list or read view. Enter an expression UUID to amend it;
          newly created and amended representations remain here only until this page is reloaded.
        </p>
      </aside>

      {feedback?.kind === "error" ? (
        <div
          ref={alertRef}
          className="notice notice-error"
          role="alert"
          tabIndex={feedback.focusAlert ? -1 : undefined}
        >
          <h2>{feedback.title}</h2>
          <p>{feedback.message}</p>
          {feedback.sourceField ? <a href="#source-url">Check source URL</a> : null}
          {feedback.unauthorised ? (
            <a className="button-link" href="/oauth2/start?rd=%2Fadmin">
              Sign in with GitHub
            </a>
          ) : null}
        </div>
      ) : null}
      {feedback?.kind === "success" ? (
        <p className="notice notice-success" role="status" aria-live="polite">
          {feedback.message}
        </p>
      ) : null}

      <form onSubmit={(event) => void submitEditor(event)} noValidate>
        <fieldset
          className="editor-sheet"
          disabled={busy}
          style={{ minInlineSize: 0 }}
        >
          <legend>Expression editor</legend>
        <div className="field-group field-span">
          <label htmlFor="expression-id">Expression UUID (for amendments)</label>
          <input
            id="expression-id"
            value={fields.id}
            onChange={(event) => setField("id", event.target.value)}
            placeholder="Leave blank to create a new expression"
          />
          <p className="field-hint">There is no owner lookup; enter the UUID and only the fields you intend to amend.</p>
        </div>
        <div className="field-group">
          <label htmlFor="expression-text">Expression</label>
          <input
            ref={expressionRef}
            id="expression-text"
            value={fields.expression}
            required={!fields.id.trim()}
            aria-invalid={fieldErrors.expression ? true : undefined}
            aria-describedby={fieldErrors.expression ? "expression-text-error" : undefined}
            onChange={(event) => setField("expression", event.target.value)}
          />
          {fieldErrors.expression ? (
            <p className="field-hint field-error" id="expression-text-error">
              {fieldErrors.expression}
            </p>
          ) : null}
        </div>
        <div className="field-group">
          <label htmlFor="meaning-ko">Korean meaning</label>
          <textarea
            ref={meaningRef}
            id="meaning-ko"
            lang="ko"
            value={fields.meaningKo}
            required={!fields.id.trim()}
            aria-invalid={fieldErrors.meaningKo ? true : undefined}
            aria-describedby={fieldErrors.meaningKo ? "meaning-ko-error" : undefined}
            onChange={(event) => setField("meaningKo", event.target.value)}
          />
          {fieldErrors.meaningKo ? (
            <p className="field-hint field-error" id="meaning-ko-error">
              {fieldErrors.meaningKo}
            </p>
          ) : null}
        </div>
        <div className="field-group field-span">
          <label htmlFor="public-note">Public note</label>
          <textarea
            id="public-note"
            value={fields.note}
            onChange={(event) => setField("note", event.target.value)}
          />
        </div>
        <div className="field-group">
          <label htmlFor="expression-tags">Tags, separated by commas</label>
          <input
            id="expression-tags"
            value={fields.tags}
            onChange={(event) => setField("tags", event.target.value)}
          />
        </div>
        <div className="field-group">
          <label htmlFor="source-url">Source URL</label>
          <input
            ref={sourceUrlRef}
            id="source-url"
            type="url"
            value={fields.sourceUrl}
            required={!fields.id.trim() && creationStatus === "published"}
            aria-invalid={fieldErrors.sourceUrl ? true : undefined}
            aria-describedby={fieldErrors.sourceUrl ? "source-url-error" : undefined}
            onChange={(event) => setField("sourceUrl", event.target.value)}
          />
          {fieldErrors.sourceUrl ? (
            <p className="field-hint field-error" id="source-url-error">
              {fieldErrors.sourceUrl}
            </p>
          ) : null}
        </div>
        <fieldset className="field-span status-choice" disabled={Boolean(fields.id)}>
          <legend>Creation status</legend>
          <label>
            <input
              type="radio"
              name="creation-status"
              value="draft"
              checked={creationStatus === "draft"}
              onChange={() => setCreationStatus("draft")}
            />
            Save as draft
          </label>
          <label>
            <input
              type="radio"
              name="creation-status"
              value="published"
              checked={creationStatus === "published"}
              onChange={() => setCreationStatus("published")}
            />
            Publish directly
          </label>
        </fieldset>
        <div className="field-span editor-actions">
          <button type="submit" disabled={busy}>
            {fields.id.trim() ? "Amend expression" : "Save new expression"}
          </button>
          <button
            className="quiet-button"
            type="button"
            disabled={busy}
            onClick={() => {
              setFields(emptyFields);
              setWorkingCopy(null);
              setFeedback(null);
              setCreationStatus("draft");
              setDirtyFields(new Set());
              setFieldErrors({});
            }}
          >
            Start a fresh entry
          </button>
        </div>
        </fieldset>
      </form>

      {workingCopy ? (
        <section className="working-copy" aria-label="Current expression">
          <div>
            <p className="eyebrow">Working copy</p>
            <h2>{workingCopy.expression}</h2>
            <p>
              Status: <strong>{workingCopy.status}</strong>
            </p>
            <p className="identifier">{workingCopy.id}</p>
          </div>
          <div className="working-actions">
            {workingCopy.status === "draft" ? (
              <button type="button" disabled={busy} onClick={() => void transition("published")}>
                Publish draft
              </button>
            ) : null}
            {workingCopy.status === "published" ? (
              <button type="button" disabled={busy} onClick={() => void transition("archived")}>
                Archive expression
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
