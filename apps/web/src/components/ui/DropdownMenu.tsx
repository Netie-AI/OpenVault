"use client";

import React, { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { DismissiblePopover } from "./Popover";

export interface MenuAction {
  id: string;
  /** Omit `label` (and `onClick`) to render a pure horizontal divider row. */
  label?: string;
  icon?: React.ReactNode;
  onClick?: () => void;
  variant?: "default" | "danger" | "success" | "warning";
  disabled?: boolean;
  /** Render a horizontal rule after this item (or as this item when `label` is omitted). */
  divider?: boolean;
}

interface DropdownMenuProps {
  actions: MenuAction[];
  trigger?: React.ReactNode;
  align?: "left" | "right";
  className?: string;
  triggerClassName?: string;
  disabled?: boolean;
}

/**
 * Colour values are read straight from the token layer. The vendor original
 * wrapped these in `hsl(...)` — that only works for themes that store colours
 * as bare HSL triples, and ours stores rgba/hex, so `hsl(#0a0a0a)` parsed as
 * invalid and every default-variant item lost its colour. Use the var directly.
 */
const getVariantStyles = (variant: MenuAction["variant"]) => {
  switch (variant) {
    case "danger":
      return { color: "var(--color-danger)", hoverBg: "rgba(239, 68, 68, 0.08)" };
    case "success":
      return { color: "var(--color-success)", hoverBg: "rgba(16, 185, 129, 0.08)" };
    case "warning":
      return { color: "var(--color-warning)", hoverBg: "rgba(245, 158, 11, 0.08)" };
    default:
      return { color: "var(--foreground)", hoverBg: "var(--muted)" };
  }
};

const DropdownMenu: React.FC<DropdownMenuProps> = ({
  actions,
  trigger,
  align = "right",
  className = "",
  triggerClassName = "",
  disabled = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);

  const handleActionClick = (action: MenuAction) => {
    if (!action.disabled && action.onClick) {
      action.onClick();
      setIsOpen(false);
    }
  };

  return (
    <DismissiblePopover
      open={isOpen}
      onOpenChange={setIsOpen}
      className={`relative ${className}`}
    >
      {trigger ? (
        <div
          role="button"
          tabIndex={disabled ? -1 : 0}
          aria-disabled={disabled || undefined}
          className={triggerClassName || "inline-flex"}
          onClick={() => !disabled && setIsOpen(!isOpen)}
          onKeyDown={(e) => {
            if (disabled) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setIsOpen(!isOpen);
            }
          }}
        >
          {trigger}
        </div>
      ) : (
        <button
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          className={`${
            triggerClassName ||
            `p-2 rounded-lg transition-all duration-200 ${
              disabled ? "opacity-50 cursor-not-allowed" : "hover:bg-muted active:bg-muted/80"
            }`
          }`}
          type="button"
          style={{
            backgroundColor: isOpen && !triggerClassName ? "var(--muted)" : undefined,
          }}
        >
          <MoreHorizontal className="w-4 h-4 text-muted-foreground" />
        </button>
      )}

      {isOpen && (
        <div
          className={`absolute z-50 mt-2 rounded-2xl border border-border/60 bg-popover/70 backdrop-blur-xl shadow-xl shadow-black/[0.08] overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200 ${
            align === "right" ? "end-0" : "start-0"
          }`}
          style={{
            // Frosted-glass surface: `bg-popover/70` (Tailwind v4 color-mix)
            // tints it translucent and `backdrop-blur-xl` blurs what's behind.
            minWidth: "220px",
          }}
        >
          <div className="py-2 px-2 flex flex-col">
            {actions.map((action, index) => {
              // Pure divider row - no button, just a line.
              if (!action.label && action.divider) {
                return (
                  <div key={action.id} className="my-2 mx-3 border-t border-border/50" />
                );
              }

              const styles = getVariantStyles(action.variant);
              return (
                <React.Fragment key={action.id}>
                  <button
                    onClick={() => handleActionClick(action)}
                    disabled={action.disabled}
                    className={`w-full hover:bg-foreground/[0.06] active:bg-foreground/[0.1] px-3 py-2.5 text-start flex items-center gap-3 transition-all duration-200 rounded-xl ${
                      action.disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"
                    }`}
                    type="button"
                    style={{
                      color: action.disabled ? "var(--muted-foreground)" : styles.color,
                    }}
                  >
                    {action.icon && (
                      <div className="icon-wrapper w-5 h-5 flex items-center justify-center flex-shrink-0 transition-transform duration-200">
                        {action.icon}
                      </div>
                    )}
                    <span className="text-[14px] font-medium truncate">{action.label}</span>
                  </button>
                  {action.divider && index < actions.length - 1 && (
                    <div className="my-2 mx-3 border-t border-border/50" />
                  )}
                </React.Fragment>
              );
            })}
          </div>
        </div>
      )}
    </DismissiblePopover>
  );
};

export default DropdownMenu;
