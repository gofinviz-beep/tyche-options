"""Unit tests for panel memory helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tyche.ml.panel_memory import downcast_panel


def test_downcast_panel_float32_and_category():
    df = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "AAPL"],
            "x": [1.0, 2.0, 3.0],
            "y": np.array([1, 2, 3], dtype=np.int64),
        }
    )
    out = downcast_panel(df)
    assert out["x"].dtype == np.float32
    assert out["ticker"].dtype.name == "category"
    assert out["y"].dtype in (np.int8, np.int16, np.int32)
