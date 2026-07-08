"""Generate and sign the "Play Flow Crate" Apple Shortcut."""

import os
import plistlib
import subprocess
import tempfile


class ShortcutError(RuntimeError):
    pass


def _text(value):
    return {"Value": {"string": value}, "WFSerializationType": "WFTextTokenString"}


def build_workflow(url, token):
    return {
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowClientVersion": "2607.1.3",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4274264319,
            "WFWorkflowIconGlyphNumber": 59511,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                "WFWorkflowActionParameters": {
                    "WFURL": url,
                    "WFHTTPMethod": "POST",
                    "WFHTTPHeaders": {
                        "Value": {
                            "WFDictionaryFieldValueItems": [
                                {
                                    "WFItemType": 0,
                                    "WFKey": _text("X-FlowCrate-Token"),
                                    "WFValue": _text(token),
                                }
                            ]
                        },
                        "WFSerializationType": "WFDictionaryFieldValue",
                    },
                    "WFHTTPBodyType": "JSON",
                    "WFJSONValues": {
                        "Value": {"WFDictionaryFieldValueItems": []},
                        "WFSerializationType": "WFDictionaryFieldValue",
                    },
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
                "WFWorkflowActionParameters": {
                    "WFGetDictionaryValueType": "Value",
                    "WFDictionaryKey": "speak",
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
                "WFWorkflowActionParameters": {},
            },
        ],
    }


def signed_shortcut(url, token):
    """Build, sign, and return the .shortcut file bytes for the Siri shortcut."""
    workflow = build_workflow(url, token)
    with tempfile.TemporaryDirectory() as tmp:
        # The sign command rejects inputs without a .shortcut extension.
        unsigned_path = os.path.join(tmp, "unsigned.shortcut")
        signed_path = os.path.join(tmp, "Play Flow Crate.shortcut")
        with open(unsigned_path, "wb") as fh:
            plistlib.dump(workflow, fh)
        try:
            result = subprocess.run(
                [
                    "shortcuts",
                    "sign",
                    "--mode",
                    "anyone",
                    "--input",
                    unsigned_path,
                    "--output",
                    signed_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            raise ShortcutError(
                "The 'shortcuts' command-line tool is not available on this system."
            )
        except subprocess.TimeoutExpired:
            raise ShortcutError("Signing the shortcut timed out.")

        # The sign command emits harmless "ERROR: Unrecognized attribute string
        # flag" lines on stderr even on success, so judge success only by the
        # return code and the presence of the output file.
        if result.returncode != 0 or not os.path.exists(signed_path):
            detail = ""
            if result.stderr:
                lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
                if lines:
                    detail = f" {lines[-1].strip()}"
            raise ShortcutError(f"Signing the shortcut failed.{detail}")

        with open(signed_path, "rb") as fh:
            return fh.read()
