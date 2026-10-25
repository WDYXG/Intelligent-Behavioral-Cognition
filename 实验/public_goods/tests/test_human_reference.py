"""Offline checks of scoring, sample separation and behavioral denominators."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

from public_goods.evaluation import human_reference as ref


def fixture():
    """One complete artificial group, deliberately including equal peers."""
    rows = []
    for phase in ("N", "P"):
        for r in range(1, 11):
            for pid in range(4):
                rows.append({"group_id": "g1", "participant_id": str(pid), "phase": phase,
                             "round": r, "contribution": 10, "received_points": None if phase == "N" else 0,
                             "targets": [] if phase == "N" else [{"contribution": 10, "points": 0} for _ in range(3)]})
    return rows


def load_fixture(rows):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "trajectory.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows))
        return ref.load_agent(path)


class MetricTests(unittest.TestCase):
    def test_identical_distribution_zero(self):
        p, _ = load_fixture(fixture())
        self.assertEqual(ref.primary_score(p, p, repeats=40)["mean_round_normalized_W1"], 0)

    def test_extreme_distance_one(self):
        a, _ = load_fixture(fixture())
        b = a.copy()
        a["contribution"], b["contribution"] = 0, 20
        self.assertEqual(ref.primary_score(a, b, repeats=40)["mean_round_normalized_W1"], 1)

    def test_same_mean_different_distributions(self):
        a, _ = load_fixture(fixture())
        b = a.copy()
        a["contribution"] = np.where(a.participant_id.isin(["0", "1"]), 0, 20)
        result = ref.primary_score(a, b, repeats=40)
        self.assertEqual(result["mean_curve_MAE_tokens"], 0)
        self.assertAlmostEqual(result["mean_round_normalized_W1"], .5)

    def test_round_order_not_pooled(self):
        a, _ = load_fixture(fixture())
        b = a.copy()
        a["contribution"] = np.where(a["round"] <= 5, 0, 20)
        b["contribution"] = 20 - a.contribution
        self.assertEqual(a.contribution.mean(), b.contribution.mean())
        self.assertEqual(ref.primary_score(a, b, repeats=40)["mean_round_normalized_W1"], 1)

    def test_distance_matches_independent_scipy(self):
        rng = np.random.default_rng(17)
        for _ in range(20):
            a, b = rng.integers(0, 21, 43), rng.integers(0, 21, 28)
            ah, bh = np.bincount(a, minlength=21) / len(a), np.bincount(b, minlength=21) / len(b)
            self.assertAlmostEqual(float(ref.w1_cells(ah, bh)), wasserstein_distance(a, b) / 20)

    def test_bootstrap_preserves_city_weight_and_is_reproducible(self):
        strata = np.array(["A", "A", "B", "B", "B"])
        weights = ref.bootstrap_weights(strata, repeats=100)
        np.testing.assert_allclose(weights[:, :2].sum(axis=1), .4)
        np.testing.assert_allclose(weights[:, 2:].sum(axis=1), .6)
        np.testing.assert_array_equal(weights, ref.bootstrap_weights(strata, repeats=100))

    def test_comparison_direction_and_shared_human_draws(self):
        human, _ = load_fixture(fixture())
        first, second = human.copy(), human.copy()
        first["contribution"], second["contribution"] = 10, 20
        result = ref.primary_difference(first, second, human, repeats=40)
        self.assertAlmostEqual(result["first_minus_second"], -.5)
        np.testing.assert_allclose(result["ci95"], [-.5, -.5])
        other_group = human.copy()
        other_group["group_id"], other_group["contribution"] = "g2", 0
        variable_human = pd.concat([human, other_group])
        identical_models = ref.primary_difference(first, first, variable_human, repeats=40)
        np.testing.assert_allclose(identical_models["ci95"], [0, 0])


class RecordTests(unittest.TestCase):
    def test_equal_peer_rows_retained_and_empty_bins_not_zero(self):
        p, peers = load_fixture(fixture())
        self.assertEqual(len(peers), 120)
        result = ref.behavior_statistics(p, peers)
        bins = result["punishment_by_contribution_difference"]
        self.assertEqual(bins[2]["opportunities"], 120)
        self.assertEqual(bins[2]["mean_points"], 0)
        self.assertIsNone(bins[0]["mean_points"])
        self.assertEqual(sum(x["person_transitions"] for x in result["next_round_change"]), 36)

    def test_punishment_bin_edges_and_zero_denominator(self):
        p, _ = load_fixture(fixture())
        peers = pd.DataFrame({"contribution": [20, 20, 20, 20, 10, 0, 0, 0, 0],
                              "target_contribution": [0, 9, 10, 19, 10, 1, 10, 11, 20],
                              "assigned_points": [0, 2, 1, 3, 0, 0, 2, 4, 6]})
        bins = ref.behavior_statistics(p, peers)["punishment_by_contribution_difference"]
        self.assertEqual([b["opportunities"] for b in bins], [2, 2, 1, 2, 2])
        self.assertEqual([b["mean_points"] for b in bins], [1, 2, 0, 1, 5])
        self.assertEqual(bins[0]["positive_rate"], .5)

    def test_next_round_does_not_cross_phase(self):
        p, peers = load_fixture(fixture())
        p.loc[p.phase == "N", "contribution"] = 20
        p.loc[p.phase == "P", "contribution"] = p.loc[p.phase == "P", "round"]
        reactions = ref.behavior_statistics(p, peers)["next_round_change"]
        occupied = [x for x in reactions if x["person_transitions"]]
        self.assertEqual(len(occupied), 1)
        self.assertEqual(occupied[0]["person_transitions"], 36)
        self.assertEqual(occupied[0]["mean_change"], 1)

    def test_incomplete_duplicate_illegal_and_corrupt_records_rejected(self):
        base = fixture()
        variants = [base[:-1], base + [copy.deepcopy(base[0])]]
        for column, value in [("contribution", 21), ("received_points", 0), ("participant_id", "new")]:
            rows = copy.deepcopy(base)
            rows[0][column] = value
            variants.append(rows)
        rows = copy.deepcopy(base)
        rows[-1]["received_points"] = 1
        variants.append(rows)
        rows = copy.deepcopy(base)
        rows[-1]["targets"][0]["contribution"] = 11
        variants.append(rows)
        for rows in variants:
            with self.subTest(record=rows[0]), self.assertRaises(ValueError):
                load_fixture(rows)


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.digest = ref.read_source()
        cls.plan = ref.make_plan(cls.raw, cls.digest)

    def test_allocation_is_order_invariant_and_no_people_overlap(self):
        self.assertEqual(self.plan, ref.make_plan(self.raw.sample(frac=1, random_state=7), self.digest))
        groups = self.plan["groups"]
        dev = {p for g in groups if g["split"] == "development" for p in g["participant_ids"]}
        final = {p for g in groups if g["split"] == "evaluation" for p in g["participant_ids"]}
        self.assertFalse(dev & final)
        self.assertEqual(len(dev | final), 840)
        for city in {g["city"] for g in groups}:
            self.assertEqual({g["split"] for g in groups if g["city"] == city}, {"development", "evaluation"})

    def test_development_counts_and_direct_source_means(self):
        p, peers = ref.human_frames(self.raw, self.plan, "development")
        ids = [g["group_id"] for g in self.plan["groups"] if g["split"] == "development"]
        self.assertEqual(len(p), len(ids) * 80)
        self.assertEqual(len(peers), len(ids) * 120)
        original = self.raw[self.raw.groupid.isin(ids)]
        hist, _ = ref.group_histograms(p)
        np.testing.assert_allclose(hist.sum(axis=-1), 1)
        for i, (phase, r) in enumerate(ref.CELLS):
            # Direct raw mean is valid here only because every person has exactly 3 rows.
            raw_mean = original.loc[(original.p == f"{phase}-experiment") & (original.period == r), "senderscontribution"].mean()
            self.assertAlmostEqual(float((hist[:, i] @ np.arange(21)).mean()), raw_mean)


if __name__ == "__main__":
    unittest.main()
