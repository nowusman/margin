const ACCESS_TOKEN_KEY = "margin_access_token";
const ID_TOKEN_KEY = "margin_id_token";
const USER_KEY = "margin_user";
const GROUPS_KEY = "margin_groups";
const PKCE_VERIFIER_KEY = "margin_oidc_pkce_verifier";
const OIDC_STATE_KEY = "margin_oidc_state";
const OIDC_RETURN_PATH_KEY = "margin_oidc_return_path";

type RuntimeEnv = Record<string, string | undefined>;

type TokenResponse = {
  access_token?: string;
  id_token?: string;
};

const runtimeEnv = (): RuntimeEnv => ({
  ...((globalThis as unknown as { process?: { env?: RuntimeEnv } }).process?.env ?? {}),
  ...((import.meta as unknown as { env?: RuntimeEnv }).env ?? {}),
});

const trimTrailingSlash = (value: string): string => value.replace(/\/$/, "");

const oidcIssuer = (): string => trimTrailingSlash(runtimeEnv().VITE_OIDC_ISSUER?.trim() ?? "");

const oidcClientId = (): string => runtimeEnv().VITE_OIDC_CLIENT_ID?.trim() ?? "margin-web";

export const hasOidcConfig = (): boolean => Boolean(oidcIssuer() && oidcClientId());

const authEndpoint = (): string => `${oidcIssuer()}/protocol/openid-connect/auth`;

const tokenEndpoint = (): string => `${oidcIssuer()}/protocol/openid-connect/token`;

const logoutEndpoint = (): string => `${oidcIssuer()}/protocol/openid-connect/logout`;

const base64UrlEncode = (bytes: Uint8Array): string => {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return globalThis.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
};

const randomToken = (): string => {
  const bytes = new Uint8Array(32);
  globalThis.crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
};

const sha256 = async (value: string): Promise<string> => {
  const bytes = new TextEncoder().encode(value);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return base64UrlEncode(new Uint8Array(digest));
};

const readSessionStorage = (key: string): string | null => {
  if (typeof globalThis.sessionStorage === "undefined") return null;
  try {
    return globalThis.sessionStorage.getItem(key);
  } catch {
    return null;
  }
};

const writeSessionStorage = (key: string, value: string): void => {
  if (typeof globalThis.sessionStorage === "undefined") return;
  try {
    globalThis.sessionStorage.setItem(key, value);
  } catch {
    // Ignore storage failures and let the token exchange fail closed.
  }
};

const removeSessionStorage = (key: string): void => {
  if (typeof globalThis.sessionStorage === "undefined") return;
  try {
    globalThis.sessionStorage.removeItem(key);
  } catch {
    // Ignore storage failures during cleanup.
  }
};

const normalizeReturnPath = (value: string): string => {
  if (!value.startsWith("/")) return "/";
  if (value.startsWith("//")) return "/";
  return value;
};

const writeLocalStorage = (key: string, value: string): void => {
  if (typeof globalThis.localStorage === "undefined") return;
  try {
    globalThis.localStorage.setItem(key, value);
  } catch {
    // Ignore storage failures and keep browser navigation stable.
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

const removeLocalStorage = (key: string): void => {
  if (typeof globalThis.localStorage === "undefined") return;
  try {
    globalThis.localStorage.removeItem(key);
  } catch {
    // Ignore storage failures during cleanup.
  }
};

const redirectUri = (): string => `${window.location.origin}${window.location.pathname}`;

const loggedOutUri = (): string => `${window.location.origin}/logged-out`;

export const isOidcCallback = (): boolean => {
  if (typeof globalThis.window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  return Boolean(params.get("code") && params.get("state"));
};

export const startOidcLogin = async (redirectPath?: string): Promise<void> => {
  if (typeof globalThis.window === "undefined" || !hasOidcConfig()) return;
  const verifier = randomToken();
  const state = randomToken();
  writeSessionStorage(PKCE_VERIFIER_KEY, verifier);
  writeSessionStorage(OIDC_STATE_KEY, state);

  const target =
    redirectPath ?? `${window.location.pathname}${window.location.search}${window.location.hash}`;
  writeSessionStorage(OIDC_RETURN_PATH_KEY, normalizeReturnPath(target));
  const callbackPath = target.startsWith("/")
    ? target.split(/[?#]/, 1)[0]
    : window.location.pathname;
  const callbackUri = `${window.location.origin}${callbackPath}`;
  const params = new URLSearchParams({
    client_id: oidcClientId(),
    code_challenge: await sha256(verifier),
    code_challenge_method: "S256",
    redirect_uri: callbackUri,
    response_type: "code",
    scope: "openid profile email",
    state,
  });
  window.location.assign(`${authEndpoint()}?${params.toString()}`);
};

export const completeOidcLogin = async (): Promise<boolean> => {
  if (typeof globalThis.window === "undefined" || !hasOidcConfig() || !isOidcCallback())
    return false;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code") ?? "";
  const state = params.get("state") ?? "";
  const expectedState = readSessionStorage(OIDC_STATE_KEY);
  const verifier = readSessionStorage(PKCE_VERIFIER_KEY);
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    clearAuthSession();
    return false;
  }

  const body = new URLSearchParams({
    client_id: oidcClientId(),
    code,
    code_verifier: verifier,
    grant_type: "authorization_code",
    redirect_uri: redirectUri(),
  });
  const response = await fetch(tokenEndpoint(), {
    body,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    method: "POST",
  });
  if (!response.ok) {
    clearAuthSession();
    return false;
  }
  const tokenResponse = (await response.json()) as TokenResponse;
  if (!tokenResponse.access_token) {
    clearAuthSession();
    return false;
  }
  writeLocalStorage(ACCESS_TOKEN_KEY, tokenResponse.access_token);
  if (tokenResponse.id_token) {
    writeLocalStorage(ID_TOKEN_KEY, tokenResponse.id_token);
  }
  removeLocalStorage(USER_KEY);
  removeLocalStorage(GROUPS_KEY);
  const returnPath = normalizeReturnPath(
    readSessionStorage(OIDC_RETURN_PATH_KEY) ?? window.location.pathname
  );
  removeSessionStorage(PKCE_VERIFIER_KEY);
  removeSessionStorage(OIDC_STATE_KEY);
  removeSessionStorage(OIDC_RETURN_PATH_KEY);
  window.history.replaceState({}, document.title, returnPath);
  return true;
};

export const clearAuthSession = (): void => {
  removeLocalStorage(ACCESS_TOKEN_KEY);
  removeLocalStorage(ID_TOKEN_KEY);
  removeLocalStorage(USER_KEY);
  removeLocalStorage(GROUPS_KEY);
  removeSessionStorage(PKCE_VERIFIER_KEY);
  removeSessionStorage(OIDC_STATE_KEY);
  removeSessionStorage(OIDC_RETURN_PATH_KEY);
};

export const redirectToLoggedOut = (): void => {
  if (typeof globalThis.window === "undefined") return;
  const idToken = readLocalStorage(ID_TOKEN_KEY);
  clearAuthSession();
  if (!hasOidcConfig()) {
    window.location.assign("/logged-out");
    return;
  }
  const params = new URLSearchParams({
    client_id: oidcClientId(),
    post_logout_redirect_uri: loggedOutUri(),
  });
  if (idToken) {
    params.set("id_token_hint", idToken);
  }
  window.location.assign(`${logoutEndpoint()}?${params.toString()}`);
};
