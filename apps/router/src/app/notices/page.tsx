// Copyright (c) 2026 Netie AI. MIT.
import type { Metadata } from "next";
import Link from "next/link";
import { UPSTREAM_NOTICES } from "./upstreamLicenses";

export const metadata: Metadata = {
  title: "Notices, FreeRoute",
  description: "Third-party notices and attribution for FreeRoute.",
};

export default function NoticesPage() {
  return (
    <main className="min-h-screen text-text-main">
      <div className="max-w-3xl mx-auto px-6 py-16">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-text-muted hover:text-primary transition-colors mb-8"
        >
          <span className="material-symbols-outlined text-[18px]">arrow_back</span>
          Back to home
        </Link>

        <h1 className="text-3xl font-bold mb-2">Notices</h1>
        <p className="text-sm text-text-muted mb-10">Attribution for FreeRoute&apos;s upstream code.</p>

        <div className="space-y-6 text-text-muted leading-relaxed">
          <section>
            <h2 className="text-lg font-semibold text-text-main mb-3">About FreeRoute</h2>
            <p>
              FreeRoute is Netie&apos;s free AI gateway, part of OpenVault. It routes API-key
              traffic to real providers. It does not pool consumer subscription accounts and does
              not relay browser chat sessions.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-text-main mb-3">Credit</h2>
            <p>
              Based on OmniRoute by diegosouzapw (MIT), itself based on 9router (MIT), and
              including a TypeScript port of CLIProxyAPI (MIT). The full license texts follow.
              They also ship in this installation&apos;s files: <code className="text-primary text-sm">LICENSE</code>,{" "}
              <code className="text-primary text-sm">NETIE_NOTICES.md</code> and{" "}
              <code className="text-primary text-sm">THIRD_PARTY_NOTICES.md</code>.
            </p>
          </section>

          {UPSTREAM_NOTICES.map((notice) => (
            <section key={notice.name}>
              <h2 className="text-lg font-semibold text-text-main mb-1">{notice.name}</h2>
              <p className="text-sm mb-3">
                {notice.role}{" "}
                <a
                  href={notice.url}
                  className="text-primary hover:underline break-all"
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  {notice.url}
                </a>
              </p>
              <pre className="whitespace-pre-wrap text-xs bg-black/20 rounded-md p-4 overflow-x-auto">
                {notice.licenseText}
              </pre>
            </section>
          ))}

          <section>
            <h2 className="text-lg font-semibold text-text-main mb-3">Trademarks</h2>
            <p>
              OmniRoute, 9router and CLIProxyAPI are names of their respective owners. They appear
              here only as attribution.
            </p>
          </section>
        </div>
      </div>
    </main>
  );
}
