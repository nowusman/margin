import { useCallback, useEffect, useRef, useState } from "react";

type LoadOptions = {
  enabled?: boolean;
  refreshMs?: number;
};

export type LoadState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

export function useLoad<T>(
  key: string,
  loader: (_signal?: AbortSignal) => Promise<T>,
  options: LoadOptions = {}
): LoadState<T> {
  const { enabled = true, refreshMs } = options;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);
  const [refreshIndex, setRefreshIndex] = useState(0);

  const refresh = useCallback(() => setRefreshIndex((value) => value + 1), []);

  const loadedKey = useRef(key);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }

    let active = true;
    const controller = new AbortController();
    if (loadedKey.current !== key) {
      // The reader asked for a different record, so the previous one must not
      // stay on the screen while the new one is on its way.
      loadedKey.current = key;
      setData(null);
      setError(null);
    }
    setLoading(true);
    loader(controller.signal)
      .then((response) => {
        if (!active) return;
        setData(response);
        setError(null);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Request failed");
        setData(null);
        setLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [key, enabled, refreshIndex]);

  useEffect(() => {
    if (!enabled || !refreshMs) return;
    const interval = window.setInterval(() => {
      setRefreshIndex((value) => value + 1);
    }, refreshMs);
    return () => window.clearInterval(interval);
  }, [enabled, refreshMs]);

  return { data, loading, error, refresh };
}

export type CommandState = {
  busy: boolean;
  message: string | null;
  runCommand: (
    _label: string,
    _action: () => Promise<unknown | false>,
    _describe?: (_result: unknown) => string
  ) => void;
};

export function useCommand(): CommandState {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const inFlight = useRef(false);

  const runCommand = useCallback(
    (
      label: string,
      action: () => Promise<unknown | false>,
      describe?: (_result: unknown) => string
    ): void => {
      // A command that is already running is not started twice, so a reader who
      // presses the button twice does not write the record twice.
      if (inFlight.current) return;
      inFlight.current = true;
      setBusy(true);
      setMessage(`${label}...`);
      action()
        .then((result) => {
          if (result === false) {
            setMessage(null);
            return;
          }
          setMessage(describe ? describe(result) : `${label} succeeded.`);
        })
        .catch((reason: unknown) => {
          setMessage(
            `${label} failed: ${reason instanceof Error ? reason.message : "Request failed"}`
          );
        })
        .finally(() => {
          inFlight.current = false;
          setBusy(false);
        });
    },
    []
  );

  return { busy, message, runCommand };
}
