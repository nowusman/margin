import { clearAuthSession, startOidcLogin } from "../auth/oidc";

export type ApiRequestOptions = {
  body?: unknown;
  headers?: Record<string, string>;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  signal?: AbortSignal;
  timeoutMs?: number;
};

export class ApiError extends Error {
  readonly detail: unknown;
  readonly status: number;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.detail = detail;
    this.status = status;
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;

const abortReason = (signal?: AbortSignal): unknown => {
  if (signal && "reason" in signal && signal.reason) return signal.reason;
  return new Error("API request was cancelled.");
};

const timeoutError = (timeoutMs: number): Error =>
  new Error(`API request timed out after ${Math.round(timeoutMs / 1000)} seconds.`);

const apiBaseUrl = (): string => {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env
    ?.VITE_MARGIN_API_BASE_URL;
  return env?.replace(/\/$/, "") ?? "";
};

const isTestRuntime = (): boolean => {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  return Boolean(env?.MODE === "test" || env?.VITEST === "true");
};

const authAutoRedirectEnabled = (): boolean => {
  if (isTestRuntime()) return false;
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  const configured = env?.VITE_AUTH_AUTO_REDIRECT;
  if (!configured) return true;
  return !["0", "false", "no", "off"].includes(configured.trim().toLowerCase());
};

const redirectToLogin = (): void => {
  if (!authAutoRedirectEnabled()) return;
  if (typeof globalThis.window === "undefined") return;
  try {
    void startOidcLogin();
  } catch {
    // Ignore navigation failures in non-browser test runtimes.
  }
};

const readLocalStorage = (key: string): string | null => {
  if (typeof globalThis.localStorage === "undefined") return null;
  try {
    return globalThis.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const authHeaders = (): Record<string, string> => {
  const token = readLocalStorage("margin_access_token");
  const headers: Record<string, string> = {};
  if (!token) return headers;
  const user = readLocalStorage("margin_user")?.trim();
  const groups = readLocalStorage("margin_groups")?.trim();
  headers.Authorization = `Bearer ${token}`;
  if (user) headers["x-margin-user"] = user;
  if (groups) headers["x-margin-groups"] = groups;
  return headers;
};

const parseJson = async (response: Response): Promise<unknown> => {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
};

const errorMessage = (status: number, payload: unknown): string => {
  if (payload && typeof payload === "object" && "detail" in payload) {
    return String((payload as { detail: unknown }).detail);
  }
  return `API request failed with status ${status}`;
};

export const apiClient = {
  request: async <T>(path: string, options: ApiRequestOptions = {}): Promise<T> => {
    const controller = new AbortController();
    const abortFromCaller = (): void => controller.abort(abortReason(options.signal));
    if (options.signal) {
      if (options.signal.aborted) controller.abort(abortReason(options.signal));
      else options.signal.addEventListener("abort", abortFromCaller, { once: true });
    }
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const timeout = globalThis.setTimeout(
      () => controller.abort(timeoutError(timeoutMs)),
      timeoutMs
    );

    const headers: Record<string, string> = {
      Accept: "application/json",
      ...authHeaders(),
      ...options.headers,
    };
    const isFormData =
      typeof globalThis.FormData !== "undefined" && options.body instanceof globalThis.FormData;
    const body: RequestInit["body"] =
      options.body === undefined
        ? undefined
        : isFormData
          ? (options.body as RequestInit["body"])
          : JSON.stringify(options.body);
    if (body !== undefined && !isFormData) headers["Content-Type"] = "application/json";

    try {
      const response = await fetch(`${apiBaseUrl()}${path}`, {
        body,
        headers,
        method: options.method ?? (body === undefined ? "GET" : "POST"),
        signal: controller.signal,
      });
      const payload = await parseJson(response);
      if (response.status === 401) {
        clearAuthSession();
        redirectToLogin();
      }
      if (!response.ok)
        throw new ApiError(errorMessage(response.status, payload), response.status, payload);
      return payload as T;
    } finally {
      globalThis.clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abortFromCaller);
    }
  },
  get: <T>(path: string, options?: Omit<ApiRequestOptions, "body" | "method">): Promise<T> =>
    apiClient.request<T>(path, { ...options, method: "GET" }),
  patch: <T>(
    path: string,
    body: unknown,
    options?: Omit<ApiRequestOptions, "body" | "method">
  ): Promise<T> => apiClient.request<T>(path, { ...options, body, method: "PATCH" }),
  post: <T>(
    path: string,
    body?: unknown,
    options?: Omit<ApiRequestOptions, "body" | "method">
  ): Promise<T> => apiClient.request<T>(path, { ...options, body, method: "POST" }),
  put: <T>(
    path: string,
    body?: unknown,
    options?: Omit<ApiRequestOptions, "body" | "method">
  ): Promise<T> => apiClient.request<T>(path, { ...options, body, method: "PUT" }),
  delete: <T>(path: string, options?: Omit<ApiRequestOptions, "body" | "method">): Promise<T> =>
    apiClient.request<T>(path, { ...options, method: "DELETE" }),
  blob: async (
    path: string,
    options: Omit<ApiRequestOptions, "body" | "method"> = {}
  ): Promise<Blob> => {
    const response = await fetch(`${apiBaseUrl()}${path}`, {
      headers: {
        Accept: "*/*",
        ...authHeaders(),
        ...options.headers,
      },
      method: "GET",
      signal: options.signal,
    });
    if (response.status === 401) {
      clearAuthSession();
      redirectToLogin();
    }
    if (!response.ok) {
      const payload = await parseJson(response);
      throw new ApiError(errorMessage(response.status, payload), response.status, payload);
    }
    return response.blob();
  },
};
