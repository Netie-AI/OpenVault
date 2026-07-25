"""Public exports for ``openmw.openvault.route``."""

from openmw.openvault.route.breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    get_all_breaker_statuses,
    get_circuit_breaker,
    is_tripping_status,
    reset_all_circuit_breakers,
    reset_circuit_breaker,
)
from openmw.openvault.route.fallback_signals import (
    ACCOUNT_DEACTIVATED_SIGNALS,
    CONTEXT_OVERFLOW_PATTERNS,
    CREDITS_EXHAUSTED_SIGNALS,
    OAUTH_INVALID_TOKEN_SIGNALS,
    RATE_LIMIT_TEXT_PATTERNS,
    check_fallback_error,
    parse_reset_from_headers,
    parse_retry_from_error_text,
    parse_upstream_retry_hint_ms,
)
from openmw.openvault.route.key_rotator import get_valid_api_key, record_key_failure, record_key_success
from openmw.openvault.route.registry import get_route_state, record_target_result, reset_route_state, set_strategy, set_targets
from openmw.openvault.route.rr_state import get_next_from_deck, reset_decks
from openmw.openvault.route.semaphore import SEMAPHORE_QUEUE_FULL, acquire as acquire_semaphore
from openmw.openvault.route.sorters import order_by_p2c, select_weighted_target, sort_by_cost, sort_by_usage
from openmw.openvault.route.strategies import apply_strategy, normalize_strategy
from openmw.openvault.route.types import STRATEGY_NAMES, ComboMetrics, RouteTarget, StrategyName
from openmw.openvault.route.window_limiter import AcquireResult, RateLimitWindow, SlidingWindowLimiter

__all__ = [
    "ACCOUNT_DEACTIVATED_SIGNALS",
    "CONTEXT_OVERFLOW_PATTERNS",
    "CREDITS_EXHAUSTED_SIGNALS",
    "OAUTH_INVALID_TOKEN_SIGNALS",
    "RATE_LIMIT_TEXT_PATTERNS",
    "SEMAPHORE_QUEUE_FULL",
    "STRATEGY_NAMES",
    "AcquireResult",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "ComboMetrics",
    "RateLimitWindow",
    "RouteTarget",
    "SlidingWindowLimiter",
    "StrategyName",
    "apply_strategy",
    "check_fallback_error",
    "get_all_breaker_statuses",
    "get_circuit_breaker",
    "get_next_from_deck",
    "get_route_state",
    "get_valid_api_key",
    "is_tripping_status",
    "normalize_strategy",
    "order_by_p2c",
    "parse_reset_from_headers",
    "parse_retry_from_error_text",
    "parse_upstream_retry_hint_ms",
    "record_key_failure",
    "record_key_success",
    "record_target_result",
    "reset_all_circuit_breakers",
    "reset_circuit_breaker",
    "reset_decks",
    "reset_route_state",
    "acquire_semaphore",
    "select_weighted_target",
    "set_strategy",
    "set_targets",
    "sort_by_cost",
    "sort_by_usage",
]
