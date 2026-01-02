import { useEffect, useRef, useState } from "react";

export type AccountPosition = {
  symbol?: string;
  qty?: number;
  entry?: number;
  notional?: number | null;
  side?: string;
  status?: string | null;
};

export type AccountSnapshot = {
  name: string;
  type: string;
  balance?: {
    total?: number | null;
    available?: number | null;
  };
  pnl?: {
    total?: number | null;
    error?: string;
  };
  positions?: AccountPosition[];
  error?: string;
};

type AccountsStreamPayload = {
  ts?: number;
  accounts?: AccountSnapshot[];
};

type AccountsStreamOptions = {
  apiBase: string;
  authHeaders: Record<string, string>;
  enabled?: boolean;
};

const extractToken = (authHeaders: Record<string, string>) => {
  const auth = (authHeaders as any)?.Authorization;
  if (!auth || typeof auth !== "string") {
    return null;
  }
  if (!auth.toLowerCase().startsWith("basic ")) {
    return null;
  }
  return auth.split(" ")[1] || null;
};

export function useAccountsStream({ apiBase, authHeaders, enabled = true }: AccountsStreamOptions) {
  const [accounts, setAccounts] = useState<AccountSnapshot[]>([]);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    closedRef.current = false;
    const token = extractToken(authHeaders);
    const proto = apiBase.startsWith("https") ? "wss" : "ws";
    const wsUrl = `${proto}://${apiBase.replace(/^https?:\/\//, "")}/ws/accounts${
      token ? `?token=${encodeURIComponent(token)}` : ""
    }`;

    const connect = () => {
      if (closedRef.current) {
        return;
      }
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onopen = () => {
        setConnected(true);
        setError("");
      };
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as AccountsStreamPayload;
          if (payload?.accounts) {
            setAccounts(payload.accounts);
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : "Invalid stream payload");
        }
      };
      ws.onerror = () => {
        setError("Accounts stream error");
      };
      ws.onclose = () => {
        setConnected(false);
        if (closedRef.current) {
          return;
        }
        reconnectRef.current = setTimeout(connect, 5000);
      };
    };

    connect();
    return () => {
      closedRef.current = true;
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
      wsRef.current = null;
      setConnected(false);
    };
  }, [apiBase, authHeaders, enabled]);

  return { accounts, error, connected };
}
