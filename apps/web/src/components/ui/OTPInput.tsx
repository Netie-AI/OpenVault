"use client";

import React, { useRef, useState, type KeyboardEvent, type ClipboardEvent } from "react";

interface OTPInputProps {
  length?: number;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
}

/** Digit-per-box code entry — mesh handshake approval and passkey flows. */
const OTPInput: React.FC<OTPInputProps> = ({
  length = 6,
  value,
  onChange,
  disabled = false,
  autoFocus = false,
}) => {
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);
  const [focusedIndex, setFocusedIndex] = useState<number | null>(autoFocus ? 0 : null);

  if (inputRefs.current.length !== length) {
    inputRefs.current = Array(length).fill(null);
  }

  const digits = value.split("").concat(Array(length).fill("")).slice(0, length);

  const focusInput = (index: number) => {
    if (index >= 0 && index < length) {
      inputRefs.current[index]?.focus();
      setFocusedIndex(index);
    }
  };

  const handleChange = (index: number, digit: string) => {
    // Keep only the last typed digit: browsers deliver the whole field value
    // on autofill, and maxLength does not stop that.
    const newDigit = digit.replace(/\D/g, "").slice(-1);

    const newDigits = [...digits];
    newDigits[index] = newDigit;
    onChange(newDigits.join("").replace(/\s/g, ""));

    if (newDigit && index < length - 1) {
      focusInput(index + 1);
    }
  };

  const handleKeyDown = (index: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace") {
      if (!digits[index] && index > 0) {
        // Empty box: step back and clear the previous one.
        const newDigits = [...digits];
        newDigits[index - 1] = "";
        onChange(newDigits.join("").replace(/\s/g, ""));
        focusInput(index - 1);
      } else if (digits[index]) {
        const newDigits = [...digits];
        newDigits[index] = "";
        onChange(newDigits.join("").replace(/\s/g, ""));
      }
      e.preventDefault();
    } else if (e.key === "ArrowLeft" && index > 0) {
      focusInput(index - 1);
    } else if (e.key === "ArrowRight" && index < length - 1) {
      focusInput(index + 1);
    }
  };

  const handlePaste = (e: ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault();
    const pastedData = e.clipboardData
      .getData("text/plain")
      .replace(/\D/g, "")
      .slice(0, length);
    onChange(pastedData);

    focusInput(Math.min(pastedData.length, length - 1));
  };

  return (
    <div className="flex gap-2 justify-center">
      {Array.from({ length }).map((_, index) => (
        <input
          key={index}
          ref={(el) => {
            inputRefs.current[index] = el;
          }}
          type="text"
          inputMode="numeric"
          maxLength={1}
          value={digits[index] || ""}
          onChange={(e) => handleChange(index, e.target.value)}
          onKeyDown={(e) => handleKeyDown(index, e)}
          onPaste={handlePaste}
          onFocus={() => setFocusedIndex(index)}
          onBlur={() => setFocusedIndex(null)}
          disabled={disabled}
          autoFocus={autoFocus && index === 0}
          className={`h-14 w-12 rounded-xl border bg-background text-center font-mono text-2xl font-semibold text-foreground transition-all
            ${
              focusedIndex === index
                ? "border-ring ring-2 ring-ring/30"
                : digits[index]
                  ? "border-foreground/40"
                  : "border-input"
            }
            ${disabled ? "cursor-not-allowed opacity-50" : "hover:border-foreground/30"}
            focus:outline-none
          `}
        />
      ))}
    </div>
  );
};

export default OTPInput;
