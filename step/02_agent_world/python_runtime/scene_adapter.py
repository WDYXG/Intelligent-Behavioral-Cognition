"""Scene contract and local plugin loader. Loading a scene never dispatches an API.

Implementations own all domain rules and visibility. Core state metadata is only
round/version; everything else in World is scene-owned. Observation.content uses
scene, self, events, action_result, decision_phase and event_cursors. Events have
event_id, monotonically increasing world_sequence, round, phase, visibility,
epistemic_status and payload. All new authorized events must be delivered even
when older history is windowed. Scene state metadata is round/version; the
adapter maintains its own event sequence. An utterance is
an observed act of speaking, never verification of the proposition it contains.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Protocol

from common import require


class SceneAdapter(Protocol):
    aliases: list[str]
    state: dict

    def __init__(self, aliases: list[str], config: dict): ...
    @classmethod
    def validate_config(cls, config: dict) -> None: ...
    @classmethod
    def phase_names(cls, config: dict) -> list[str]: ...
    def phases(self) -> list[str]: ...
    def snapshot(self) -> dict: ...
    def begin_round(self, number: int) -> dict: ...
    def describe(self, alias: str) -> dict: ...
    def capabilities(self, alias: str) -> dict: ...
    def observe(self, alias: str, phase: str, feedback: dict | None = None,
                since: dict | None = None) -> dict: ...
    def validate_phase(self, phase: str) -> None: ...
    def interpret_open_action(self, request: dict, before: dict) -> dict: ...
    def check_preconditions(self, state: dict, request: dict, before: dict) -> None: ...
    def execute_action(self, state: dict, request: dict, before: dict) -> tuple[dict, list[str]]: ...
    def settle_phase(self, state: dict, phase: str) -> list[dict]: ...
    def assert_invariants(self, state: dict) -> None: ...
    def collect_world_events(self, before: dict, after: dict, phase: str) -> list[dict]: ...
    def rejection_options(self, request: dict, before: dict, reason: str) -> list[str]: ...
    def termination(self) -> dict: ...
    def metrics(self, transaction: dict) -> dict: ...


def load_scene_class(config: dict):
    """Explicit local Python module:Class; configuration is trusted executable config.

    A new scene requires a module beside this file and a config change, not a
    registry edit. Keeping plugins local also includes them in code_snapshot.
    """
    default = ("scene_community:LivingCommunityScene" if config.get("kernel") == "community_daily_life_v1"
               else "scene_community:LegacyCommunityScene")
    if "scene" not in config:
        require(config.get("kernel") in ("community_daily_life_v1", "community_projects_v1"), "Unknown legacy kernel; specify scene.adapter")
    entry = config.get("scene", {}).get("adapter", default)
    require(isinstance(entry, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*:[A-Za-z_][A-Za-z_0-9]*", entry) is not None,
            "scene.adapter must be local_module:Class")
    module, name = entry.split(":")
    require((Path(__file__).parent / (module + ".py")).is_file(), "Scene module must be in python_runtime for source snapshotting")
    loaded = importlib.import_module(module)
    require(Path(loaded.__file__).resolve() == (Path(__file__).parent / (module + ".py")).resolve(), "Scene module resolved outside snapshotted runtime")
    cls = getattr(loaded, name, None)
    methods = ("validate_config", "phase_names", "phases", "snapshot", "begin_round", "describe", "capabilities",
               "observe", "validate_phase", "interpret_open_action", "check_preconditions", "execute_action",
               "settle_phase", "assert_invariants", "collect_world_events", "rejection_options", "termination", "metrics")
    require(isinstance(cls, type) and all(callable(getattr(cls, m, None)) for m in methods), "Incomplete SceneAdapter implementation")
    return cls
