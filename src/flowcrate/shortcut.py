"""Generate and sign the "Play Flow Crate" Apple Shortcut."""

import os
import plistlib
import subprocess
import tempfile
import uuid


class ShortcutError(RuntimeError):
    pass


def _text(value):
    return {"Value": {"string": value}, "WFSerializationType": "WFTextTokenString"}


def build_workflow(url, token):
    # Actions don't chain implicitly in generated files: downstream parameters
    # must reference the upstream action's output by UUID (the "magic variable"
    # wiring the Shortcuts editor normally adds for you).
    dict_value_uuid = str(uuid.uuid4()).upper()
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
                    "UUID": dict_value_uuid,
                },
            },
            # Show Result both speaks the text (when run via Siri) and shows
            # it in the Siri notification.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
                "WFWorkflowActionParameters": {
                    "Text": {
                        "Value": {
                            "attachmentsByRange": {
                                "{0, 1}": {
                                    "OutputName": "Dictionary Value",
                                    "OutputUUID": dict_value_uuid,
                                    "Type": "ActionOutput",
                                }
                            },
                            "string": "\ufffc",  # object-replacement char the attachment replaces
                        },
                        "WFSerializationType": "WFTextTokenString",
                    },
                },
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
