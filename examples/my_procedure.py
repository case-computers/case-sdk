"""Simplest possible local procedure — opens TextEdit. Used in P2 verify."""

from case_sdk import Action


def procedure(params):
    yield Action("open_app", name="TextEdit")
    return {"success": True}
