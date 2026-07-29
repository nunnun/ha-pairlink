"""Tests for secret redaction."""

from __future__ import annotations

import json
from types import SimpleNamespace

from custom_components.pairlink.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.pairlink.models import SessionDiagnostics

from .test_models import ENTRY_DATA


async def test_diagnostics_contains_no_stable_identifier(hass) -> None:
    """Every sensitive persisted value must be absent after JSON encoding."""
    entry = SimpleNamespace(
        data=ENTRY_DATA,
        runtime_data=SimpleNamespace(diagnostics=SessionDiagnostics()),
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    rendered = json.dumps(diagnostics)
    for secret in (
        ENTRY_DATA["address"],
        ENTRY_DATA["remote_id"],
        ENTRY_DATA["home_id"],
        ENTRY_DATA["password"],
        ENTRY_DATA["light_id"],
    ):
        assert secret not in rendered
    assert diagnostics["entry"]["remote_channel"] == 1
