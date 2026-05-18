"""P4 example — open TextEdit, type, trigger save.

Exercises ``open_app``, ``wait``, ``type``, and ``hotkey``. The save dialog
should appear; we don't fill it in here.
"""

from case_sdk import Action


def procedure(params):
    yield Action("open_app", name="TextEdit")
    yield Action("wait", ms=500)
    yield Action("type", text="hi from case-sdk")
    yield Action("hotkey", combo=["cmd", "s"])
    return {"success": True}
