import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterator


@dataclass
class ProcessMetric:
    calls: int = 0
    total_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @property
    def average_seconds(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.total_seconds / self.calls


class MetricsTracker:
    def __init__(self) -> None:
        self._metrics: dict[str, ProcessMetric] = {}

    def reset(self) -> None:
        self._metrics.clear()

    def _get_metric(self, process: str) -> ProcessMetric:
        if process not in self._metrics:
            self._metrics[process] = ProcessMetric()
        return self._metrics[process]

    @contextmanager
    def track(self, process: str) -> Iterator[None]:
        """Measure one call, including calls that raise an exception."""

        started_at = perf_counter()
        try:
            yield
        finally:
            elapsed = perf_counter() - started_at
            metric = self._get_metric(process)
            metric.calls += 1
            metric.total_seconds += elapsed

    def add_tokens(
        self,
        process: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int | None = None,
    ) -> None:
        """Add token usage reported by a model response."""

        metric = self._get_metric(process)
        metric.input_tokens += input_tokens
        metric.output_tokens += output_tokens
        metric.total_tokens += (
            total_tokens
            if total_tokens is not None
            else input_tokens + output_tokens
        )

    def as_dict(self) -> dict[str, dict[str, float | int]]:
        report: dict[str, dict[str, float | int]] = {}

        for process, metric in self._metrics.items():
            values = asdict(metric)
            values["average_seconds"] = metric.average_seconds
            report[process] = values

        return report

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2),
            encoding="utf-8",
        )

    def print_summary(self) -> None:
        if not self._metrics:
            return

        print()
        print("[METRICS] Run summary")
        print(
            f"{'Process':<14} {'Calls':>7} {'Total(s)':>10} "
            f"{'Avg(s)':>10} {'Input':>10} {'Output':>10} {'Tokens':>10}"
        )

        for process, metric in self._metrics.items():
            print(
                f"{process:<14} "
                f"{metric.calls:>7} "
                f"{metric.total_seconds:>10.3f} "
                f"{metric.average_seconds:>10.3f} "
                f"{metric.input_tokens:>10} "
                f"{metric.output_tokens:>10} "
                f"{metric.total_tokens:>10}"
            )


metrics_tracker = MetricsTracker()
