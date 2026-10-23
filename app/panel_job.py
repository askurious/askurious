"""The models over the waves of a panel as an estimation of their own (Ralf 2026-10-17, thread "Rechenknecht"): the
invariance over time of one scale took 105 s with 1500 linked persons, eight statements and four waves, a latent
RI-CLPM over ten minutes, which held a web request (nginx ends it after 300 s). Started by "Längsschnitt rechnen"
(app/panel_models.start), run like HB, the segments and the bootstraps in the estimation process at low priority
(app/estimate.py) or on the Rechenknecht (app/compute.py), with the same marker, places and progress
(maxdiff_hb._estimate_and_store). No database and no ids: the linked scores and statements as numbers
(panel_models.gather), the chosen variables and the settings."""
import math

import numpy as np

from app import panel_models


def plain(x):
    """JSON of a result: numpy numbers as Python numbers, tuples as lists, NaN as None."""
    if isinstance(x, dict):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return plain(x.tolist())
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if math.isnan(x) or math.isinf(x) else float(x)
    return x


def run(inp, progress=None):
    """{"key", "result": the block of panel_models.numbers}."""
    return {"key": inp["key"], "result": plain(panel_models.numbers(inp, progress))}
