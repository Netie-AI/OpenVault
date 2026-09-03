/**
 * SSE frame contract — shared vocabulary between the stream reader, the message
 * processors, and the FastAPI endpoints that emit the frames.
 *
 * This is deliberately the *vendor's* shape, not one of our own invention:
 * `GET /api/ship/engine/{id}/stream` is written against it, so the terminal and
 * the progress rail work without a translation layer on either side.
 */

/** Generic SSE message — every frame carries a `type` discriminator. */
export interface SSEMessage {
  type: string;
  [key: string]: unknown;
}

/** Everything a message handler is handed alongside the parsed frame. */
export interface SSEMessageContext {
  /** `data` base64-decoded to text, when the frame carried a payload. */
  rawText?: string;
  /** `data` base64-decoded to bytes — this is what xterm wants, not the text. */
  rawBytes?: Uint8Array;
  writeToTerminal?: (bytes: Uint8Array) => void;
}

/**
 * Parses and handles SSE messages. This is where per-stream business logic
 * lives; the stream reader itself stays generic.
 */
export interface SSEMessageProcessor<T extends SSEMessage = SSEMessage> {
  /**
   * Turn raw parsed JSON into a typed message. Deliberately `any`: the input is
   * whatever came off the wire, and every processor narrows it itself.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  parseMessage: (jsonData: any) => T;
  /** Handle the parsed message. Return `false` to stop processing the chunk. */
  handleMessage: (message: T, context: SSEMessageContext) => boolean | void;
}
