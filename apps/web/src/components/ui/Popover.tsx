"use client";

import React, { useCallback, useEffect, useRef } from "react";

interface DismissiblePopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  className?: string;
  children: React.ReactNode;
}

export const DismissiblePopover: React.FC<DismissiblePopoverProps> = ({
  open,
  onOpenChange,
  className = "",
  children,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    onOpenChange(false);
  }, [onOpenChange]);

  useEffect(() => {
    if (!open) return;

    const isInside = (target: EventTarget | null) =>
      target instanceof Node && !!containerRef.current?.contains(target);

    const handlePointerDown = (event: PointerEvent) => {
      if (!isInside(event.target)) close();
    };

    const handleFocusIn = (event: FocusEvent) => {
      if (!isInside(event.target)) close();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };

    // Capture phase on pointerdown: a click on a trigger inside another popover
    // must still dismiss this one before that trigger's own handler runs.
    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [close, open]);

  return (
    <div ref={containerRef} className={className}>
      {children}
    </div>
  );
};
