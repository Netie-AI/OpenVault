"use client";

import { PageContainer } from "@/components/ui/PageContainer";
import { PageHeader } from "@/components/ui/PageHeader";
import { FreeRouteSmoke } from "@/components/FreeRouteSmoke";

/** Thin FreeRoute smoke. Not OmniRoute PlaygroundStudio. Completions stay on :5000. */
export default function PlaygroundPage() {
  return (
    <PageContainer>
      <div className="mb-6">
        <PageHeader
          title="Playground"
          description="Same POST /v1/chat/completions as Route smoke. Register help on this page. No second vault."
        />
      </div>
      <FreeRouteSmoke />
    </PageContainer>
  );
}
