"use client";

import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  children: ReactNode;
  width?: string;
  maxWidth?: string;
  minWidth?: string;
  maxHeight?: string;
  minHeight?: string;
  height?: string;
  showCloseButton?: boolean;
  closable?: boolean; // If false, prevents backdrop clicks from closing
  footer?: ReactNode;
  zIndex?: number; // Support custom z-index for modal layering
  overflow?: "hidden" | "auto";
}

export function Modal({
  isOpen,
  onClose,
  children,
  width = "auto",
  maxWidth = "80vw",
  minWidth = "auto",
  minHeight = "auto",
  maxHeight = "90vh",
  height = "auto",
  showCloseButton = true,
  closable = true,
  footer = null,
  zIndex = 10000,
  overflow = "auto",
}: ModalProps) {
  const [isVisible, setIsVisible] = useState(false);
  // Portal target only exists after mount (SSR has no document).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (isOpen) {
      // Two nested frames so the initial (transparent, scaled-down) state is
      // actually painted before the transition starts — one frame is not
      // enough, the browser coalesces it with the mount.
      const frame = requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          setIsVisible(true);
        });
      });
      return () => cancelAnimationFrame(frame);
    } else {
      setIsVisible(false);
    }
  }, [isOpen]);

  if (!isOpen || !mounted) return null;

  const handleBackdropClick = (e: MouseEvent) => {
    if (closable && e.target === e.currentTarget) {
      onClose();
    }
  };

  const handleBackdropDivClick = () => {
    if (closable) {
      onClose();
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center p-4"
      style={{ zIndex }}
      onClick={handleBackdropClick}
    >
      {/* Backdrop. Light mode = white frost (`bg-background/80` over the white
          page). In dark mode `--background` is near-black, so `/80` over the
          also-black page collapses to a dead void with nothing for the blur to
          show — override to the theme's own scrim value + a stronger blur so
          the page frosts through instead. */}
      <div
        className="absolute inset-0 bg-background/70 dark:bg-black/55 dim:bg-black/55 backdrop-blur-lg dark:backdrop-blur-xl dim:backdrop-blur-xl transition-opacity duration-300"
        style={{ opacity: isVisible ? 1 : 0 }}
        onClick={handleBackdropDivClick}
      />

      {/* Modal surface: a lightly-translucent frosted panel (card color at ~93%
          + its own backdrop-blur) so it reads as elevated glass over the blurred
          page — not a flat clone of the page cards. The inset ring adds a subtle
          top-edge highlight; border + shadow give it definition. Still ~opaque
          enough to keep dense content perfectly readable. */}
      <div
        className="relative w-full border border-border/60 ring-1 ring-inset ring-foreground/[0.06] rounded-2xl shadow-2xl backdrop-blur-2xl flex flex-col !overflow-x-hidden"
        style={{
          background: "color-mix(in oklab, var(--th-card-bg-solid) 93%, transparent)",
          width,
          overflow,
          maxWidth,
          maxHeight,
          height,
          minWidth,
          minHeight,
          opacity: isVisible ? 1 : 0,
          transform: isVisible ? "translateY(0)" : "translateY(8px)",
          transition: isVisible
            ? `opacity var(--ov-dur-enter) var(--ov-ease-enter), transform var(--ov-dur-enter) var(--ov-ease-enter)`
            : `opacity var(--ov-dur-exit) var(--ov-ease-exit), transform var(--ov-dur-exit) var(--ov-ease-exit)`,
        }}
      >
        {showCloseButton && (
          <button
            onClick={onClose}
            className="absolute top-4 end-4 z-10 p-1.5 rounded-lg bg-card hover:bg-muted transition-colors text-muted-foreground hover:text-foreground shadow-sm"
          >
            <X className="w-5 h-5" />
          </button>
        )}

        <div className="w-full h-full flex flex-col">{children}</div>
        {footer}
      </div>
    </div>,
    document.body,
  );
}

export default Modal;
