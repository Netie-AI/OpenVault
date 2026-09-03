"use client";

import { useCallback, useMemo, useRef } from "react";
import type { RefObject } from "react";
import type { Terminal } from "@xterm/xterm";
import type { SSEMessage, SSEMessageProcessor } from "./types";

/**
 * Core SSE stream hook — generic transport, no business logic.
 *
 * Uses `fetch` + a stream reader rather than `EventSource` on purpose:
 * `EventSource` cannot POST, cannot set headers, and cannot be aborted
 * deterministically. Deploy streams need all three.
 */
export interface SSEStreamOptions<T extends SSEMessage = SSEMessage> {
  terminalRef?: RefObject<Terminal | null>;
  autoWriteToTerminal?: boolean;

  messageProcessor?: SSEMessageProcessor<T>;

  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;

  /** Fires for every frame, before the processor runs. */
  onRawMessage?: (message: T, rawText?: string, rawBytes?: Uint8Array) => void;
}

export interface SSEConnectOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  /** Abort the stream if no bytes arrive for this long. */
  idleTimeoutMs?: number;
}

export const useSSEStream = <T extends SSEMessage = SSEMessage>(
  options: SSEStreamOptions<T> = {},
) => {
  const {
    terminalRef,
    autoWriteToTerminal = true,
    messageProcessor,
    onConnect,
    onDisconnect,
    onError,
    onRawMessage,
  } = options;

  const abortControllerRef = useRef<AbortController | null>(null);
  const isConnectedRef = useRef(false);
  /** Holds the tail of a frame that was split across two network chunks. */
  const sseBufferRef = useRef("");

  const processLogData = useCallback(
    (data: string): { bytes: Uint8Array; text: string } | null => {
      try {
        // Logs travel base64-encoded because they are raw terminal bytes —
        // ANSI escapes and partial UTF-8 sequences included — and JSON cannot
        // carry those intact.
        const binaryData = atob(data);
        const bytes = new Uint8Array(binaryData.length);
        for (let i = 0; i < binaryData.length; i++) {
          bytes[i] = binaryData.charCodeAt(i);
        }

        return { bytes, text: new TextDecoder().decode(bytes) };
      } catch (err) {
        console.error("Error processing log data:", err);
        return null;
      }
    },
    [],
  );

  const writeToTerminal = useCallback(
    (bytes: Uint8Array) => {
      if (terminalRef?.current && autoWriteToTerminal) {
        terminalRef.current.write(bytes);
      }
    },
    [terminalRef, autoWriteToTerminal],
  );

  const defaultParseMessage = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (jsonData: any): T => ({ type: jsonData?.type || "unknown", ...jsonData }) as T,
    [],
  );

  /**
   * Process a chunk of SSE text: handles `event:` + `data:` lines, multi-line
   * `data:`, and frame boundaries (`\n\n`) that straddle chunks.
   */
  const processSSEChunk = useCallback(
    (chunk: string) => {
      try {
        const buffer = sseBufferRef.current + chunk;
        const parts = buffer.split("\n\n");

        // The last element may be a partial frame — hold it for the next chunk.
        sseBufferRef.current = parts.pop() || "";

        for (const part of parts) {
          if (!part.trim()) continue;

          let eventType = "message";
          let dataStr = "";

          for (const line of part.split("\n")) {
            const trimmed = line.trim();
            if (trimmed.startsWith("event:")) {
              eventType = trimmed.substring(6).trim();
            } else if (trimmed.startsWith("data:")) {
              const d = trimmed.substring(5).trim();
              dataStr = dataStr ? dataStr + "\n" + d : d;
            } else if (trimmed.startsWith("{")) {
              // Bare-JSON fallback for producers that skip the SSE framing.
              dataStr = trimmed;
            }
          }

          if (!dataStr) continue;

          let jsonData: Record<string, unknown>;
          try {
            jsonData = JSON.parse(dataStr);
          } catch {
            continue;
          }

          // Let the SSE event name supply `type` when the payload omits it.
          if (!jsonData.type && eventType !== "message") {
            jsonData.type = eventType;
          }

          const parser = messageProcessor?.parseMessage || defaultParseMessage;
          const message = parser(jsonData);

          let rawText: string | undefined;
          let rawBytes: Uint8Array | undefined;

          const payload = message.data;
          if (typeof payload === "string" && payload.length > 0) {
            const processed = processLogData(payload);
            if (processed) {
              rawText = processed.text;
              rawBytes = processed.bytes;
            }
          }

          onRawMessage?.(message, rawText, rawBytes);

          if (messageProcessor?.handleMessage) {
            const shouldContinue = messageProcessor.handleMessage(message, {
              rawText,
              rawBytes,
              writeToTerminal,
            });

            if (shouldContinue === false) break;
          }
        }
      } catch (err) {
        console.error("Error processing SSE chunk:", err);
        onError?.(err as Error);
      }
    },
    [
      messageProcessor,
      processLogData,
      writeToTerminal,
      onRawMessage,
      onError,
      defaultParseMessage,
    ],
  );

  const connect = useCallback(
    async (url: string, connectOptions: SSEConnectOptions = {}) => {
      abortControllerRef.current?.abort();
      sseBufferRef.current = "";

      const controller = new AbortController();
      abortControllerRef.current = controller;

      let idleTimer: ReturnType<typeof setTimeout> | null = null;
      const clearIdleTimer = () => {
        if (idleTimer) {
          clearTimeout(idleTimer);
          idleTimer = null;
        }
      };
      const resetIdleTimer = () => {
        if (!connectOptions.idleTimeoutMs) return;
        clearIdleTimer();
        idleTimer = setTimeout(() => controller.abort(), connectOptions.idleTimeoutMs);
      };
      const releaseController = () => {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
      };

      try {
        const response = await fetch(url, {
          method: connectOptions.method || "GET",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...connectOptions.headers,
          },
          body: connectOptions.body ? JSON.stringify(connectOptions.body) : undefined,
          signal: controller.signal,
        });

        if (!response.ok) {
          let message = response.statusText;
          try {
            const json = await response.json();
            message = json.error || json.detail || message;
          } catch {
            /* body was not JSON — keep the status text */
          }
          throw new Error(`SSE connection failed: ${message}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No reader available");

        isConnectedRef.current = true;
        onConnect?.();
        resetIdleTimer();

        const decoder = new TextDecoder();

        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;

          resetIdleTimer();
          processSSEChunk(decoder.decode(value, { stream: true }));
        }

        clearIdleTimer();
        releaseController();
        isConnectedRef.current = false;
        onDisconnect?.();
      } catch (err) {
        clearIdleTimer();
        releaseController();
        isConnectedRef.current = false;

        if ((err as Error).name === "AbortError") {
          onDisconnect?.();
        } else {
          console.error("SSE connection error:", err);
          onError?.(err as Error);
        }
      }
    },
    [processSSEChunk, onConnect, onDisconnect, onError],
  );

  const disconnect = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    isConnectedRef.current = false;
    sseBufferRef.current = "";
  }, []);

  const isConnected = useCallback(() => isConnectedRef.current, []);

  return useMemo(
    () => ({ connect, disconnect, isConnected, processSSEChunk }),
    [connect, disconnect, isConnected, processSSEChunk],
  );
};
