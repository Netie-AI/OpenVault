"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Terminal } from "@xterm/xterm";
import TerminalSurface from "./TerminalSurface";
import { useTheme } from "@/components/theme-provider";
import { useSSEStream } from "@/lib/sse/useSSEStream";
import { createBuildMessageProcessor, type BuildMessage } from "@/lib/sse/messages";
import { BUILD_PHASES } from "@/lib/sse/steps";

type PaneStatus = "idle" | "connecting" | "streaming" | "succeeded" | "failed" | "cancelled";

interface BuildLogPaneProps {
  /** SSE endpoint. Omit to render an idle terminal (nothing connects). */
  streamUrl?: string;
  /** Connect as soon as `streamUrl` is present. */
  autoStart?: boolean;
  onSuccess?: (message?: BuildMessage) => void;
  onFailure?: (message?: string, errorCode?: string) => void;
  className?: string;
  /** CSS height for the terminal box. */
  height?: string;
}

const STATUS_LABEL: Record<PaneStatus, string> = {
  idle: "Idle",
  connecting: "Connecting",
  streaming: "Streaming",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATUS_TONE: Record<PaneStatus, string> = {
  idle: "text-muted-foreground",
  connecting: "text-muted-foreground",
  streaming: "text-info",
  succeeded: "text-success",
  failed: "text-danger",
  cancelled: "text-warning",
};

/**
 * Live build/deploy log pane: xterm + the SSE build-message contract + the
 * step rail, wired together.
 *
 * Progress is driven only by `progress` frames from the server — never by a
 * timer — so the bar cannot claim work that did not happen.
 */
export default function BuildLogPane({
  streamUrl,
  autoStart = true,
  onSuccess,
  onFailure,
  className = "",
  height = "420px",
}: BuildLogPaneProps) {
  const { resolvedTheme } = useTheme();
  const terminalRef = useRef<Terminal | null>(null);

  const [status, setStatus] = useState<PaneStatus>("idle");
  const [currentStep, setCurrentStep] = useState(0);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  /**
   * Frames are deduped on the server-assigned monotonic `eventId`, which is
   * what makes a reconnect safe: the replayed head of the ring buffer is
   * dropped instead of being painted twice.
   */
  const lastEventIdRef = useRef<number | undefined>(undefined);

  // Callbacks are read through a ref so the processor (and therefore the SSE
  // connect function) keeps a stable identity across renders. Rebuilding it
  // every render would tear down and reopen the stream on every state update.
  const callbacksRef = useRef({ onSuccess, onFailure });
  callbacksRef.current = { onSuccess, onFailure };

  const write = useCallback((bytes: Uint8Array) => {
    terminalRef.current?.write(bytes);
  }, []);

  const messageProcessor = useMemo(
    () =>
      createBuildMessageProcessor({
        onLog: (message, _rawText, rawBytes) => {
          if (message.eventId !== undefined) {
            if (
              lastEventIdRef.current !== undefined &&
              message.eventId <= lastEventIdRef.current
            ) {
              return;
            }
            lastEventIdRef.current = message.eventId;
          }
          if (rawBytes) write(rawBytes);
        },
        onProgress: (step, pct) => {
          setCurrentStep(step);
          setProgress(pct);
        },
        onSuccess: (message) => {
          setStatus("succeeded");
          setProgress(100);
          callbacksRef.current.onSuccess?.(message);
        },
        onFailure: (message, errorCode) => {
          setStatus("failed");
          setError(message ?? "Build failed");
          callbacksRef.current.onFailure?.(message, errorCode);
        },
        onCanceled: (message) => {
          setStatus("cancelled");
          setError(message ?? "Cancelled");
        },
        onContainerExit: (exitCode, message) => {
          setStatus("failed");
          setError(message ?? `Exited with code ${exitCode}`);
        },
      }),
    [write],
  );

  // Every option below has to be referentially stable, or `connect` changes on
  // each render, the effect re-runs, and the pane reconnects in a loop.
  const handleConnect = useCallback(() => setStatus("streaming"), []);
  const handleError = useCallback((err: Error) => {
    setStatus("failed");
    setError(err.message);
  }, []);

  const { connect, disconnect } = useSSEStream<BuildMessage>({
    terminalRef,
    // The processor writes log bytes itself (after the eventId check), so the
    // transport must not also auto-write or every line would double.
    autoWriteToTerminal: false,
    messageProcessor,
    onConnect: handleConnect,
    onError: handleError,
  });

  useEffect(() => {
    if (!streamUrl || !autoStart) return;

    setStatus("connecting");
    setError(null);
    lastEventIdRef.current = undefined;
    void connect(streamUrl);

    return () => disconnect();
  }, [streamUrl, autoStart, connect, disconnect]);

  return (
    <div className={`flex flex-col gap-3 ${className}`}>
      {/* Step rail */}
      <div className="flex items-center gap-2">
        {BUILD_PHASES.map((phase, index) => {
          const done = index < currentStep || status === "succeeded";
          const active = index === currentStep && status === "streaming";
          return (
            <div key={phase.id} className="flex-1">
              <div
                className={`h-1 rounded-full transition-colors ${
                  done ? "bg-primary" : active ? "bg-primary/50" : "bg-muted"
                }`}
              />
              <p
                className={`mt-1.5 text-[11px] font-medium ${
                  done || active ? "text-foreground" : "text-muted-foreground"
                }`}
              >
                {phase.label}
              </p>
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className={`font-medium ${STATUS_TONE[status]}`}>{STATUS_LABEL[status]}</span>
        <span className="text-muted-foreground tabular-nums">{progress}%</span>
      </div>

      {error && <p className="text-xs text-danger break-words">{error}</p>}

      <div
        className="rounded-2xl border border-border overflow-hidden bg-background"
        style={{ height }}
      >
        <TerminalSurface
          terminalRef={terminalRef}
          theme={resolvedTheme === "light" ? "light" : "dark"}
        />
      </div>
    </div>
  );
}
