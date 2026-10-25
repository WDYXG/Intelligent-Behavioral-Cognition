"""A: one direct decision from the participant's own observation."""

import argparse
import json
from pathlib import Path

from public_goods.agents.runner import (BaseRunner, BudgetExceeded, DecisionFailed,
                                      RunConfig, parse_action)
from public_goods.environment.world import World
from public_goods.models.client import MockModel


SYSTEM_PROMPT = (
    "你是实验中的一名参与者。请依据当前提供的规则和你本人可见的经历作出本次选择。"
    "只返回符合当前 action_schema 的一个 JSON 对象，不添加字段、解释或 Markdown 标记。"
    "other_contributions 中的位置只在该轮有效，不能作为跨轮的参与者身份。"
)


class AgentRunner(BaseRunner):
    implementation = "A"
    source_files = ("agents/agent_a.py",)

    def _config_metadata(self):
        return {"system_prompt": SYSTEM_PROMPT}

    def _decide(self, pid, observation):
        return self._call(
            pid, observation, observation, SYSTEM_PROMPT, "decision",
            lambda text: parse_action(text, observation["stage"], self.config.max_response_bytes),
            self.config.max_output_tokens,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    group = World("A-offline-fixture", ["member-a", "member-b", "member-c", "member-d"], seed=20261019)
    print(json.dumps(AgentRunner(group, MockModel(), output=args.output).run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
