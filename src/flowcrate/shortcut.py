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


def _action_output(output_name, output_uuid):
    """Reference another action's output in a non-text parameter (e.g. WFInput)."""
    return {
        "Value": {
            "OutputName": output_name,
            "OutputUUID": output_uuid,
            "Type": "ActionOutput",
        },
        "WFSerializationType": "WFTextTokenAttachment",
    }


def _text_with_output(output_name, output_uuid):
    """Reference another action's output inside a text parameter."""
    return {
        "Value": {
            "attachmentsByRange": {
                "{0, 1}": {
                    "Aggrandizements": [],
                    "OutputName": output_name,
                    "OutputUUID": output_uuid,
                    "Type": "ActionOutput",
                }
            },
            "string": "￼",  # object-replacement char the attachment replaces
        },
        "WFSerializationType": "WFTextTokenString",
    }


def build_workflow(url, token):
    # Actions don't chain implicitly in generated files: downstream parameters
    # must reference the upstream action's output by UUID (the "magic variable"
    # wiring the Shortcuts editor normally adds for you).
    download_uuid = str(uuid.uuid4()).upper()
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
        "WFQuickActionSurfaces": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                "WFWorkflowActionParameters": {
                    "UUID": download_uuid,
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
                    "WFInput": _action_output("Contents of URL", download_uuid),
                },
            },
            # Speak Text always reads the summary aloud; Siri's own reading of
            # Show Result only happens in hands-free contexts.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
                "WFWorkflowActionParameters": {
                    "WFText": _text_with_output("Dictionary Value", dict_value_uuid),
                },
            },
            # Show Result displays the text in the Siri card / an alert.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
                "WFWorkflowActionParameters": {
                    "Text": _text_with_output("Dictionary Value", dict_value_uuid),
                },
            },
            # Stop and Output makes the text retrievable from the command line
            # (shortcuts run "Play Flow Crate" --output-path \u2026) for debugging.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.output",
                "WFWorkflowActionParameters": {
                    "WFOutput": _text_with_output("Dictionary Value", dict_value_uuid),
                    "WFNoOutputSurfaceBehavior": "Do Nothing",
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
