import { apiClient } from "./api-client";
import { asArray, num, rec, str } from "./apiMappers";
import type {
  AssistantMessage,
  AssistantThread,
  AssistantThreadSummary,
  PersonalFeed,
  PersonalFeedEntry,
  PersonalNote,
  SavedItem,
} from "./apiTypes";

export type NoteInput = {
  targetType: string;
  targetId: string;
  body: string;
  domainId?: string;
  visibility?: string;
};

export type SavedInput = {
  targetType: string;
  targetId: string;
  label?: string;
  tags?: string[];
  domainId?: string;
};

const note = (value: unknown): PersonalNote => {
  const item = rec(value);
  return {
    id: str(item.id),
    targetType: str(item.target_type),
    targetId: str(item.target_id),
    targetLabel: str(item.target_label),
    targetKind: str(item.target_kind),
    provenance: asArray<string>(item.provenance).filter(Boolean),
    body: str(item.body),
    visibility: str(item.visibility, "private"),
    domainId: str(item.domain_id),
    createdAt: str(item.created_at),
    updatedAt: str(item.updated_at),
  };
};

const savedItem = (value: unknown): SavedItem => {
  const item = rec(value);
  return {
    id: str(item.id),
    targetType: str(item.target_type),
    targetId: str(item.target_id),
    targetLabel: str(item.target_label),
    targetKind: str(item.target_kind),
    provenance: asArray<string>(item.provenance).filter(Boolean),
    label: str(item.label),
    tags: asArray<string>(item.tags).filter(Boolean),
    domainId: str(item.domain_id),
    createdAt: str(item.created_at),
  };
};

const feedEntry = (value: unknown): PersonalFeedEntry => {
  const item = rec(value);
  return {
    targetType: str(item.target_type),
    targetId: str(item.target_id),
    targetLabel: str(item.target_label),
    targetKind: str(item.target_kind),
    provenance: asArray<string>(item.provenance).filter(Boolean),
    reason: str(item.reason),
    viewedAt: str(item.viewed_at),
    revisionId: str(item.revision_id),
  };
};

const threadSummary = (value: unknown): AssistantThreadSummary => {
  const item = rec(value);
  return {
    id: str(item.id),
    title: str(item.title, "Conversation"),
    domainId: str(item.domain_id),
    createdAt: str(item.created_at),
    updatedAt: str(item.updated_at),
  };
};

const message = (value: unknown): AssistantMessage => {
  const item = rec(value);
  return {
    id: str(item.id),
    role: str(item.role),
    content: str(item.content),
    citationIds: asArray<string>(item.citation_ids).filter(Boolean),
    position: num(item.position),
    createdAt: str(item.created_at),
  };
};

export const personalClient = {
  notes: (): Promise<PersonalNote[]> =>
    apiClient
      .get<unknown>("/api/personal/notes")
      .then((payload) => asArray<unknown>(rec(payload).notes).map(note)),

  saveNote: (input: NoteInput): Promise<PersonalNote> =>
    apiClient
      .put<unknown>("/api/personal/notes", {
        target_type: input.targetType,
        target_id: input.targetId,
        body: input.body,
        domain_id: input.domainId ?? null,
        visibility: input.visibility ?? "private",
      })
      .then((payload) => note(rec(payload).note)),

  deleteNote: (noteId: string): Promise<unknown> =>
    apiClient.delete(`/api/personal/notes/${encodeURIComponent(noteId)}`),

  saved: (): Promise<SavedItem[]> =>
    apiClient
      .get<unknown>("/api/personal/saved")
      .then((payload) => asArray<unknown>(rec(payload).saved_items).map(savedItem)),

  saveItem: (input: SavedInput): Promise<SavedItem> =>
    apiClient
      .put<unknown>("/api/personal/saved", {
        target_type: input.targetType,
        target_id: input.targetId,
        label: input.label ?? null,
        tags: input.tags ?? [],
        domain_id: input.domainId ?? null,
      })
      .then((payload) => savedItem(rec(payload).saved_item)),

  deleteSaved: (itemId: string): Promise<unknown> =>
    apiClient.delete(`/api/personal/saved/${encodeURIComponent(itemId)}`),

  recordView: (targetType: string, targetId: string, domainId?: string): Promise<unknown> =>
    apiClient.post("/api/personal/views", {
      target_type: targetType,
      target_id: targetId,
      domain_id: domainId ?? null,
    }),

  feed: (): Promise<PersonalFeed> =>
    apiClient.get<unknown>("/api/personal/feed").then((payload) => {
      const item = rec(payload);
      return {
        feed: asArray<unknown>(item.feed).map(feedEntry),
        queue: asArray<unknown>(item.queue).map(feedEntry),
        permittedDomains: asArray<string>(item.permitted_domains).filter(Boolean),
      };
    }),

  threads: (): Promise<AssistantThreadSummary[]> =>
    apiClient
      .get<unknown>("/api/personal/threads")
      .then((payload) => asArray<unknown>(rec(payload).threads).map(threadSummary)),

  createThread: (title: string, domainId?: string): Promise<AssistantThreadSummary> =>
    apiClient
      .post<unknown>("/api/personal/threads", { title, domain_id: domainId ?? null })
      .then((payload) => threadSummary(rec(payload).thread)),

  thread: (threadId: string): Promise<AssistantThread> =>
    apiClient
      .get<unknown>(`/api/personal/threads/${encodeURIComponent(threadId)}`)
      .then((payload) => {
        const item = rec(payload);
        return {
          ...threadSummary(item.thread),
          messages: asArray<unknown>(item.messages).map(message),
        };
      }),

  deleteThread: (threadId: string): Promise<unknown> =>
    apiClient.delete(`/api/personal/threads/${encodeURIComponent(threadId)}`),
};
