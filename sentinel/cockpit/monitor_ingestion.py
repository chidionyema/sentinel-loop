"""Subsystem 4: Monitoring Alert Ingestion.

Multi-source alert parsing (Sentry, Better Stack, Logtail, Datadog),
state override engine, and emergency button generation.

H6: service names in callback_data are sanitized via
``sanitize_callback_token``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
#  AlertData dataclass
# ---------------------------------------------------------------------------


@dataclass
class AlertData:
    """Normalized alert data from any monitoring source."""
    service: str
    message: str
    severity: str          # "critical", "warning", "info"
    source: str             # "sentry", "betterstack", "logtail", "datadog", "generic"
    stack_trace: str | None = None
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
#  Source-specific parsers
# ---------------------------------------------------------------------------


def _parse_sentry(payload: dict) -> AlertData:
    event = payload.get("event", {}) or {}
    title = event.get("title", "Unknown error")
    culprit = event.get("culprit", "unknown")
    service = culprit.split(".")[0] if "." in culprit else culprit

    # Extract stack trace if available
    stack_trace = None
    entries = event.get("entries", [])
    for entry in entries:
        if entry.get("type") == "exception":
            values = entry.get("data", {}).get("values", [])
            if values:
                stack = values[0].get("stacktrace", {})
                frames = stack.get("frames", [])
                if frames:
                    trace_lines = []
                    for frame in frames[-10:]:  # Last 10 frames
                        filename = frame.get("filename", "?")
                        line_no = frame.get("lineNo", "?")
                        func = frame.get("function", "?")
                        trace_lines.append(f"  File \"{filename}\", line {line_no}, in {func}")
                    if trace_lines:
                        stack_trace = "Traceback:\n" + "\n".join(trace_lines)

    level = payload.get("level", "error")
    severity = "critical" if level == "fatal" else ("warning" if level == "warning" else "info")

    return AlertData(
        service=service,
        message=title,
        severity=severity,
        source="sentry",
        stack_trace=stack_trace,
        raw=payload,
    )


def _parse_datadog(payload: dict) -> AlertData:
    title = payload.get("title", "Unknown alert")
    body = payload.get("body", "")
    alert_type = payload.get("alert_type", "info")

    # Extract service from tags
    service = "unknown"
    tags = payload.get("tags", [])
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("service:"):
            service = tag[len("service:"):]
            break

    # Clean body text
    message = body.replace("{{#is_alert}}", "").replace("{{/is_alert}}", "").strip()

    severity = "critical" if alert_type == "error" else ("warning" if alert_type == "warning" else "info")

    return AlertData(
        service=service,
        message=title if not message else message,
        severity=severity,
        source="datadog",
        stack_trace=None,
        raw=payload,
    )


def _parse_betterstack(payload: dict) -> AlertData:
    return AlertData(
        service=payload.get("service", payload.get("monitor_name", "unknown")),
        message=payload.get("title", payload.get("message", "Unknown alert")),
        severity=payload.get("severity", "warning"),
        source="betterstack",
        stack_trace=payload.get("stack_trace"),
        raw=payload,
    )


def _parse_logtail(payload: dict) -> AlertData:
    return AlertData(
        service=payload.get("source", payload.get("service", "unknown")),
        message=payload.get("message", "Log alert"),
        severity=payload.get("level", "info"),
        source="logtail",
        stack_trace=None,
        raw=payload,
    )


def _parse_generic(payload: dict, source: str = "generic") -> AlertData:
    return AlertData(
        service=payload.get("service", "unknown"),
        message=payload.get("message", payload.get("title", "Unknown alert")),
        severity=payload.get("severity", "warning"),
        source=source,
        stack_trace=payload.get("stack_trace"),
        raw=payload,
    )


_PARSERS = {
    "sentry": _parse_sentry,
    "datadog": _parse_datadog,
    "betterstack": _parse_betterstack,
    "logtail": _parse_logtail,
    "generic": _parse_generic,
}


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------


def parse_alert(payload: dict, source: str) -> AlertData:
    """Parse a monitoring webhook payload into a normalized AlertData.

    Handles sentry, datadog, betterstack, logtail, and unknown sources
    gracefully without crashing.
    """
    source_lower = source.lower()
    parser = _PARSERS.get(source_lower)
    if parser is None:
        return _parse_generic(payload, source=source)
    try:
        return parser(payload)
    except Exception:
        # Graceful degradation on parse failure
        return AlertData(
            service=payload.get("service", "unknown"),
            message=payload.get("message", payload.get("title", "Parse error")),
            severity="warning",
            source=source,
            stack_trace=None,
            raw=payload,
        )


def should_override(alert: AlertData, current_state: dict) -> bool:
    """Determine if this alert should override the current chat state.

    H7: severity is validated against a closed set BEFORE any decision.
    An attacker-supplied value like ``"critical"`` from an untrusted
    webhook body is only trusted if it matches a known label exactly.
    Unknown severities are downgraded to ``"info"`` and can never
    trigger an override.
    """
    VALID_SEVERITIES = {"critical", "warning", "info"}
    severity = alert.severity if alert.severity in VALID_SEVERITIES else "info"

    if severity != "critical":
        return False

    current_alert_severity = current_state.get("alert_active")
    if current_alert_severity == "critical":
        return False

    return True


def build_emergency_buttons(alert: AlertData) -> list[list[dict]]:
    """Build inline keyboard rows with emergency action buttons.

    Returns:
        [[{"text": "Restart", "callback_data": "restart:svc:"}, ...],
         [{"text": "Rollback", "callback_data": "rollback:svc:latest"}, ...],
         [{"text": "Mute 30m", "callback_data": "mute:svc:30"}]]
    """
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    svc = sanitize_callback_token(alert.service)

    return [
        [
            {
                "text": "🔄 Restart Container",
                "callback_data": f"restart:{svc}:container",
            },
            {
                "text": "⏪ Rollback",
                "callback_data": f"rollback:{svc}:latest",
            },
        ],
        [
            {
                "text": "🔇 Mute 30m",
                "callback_data": f"mute:{svc}:30",
            },
        ],
    ]


def format_critical_alert(alert: AlertData) -> str:
    """Format a critical alert as a Telegram message with truncated stack trace.

    Stack trace is truncated to 500 characters.
    """
    lines = [
        f"🚨 **CRITICAL: {alert.service}**",
        f"Source: {alert.source}",
        "",
        alert.message,
    ]

    if alert.stack_trace:
        truncated = alert.stack_trace[:500]
        if len(alert.stack_trace) > 500:
            truncated += "\n..."
        lines.append("")
        lines.append("```")
        lines.append(truncated)
        lines.append("```")

    return "\n".join(lines)
