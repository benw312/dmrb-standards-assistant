import json
from decimal import Decimal
from pathlib import Path

import pytest

from agent.tools.design_speed_table import (
    DESIGN_SPEEDS_KPH,
    MIN_R_ADVERSE_CAMBER_M,
    MIN_R_SUPERELEVATION_2_5_M,
    TABLE_ID,
)

TABLE_FILE = Path(__file__).resolve().parent.parent / "data" / "chunks" / "CD109.jsonl"


def test_rows_follow_their_v2_over_r_values():
    # Table 2.10's last column: 5 for adverse camber, 7.07 for 2.5%. Radii are rounded.
    for v in DESIGN_SPEEDS_KPH:
        assert abs(Decimal(v * v) / MIN_R_ADVERSE_CAMBER_M[v] - 5) < Decimal("0.2")
        assert abs(Decimal(v * v) / MIN_R_SUPERELEVATION_2_5_M[v] - Decimal("7.07")) < Decimal(
            "0.3"
        )


@pytest.mark.skipif(not TABLE_FILE.exists(), reason="needs local data/chunks")
def test_radii_match_the_indexed_table():
    table = next(
        json.loads(line)["text"] for line in TABLE_FILE.open() if json.loads(line)["id"] == TABLE_ID
    )
    rows = {
        "Minimum R* with adverse camber and without transitions": MIN_R_ADVERSE_CAMBER_M,
        "Minimum R* with superelevation of 2.5%": MIN_R_SUPERELEVATION_2_5_M,
    }
    for label, radii in rows.items():
        line = next(row for row in table.splitlines() if row.startswith(f"| {label} |"))
        printed = [cell.strip() for cell in line.strip("|").split("|")][1:7]
        assert printed == [str(radii[v]) for v in DESIGN_SPEEDS_KPH], label
