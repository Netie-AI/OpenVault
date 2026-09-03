/**
 * Trusted headers stamped by the authz pipeline.
 *
 * Client-supplied values are stripped before any policy runs so a remote
 * caller cannot forge locality or route-class verdicts.
 */

export const AUTHZ_HEADER_REQUEST_ID = "x-request-id";

export const AUTHZ_HEADER_ROUTE_CLASS = "x-openvault-route-class";

/**
 * Trusted locality verdict ("loopback" | "remote") derived from the request
 * peer address. Route handlers should read this instead of Host.
 */
export const AUTHZ_HEADER_PEER_LOCALITY = "x-openvault-peer-locality";

/** Headers that must never be trusted from incoming client requests. */
export const AUTHZ_TRUSTED_HEADERS: ReadonlyArray<string> = [
  AUTHZ_HEADER_ROUTE_CLASS,
  AUTHZ_HEADER_PEER_LOCALITY,
];
