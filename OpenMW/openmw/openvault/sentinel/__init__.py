"""Real NVMe telemetry for the OpenVault console, over the in-repo nvme_sentinel engine.

Every payload produced here carries ``source`` ("live" or "mock") and, when the
adapter ladder fell back, a human-readable ``degraded_reason``. Mock data is only
ever produced when it is explicitly asked for — there is no silent fallback,
because a console that dresses fixtures as hardware is worse than one that says
"I could not read this drive".
"""
