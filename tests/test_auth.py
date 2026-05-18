"""Bearer auth tests against the mock server.

We bypass RemoteDriver here because RemoteDriver always sends a key —
which means it can't reproduce the no-header case. Drop down to raw
`websockets.connect` so each scenario is exact.
"""

from __future__ import annotations

import asyncio

import pytest
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidStatus, WebSocketException

from tests._mock_server_fixture import mock_server


async def _try_connect(url: str, headers: dict | None) -> bool:
    """Return True if the WS upgrade succeeded; False if it was rejected."""
    try:
        kwargs: dict = {"open_timeout": 5}
        if headers is not None:
            kwargs["additional_headers"] = headers
        async with ws_connect(url, **kwargs) as ws:
            # Send something tiny to confirm the channel is live.
            await ws.send('{"type":"ping"}')
            return True
    except (InvalidStatus, WebSocketException, OSError):
        return False


def test_missing_authorization_header_rejected():
    with mock_server() as srv:
        url = f"{srv['url']}/ws"
        ok = asyncio.run(_try_connect(url, headers=None))
    assert ok is False


def test_wrong_bearer_token_rejected():
    with mock_server() as srv:
        url = f"{srv['url']}/ws"
        ok = asyncio.run(
            _try_connect(url, headers={"Authorization": "Bearer not-the-key"})
        )
    assert ok is False


def test_correct_bearer_token_accepted():
    with mock_server() as srv:
        url = f"{srv['url']}/ws"
        ok = asyncio.run(
            _try_connect(url, headers={"Authorization": "Bearer dev-key"})
        )
    assert ok is True


def test_remote_driver_with_wrong_key_raises_procedure_error():
    """Plumb the auth failure all the way through RemoteDriver."""
    from case_sdk.drivers.remote import RemoteDriver
    from case_sdk.protocol import ProcedureError
    from tests._mock_server_fixture import fake_harness_recording

    with mock_server() as srv:
        harness, _ = fake_harness_recording()
        driver = RemoteDriver(
            harness,
            api_base=str(srv["url"]),
            api_key="totally-wrong",
            max_reconnects=0,
        )
        with pytest.raises(ProcedureError):
            driver.execute("save_textedit", {})
