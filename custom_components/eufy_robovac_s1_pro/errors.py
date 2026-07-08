"""Error-code → human-readable message mapping for the Eufy RoboVac S1 Pro.

Ported from damacus/robovac (custom_components/robovac/errors.py). Only the
device-reported fault codes (DPS 106) are kept here — integration/setup-level
strings from the source are intentionally omitted.

Keys may be ``int`` (numeric faults) or ``str`` (sensor "Sx" codes and named
faults); ``get_error_message`` looks up both forms so a raw DPS value like
"8" or "Wheel_stuck" resolves regardless of type.
"""

from __future__ import annotations

from typing import Any

ERROR_MESSAGES: dict[Any, str] = {
    "no_error": "None",
    1: "Front bumper stuck",
    2: "Wheel stuck",
    3: "Side brush",
    4: "Rolling brush bar stuck",
    5: "Device trapped",
    6: "Device trapped",
    7: "Wheel suspended",
    8: "Low battery",
    9: "Magnetic boundary",
    12: "Right wall sensor",
    13: "Device tilted",
    14: "Insert dust collector",
    17: "Restricted area detected",
    18: "Laser cover stuck",
    19: "Laser sensor stuck",
    20: "Laser sensor blocked",
    21: "Base blocked",
    "S1": "Battery",
    "S2": "Wheel Module",
    "S3": "Side Brush",
    "S4": "Suction Fan",
    "S5": "Rolling Brush",
    "S8": "Path Tracking Sensor",
    "Wheel_stuck": "Wheel stuck",
    "R_brush_stuck": "Rolling brush stuck",
    "Crash_bar_stuck": "Front bumper stuck",
    "sensor_dirty": "Sensor dirty",
    "N_enough_pow": "Low battery",
    "Stuck_5_min": "Device trapped",
    "Fan_stuck": "Fan stuck",
    "S_brush_stuck": "Side brush stuck",
}

# Per-code actionable guidance, surfaced as vacuum attributes only while an
# error is active. Keyed by the numeric fault code.
TROUBLESHOOTING_CONTEXT: dict[int, dict[str, list[str]]] = {
    1: {
        "troubleshooting": [
            "Check front bumper for obstructions",
            "Clean bumper sensors",
            "Ensure bumper moves freely",
        ],
        "common_causes": [
            "Hair or debris blocking bumper",
            "Damaged bumper spring",
            "Sensor misalignment",
        ],
    },
    2: {
        "troubleshooting": [
            "Check wheels for obstructions",
            "Clean wheel sensors",
            "Ensure wheels rotate freely",
        ],
        "common_causes": [
            "Hair wrapped around wheel",
            "Debris in wheel mechanism",
            "Damaged wheel motor",
        ],
    },
    8: {
        "troubleshooting": [
            "Charge the vacuum fully",
            "Check charging contacts for dirt",
            "Ensure dock is properly positioned",
        ],
        "common_causes": [
            "Battery depleted",
            "Poor charging connection",
            "Faulty charging dock",
        ],
    },
    19: {
        "troubleshooting": [
            "Remove any stickers or tape from laser sensor",
            "Clean laser sensor cover",
            "Check for physical damage to sensor",
            "Restart vacuum",
        ],
        "common_causes": [
            "Protective film not removed",
            "Dust or debris on sensor",
            "Physical damage to sensor cover",
        ],
    },
}


def _candidates(code: Any):
    """Yield the raw code and its int form (if convertible) for lookup."""
    yield code
    try:
        yield int(code)
    except (ValueError, TypeError):
        pass


def get_error_message(code: Any) -> str:
    """Return the human-readable message for a DPS 106 code, else str(code)."""
    for key in _candidates(code):
        if key in ERROR_MESSAGES:
            return ERROR_MESSAGES[key]
    return str(code)


def get_error_context(code: Any) -> dict[str, list[str]]:
    """Return troubleshooting steps / common causes for a code, if known."""
    for key in _candidates(code):
        if isinstance(key, int) and key in TROUBLESHOOTING_CONTEXT:
            return TROUBLESHOOTING_CONTEXT[key]
    return {}
