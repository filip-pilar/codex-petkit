from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class State:
    id: str
    row: int
    frame_count: int
    durations_ms: tuple[int, ...]
    purpose: str
    directions_degrees: tuple[float, ...] = ()


@dataclass(frozen=True)
class Contract:
    version: int
    width: int
    height: int
    columns: int
    rows: int
    standard_rows: int
    cell_width: int
    cell_height: int
    max_bytes: int
    neutral_look_frame: tuple[int, int]
    look_directions_degrees: tuple[float, ...]
    states: tuple[State, ...]
    raw: dict[str, Any]

    def state(self, state_id: str) -> State:
        for state in self.states:
            if state.id == state_id:
                return state
        available = ", ".join(item.id for item in self.states)
        raise ValueError(f"unknown state {state_id!r}; expected one of: {available}")

    @property
    def standard_states(self) -> tuple[State, ...]:
        return tuple(state for state in self.states if state.row < self.standard_rows)

    @property
    def look_states(self) -> tuple[State, ...]:
        return tuple(state for state in self.states if state.row >= self.standard_rows)

    def cell_is_used(self, state: State, column: int) -> bool:
        return column < state.frame_count or (state.row, column) == self.neutral_look_frame


@lru_cache(maxsize=None)
def load_contract(version: int = 2) -> Contract:
    path = PACKAGE_ROOT / "contracts" / f"v{version}.json"
    if not path.is_file():
        raise ValueError(f"unsupported pet contract version: {version}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    atlas = raw["atlas"]
    states = tuple(
        State(
            id=item["id"],
            row=item["row"],
            frame_count=item["frame_count"],
            durations_ms=tuple(item["durations_ms"]),
            purpose=item["purpose"],
            directions_degrees=tuple(float(value) for value in item.get("directions_degrees", [])),
        )
        for item in raw["states"]
    )
    contract = Contract(
        version=raw["version"],
        width=atlas["width"],
        height=atlas["height"],
        columns=atlas["columns"],
        rows=atlas["rows"],
        standard_rows=atlas["standard_rows"],
        cell_width=atlas["cell_width"],
        cell_height=atlas["cell_height"],
        max_bytes=atlas["max_bytes"],
        neutral_look_frame=(atlas["neutral_look_frame"]["row"], atlas["neutral_look_frame"]["column"]),
        look_directions_degrees=tuple(float(value) for value in raw["look_directions_degrees"]),
        states=states,
        raw=raw,
    )
    _validate_contract(contract)
    return contract


def _validate_contract(contract: Contract) -> None:
    if contract.width != contract.columns * contract.cell_width:
        raise ValueError("contract atlas width does not match columns × cell width")
    if contract.height != contract.rows * contract.cell_height:
        raise ValueError("contract atlas height does not match rows × cell height")
    if len(contract.states) != contract.rows:
        raise ValueError("contract must define exactly one state per row")
    rows = {state.row for state in contract.states}
    if rows != set(range(contract.rows)):
        raise ValueError("contract state rows must be contiguous")
    for state in contract.states:
        if state.frame_count > contract.columns:
            raise ValueError(f"{state.id} uses more frames than atlas columns")
        if len(state.durations_ms) != state.frame_count:
            raise ValueError(f"{state.id} durations do not match frame count")
        if state.directions_degrees and len(state.directions_degrees) != state.frame_count:
            raise ValueError(f"{state.id} direction labels do not match frame count")
    directions = tuple(value for state in contract.look_states for value in state.directions_degrees)
    if directions != contract.look_directions_degrees or len(directions) != 16:
        raise ValueError("contract look states must define the complete ordered 16-direction loop")
