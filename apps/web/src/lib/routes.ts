import { useCallback, useEffect, useMemo, useState } from "react";
import { AppRoute, ROUTE_META } from "../features/pages/types";

type RoutePattern = {
  route: AppRoute;
  regex: RegExp;
  params: string[];
};

export type RouteMatch = {
  route: AppRoute;
  params: Record<string, string>;
  query: Record<string, string>;
  path: string;
};

const ROUTES = Object.keys(ROUTE_META) as AppRoute[];
const ROUTE_PATTERNS: RoutePattern[] = ROUTES.map((route) => {
  const params: string[] = [];
  const pattern = route.replace(/:([^/]+)/g, (_match, name: string) => {
    params.push(name);
    return "([^/]+)";
  });
  return { route, regex: new RegExp(`^${pattern}$`), params };
});

const splitPath = (path: string): { pathname: string; search: string } => {
  const withoutHash = path.split("#")[0];
  const separator = withoutHash.indexOf("?");
  if (separator < 0) return { pathname: withoutHash || "/", search: "" };
  return {
    pathname: withoutHash.slice(0, separator) || "/",
    search: withoutHash.slice(separator + 1),
  };
};

const parseQuery = (search: string): Record<string, string> => {
  const query: Record<string, string> = {};
  new URLSearchParams(search).forEach((value, key) => {
    query[key] = value;
  });
  return query;
};

export const matchRoute = (path: string): RouteMatch => {
  const { pathname, search } = splitPath(path);
  const query = parseQuery(search);
  for (const pattern of ROUTE_PATTERNS) {
    const match = pathname.match(pattern.regex);
    if (!match) continue;
    const params: Record<string, string> = {};
    pattern.params.forEach((param, index) => {
      params[param] = decodeURIComponent(match[index + 1]);
    });
    return { route: pattern.route, params, query, path: pathname };
  }
  return { route: "/not-found", params: {}, query, path: pathname };
};

export const buildPath = (
  route: AppRoute,
  params: Record<string, string> = {},
  query: Record<string, string> = {}
): string => {
  const path = route.replace(/:([^/]+)/g, (_match, name: string) =>
    encodeURIComponent(params[name] ?? "")
  );
  const search = new URLSearchParams(query).toString();
  return search ? `${path}?${search}` : path;
};

export type RouterState = {
  route: AppRoute;
  params: Record<string, string>;
  query: Record<string, string>;
  path: string;
  navigate: (
    _route: AppRoute,
    _params?: Record<string, string>,
    _query?: Record<string, string>
  ) => void;
};

export const useRouter = (initialRoute: AppRoute): RouterState => {
  const initial = useMemo<RouteMatch>(() => {
    if (typeof globalThis.location === "undefined") {
      return matchRoute(initialRoute);
    }
    return matchRoute(`${globalThis.location.pathname}${globalThis.location.search ?? ""}`);
  }, [initialRoute]);

  const [match, setMatch] = useState<RouteMatch>(initial);

  useEffect(() => {
    if (typeof globalThis.addEventListener === "undefined") return;
    const handler = () =>
      setMatch(matchRoute(`${globalThis.location.pathname}${globalThis.location.search ?? ""}`));
    globalThis.addEventListener("popstate", handler);
    return () => globalThis.removeEventListener("popstate", handler);
  }, []);

  const navigate = useCallback(
    (
      route: AppRoute,
      params: Record<string, string> = {},
      query: Record<string, string> = {}
    ): void => {
      const path = buildPath(route, params, query) || "/";
      if (typeof globalThis.history?.pushState === "function") {
        globalThis.history.pushState({}, "", path);
        setMatch(matchRoute(path));
      } else {
        setMatch({ route, params, query, path });
      }
    },
    []
  );

  return {
    route: match.route,
    params: match.params,
    query: match.query,
    path: match.path,
    navigate,
  };
};
