import json

import pandas as pd
import pytest

from app.ingest.parsers import (
    ParseError,
    parse_fault_codes_json,
    parse_notes_json,
    parse_telematics_csv,
)
from seed import generate


@pytest.fixture(scope="module")
def data():
    out, scenarios = generate.generate(seed=42)
    return out, scenarios


def _telematics(data) -> pd.DataFrame:
    df = pd.DataFrame(data[0].telematics)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["day"] = (df["timestamp"] - df["timestamp"].min()).dt.days
    return df


def test_deterministic():
    a, _ = generate.generate(seed=42)
    b, _ = generate.generate(seed=42)
    assert a.telematics == b.telematics and a.faults == b.faults


def test_fleet_shape(data):
    df = _telematics(data)
    assert df["asset_tag"].nunique() == 10
    assert set(df["timestamp"].dt.dayofweek) <= {0, 1, 2, 3, 4, 5}  # no Sunday shifts


def test_radiator_blockage_drifts_while_peers_stay_normal(data):
    df = _telematics(data)
    late = df[df.day >= 53].groupby("asset_tag")["coolant_temp_max_c"].mean()
    assert late["EX-320-A"] > 100
    assert late["EX-336-B"] < 92  # same site, same weather, healthy cooling


def test_hydraulic_leaks_cross_thresholds(data):
    df = _telematics(data)
    late = df[df.day >= 56].groupby("asset_tag")["hydraulic_pressure_bar"].mean()
    assert late["EX-336-B"] < 280   # excavator warn
    assert late["DZ-D6-A"] < 170    # near dozer critical (160)
    faults = pd.DataFrame(data[0].faults)
    assert "1762-1" in set(faults[faults.asset_tag == "DZ-D6-A"].code)


def test_repair_is_held_out(data):
    out, _ = data
    assert all(r["asset_tag"] == "WL-966-B" for r in out.holdout_telematics)
    assert any("Replaced radiator" in n["text"] for n in out.holdout_notes)
    assert not any("Replaced radiator" in n["text"] for n in out.notes)
    assert max(r["coolant_temp_max_c"] for r in out.holdout_telematics) < 95


def test_sensor_glitch_is_isolated(data):
    df = _telematics(data)
    d = df[df.asset_tag == "EX-336-D"].sort_values("timestamp").reset_index(drop=True)
    spikes = d.index[d.coolant_temp_max_c > 105].tolist()
    assert len(spikes) == 3
    for i in spikes:  # neighbours are normal
        assert d.loc[i - 1, "coolant_temp_max_c"] < 95 and d.loc[i + 1, "coolant_temp_max_c"] < 95


def test_parsers_roundtrip(tmp_path):
    generate.write(tmp_path, seed=42)
    tel = parse_telematics_csv((tmp_path / "telematics.csv").read_bytes())
    faults = parse_fault_codes_json((tmp_path / "fault_codes.json").read_bytes())
    notes = parse_notes_json((tmp_path / "notes.json").read_bytes())
    assert len(tel) == 1016 and tel[0].payload["coolant_temp_max_c"] > 0
    assert all(f.payload["code"] for f in faults)
    assert all(n.text for n in notes)
    assert all(r.observed_at.tzinfo is not None for r in tel + faults + notes)


def test_parser_rejects_bad_input():
    with pytest.raises(ParseError):
        parse_telematics_csv("foo,bar\n1,2\n")
    with pytest.raises(ParseError):
        parse_fault_codes_json(json.dumps({"not": "a list"}))
