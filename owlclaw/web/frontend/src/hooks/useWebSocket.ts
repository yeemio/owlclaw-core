import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { type OverviewSnapshot } from "@/hooks/useApi";

type OverviewWsMessage = {
  type: "overview";
  payload?: OverviewSnapshot;
  data?: OverviewSnapshot;
};

type LedgerWsMessage = { type: "ledger" };
type TriggerWsMessage = { type: "triggers" };

type AnyWsMessage = OverviewWsMessage | LedgerWsMessage | TriggerWsMessage | { type: string; payload?: unknown; data?: unknown };

function getWebSocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const token = localStorage.getItem("owlclaw_token");
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${protocol}//${window.location.host}/api/v1/ws${query}`;
}

export function useConsoleWebSocket() {
  const queryClient = useQueryClient();
  const reconnectTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    let closedByUser = false;
    let ws: WebSocket | null = null;

    const clearReconnect = () => {
      if (reconnectTimerRef.current !== undefined) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = undefined;
      }
    };

    const connect = () => {
      ws = new WebSocket(getWebSocketUrl());

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as AnyWsMessage;
          if (payload.type === "overview") {
            const overview = payload.payload ?? payload.data;
            if (overview) {
              queryClient.setQueryData(["overview"], overview);
            }
          }
          if (payload.type === "ledger") {
            queryClient.invalidateQueries({ queryKey: ["ledger"] });
          }
          if (payload.type === "triggers") {
            queryClient.invalidateQueries({ queryKey: ["triggers"] });
          }
        } catch {
          // Ignore malformed websocket messages to keep UI alive.
        }
      };

      ws.onclose = () => {
        if (closedByUser) {
          return;
        }
        clearReconnect();
        reconnectTimerRef.current = window.setTimeout(connect, 2_000);
      };
    };

    connect();

    return () => {
      closedByUser = true;
      clearReconnect();
      if (ws) {
        ws.close();
      }
    };
  }, [queryClient]);
}
