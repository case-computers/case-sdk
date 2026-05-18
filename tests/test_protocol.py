"""Round-trip and shape tests for the wire protocol."""

from case_sdk import protocol
from case_sdk.protocol import Action, Envelope, Sensor


def test_envelope_roundtrip():
    env = protocol.action_msg("click", {"x": 10, "y": 20}, session_id="abc", step_id=1)
    s = env.to_json()
    parsed = Envelope.from_json(s)
    assert parsed.type == "action"
    assert parsed.session_id == "abc"
    assert parsed.step_id == 1
    assert parsed.payload == {"name": "click", "args": {"x": 10, "y": 20}}
    assert parsed.v == 1


def test_envelope_defaults_when_fields_missing():
    parsed = Envelope.from_json('{"type": "ping"}')
    assert parsed.type == "ping"
    assert parsed.session_id == ""
    assert parsed.step_id == 0
    assert parsed.payload == {}
    assert parsed.v == 1


def test_action_yieldable():
    a = Action("click", x=10, y=20)
    assert a.name == "click"
    assert a.args == {"x": 10, "y": 20}


def test_sensor_yieldable():
    s = Sensor("window_title")
    assert s.name == "window_title"
    assert s.args == {}


def test_sensor_with_args():
    s = Sensor("ax_value", element_id="btn1")
    assert s.args == {"element_id": "btn1"}


def test_hello_builder_auto_session_id():
    env = protocol.hello("save_textedit", {"text": "hi"})
    assert env.type == "hello"
    assert env.payload == {"procedure": "save_textedit", "params": {"text": "hi"}}
    assert env.session_id  # auto-generated UUID


def test_hello_builder_explicit_session_id():
    env = protocol.hello("proc", session_id="fixed-id")
    assert env.session_id == "fixed-id"


def test_result_ok_omits_error_key():
    env = protocol.result_msg(ok=True, observation={"x": 1})
    assert env.payload["ok"] is True
    assert env.payload["observation"] == {"x": 1}
    assert "error" not in env.payload


def test_result_with_error_includes_error_key():
    env = protocol.result_msg(ok=False, error="bad coords")
    assert env.payload["ok"] is False
    assert env.payload["error"] == "bad coords"


def test_done_builder():
    env = protocol.done_msg(success=True, reason="all good", result={"count": 3})
    assert env.payload == {"success": True, "reason": "all good", "result": {"count": 3}}


def test_error_builder():
    env = protocol.error_msg("E_TIMEOUT", "step timed out", {"step_id": 5})
    assert env.payload == {
        "code": "E_TIMEOUT",
        "message": "step timed out",
        "details": {"step_id": 5},
    }


def test_ping_pong():
    p = protocol.ping_msg(session_id="s1")
    q = protocol.pong_msg(session_id="s1")
    assert p.type == "ping"
    assert q.type == "pong"
    assert p.session_id == q.session_id == "s1"


def test_full_session_message_sequence_roundtrips():
    """Smoke: serialize and parse one of every type without exception."""
    msgs = [
        protocol.hello("save_textedit", session_id="sess"),
        protocol.action_msg("open_app", {"name": "TextEdit"}, session_id="sess", step_id=1),
        protocol.result_msg(ok=True, session_id="sess", step_id=1),
        protocol.sensor_msg("window_title", session_id="sess", step_id=2),
        protocol.result_msg(ok=True, observation={"value": "TextEdit"}, session_id="sess", step_id=2),
        protocol.ping_msg(session_id="sess"),
        protocol.pong_msg(session_id="sess"),
        protocol.error_msg("E_GENERIC", "oops", session_id="sess", step_id=3),
        protocol.done_msg(success=True, reason="ok", session_id="sess"),
    ]
    for m in msgs:
        parsed = Envelope.from_json(m.to_json())
        assert parsed.type == m.type
        assert parsed.payload == m.payload
        assert parsed.session_id == m.session_id
        assert parsed.step_id == m.step_id
