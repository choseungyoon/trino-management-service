import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icon";
import { useApi } from "../useApi";

interface Card {
  key: string;
  title: string;
  kind: string;
  release: string | null;
  blocked_by: string | null;
}

interface Column {
  status: string;
  label: string;
  meaning: string;
  cards: Card[];
}

interface Board {
  available: boolean;
  error: string | null;
  columns: Column[];
  summary: Record<string, number>;
}

export function Work() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const { data, error, reload } = useApi<Board>(
    `/work${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`);
  const [raising, setRaising] = useState(false);

  const columns = data?.columns ?? [];
  const kinds = Array.from(
    new Set(columns.flatMap((c) => c.cards.map((card) => card.kind)))).sort();
  const total = columns.reduce((n, c) => n + c.cards.length, 0);

  const setKind = (next: string) => {
    const copy = new URLSearchParams(params);
    if (next) copy.set("kind", next);
    else copy.delete("kind");
    setParams(copy, { replace: true });
  };

  return (
    <>
      <header className="topbar">
        <span className="topbar__title">Work Board</span>
        <span className="spacer" />
        <a className="btn btn--sm" href="/api/v1/work.md">
          <Icon name="audit" size={12} />
          WORK_BOARD.md
        </a>
      </header>

      <main className="content" id="main">
        {/* ⛔ Said once, at the top. Everything below is a status, and a status
            read without this invites someone to "fix" the board when it
            disagrees with a document — which is exactly backwards. */}
        <div className="banner" role="note">
          <Icon name="overview" size={15} />
          <div>
            <strong>This board owns status. The documents own the reasoning.</strong>{" "}
            Where a card disagrees with the document it points at,{" "}
            <strong>the document wins</strong>. The only thing you can raise here
            is a <span className="mono">request</span> — decisions and
            requirements start in their own documents.
          </div>
        </div>

        {error ? (
          <div className="banner banner--bad" role="alert">
            <Icon name="bad" />
            <div>{error.message}</div>
          </div>
        ) : null}

        {data && !data.available ? (
          <section className="panel">
            <div className="empty">
              <Icon name="bad" size={20} stroke={1.6} />
              <div className="empty__title">The board cannot be read</div>
              <div className="empty__desc">
                {data.error ?? "The TMS database did not answer."}
              </div>
            </div>
          </section>
        ) : null}

        {data?.available ? (
          <>
            <div className="filters">
              <button type="button" className={`chip${kind ? "" : " chip--on"}`}
                      aria-pressed={!kind} onClick={() => setKind("")}>
                All <span className="chip__n">{total}</span>
              </button>
              {kinds.map((name) => (
                <button key={name} type="button"
                        className={`chip${kind === name ? " chip--on" : ""}`}
                        aria-pressed={kind === name}
                        onClick={() => setKind(name)}>
                  {name}{" "}
                  <span className="chip__n">
                    {columns.reduce(
                      (n, c) => n + c.cards.filter((x) => x.kind === name).length, 0)}
                  </span>
                </button>
              ))}
            </div>

            <div className="board">
              {columns.map((column) => (
                <section className="board__col" key={column.status}
                         aria-labelledby={`col-${column.status}`}>
                  <div className="board__head">
                    <h2 className="board__title" id={`col-${column.status}`}>
                      <span className={`board__mark board__mark--${column.status}`}
                            aria-hidden="true" />
                      {column.label}
                      <span className="chip__n">{column.cards.length}</span>
                    </h2>
                    <p className="board__meaning">{column.meaning}</p>
                  </div>

                  {column.cards.length ? (
                    <ul className="board__list">
                      {column.cards.map((card) => (
                        <li className="wi" key={card.key}>
                          <Link className="wi__link" to={`/work/${card.key}`}>
                            <span className="wi__key mono">{card.key}</span>
                            <span className="wi__title">{card.title}</span>
                          </Link>
                          <div className="wi__meta">
                            <span className="tag">{card.kind}</span>
                            {card.release ? (
                              <span className="mono">{card.release}</span>
                            ) : null}
                          </div>
                          {card.blocked_by ? (
                            /* The blocker is the only reason this card is in
                               this column, so it belongs on the card rather
                               than one click away. */
                            <div className="wi__blocked">
                              <Icon name="lock" size={11} stroke={2} />
                              <span>{card.blocked_by}</span>
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="board__none">Nothing here</p>
                  )}
                </section>
              ))}
            </div>

            <section className="panel">
              <div className="panel__head">
                <div className="panel__title">Raise a request</div>
                <div className="panel__sub">
                  It gets the next <span className="mono">REQ-n</span> key
                  automatically
                </div>
              </div>
              <RaiseForm busy={raising} setBusy={setRaising} onDone={reload} />
            </section>
          </>
        ) : null}
      </main>
    </>
  );
}

function RaiseForm({ busy, setBusy, onDone }: {
  busy: boolean;
  setBusy: (value: boolean) => void;
  onDone: () => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/work", { title, body });
      setTitle("");
      setBody("");
      onDone();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="wi-form" onSubmit={submit}>
      {error ? (
        <div className="banner banner--bad" role="alert">
          <Icon name="bad" />
          <div>{error}</div>
        </div>
      ) : null}
      <label className="field">
        Title
        <input className="input" value={title} required maxLength={200}
               placeholder="What you need, in one line"
               onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field">
        <span>
          Details <span className="dim">(optional)</span>
        </span>
        <textarea className="input" rows={4} value={body}
                  placeholder="Why it is needed, and how you work around it today"
                  onChange={(e) => setBody(e.target.value)} />
      </label>
      <div className="row-actions">
        <button className="btn btn--primary" type="submit"
                disabled={busy || !title.trim()}>
          {busy ? "Raising…" : "Raise it"}
        </button>
      </div>
    </form>
  );
}
