"""P6 example — open TextEdit and read back its window title via a sensor."""

from case_sdk import Action, Sensor


def procedure(params):
    yield Action("open_app", name="TextEdit")
    yield Action("wait", ms=800)
    obs = yield Sensor("window_title")
    return {"success": True, "title": obs.get("value", "")}
