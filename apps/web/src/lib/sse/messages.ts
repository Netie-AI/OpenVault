/**
 * SSE message processors.
 *
 * Each processor says how one kind of stream is parsed and handled. The
 * `BuildMessage["type"]` union below is the contract our FastAPI build stream
 * must emit — copied unchanged from OpenShip so their terminal, progress rail
 * and reconnect logic keep working against our backend.
 *
 * The authoritative, both-ends statement of that wire contract lives in
 * `./frames.ts`. Read it before changing anything here or writing the Python
 * producer.
 */

import type { SSEMessage, SSEMessageProcessor } from "./types";
import { STEP_INDEX, progressForStep, type StepStatus } from "./steps";

// ============================================================================
// BUILD MESSAGE PROCESSOR
// ============================================================================

export interface ServiceStatusEvent {
  serviceName: string;
  serviceId: string;
  status: "pending" | "building" | "built" | "deploying" | "running" | "failed";
  error?: string;
  containerId?: string;
  hostPort?: number;
}

export interface BuildMessage extends SSEMessage {
  type:
    | "log"
    | "success"
    | "failure"
    | "phase"
    | "progress"
    | "reconnected"
    | "complete"
    | "end"
    | "connected"
    | "started"
    | "error"
    | "cancelled"
    | "prompt"
    | "service-status"
    | "unknown";
  promptId?: string;
  title?: string;
  actions?: Array<{ id: string; label: string; variant?: string }>;
  details?: Record<string, unknown>;
  /** base64 of the raw log bytes — decoded before it reaches a handler. */
  data?: string;
  success?: boolean;
  message?: string;
  error?: string;
  phase?: string;
  currentStep?: number;
  progress?: number;
  /** Monotonic sequence assigned before the ring-buffer trim; drives dedup. */
  eventId?: number;
  /** Build step this frame belongs to (`prepare`/`clone`/…). */
  step?: string;
  stepStatus?: StepStatus;
  level?: string;
  exitCode?: number;
  running?: boolean;
  errorCode?: string;
  errorDetails?: Record<string, unknown>;
  warningMessage?: string;
  serviceName?: string;
  serviceId?: string;
}

export interface BuildMessageCallbacks {
  onLog?: (message: BuildMessage, rawText?: string, rawBytes?: Uint8Array) => void;
  onSuccess?: (data?: BuildMessage) => void;
  onFailure?: (
    message?: string,
    errorCode?: string,
    errorDetails?: Record<string, unknown>,
  ) => void;
  onCanceled?: (message?: string) => void;
  onPhaseChange?: (phase: string) => void;
  onProgress?: (currentStep: number, progress: number) => void;
  onReconnected?: () => void;
  onContainerExit?: (exitCode: number, message?: string) => void;
  onPrompt?: (prompt: {
    promptId: string;
    title: string;
    message: string;
    actions: Array<{ id: string; label: string; variant?: string }>;
    details?: Record<string, unknown>;
  }) => void;
  onServiceStatus?: (status: ServiceStatusEvent) => void;
}

export const createBuildMessageProcessor = (
  callbacks: BuildMessageCallbacks = {},
): SSEMessageProcessor<BuildMessage> => {
  /**
   * Move the step rail from whichever fields the frame actually carried.
   *
   * The numeric `currentStep`/`progress` pair is the contract, but `step` +
   * `stepStatus` is the same information in the producer's own vocabulary and
   * costs nothing to accept. Deriving it here means a Python side that emits
   * only step names still drives the rail instead of leaving it frozen at 0 —
   * the kind of divergence that looks like "the build is stuck".
   */
  const emitProgress = (message: BuildMessage) => {
    const step = message.step;
    const derivedStep = step !== undefined ? STEP_INDEX[step] : undefined;
    const derivedProgress =
      step !== undefined && STEP_INDEX[step] !== undefined
        ? progressForStep(step, message.stepStatus)
        : undefined;

    const currentStep = message.currentStep ?? derivedStep;
    const progress = message.progress ?? derivedProgress;

    if (currentStep === undefined || progress === undefined) return;
    callbacks.onProgress?.(currentStep, progress);
  };

  return {
    parseMessage: (jsonData): BuildMessage => {
      // Build finished (success flag says which way).
      if (jsonData?.type === "complete") {
        return { type: "complete", ...jsonData };
      }

      // Stream end.
      if (jsonData?.type === "end") {
        return { type: "end", ...jsonData };
      }

      if (jsonData?.type === "connected") {
        return { type: "connected", ...jsonData };
      }

      // No `&& !success` guard: an `{type:"error", success:true}` frame is a
      // contradiction, and resolving it as a success would report a failed
      // deploy as green. The error wins.
      if (jsonData?.type === "error") {
        return { type: "error", ...jsonData };
      }

      // Per-service status update (compose-style multi-service deploys).
      if (jsonData?.type === "service-status") {
        return { type: "service-status", ...jsonData };
      }

      // Pipeline is waiting on a user decision.
      if (jsonData?.type === "prompt") {
        return { type: "prompt", ...jsonData };
      }

      if (jsonData?.type === "reconnected") {
        return { type: "reconnected", ...jsonData };
      }

      if (jsonData?.type === "cancelled") {
        return { type: "cancelled", ...jsonData };
      }

      // Build acknowledged — NOT a success event.
      if (jsonData?.type === "started") {
        return { type: "started", ...jsonData };
      }

      // Log line — must be checked before the success/failure catch-alls.
      if (jsonData?.type === "log" && jsonData?.data) {
        return { type: "log", ...jsonData };
      }

      if (jsonData?.type === "progress") {
        return { type: "progress", ...jsonData };
      }

      // Catch-alls for frames that carry only a success flag.
      if (jsonData?.success === true) {
        return { type: "success", ...jsonData };
      }

      if (jsonData?.success === false) {
        return { type: "failure", ...jsonData };
      }

      if (jsonData?.phase) {
        return { type: "phase", ...jsonData };
      }

      if (jsonData?.currentStep !== undefined || jsonData?.progress !== undefined) {
        return { type: "progress", ...jsonData };
      }

      return { type: "unknown", ...jsonData };
    },

    handleMessage: (message, context) => {
      const { rawText, rawBytes, writeToTerminal } = context;

      switch (message.type) {
        /**
         * A `complete` frame is TERMINAL, flag or no flag.
         *
         * The contract (frames.ts) requires `success` on every `complete`. When
         * it is missing we must still resolve, because the alternative is what
         * this branch used to do: fall through silently, leaving the pane on
         * "Streaming" with no error, no timeout and no way out.
         *
         * The fallback order is evidence-based and deliberately pessimistic:
         * `exitCode` if the producer sent one, otherwise failure. Announcing a
         * successful deploy on the strength of a frame that never claimed
         * success is not a trade we make.
         */
        case "complete":
          if (message.success === true) {
            callbacks.onSuccess?.(message);
          } else if (message.success === false) {
            callbacks.onFailure?.(
              message.message || message.error || "Build completed with errors",
              message.errorCode,
              message.errorDetails,
            );
          } else if (message.exitCode === 0) {
            callbacks.onSuccess?.(message);
          } else if (message.exitCode !== undefined) {
            callbacks.onFailure?.(
              message.message ||
                message.error ||
                `Build exited with code ${message.exitCode}`,
              message.errorCode,
              message.errorDetails,
            );
          } else {
            callbacks.onFailure?.(
              message.message ||
                message.error ||
                "Build ended without reporting success or failure",
              message.errorCode,
              message.errorDetails,
            );
          }
          break;

        case "reconnected":
          callbacks.onReconnected?.();
          break;

        case "progress":
          emitProgress(message);
          // A progress frame may also carry a log line.
          if (message.data && rawBytes) {
            writeToTerminal?.(rawBytes);
            callbacks.onLog?.(message, rawText, rawBytes);
          }
          break;

        case "success":
          callbacks.onSuccess?.(message);
          break;

        case "failure":
          callbacks.onFailure?.(
            message.message || message.error,
            message.errorCode,
            message.errorDetails,
          );
          break;

        case "cancelled":
          callbacks.onCanceled?.(message.message || "Build cancelled by user");
          break;

        case "phase":
          callbacks.onPhaseChange?.(message.phase!);
          // Phase frames carry `step`/`stepStatus` too — same rail, same table.
          emitProgress(message);
          if (message.data && rawBytes) {
            writeToTerminal?.(rawBytes);
            callbacks.onLog?.(message, rawText, rawBytes);
          }
          break;

        case "log":
          if (message.data && rawBytes) {
            writeToTerminal?.(rawBytes);
            callbacks.onLog?.(message, rawText, rawBytes);
          }
          break;

        case "end":
          if (message.exitCode !== undefined && message.exitCode !== 0) {
            const exitMessage =
              message.message || `Process exited with code ${message.exitCode}`;
            callbacks.onContainerExit?.(message.exitCode, exitMessage);
          }
          break;

        case "connected":
          if (
            message.running === false &&
            message.exitCode !== undefined &&
            message.exitCode !== 0
          ) {
            callbacks.onContainerExit?.(
              message.exitCode,
              `Not running (exit code: ${message.exitCode})`,
            );
          }
          break;

        case "error": {
          const errorMsg = message.error || message.message || "Stream error";
          callbacks.onFailure?.(errorMsg, message.errorCode, message.errorDetails);
          break;
        }

        case "prompt":
          if (message.promptId && message.message) {
            callbacks.onPrompt?.({
              promptId: message.promptId,
              title: message.title || "Action Required",
              message: message.message,
              actions: message.actions || [],
              details: message.details,
            });
          }
          break;

        case "started":
          // Acknowledged — logs follow, nothing to do.
          break;

        case "service-status":
          callbacks.onServiceStatus?.({
            serviceName: message.serviceName ?? "",
            serviceId: message.serviceId ?? "",
            status: (message.status as ServiceStatusEvent["status"]) ?? "pending",
            error: message.error,
            containerId: message.containerId as string | undefined,
            hostPort: message.hostPort as number | undefined,
          });
          break;
      }

      return true; // Continue processing.
    },
  };
};

// ============================================================================
// LIVE LOGS MESSAGE PROCESSOR
// ============================================================================

export interface LogMessage extends SSEMessage {
  type: "log" | "connected" | "error" | "end" | "unknown";
  data?: string;
  message?: string;
  error?: string;
  exitCode?: number;
  running?: boolean;
}

export interface LogMessageCallbacks {
  onLog?: (message: LogMessage, rawText?: string, rawBytes?: Uint8Array) => void;
  onError?: (message: string) => void;
  onContainerExit?: (exitCode: number, message?: string) => void;
}

export const createLogMessageProcessor = (
  callbacks: LogMessageCallbacks = {},
): SSEMessageProcessor<LogMessage> => {
  return {
    parseMessage: (jsonData): LogMessage => {
      if (jsonData?.type === "connected") {
        return { type: "connected", ...jsonData };
      }

      if (jsonData?.type === "error") {
        return { type: "error", ...jsonData };
      }

      if (jsonData?.type === "end") {
        return { type: "end", ...jsonData };
      }

      if (jsonData?.type === "log" || jsonData?.data) {
        return { type: "log", ...jsonData };
      }

      return { type: "unknown", ...jsonData };
    },

    handleMessage: (message, context) => {
      const { rawText, rawBytes, writeToTerminal } = context;

      switch (message.type) {
        case "log":
          if (message.data && rawBytes) {
            writeToTerminal?.(rawBytes);
            callbacks.onLog?.(message, rawText, rawBytes);
          } else if (message.message) {
            // Plain-text fallback for producers that skip the base64 payload.
            const bytes = new TextEncoder().encode(message.message);
            writeToTerminal?.(bytes);
            callbacks.onLog?.(message, message.message, bytes);
          }
          break;

        case "connected":
          if (
            message.running === false &&
            message.exitCode !== undefined &&
            message.exitCode !== 0
          ) {
            callbacks.onContainerExit?.(
              message.exitCode,
              `Not running (exit code: ${message.exitCode})`,
            );
          }
          break;

        case "end":
          if (message.exitCode !== undefined && message.exitCode !== 0) {
            callbacks.onContainerExit?.(
              message.exitCode,
              message.message || `Process exited with code ${message.exitCode}`,
            );
          }
          break;

        case "error": {
          callbacks.onError?.(message.error || message.message || "Stream error");
          break;
        }
      }

      return true;
    },
  };
};

// ============================================================================
// GENERIC / PASSTHROUGH MESSAGE PROCESSOR
// ============================================================================

export interface GenericMessage extends SSEMessage {
  type: string;
}

export const createGenericMessageProcessor = (
  onMessage?: (
    message: GenericMessage,
    context: {
      rawText?: string;
      rawBytes?: Uint8Array;
      writeToTerminal?: (bytes: Uint8Array) => void;
    },
  ) => void,
): SSEMessageProcessor<GenericMessage> => {
  return {
    parseMessage: (jsonData): GenericMessage => ({
      type: jsonData?.type || "message",
      ...jsonData,
    }),

    handleMessage: (message, context) => {
      onMessage?.(message, context);
      return true;
    },
  };
};
