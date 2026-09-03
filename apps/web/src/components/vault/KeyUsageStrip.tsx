"use client";

/** Tokens this vault key served. priced stays false -- never invent a bill. */

export function KeyUsageStrip({
  requests,
  totalTokens,
  estimatedTokens,
  priced,
}: {
  requests: number;
  totalTokens: number;
  estimatedTokens: number;
  priced: boolean;
}) {
  if (requests <= 0 && totalTokens <= 0) {
    return (
      <p className="mt-1 text-[10px] text-muted-foreground">no token usage yet</p>
    );
  }
  const est =
    estimatedTokens > 0 ? ` · ${estimatedTokens.toLocaleString()} estimated` : "";
  return (
    <p className="mt-1 text-[10px] text-muted-foreground">
      {totalTokens.toLocaleString()} tokens · {requests} call{requests === 1 ? "" : "s"}
      {est}
      {priced ? "" : " · unpriced"}
    </p>
  );
}
