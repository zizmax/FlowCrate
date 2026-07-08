"""Direct Sonos speaker control via SoCo for Flow Crate.

SSDP discovery is unreliable on some networks, so a direct speaker IP is
preferred (see SONOS_IP in Settings). All playback targets the group
coordinator so grouped speakers behave predictably.
"""
import errno
import logging

import soco
from soco.discovery import scan_network
from soco.plugins.sharelink import ShareLinkPlugin


class SonosError(RuntimeError):
    """User-facing Sonos failure with a friendly, actionable message."""


_LOCAL_NETWORK_HINT = (
    "No Sonos speakers were reachable. Make sure you are on the same Wi-Fi as the "
    "speakers, and check macOS System Settings -> Privacy & Security -> Local Network "
    "so Flow Crate is allowed to reach them. You can also set a direct SONOS_IP in Settings."
)


def _is_host_unreachable(exc):
    """True when an error looks like the macOS Local Network permission blocking access."""
    if getattr(exc, "errno", None) == errno.EHOSTUNREACH:  # errno 65
        return True
    for arg in getattr(exc, "args", []):
        if getattr(arg, "errno", None) == errno.EHOSTUNREACH:
            return True
    return False


def get_speaker(ip=None, room=None, timeout=5):
    """Return the group coordinator SoCo device to play on.

    If ``ip`` is given, connect directly (skipping discovery). Otherwise discover
    speakers and, if ``room`` is set, match its player name case-insensitively.
    """
    if ip:
        try:
            speaker = soco.SoCo(ip)
            # Touch a property so an unreachable IP fails here with a clear message.
            coordinator = speaker.group.coordinator
        except Exception as exc:
            if _is_host_unreachable(exc):
                raise SonosError(_LOCAL_NETWORK_HINT) from exc
            raise SonosError(f"Could not reach a Sonos speaker at {ip}: {exc}") from exc
        return coordinator

    try:
        zones = soco.discover(timeout=timeout) or scan_network(scan_timeout=2.0) or set()
    except Exception as exc:
        if _is_host_unreachable(exc):
            raise SonosError(_LOCAL_NETWORK_HINT) from exc
        raise SonosError(f"Sonos discovery failed: {exc}") from exc

    zones = sorted(zones, key=lambda z: z.player_name)
    if not zones:
        raise SonosError(_LOCAL_NETWORK_HINT)

    if not room:
        return zones[0].group.coordinator

    matches = [z for z in zones if z.player_name.lower() == room.lower()]
    if not matches:
        found = ", ".join(z.player_name for z in zones)
        raise SonosError(f"No Sonos speaker named '{room}'. Found: {found}.")
    return matches[0].group.coordinator


def queue_and_play(speaker, uris, play=True):
    """Append each Spotify URI to the speaker's queue and optionally start playback."""
    sharelink = ShareLinkPlugin(speaker)
    first_position = None
    queued = 0
    for uri in uris:
        position = sharelink.add_share_link_to_queue(uri)  # 1-based queue position
        if first_position is None:
            first_position = position
        queued += 1
        logging.info("Queued %s at Sonos position %s", uri, position)

    if queued == 0:
        raise SonosError("No Spotify tracks to queue on Sonos.")

    if play:
        speaker.play_from_queue(first_position - 1)  # play_from_queue is 0-based

    return {
        "queued": queued,
        "first_position": first_position,
        "room": speaker.player_name,
    }
