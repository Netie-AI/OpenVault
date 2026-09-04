/**
 * The desktop bridge the Electron shell exposes on `window.openvault`.
 *
 * One declaration on purpose: TypeScript requires every `declare global` of
 * the same property to carry the identical type, and the two partial copies
 * that used to live in Settings and ClipDropZone had drifted apart, so
 * `next build` failed to type-check. Add bridge methods here, nowhere else.
 */
export {};

declare global {
  interface Window {
    openvault?: {
      isDesktop?: boolean;
      /** Subscribe to secrets the shell saw on the clipboard; returns an unsubscribe. */
      onClipboardSecret?: (cb: (secret: string) => void) => () => void;
      getClipboardWatch?: () => Promise<{ enabled: boolean }>;
      setClipboardWatch?: (enabled: boolean) => Promise<{ enabled: boolean }>;
    };
  }
}
