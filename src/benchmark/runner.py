import json
import os
from dataclasses import dataclass, asdict
from typing import List

from common.hierarchical_logger import hlog, htrack_block
from .scenario import Scenario, ScenarioSpec, create_scenario
from .adapter import AdapterSpec, Adapter, ScenarioState
from .executor import ExecutionSpec, Executor
from .metric import Metric, MetricSpec, create_metric, Stat


@dataclass(frozen=True)
class RunSpec:
    """
    Specifies how to do a single run, which gets a scenario, adapts it, and
    computes a list of metrics.
    """

    scenario: ScenarioSpec  # Which scenario
    adapter_spec: AdapterSpec  # Specifies how to adapt an instance into a set of requests
    metrics: List[MetricSpec]  # What to evaluate on
    output_path: str  # Output path


class Runner:
    """
    The main entry point for running the entire benchmark.  Mostly just
    dispatches to other classes.
    """

    def __init__(self, execution_spec: ExecutionSpec, run_specs: List[RunSpec]):
        self.executor = Executor(execution_spec)
        self.run_specs = run_specs

    def run_all(self):
        for run_spec in self.run_specs:
            self.run_one(run_spec)
        hlog("\nDone.")

    def run_one(self, run_spec: RunSpec):
        def write(file_name: str, content: str):
            path: str = os.path.join(run_spec.output_path, file_name)
            with open(path, "w") as f:
                f.write(content)

        # Load the scenario
        scenario: Scenario = create_scenario(run_spec.scenario)

        # Adaptation
        adapter = Adapter(run_spec.adapter_spec)
        scenario_state: ScenarioState = adapter.adapt(scenario)

        # Execution
        scenario_state = self.executor.execute(scenario_state)

        # Apply the metrics
        metrics: List[Metric] = [create_metric(metric) for metric in run_spec.metrics]
        hlog(f"{len(metrics)} metrics")
        stats: List[Stat] = []
        for metric in metrics:
            stats.extend(metric.evaluate(scenario_state))

        # Print out stats
        with htrack_block("Stats"):
            for stat in stats:
                hlog(stat)

        # Output benchmarking information out to files.
        # Create output directory if it doesn't exist.
        os.makedirs(run_spec.output_path, exist_ok=True)

        scenario_dict = asdict(scenario)
        scenario_dict["instances"] = [asdict(instance) for instance in scenario_state.instances]
        write("scenario.txt", str(scenario))
        write("scenario.json", json.dumps(scenario_dict, indent=4))

        write("scenario_state.txt", str(scenario_state))
        write("scenario_state.json", json.dumps(asdict(scenario_state), indent=4))

        write("metrics.txt", "\n".join(str(stat) for stat in stats))
        write("metrics.json", json.dumps([asdict(stat) for stat in stats], indent=4))
