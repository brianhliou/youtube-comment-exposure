"""Population freeze: hash stability, tamper detection, and budget estimation."""

from __future__ import annotations

import json

import pytest

from studies.comments.population import (
    FreezeResult,
    PopulationConfig,
    estimated_units,
    load_population,
    population_hash,
    write_population,
)

CONFIG = PopulationConfig(
    name="t",
    channels=["@a", "@b"],
    published_after="2025-08-13",
    published_before="2026-07-14",
)

VIDEOS = [
    {"video_id": "v1", "channel_id": "c1", "comment_count": 300},
    {"video_id": "v2", "channel_id": "c2", "comment_count": 900},
]


def test_hash_is_order_independent_over_members():
    assert population_hash(CONFIG, ["v1", "v2"]) == population_hash(CONFIG, ["v2", "v1"])


def test_hash_moves_when_membership_changes():
    assert population_hash(CONFIG, ["v1"]) != population_hash(CONFIG, ["v1", "v2"])


def test_hash_moves_when_the_sample_definition_changes():
    widened = PopulationConfig(**{**CONFIG.as_dict(), "min_comments": 1})
    assert population_hash(widened, ["v1"]) != population_hash(CONFIG, ["v1"])


def test_estimated_units_covers_every_page_plus_the_relevance_probe():
    assert estimated_units(100) == 2  # one full page + probe
    assert estimated_units(101) == 3  # spills to a second page
    assert estimated_units(0) == 2  # a page is still requested


def test_write_then_load_round_trips(tmp_path):
    path = tmp_path / "pop.json"
    write_population(path, CONFIG, FreezeResult(videos=VIDEOS, channels=[{"handle": "@a"}]))

    loaded = load_population(path)
    assert [v["video_id"] for v in loaded["videos"]] == ["v1", "v2"]
    assert loaded["config"]["n_videos"] == 2
    assert loaded["config"]["estimated_comments"] == 1200


def test_manifest_records_exclusions_rather_than_dropping_them_silently(tmp_path):
    path = tmp_path / "pop.json"
    excluded = {"too_many_comments": 7, "unresolved_channel": 1}
    write_population(path, CONFIG, FreezeResult(videos=VIDEOS, channels=[], excluded=excluded))

    assert load_population(path)["config"]["excluded"] == excluded


def test_editing_the_member_list_after_freeze_is_detected(tmp_path):
    path = tmp_path / "pop.json"
    write_population(path, CONFIG, FreezeResult(videos=VIDEOS, channels=[]))

    tampered = json.loads(path.read_text())
    tampered["videos"].append({"video_id": "v3", "channel_id": "c3", "comment_count": 500})
    path.write_text(json.dumps(tampered))

    with pytest.raises(ValueError, match="edited after freeze"):
        load_population(path)


def test_config_rejects_unknown_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**CONFIG.as_dict(), "mystery_knob": 3}))
    with pytest.raises(ValueError, match="unknown config keys"):
        PopulationConfig.load(path)
