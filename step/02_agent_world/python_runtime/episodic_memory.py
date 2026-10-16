"""Replaceable, actor-local memory selection; policies select references, not facts."""
from __future__ import annotations

import copy
import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common import canonical, file_hash, require


def tokens(value: object) -> set[str]:
    # Lexical features for ranking ONLY, not model-token estimates.
    text = canonical(value).decode("utf-8").lower()
    words = set(re.findall(r"[a-z0-9_]{3,}", text))
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(span[i:i + 2] for i in range(len(span) - 1))
    return words


@dataclass(frozen=True)
class MemoryChoice:
    observation_id: str
    event_ids: tuple[str, ...]
    include_action_result: bool
    priority: int  # Larger means keep longer when the context budget is tight.


class MemoryAccessPolicy(Protocol):
    """Select from already-delivered, deduplicated history only.

    Empty selection is legal. Context Builder separately protects new delivery;
    a policy cannot suppress it or supply generated summaries/new facts.
    """
    def select(self, history: list[dict], current: dict, tasks: dict) -> list[MemoryChoice]: ...


class BoundedRelevantPolicy:
    """Default context policy, not a framework-wide memory-access restriction."""
    def __init__(self, options: dict):
        require(set(options) == {"recent_limit", "related_limit", "event_limit"}, "Invalid default memory policy options")
        for key, value in options.items():
            require(type(value) is int and value >= 0, f"Invalid memory policy {key}")
        self.options = dict(options)

    def select(self, history: list[dict], current: dict, tasks: dict) -> list[MemoryChoice]:
        query = tokens({"events": current["content"].get("events", []),
                        "self": current["content"].get("self", {}), "tasks": tasks})
        recent = set(range(max(0, len(history) - self.options["recent_limit"]), len(history)))
        scores = {i: len(query & tokens(o)) for i, o in enumerate(history)}
        ranked = sorted((i for i in scores if i not in recent), key=lambda i: (scores[i], i), reverse=True)
        selected = recent | {i for i in ranked[:self.options["related_limit"]] if scores[i] > 0}
        result = []
        for i in sorted(selected):
            obs = history[i]
            ranked_events = sorted(enumerate(obs.get("events", [])),
                key=lambda pair: (len(query & tokens(pair[1])), pair[0]), reverse=True)
            event_ids = tuple(e["event_id"] for _, e in sorted(ranked_events[:self.options["event_limit"]]))
            result.append(MemoryChoice(obs["observation_id"], event_ids, obs.get("action_result") is not None,
                                       (len(history) + 1) * scores[i] + i))
        return result


def load_policy(config: dict) -> tuple[MemoryAccessPolicy, dict]:
    """Trusted local plug-in covered by the existing *.py code snapshots.

    No no-retrieval/recency/full-history alternatives are implemented here.
    A future policy implements select() and supplies explicit options in config.
    """
    setting = config.get("context", {}).get("memory_policy", {})
    adapter = setting.get("adapter", "episodic_memory:BoundedRelevantPolicy")
    require(isinstance(adapter, str) and re.fullmatch(r"[A-Za-z_]\w*:[A-Za-z_]\w*", adapter) is not None,
            "context.memory_policy.adapter must be local_module:Class")
    module, name = adapter.split(":")
    path = Path(__file__).with_name(module + ".py")
    require(path.is_file() and not path.is_symlink(), "Memory policy must be a local snapshotted runtime module")
    if adapter == "episodic_memory:BoundedRelevantPolicy":
        cognitive = config.get("cognition", {})
        options = setting.get("options", {"recent_limit": config["observation"]["memory_window"],
            "related_limit": cognitive.get("related_memories", 0), "event_limit": cognitive.get("memory_event_limit", 6)})
    else:
        require("options" in setting, "Custom memory policy requires explicit options")
        options = setting["options"]
    require(isinstance(options, dict), "Memory policy options must be an object")
    imported = importlib.import_module(module)
    require(Path(imported.__file__).resolve() == path.resolve(), "Memory policy import resolved outside runtime")
    policy = getattr(imported, name)(copy.deepcopy(options))
    require(callable(getattr(policy, "select", None)), "Memory policy must implement select")
    return policy, {"adapter": adapter, "options": copy.deepcopy(options), "code_sha256": file_hash(path)}


def retrieve(episodes: list[dict], tasks: dict, recent_limit: int, related_limit: int, event_limit: int) -> list[dict]:
    """Legacy helper; production decisions use delivery-aware Context Builder."""
    if not episodes:
        return []
    from context_builder import select_history
    policy = BoundedRelevantPolicy({"recent_limit": recent_limit, "related_limit": related_limit, "event_limit": event_limit})
    seen_events = {e["event_id"] for o in episodes[:-1] for e in o["content"].get("events", [])}
    seen_results = {o["content"]["action_result"]["request_id"] for o in episodes[:-1] if o["content"].get("action_result")}
    records, _, _ = select_history(episodes, tasks, seen_events, seen_results, policy)
    return records
