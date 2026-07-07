"""Delay-risk prediction: learn from historical cases which new cases will be slow.

A small logistic-regression model trained with gradient descent on binary
features (activities present, starting resource, case attributes). Pure
Python — no external ML dependency — which is plenty for event logs of this
size and keeps the model's drivers fully inspectable.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

from .schema import Case, EventLog


class DelayModel:
    def __init__(self, log: EventLog):
        self.log = log
        self.threshold = median(log.durations) * 2
        self.features = self._feature_names()
        self.weights = [0.0] * (len(self.features) + 1)  # last slot = bias
        self._train()

    # -- features ---------------------------------------------------------
    def _feature_names(self) -> list[str]:
        names: dict[str, None] = {}
        for c in self.log.cases:
            for f in self._raw_features(c):
                names.setdefault(f)
        return list(names)

    @staticmethod
    def _raw_features(case: Case) -> list[str]:
        feats = [f"act:{a}" for a in set(case.variant)]
        feats.append(f"first_resource:{case.events[0].resource}")
        feats.append(f"handoffs>=4" if case.handoffs >= 4 else "handoffs<4")
        for k, v in case.attributes.items():
            if not isinstance(v, (dict, list)):
                feats.append(f"attr:{k}={v}")
        return feats

    def _vector(self, case: Case) -> list[float]:
        present = set(self._raw_features(case))
        return [1.0 if f in present else 0.0 for f in self.features]

    # -- training ---------------------------------------------------------
    def _train(self, epochs: int = 300, lr: float = 0.1, l2: float = 0.01) -> None:
        data = [(self._vector(c), 1.0 if c.duration_hours > self.threshold else 0.0) for c in self.log.cases]
        n = len(data)
        for _ in range(epochs):
            grad = [0.0] * len(self.weights)
            for x, y in data:
                p = self._predict_vec(x)
                err = p - y
                for i, xi in enumerate(x):
                    if xi:
                        grad[i] += err * xi
                grad[-1] += err
            for i in range(len(self.weights)):
                reg = l2 * self.weights[i] if i < len(self.weights) - 1 else 0.0
                self.weights[i] -= lr * (grad[i] / n + reg)

    def _predict_vec(self, x: list[float]) -> float:
        z = self.weights[-1] + sum(w * xi for w, xi in zip(self.weights, x) if xi)
        return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))

    # -- public API -------------------------------------------------------
    def predict_case(self, case: Case) -> float:
        return self._predict_vec(self._vector(case))

    def evaluate(self) -> dict[str, Any]:
        tp = fp = tn = fn = 0
        for c in self.log.cases:
            pred = self.predict_case(c) >= 0.5
            actual = c.duration_hours > self.threshold
            tp += pred and actual
            fp += pred and not actual
            tn += (not pred) and (not actual)
            fn += (not pred) and actual
        total = tp + fp + tn + fn
        return {
            "delayed_threshold_hours": round(self.threshold, 1),
            "delayed_share_pct": round(100 * (tp + fn) / total, 1),
            "accuracy_pct": round(100 * (tp + tn) / total, 1),
            "precision_pct": round(100 * tp / (tp + fp), 1) if tp + fp else None,
            "recall_pct": round(100 * tp / (tp + fn), 1) if tp + fn else None,
        }

    def risk_factors(self, top: int = 8) -> list[dict[str, Any]]:
        ranked = sorted(zip(self.features, self.weights), key=lambda kv: -kv[1])
        out = []
        for name, w in ranked[:top]:
            if w <= 0.05:
                continue
            out.append({"factor": _pretty_factor(name), "raw": name, "weight": round(w, 3)})
        return out

    def risky_open_cases(self, top: int = 10) -> list[dict[str, Any]]:
        scored = sorted(
            ((self.predict_case(c), c) for c in self.log.cases),
            key=lambda t: -t[0],
        )
        return [
            {
                "case_id": c.case_id,
                "risk_pct": round(100 * p, 1),
                "duration_hours": round(c.duration_hours, 1),
                "delayed": c.duration_hours > self.threshold,
            }
            for p, c in scored[:top]
        ]


def _pretty_factor(name: str) -> str:
    if name.startswith("act:"):
        return f"case passes through '{name[4:]}'"
    if name.startswith("first_resource:"):
        return f"case opened by {name.split(':', 1)[1]}"
    if name.startswith("attr:"):
        return f"case has {name[5:]}"
    if name.startswith("handoffs"):
        return f"case has {name.replace('handoffs', 'handoffs ')}"
    return name
