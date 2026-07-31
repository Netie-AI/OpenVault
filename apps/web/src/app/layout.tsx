import type { Metadata } from "next";
import { AppBar } from "@/components/shell";
import { ThemeProvider, ThemeScript } from "@/components/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenVault",
  description: "Custody · Keys · OpenShip · OmniRoute · Netie Engine",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body>
        <ThemeProvider>
          <AppBar />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
