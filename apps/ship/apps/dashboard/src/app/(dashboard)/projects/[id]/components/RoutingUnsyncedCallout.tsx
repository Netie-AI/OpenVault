"use client";

/** Show the persisted routing failure beside the domain cards and their repair action. */

import React from "react";
import { useProjectSettings } from "@/context/ProjectSettingsContext";
import { useI18n } from "@/components/i18n-provider";
import WarningCallout from "@/components/shared/WarningCallout";

export const RoutingUnsyncedCallout = ({
  onRetry,
  retrying = false,
}: {
  onRetry: () => void;
  retrying?: boolean;
}) => {
  const { projectData } = useProjectSettings();
  const { t } = useI18n();

  // A pending partial-failure decision outranks this: that release hasn't been
  // accepted yet, so its routes aren't the thing to fix first.
  // The inline operation owns progress while running. Keep the saved failure
  // until the server refreshes it, but don't display that stale result mid-repair.
  if (retrying || !projectData?.routingUnsynced || projectData.awaitingDecision) return null;

  return (
    <WarningCallout
      title={t.projects.routingRetry.title}
      description={projectData?.routingWarning || t.projects.routingRetry.description}
      actions={
        <button
          type="button"
          onClick={onRetry}
          className="rounded-lg bg-warning-solid px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-warning-solid/90 disabled:opacity-60"
        >
          {t.projects.routingRetry.retry}
        </button>
      }
    />
  );
};

export default RoutingUnsyncedCallout;
