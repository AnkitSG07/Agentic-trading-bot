from datetime import date, datetime
from decimal import Decimal


def make_json_serializable(value):
    """Convert nested objects into JSON-serializable values for JSONB columns."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: make_json_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_serializable(v) for v in value]
    return value
