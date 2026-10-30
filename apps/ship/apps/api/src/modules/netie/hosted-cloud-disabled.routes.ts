// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
/**
 * Stand-in for every Openship Cloud (Oblien-hosted) route this fork does not
 * ship. FreeBuild is self-hosted only (see PRODUCT_ROLES.md): there is no
 * "connect this instance to the cloud" bridge, no cloud-proxied billing, and
 * no cloud-only GitHub App auth. Every path under here answers the same
 * `hosted_cloud_disabled` 501 instead of a bare 404, so a client (or the
 * dashboard) gets a machine-readable reason rather than "route not found".
 *
 * Mounted at `/api/cloud` in place of Openship's `cloud-saas.routes` +
 * `cloud-local.routes`, and layered under `billingPlansRoutes` at
 * `/api/billing` in place of `billing-saas.routes` / `billing-local.routes`
 * (GET /api/billing/plans still answers — reading the "coming soon" plan
 * catalog is not a hosted-cloud feature).
 */
import { Hono } from "hono";
import { HostedCloudDisabledError } from "@repo/core";

export const hostedCloudDisabledRoutes = new Hono();

hostedCloudDisabledRoutes.all("*", () => {
  throw new HostedCloudDisabledError();
});
