"""Auto-detect champion names per role from League's Live Client Data API.

While a match is in progress, the League client exposes a local read-only
endpoint at https://127.0.0.1:2999/liveclientdata/allgamedata that lists every
player, their champion, team and assigned position. We poll it in a background
thread and expose a {role: champion} mapping the overlay can read each tick.

The endpoint uses a self-signed Riot certificate, so TLS verification is
disabled — this is safe because the connection is strictly local (loopback).
"""

from __future__ import annotations

import threading
import time

try:
    import requests
    import urllib3

    # Riot serves a self-signed cert on loopback; silence the expected warning.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:  # pragma: no cover - handled by requirements.txt
    requests = None  # type: ignore[assignment]


LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

# League position codes -> the role labels this overlay uses.
POSITION_MAP = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "Bot",
    "UTILITY": "Support",
}


def _player_name(player: dict) -> str:
    """Best-effort display name across old/new client payloads."""
    return player.get("riotIdGameName") or player.get("summonerName") or ""


def fetch_roles(track: str = "enemy", timeout: float = 1.0) -> dict[str, str] | None:
    """Return {role: champion} for the requested team, or None if unavailable.

    `track` is "enemy" (default) or "ally". Players without a recognised
    position are skipped.
    """
    if requests is None:
        return None
    try:
        response = requests.get(LIVE_URL, verify=False, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None  # not in a game, API not up yet, or transient error

    players = data.get("allPlayers") or []
    if not players:
        return None

    # Work out the local player's team so we can pick allies vs enemies.
    me = _player_name(data.get("activePlayer", {}))
    my_team = None
    for player in players:
        if me and _player_name(player) == me:
            my_team = player.get("team")
            break

    if my_team is None:
        # Spectating or name mismatch: assume we want CHAOS as "enemy".
        target_team = "CHAOS" if track == "enemy" else "ORDER"
    else:
        other = "CHAOS" if my_team == "ORDER" else "ORDER"
        target_team = other if track == "enemy" else my_team

    roles: dict[str, str] = {}
    for player in players:
        if player.get("team") != target_team:
            continue
        role = POSITION_MAP.get((player.get("position") or "").upper())
        champion = player.get("championName")
        if role and champion:
            roles[role] = champion
    return roles or None


class ChampionWatcher(threading.Thread):
    """Background poller that keeps the latest {role: champion} mapping."""

    def __init__(self, track: str = "enemy", interval: float = 5.0) -> None:
        super().__init__(daemon=True)
        self._track = track
        self._interval = interval
        self._lock = threading.Lock()
        self._roles: dict[str, str] = {}
        self._stop = threading.Event()

    @property
    def roles(self) -> dict[str, str]:
        """Thread-safe snapshot of the most recent mapping (may be empty)."""
        with self._lock:
            return dict(self._roles)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            result = fetch_roles(self._track)
            if result:
                with self._lock:
                    self._roles = result
            # Sleep in small slices so stop() is responsive.
            self._stop.wait(self._interval)
