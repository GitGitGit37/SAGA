"""Synthetic fleet data generator.

Produces ~10 assets x 60 days of shift-level telematics with injected failure
patterns, fault codes, and maintenance/inspection notes. Output is written as
the same CSV/JSON formats the ingest endpoint accepts, so the demo can upload
the `demo_holdout/` files live.

    python -m seed.generate              # writes seed/data/
    python -m seed.generate --seed 7     # different random draw
"""

from __future__ import annotations

import argparse
import json
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from app import config

DAYS = 60
START = datetime(2026, 8, 1, tzinfo=timezone(timedelta(hours=-5)))  # US Central (CDT)
SHIFT_START_HOURS = (6, 14)
OUT_DIR = Path(__file__).parent / "data"


# --------------------------------------------------------------------------- #
# Fleet definition
# --------------------------------------------------------------------------- #

@dataclass
class AssetSpec:
    asset_tag: str
    name: str
    asset_type: str
    model: str
    site: str
    start_hours: float
    scenario: str
    scenario_description: str
    commissioned_on: str = "2022-03-01"
    serial_number: str = ""

    def __post_init__(self) -> None:
        if not self.serial_number:
            self.serial_number = f"DEMO-{self.model}-{zlib.crc32(self.asset_tag.encode()) % 10000:04d}"


FLEET: list[AssetSpec] = [
    AssetSpec("EX-320-A", "320 Excavator A", "excavator", "320", "Peoria Quarry", 6120,
              "radiator_blockage",
              "Coolant temperature drifts upward from day 34 while ambient stays normal; "
              "110-xx coolant faults follow. Root cause: debris-packed radiator core."),
    AssetSpec("EX-336-B", "336 Excavator B", "excavator", "336", "Peoria Quarry", 8840,
              "hydraulic_leak",
              "Hydraulic pressure decays from day 38 with rising oil temp; operators top up "
              "hydraulic oil twice. Root cause: leaking boom cylinder seal."),
    AssetSpec("WL-950-A", "950 Wheel Loader A", "wheel_loader", "950", "Peoria Quarry", 4310,
              "excessive_idling",
              "Idle time runs ~50% all period; DPF soot fault late in the period."),
    AssetSpec("WL-966-B", "966 Wheel Loader B", "wheel_loader", "966", "Peoria Quarry", 7020,
              "overheat_then_repair",
              "Overheating from day 20. Radiator replaced on day 58: the repair record and the "
              "post-repair telematics are held out in demo_holdout/ for live ingest."),
    AssetSpec("DZ-D6-A", "D6 Dozer A", "dozer", "D6", "Peoria Quarry", 5530,
              "hydraulic_leak_safety",
              "Fast hydraulic pressure loss from day 45 reaching critical; severity-4 fault and "
              "operator reports heavy steering. Safety-critical."),
    AssetSpec("EX-320-C", "320 Excavator C", "excavator", "320", "Decatur Site", 3150,
              "healthy", "Healthy control asset."),
    AssetSpec("EX-336-D", "336 Excavator D", "excavator", "336", "Decatur Site", 9410,
              "sensor_glitch",
              "Three isolated single-shift coolant spikes with no corroborating signals; "
              "technician finds a corroded coolant temp sensor connector on day 54."),
    AssetSpec("WL-950-C", "950 Wheel Loader C", "wheel_loader", "950", "Decatur Site", 2280,
              "healthy", "Healthy control asset."),
    AssetSpec("DZ-D8-B", "D8 Dozer B", "dozer", "D8", "Decatur Site", 11200,
              "operator_change_idling",
              "Normal until a new operator is assigned on day 30; idle time then rises to ~40%."),
    AssetSpec("DZ-D6-C", "D6 Dozer C", "dozer", "D6", "Decatur Site", 4890,
              "healthy", "Healthy control asset."),
]

USERS = [
    {"name": "Jordan Reyes", "role": "operator"},
    {"name": "Casey Morgan", "role": "operator"},
    {"name": "Sam Okafor", "role": "technician"},
    {"name": "Riley Chen", "role": "technician"},
    {"name": "Priya Nair", "role": "fleet_manager"},
]

# Healthy baselines per asset type: (mean, std)
BASELINES: dict[str, dict[str, tuple[float, float]]] = {
    "excavator": {
        "coolant_temp_max_c": (88, 1.5), "oil_pressure_kpa": (380, 12),
        "hydraulic_pressure_bar": (320, 6), "hydraulic_oil_temp_c": (62, 2),
        "engine_rpm_avg": (1750, 50), "idle_pct": (18, 5), "load_factor_pct": (58, 7),
        "fuel_rate_lph": (24, 1.8),
    },
    "wheel_loader": {
        "coolant_temp_max_c": (89, 1.5), "oil_pressure_kpa": (360, 12),
        "hydraulic_pressure_bar": (255, 5), "hydraulic_oil_temp_c": (60, 2),
        "engine_rpm_avg": (1700, 50), "idle_pct": (20, 5), "load_factor_pct": (52, 7),
        "fuel_rate_lph": (21, 1.6),
    },
    "dozer": {
        "coolant_temp_max_c": (87, 1.5), "oil_pressure_kpa": (390, 12),
        "hydraulic_pressure_bar": (215, 4), "hydraulic_oil_temp_c": (64, 2),
        "engine_rpm_avg": (1800, 50), "idle_pct": (16, 4), "load_factor_pct": (62, 7),
        "fuel_rate_lph": (25, 1.8),
    },
}


# --------------------------------------------------------------------------- #
# Output records
# --------------------------------------------------------------------------- #

@dataclass
class Generated:
    telematics: list[dict[str, Any]] = field(default_factory=list)
    faults: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    holdout_telematics: list[dict[str, Any]] = field(default_factory=list)
    holdout_faults: list[dict[str, Any]] = field(default_factory=list)
    holdout_notes: list[dict[str, Any]] = field(default_factory=list)


def _ramp(day: float, onset: float, end: float, magnitude: float) -> float:
    """0 before onset, linear to `magnitude` at `end`, then continues at the same slope."""
    if day < onset:
        return 0.0
    return magnitude * (day - onset) / (end - onset)


# Scenario effects: (asset, day, shift, rng) -> dict of additive deltas / overrides
Effect = Callable[[AssetSpec, float, np.random.Generator], dict[str, float]]


def _radiator_blockage(_: AssetSpec, day: float, __: np.random.Generator) -> dict[str, float]:
    return {"coolant_temp_max_c": _ramp(day, 34, 59, 17)}


def _overheat_then_repair(_: AssetSpec, day: float, __: np.random.Generator) -> dict[str, float]:
    if day >= 58:  # radiator replaced on day 58
        return {}
    return {"coolant_temp_max_c": _ramp(day, 20, 57, 15)}


def _hydraulic_leak(_: AssetSpec, day: float, __: np.random.Generator) -> dict[str, float]:
    return {"hydraulic_pressure_bar": -_ramp(day, 38, 59, 58),
            "hydraulic_oil_temp_c": _ramp(day, 38, 59, 7)}


def _hydraulic_leak_safety(_: AssetSpec, day: float, __: np.random.Generator) -> dict[str, float]:
    return {"hydraulic_pressure_bar": -_ramp(day, 45, 59, 60),
            "hydraulic_oil_temp_c": _ramp(day, 45, 59, 9)}


def _excessive_idling(_: AssetSpec, __: float, ___: np.random.Generator) -> dict[str, float]:
    return {"idle_pct": 32, "load_factor_pct": -14}


def _operator_change_idling(_: AssetSpec, day: float, __: np.random.Generator) -> dict[str, float]:
    return {"idle_pct": 23, "load_factor_pct": -9} if day >= 30 else {}


GLITCH_SHIFTS = {(24, 1), (41, 0), (53, 1)}  # all working days (Sundays are off)


def _sensor_glitch(_: AssetSpec, day: float, __: np.random.Generator) -> dict[str, float]:
    d, s = int(day), round((day - int(day)) * 2)
    return {"coolant_temp_max_c": 24} if (d, s) in GLITCH_SHIFTS else {}


EFFECTS: dict[str, Effect] = {
    "radiator_blockage": _radiator_blockage,
    "overheat_then_repair": _overheat_then_repair,
    "hydraulic_leak": _hydraulic_leak,
    "hydraulic_leak_safety": _hydraulic_leak_safety,
    "excessive_idling": _excessive_idling,
    "operator_change_idling": _operator_change_idling,
    "sensor_glitch": _sensor_glitch,
}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def _site_weather(rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Daily ambient temperature per site, shared by every asset on that site."""
    days = np.arange(DAYS)
    weather = {}
    for site, offset in (("Peoria Quarry", 0.0), ("Decatur Site", 0.8)):
        seasonal = 29 + offset - 6 * days / DAYS
        weather[site] = seasonal + rng.normal(0, 2.2, DAYS)
    return weather


def _shift_times(day: int, shift: int) -> datetime:
    return START + timedelta(days=day, hours=SHIFT_START_HOURS[shift])


def _is_holdout(spec: AssetSpec, day: int) -> bool:
    return spec.scenario == "overheat_then_repair" and day >= 58


def _faults_for_row(spec: AssetSpec, row: dict[str, Any], day: int,
                    rng: np.random.Generator) -> list[str]:
    limits = config.thresholds()["asset_types"][spec.asset_type]
    codes: list[str] = []

    coolant = row["coolant_temp_max_c"]
    c_warn, c_crit = limits["coolant_temp_max_c"]["warn"], limits["coolant_temp_max_c"]["critical"]
    if coolant > c_crit:
        codes.append("110-0")
    elif coolant > (c_warn + c_crit) / 2 and rng.random() < 0.8:
        codes.append("110-16")
    elif coolant > c_warn and rng.random() < 0.6:
        codes.append("110-15")

    hyd = row["hydraulic_pressure_bar"]
    h_warn, h_crit = limits["hydraulic_pressure_bar"]["warn"], limits["hydraulic_pressure_bar"]["critical"]
    if hyd < h_crit:
        codes.append("1762-1")
    elif hyd < h_warn and rng.random() < 0.55:
        codes.append("1762-18")

    if row["hydraulic_oil_temp_c"] > limits["hydraulic_oil_temp_c"]["warn"] and rng.random() < 0.5:
        codes.append("1638-16")

    if row["idle_pct"] > 45 and day > 40 and rng.random() < 0.12:
        codes.append("3719-16")

    if rng.random() < 0.004:  # benign fleet-wide noise
        codes.append("1761-17")
    return codes


def _telematics_row(spec: AssetSpec, day: int, shift: int, ambient: float, hours: float,
                    rng: np.random.Generator) -> dict[str, Any]:
    base = BASELINES[spec.asset_type]
    v = {k: rng.normal(mu, sd) for k, (mu, sd) in base.items()}

    amb = ambient + (2.5 if shift == 0 else 0.5)  # day shift is warmer
    v["coolant_temp_max_c"] += 0.25 * (amb - 25)
    v["hydraulic_oil_temp_c"] += 0.3 * (amb - 25)

    effect = EFFECTS.get(spec.scenario)
    if effect:
        for k, delta in effect(spec, day + shift / 2, rng).items():
            v[k] += delta

    v["idle_pct"] = float(np.clip(v["idle_pct"], 2, 85))
    v["load_factor_pct"] = float(np.clip(v["load_factor_pct"], 10, 95))
    # Fuel follows load; idling burns less per hour but wastes it.
    v["fuel_rate_lph"] = max(4.0, v["fuel_rate_lph"] * (0.55 + v["load_factor_pct"] / 130))

    return {
        "asset_tag": spec.asset_tag,
        "timestamp": _shift_times(day, shift).isoformat(),
        "shift": "day" if shift == 0 else "swing",
        "engine_hours": round(hours, 1),
        "ambient_temp_c": round(amb, 1),
        "coolant_temp_max_c": round(v["coolant_temp_max_c"], 1),
        "oil_pressure_kpa": round(v["oil_pressure_kpa"]),
        "hydraulic_pressure_bar": round(v["hydraulic_pressure_bar"], 1),
        "hydraulic_oil_temp_c": round(v["hydraulic_oil_temp_c"], 1),
        "engine_rpm_avg": round(v["engine_rpm_avg"]),
        "idle_pct": round(v["idle_pct"], 1),
        "load_factor_pct": round(v["load_factor_pct"], 1),
        "fuel_rate_lph": round(v["fuel_rate_lph"], 1),
    }


def _note(spec: AssetSpec, day: int, hour: int, kind: str, author_role: str, text: str,
          **extra: Any) -> dict[str, Any]:
    ts = START + timedelta(days=day, hours=hour)
    return {"asset_tag": spec.asset_tag, "timestamp": ts.isoformat(), "kind": kind,
            "author_role": author_role, "text": text, **extra}


def _scenario_notes(spec: AssetSpec) -> list[dict[str, Any]]:
    s = spec.scenario
    n: list[dict[str, Any]] = []
    if s == "radiator_blockage":
        n += [
            _note(spec, 40, 13, "inspection_note", "operator",
                  "Temp gauge sitting higher than usual on long digs in the afternoon. Still in the green-ish."),
            _note(spec, 47, 6, "inspection_note", "technician",
                  "Walkaround: coolant level at full mark, no external coolant leaks, fan belt tension OK. "
                  "Did not pull the side screens."),
            _note(spec, 55, 13, "inspection_note", "operator",
                  "Got a coolant temp warning twice today. Let it idle down and it cleared."),
        ]
    elif s == "hydraulic_leak":
        n += [
            _note(spec, 44, 7, "maintenance_note", "technician",
                  "Oil film on boom cylinder rod and some drips under the boom foot. "
                  "Topped up hydraulic tank, 8 L. Monitor.", liters_added=8),
            _note(spec, 52, 7, "maintenance_note", "technician",
                  "Hydraulic tank low again, topped up 12 L. Boom cylinder rod seal looks wet.", liters_added=12),
            _note(spec, 56, 13, "inspection_note", "operator",
                  "Boom a little slow lifting full buckets at end of shift."),
        ]
    elif s == "hydraulic_leak_safety":
        n += [
            _note(spec, 52, 13, "inspection_note", "operator",
                  "Blade response feels a bit lazy when it's hot."),
            _note(spec, 56, 7, "maintenance_note", "technician",
                  "Found wet hose fitting at the steering/implement pump outlet. Tightened, topped up 15 L.",
                  liters_added=15),
            _note(spec, 58, 13, "inspection_note", "operator",
                  "Steering feels heavy on turns and blade slow to respond. Finished the shift."),
        ]
    elif s == "overheat_then_repair":
        n += [
            _note(spec, 33, 13, "inspection_note", "operator",
                  "Coolant temp creeping up in the afternoons. Fan seems loud."),
            _note(spec, 46, 7, "inspection_note", "technician",
                  "Coolant level OK. Radiator core has visible bent fins and packed fines. Recommend replacement; "
                  "part ordered."),
        ]
    elif s == "excessive_idling":
        n += [
            _note(spec, 12, 13, "inspection_note", "fleet_manager",
                  "Loader often left running between truck loads while waiting at the pit."),
            _note(spec, 51, 7, "maintenance_note", "technician",
                  "Forced DPF regeneration performed after soot load warning."),
        ]
    elif s == "operator_change_idling":
        n += [
            _note(spec, 30, 6, "inspection_note", "fleet_manager",
                  "New operator assigned to D8 starting today (trainee)."),
        ]
    elif s == "sensor_glitch":
        n += [
            _note(spec, 42, 7, "inspection_note", "operator",
                  "Coolant warning flashed yesterday for a minute, gauge looked normal otherwise."),
            _note(spec, 54, 7, "maintenance_note", "technician",
                  "Coolant temp sensor connector found corroded. Cleaned, applied dielectric grease, resealed. "
                  "Readings stable after test run."),
        ]
    return n


def _holdout_notes(spec: AssetSpec) -> list[dict[str, Any]]:
    if spec.scenario != "overheat_then_repair":
        return []
    return [_note(spec, 58, 5, "maintenance_note", "technician",
                  "Replaced radiator core and cleaned cooler package. Coolant flushed and refilled. "
                  "Test run 45 min at load: max coolant 89 C.",
                  work_order="WO-26-0912", parts=["radiator core assembly"])]


def generate(seed: int = 42) -> tuple[Generated, list[dict[str, Any]]]:
    rng = np.random.default_rng(seed)
    weather = _site_weather(rng)
    out = Generated()

    for spec in FLEET:
        hours = spec.start_hours
        next_pm = (int(hours // 250) + 1) * 250
        for day in range(DAYS):
            weekday = (START + timedelta(days=day)).weekday()
            if weekday == 6:  # no work on Sundays
                continue
            for shift in (0, 1):
                hours += float(np.clip(rng.normal(7.4, 0.4), 6, 8))
                row = _telematics_row(spec, day, shift, weather[spec.site][day], hours, rng)
                holdout = _is_holdout(spec, day)
                (out.holdout_telematics if holdout else out.telematics).append(row)

                for code in _faults_for_row(spec, row, day, rng):
                    minutes = int(rng.integers(30, 450))
                    ts = _shift_times(day, shift) + timedelta(minutes=minutes)
                    fault = {"asset_tag": spec.asset_tag, "timestamp": ts.isoformat(), "code": code,
                             "occurrences": int(rng.integers(1, 4))}
                    (out.holdout_faults if holdout else out.faults).append(fault)

            if hours >= next_pm:
                out.notes.append(_note(
                    spec, day, 5, "maintenance_note", "technician",
                    f"PM 250 completed at {next_pm} h: engine oil and filter, fuel filters, "
                    f"greased linkage, checked coolant and hydraulic levels.",
                    work_order=f"PM-{spec.asset_tag}-{next_pm}"))
                next_pm += 250

        out.notes += _scenario_notes(spec)
        out.holdout_notes += _holdout_notes(spec)

    out.notes.sort(key=lambda r: (r["asset_tag"], r["timestamp"]))
    out.faults.sort(key=lambda r: (r["asset_tag"], r["timestamp"]))
    scenarios = [{"asset_tag": a.asset_tag, "scenario": a.scenario,
                  "description": a.scenario_description} for a in FLEET]
    return out, scenarios


def write(out_dir: Path = OUT_DIR, seed: int = 42) -> Path:
    data, scenarios = generate(seed)
    holdout_dir = out_dir / "demo_holdout"
    holdout_dir.mkdir(parents=True, exist_ok=True)

    assets = [{k: v for k, v in asdict(a).items() if k not in ("scenario", "scenario_description")}
              for a in FLEET]
    _dump(out_dir / "assets.json", assets)
    _dump(out_dir / "users.json", USERS)
    _dump(out_dir / "scenarios.json", scenarios)  # ground truth for tests/demo, NOT loaded as memory
    pd.DataFrame(data.telematics).to_csv(out_dir / "telematics.csv", index=False)
    _dump(out_dir / "fault_codes.json", data.faults)
    _dump(out_dir / "notes.json", data.notes)

    pd.DataFrame(data.holdout_telematics).to_csv(holdout_dir / "telematics.csv", index=False)
    _dump(holdout_dir / "fault_codes.json", data.holdout_faults)
    _dump(holdout_dir / "notes.json", data.holdout_notes)
    return out_dir


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    out = write(args.out, args.seed)
    data, _ = generate(args.seed)
    print(f"Wrote {out}: {len(data.telematics)} telematics rows, {len(data.faults)} fault events, "
          f"{len(data.notes)} notes (+ holdout: {len(data.holdout_telematics)} rows, "
          f"{len(data.holdout_faults)} faults, {len(data.holdout_notes)} notes)")


if __name__ == "__main__":
    main()
