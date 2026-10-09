"""Validated discrete and interval parameter domains with frozen baselines."""
import copy
import math
import random
from .config import ExperimentConfig

ALLOWED_PARAMETERS = {"hp", "power", "speed", "cost", "defense"}
INTEGER_PARAMETERS = {"hp", "power", "cost", "defense"}
DEFENSE_CHANGE_SCALE = 100

def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ParameterSpace:
    def __init__(self, config: ExperimentConfig, settings: dict):
        self.axes = []
        self.baseline = {}
        for entry in settings["parameters"]:
            character, field = entry["character"], entry["field"]
            if field not in ALLOWED_PARAMETERS:
                raise ValueError(f"Unsupported parameter: {field}")
            key = (character, field)
            if key in self.axes:
                raise ValueError("Duplicate parameter axis")
            values = entry["values"]
            if not values or any(not finite(v) or v < 0 or (v == 0 and field != "defense") for v in values):
                raise ValueError("Candidates must be positive, except defense may be zero")
            if field in INTEGER_PARAMETERS and any(type(v) is not int for v in values):
                raise ValueError("HP/power/cost/defense candidates must be integers")
            baseline_values = set()
            for scenario in config.data["scenarios"]:
                definitions = scenario["parameters"]["battle"]["ally_definitions"]
                matches = [d for d in definitions if d["id"] == character]
                if len(matches) != 1:
                    raise ValueError(f"Every scenario needs one definition for {character}")
                baseline_values.add(matches[0][field])
            if len(baseline_values) != 1:
                raise ValueError("Baseline must be identical across scenarios")
            baseline = baseline_values.pop()
            if baseline not in values or len(values) != len(set(values)):
                raise ValueError("Unique candidate values must include the frozen baseline")
            self.axes.append(key)
            self.baseline.setdefault(character, {})[field] = baseline
        if not self.axes:
            raise ValueError("At least one parameter is required")
        self.values = [entry["values"] for entry in settings["parameters"]]

    def candidates(self, budget: int, seed: int):
        # Sample without replacement, including the baseline even with a budget of one.
        dimensions = [len(v) for v in self.values]
        total = math.prod(dimensions)
        baseline_index = 0
        for (character, field), values in zip(self.axes, self.values):
            baseline_index = baseline_index * len(values) + values.index(self.baseline[character][field])
        rng = random.Random(seed)
        selected = {baseline_index}
        yield copy.deepcopy(self.baseline)
        while len(selected) < min(budget, total):
            index = rng.randrange(total)
            if index in selected:
                continue
            selected.add(index)
            candidate = {}
            for (character, field), values in reversed(list(zip(self.axes, self.values))):
                index, digit = divmod(index, len(values))
                candidate.setdefault(character, {})[field] = values[digit]
            yield candidate

    def change_penalty(self, candidate):
        return sum(((candidate[c][f] - self.baseline[c][f]) /
                    (max(DEFENSE_CHANGE_SCALE, self.baseline[c][f]) if f == "defense" else self.baseline[c][f])) ** 2
                   for c, f in self.axes) / len(self.axes)


class RangeParameterSpace(ParameterSpace):
    """All integer values or real values inside explicit bounds, not a candidate list."""
    def __init__(self, config, settings):
        normalized = copy.deepcopy(settings)
        self.bounds = []
        for entry in normalized["parameters"]:
            if "values" in entry:
                raise ValueError("Adaptive search uses min/max ranges instead of values")
            low, high = entry["min"], entry["max"]
            field, character = entry["field"], entry["character"]
            if field not in ALLOWED_PARAMETERS:
                raise ValueError(f"Unsupported parameter: {field}")
            if not finite(low) or not finite(high) or low >= high:
                raise ValueError("Parameter ranges must have finite min < max")
            if field in INTEGER_PARAMETERS and (type(low) is not int or type(high) is not int):
                raise ValueError("Integer parameter ranges need integer bounds")
            matches = [d for d in config.data["scenarios"][0]["parameters"]["battle"]["ally_definitions"]
                       if d["id"] == character]
            if len(matches) != 1:
                raise ValueError(f"Missing baseline for {character}")
            baseline = matches[0][field]
            if not low <= baseline <= high:
                raise ValueError("Frozen baseline must lie inside the parameter range")
            entry["values"] = sorted({low, baseline, high})
            self.bounds.append((low, high))
        super().__init__(config, normalized)

    def decode(self, fractions):
        if len(fractions) != len(self.axes):
            raise ValueError("Wrong proposal dimension")
        result = {}
        for (character, field), (low, high), fraction in zip(self.axes, self.bounds, fractions):
            if not finite(fraction) or not 0 <= fraction <= 1:
                raise ValueError("Proposal fractions must lie in [0, 1]")
            value = low + (high - low) * fraction
            if field in INTEGER_PARAMETERS:
                value = round(value)
            result.setdefault(character, {})[field] = value
        return result

    def baseline_fractions(self):
        return [(self.baseline[c][f] - low) / (high - low) for (c, f), (low, high) in zip(self.axes, self.bounds)]

    def candidates(self, budget, seed):
        raise RuntimeError("Range proposals require evaluate-and-update feedback")
