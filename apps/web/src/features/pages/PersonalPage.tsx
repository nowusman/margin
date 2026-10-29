import React, { useEffect, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { TextAreaField } from "../../components/forms";
import { exportClient } from "../../lib/apiExport";
import { knowledgeClient } from "../../lib/apiKnowledge";
import { personalClient } from "../../lib/apiPersonal";
import type { PersonalFeedEntry, PersonalNote, SavedItem } from "../../lib/apiTypes";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

export type PersonalPageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

export function PersonalPage({ route, onNavigate }: PersonalPageProps): React.JSX.Element {
  const enabled = route === "/me";
  const [draftTarget, setDraftTarget] = useState<PersonalFeedEntry | null>(null);
  const [noteBody, setNoteBody] = useState("");
  const [exportDomainId, setExportDomainId] = useState("");
  const [exportId, setExportId] = useState("");
  const command = useCommand();
  const savedCommand = useCommand();
  const threadCommand = useCommand();
  const exportCommand = useCommand();

  const feed = useLoad("personal-feed", personalClient.feed, { enabled });
  const notes = useLoad("personal-notes", personalClient.notes, { enabled });
  const saved = useLoad("personal-saved", personalClient.saved, { enabled });
  const threads = useLoad("personal-threads", personalClient.threads, { enabled });
  const domains = useLoad("personal-export-domains", () => knowledgeClient.listDomains(), {
    enabled,
  });
  const [exportState, setExportState] = useState("");
  const exportRequest = useLoad(
    `personal-export-${exportId || "none"}`,
    () => exportClient.read(exportId),
    {
      enabled: Boolean(exportId),
      refreshMs: exportState === "ready" || exportState === "failed" ? undefined : 2000,
    }
  );
  useEffect(() => {
    setExportState(exportRequest.data?.state ?? "");
  }, [exportRequest.data]);
  const exportBundle = exportRequest.data?.exportId === exportId ? exportRequest.data : null;

  const refreshAll = (): void => {
    [feed, notes, saved, threads].forEach((resource) => resource.refresh());
  };

  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Your space</p>
        <h2>What you have been reading</h2>
        <p className="subtle">
          This is yours. Nothing here is published to the corpus, and nothing here changes what
          Margin believes is true.
        </p>
        {feed.loading ? <LoadingState label="Loading your feed..." /> : null}
        <ErrorState error={feed.error} />
        {feed.data ? (
          <ul className="stack-list" data-testid="personal-feed">
            {feed.data.feed.length === 0 ? (
              <li className="subtle">Nothing yet. Open a record and it will appear here.</li>
            ) : null}
            {feed.data.feed.map((entry) => (
              <li key={`${entry.targetType}-${entry.targetId}`} data-testid="personal-feed-entry">
                <strong>{entry.targetLabel}</strong>
                <span className="subtle">
                  {entry.targetKind} · {entry.reason}
                </span>
                {entry.provenance.length > 0 ? (
                  <span className="subtle" data-testid="personal-provenance">
                    From {entry.provenance.join(", ")}
                  </span>
                ) : null}
                <div className="actions-row">
                  <button
                    type="button"
                    onClick={() => {
                      setDraftTarget(entry);
                      setNoteBody("");
                    }}
                  >
                    Write a note
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      command.runCommand("Save record", () =>
                        personalClient
                          .saveItem({
                            targetType: entry.targetType,
                            targetId: entry.targetId,
                          })
                          .then(() => refreshAll())
                      )
                    }
                  >
                    Save
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </article>

      <article className="card">
        <h3>Reading queue</h3>
        <p className="subtle">
          Verified claims in domains you can read that you have not opened yet.
        </p>
        {feed.data ? (
          <ul className="stack-list" data-testid="personal-queue">
            {feed.data.queue.length === 0 ? <li className="subtle">You are up to date.</li> : null}
            {feed.data.queue.map((entry) => (
              <li key={`${entry.targetType}-${entry.targetId}`} data-testid="personal-queue-entry">
                <strong>{entry.targetLabel}</strong>
                <span className="subtle">{entry.reason}</span>
                {entry.provenance.length > 0 ? (
                  <span className="subtle" data-testid="personal-provenance">
                    From {entry.provenance.join(", ")}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </article>

      <article className="card">
        <h3>Your notes</h3>
        {notes.loading ? <LoadingState label="Loading your notes..." /> : null}
        <ErrorState error={notes.error} />
        {notes.data && notes.data.length === 0 ? (
          <EmptyState label="You have not written any notes yet." />
        ) : null}
        <ul className="stack-list" data-testid="personal-notes">
          {(notes.data ?? []).map((note: PersonalNote) => (
            <li key={note.id} data-testid="personal-note">
              <strong>{note.targetLabel}</strong>
              <span className="subtle">{note.body}</span>
              {note.provenance.length > 0 ? (
                <span className="subtle" data-testid="personal-provenance">
                  From {note.provenance.join(", ")}
                </span>
              ) : null}
              <span className="subtle">Private · {note.visibility}</span>
              <button
                type="button"
                onClick={() =>
                  command.runCommand("Delete note", () =>
                    personalClient.deleteNote(note.id).then(() => refreshAll())
                  )
                }
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
        {draftTarget ? (
          <div className="stacked-form" data-testid="personal-note-editor">
            <TextAreaField
              label={`Your note on ${draftTarget.targetLabel}`}
              rows={4}
              value={noteBody}
              onChange={(noteBodyValue) => setNoteBody(noteBodyValue)}
              placeholder="What you want to remember about this record."
            />
            <div className="actions-row">
              <button
                type="button"
                onClick={() =>
                  command.runCommand("Save note", () =>
                    personalClient
                      .saveNote({
                        targetType: draftTarget.targetType,
                        targetId: draftTarget.targetId,
                        body: noteBody,
                      })
                      .then(() => {
                        setDraftTarget(null);
                        setNoteBody("");
                        refreshAll();
                      })
                  )
                }
              >
                Save note
              </button>
              <button type="button" onClick={() => setDraftTarget(null)}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {command.message ? <p className="subtle">{command.message}</p> : null}
      </article>

      <article className="card">
        <h3>Saved records</h3>
        {savedCommand.message ? <p className="subtle">{savedCommand.message}</p> : null}
        {saved.loading ? <LoadingState label="Loading saved records..." /> : null}
        <ul className="stack-list" data-testid="personal-saved">
          {(saved.data ?? []).length === 0 ? (
            <li className="subtle">You have not saved anything yet.</li>
          ) : null}
          {(saved.data ?? []).map((item: SavedItem) => (
            <li key={item.id} data-testid="personal-saved-item">
              <strong>{item.label || item.targetLabel}</strong>
              <span className="subtle">
                {item.targetKind}
                {item.tags.length > 0 ? ` · ${item.tags.join(", ")}` : ""}
              </span>
              {item.provenance.length > 0 ? (
                <span className="subtle" data-testid="personal-provenance">
                  From {item.provenance.join(", ")}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() =>
                  savedCommand.runCommand("Remove saved record", () =>
                    personalClient.deleteSaved(item.id).then(() => refreshAll())
                  )
                }
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </article>

      <article className="card">
        <h3>Your conversations</h3>
        <div className="actions-row">
          <button
            type="button"
            onClick={() =>
              threadCommand.runCommand("Start a conversation", () =>
                personalClient
                  .createThread("New conversation")
                  .then((thread) => onNavigate("/ask/:threadId", { threadId: thread.id }))
              )
            }
            data-testid="personal-thread-new"
          >
            Start a conversation
          </button>
        </div>
        {threadCommand.message ? <p className="subtle">{threadCommand.message}</p> : null}
        {threads.loading ? <LoadingState label="Loading conversations..." /> : null}
        <ul className="stack-list" data-testid="personal-threads">
          {(threads.data ?? []).length === 0 ? (
            <li className="subtle">You have not started a conversation yet.</li>
          ) : null}
          {(threads.data ?? []).map((thread) => (
            <li key={thread.id} data-testid="personal-thread">
              <strong>{thread.title}</strong>
              <button
                type="button"
                onClick={() => onNavigate("/ask/:threadId", { threadId: thread.id })}
              >
                Continue
              </button>
              <button
                type="button"
                onClick={() =>
                  threadCommand.runCommand("Delete conversation", () =>
                    personalClient.deleteThread(thread.id).then(() => refreshAll())
                  )
                }
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      </article>

      <article className="card">
        <h3>Export a domain</h3>
        <p className="subtle">
          Take a domain's canonical records, evidence references and governance history with you as
          a portable archive. The archive carries only what you are permitted to read, and excludes
          operational material by construction.
        </p>
        {domains.loading ? <LoadingState label="Loading domains..." /> : null}
        <ErrorState error={domains.error} />
        <label className="field">
          <span>Domain to export</span>
          <select
            value={exportDomainId}
            onChange={(event) => setExportDomainId(event.target.value)}
            disabled={(domains.data ?? []).length === 0}
          >
            <option value="">Choose a domain</option>
            {(domains.data ?? []).map((domain) => (
              <option key={domain.id} value={domain.id}>
                {domain.name}
              </option>
            ))}
          </select>
        </label>
        <div className="actions-row">
          <button
            type="button"
            disabled={!exportDomainId}
            onClick={() =>
              exportCommand.runCommand("Request an export", () =>
                exportClient.request(exportDomainId).then((bundle) => setExportId(bundle.exportId))
              )
            }
          >
            Request an export
          </button>
        </div>
        {exportCommand.message ? <p className="subtle">{exportCommand.message}</p> : null}
        {exportBundle ? (
          <div data-testid="personal-export">
            <p className="subtle">
              Export {exportBundle.exportId} is {exportBundle.state}
              {exportBundle.contentHash ? ` · ${exportBundle.contentHash}` : ""}.
            </p>
            {exportBundle.error ? <p className="subtle">{exportBundle.error}</p> : null}
            <ul className="stack-list" data-testid="personal-export-records">
              {Object.entries(exportBundle.manifest.record_kinds).map(([kind, count]) => (
                <li key={kind}>
                  {count} {kind}
                </li>
              ))}
            </ul>
            <p className="subtle">
              Manifest {exportBundle.manifest.format_version}
              {exportBundle.manifest.generated_at
                ? ` · generated ${exportBundle.manifest.generated_at}`
                : ""}
            </p>
            <p className="subtle" data-testid="personal-export-exclusions">
              Excluded by construction: {exportBundle.manifest.excluded_by_construction.join(", ")}
            </p>
            {exportBundle.state === "ready" ? (
              <button
                type="button"
                onClick={() =>
                  exportCommand.runCommand("Download the archive", () =>
                    exportClient.download(exportBundle.exportId).then((blob) => {
                      const url = URL.createObjectURL(blob);
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = `margin-export-${exportBundle.domainId}.zip`;
                      link.click();
                      URL.revokeObjectURL(url);
                    })
                  )
                }
                data-testid="personal-export-download"
              >
                Download the archive
              </button>
            ) : null}
          </div>
        ) : null}
      </article>
    </section>
  );
}
