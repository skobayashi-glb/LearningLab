"""Optional observation and reversible controls, separate from numerical learning."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

from .artifacts import write_json
from .config import canonical_hash

DEFAULT_CAPTURE_DECISIONS = 10
DEFAULT_TICKS_PER_SECOND = 60
DEFAULT_TICK_SECONDS = 1 / DEFAULT_TICKS_PER_SECOND
RECENT_EPISODE_LIMIT = 100
UNIT_FIELDS = ("id", "team", "character", "hp", "defense", "x", "z", "spawned", "attacking", "statuses")


class SearchStopped(RuntimeError):
    """Requested stop; completed trials and frames remain intact."""


class SearchObserver:
    enabled = False

    def emit(self, event, **data):
        pass

    def check(self):
        pass

    def should_capture(self, decision, ended=False):
        return False


class SearchControl:
    def __init__(self):
        self.condition = threading.Condition()
        self.paused = False
        self.stopped = False

    def request(self, action):
        with self.condition:
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
            elif action == "stop":
                self.stopped = True
            else:
                raise ValueError("Unknown control action")
            self.condition.notify_all()

    def check(self, on_pause):
        waited = False
        with self.condition:
            while self.paused and not self.stopped:
                on_pause()
                waited = True
                self.condition.wait()
            if self.stopped:
                raise SearchStopped("Stopped by dashboard control")
        return waited


class FileSearchMonitor(SearchObserver):
    enabled = True

    def __init__(self, directory: Path, capture_decisions=DEFAULT_CAPTURE_DECISIONS):
        if type(capture_decisions) is not int or capture_decisions <= 0:
            raise ValueError("Capture interval must be a positive number of decisions")
        self.directory = directory.resolve()
        self.capture_decisions = capture_decisions
        self.control = SearchControl()
        self.lock = threading.RLock()
        self.status = {"state": "starting", "completed_trials": 0, "updates": 0,
                       "episode_count": 0, "phase_completed": 0, "phase_total": 0,
                       "history": [], "recent_episodes": [], "latest_episode": None}
        self.active = None
        self.frames = []
        self.started = False

    def should_capture(self, decision, ended=False):
        return ended or decision % self.capture_decisions == 0

    def check(self):
        if self.control.check(lambda: self.publish(state="paused")):
            self.publish(state="running")

    def publish(self, **values):
        with self.lock:
            self.status.update(values)
            self.status["updated_at"] = datetime.now(timezone.utc).isoformat()
            if self.started:
                write_json(self.directory / "monitor.json", self.status)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.status)

    def emit(self, event, **data):
        with self.lock:
            getattr(self, "on_" + event)(**data)

    def on_started(self, config, settings, baseline, axes, bounds):
        self.started = True
        self.publish(state="running", started_at=datetime.now(timezone.utc).isoformat(),
            run_directory=str(self.directory), budget=settings["candidate_budget"],
            strategy=settings.get("search_strategy", "random-grid"), baseline=baseline,
            axes=[{"character": c, "field": f, "min": low, "max": high}
                  for (c, f), (low, high) in zip(axes, bounds)],
            capture_decisions=self.capture_decisions, seeds=settings["seeds"],
            cases=len(settings["cases"]), config_hash=canonical_hash(config))

    def on_trial_started(self, index, candidate, distribution):
        self.publish(trial=index, candidate=candidate, distribution=distribution)

    def on_phase(self, split, total, joint, candidate):
        self.publish(split=split, joint=joint, candidate=candidate, phase_total=total, phase_completed=0)

    def on_episode_started(self, scenario, case, seed, matchup):
        battle = scenario["parameters"]["battle"]
        episode_id = self.status["episode_count"]
        self.active = {"id": episode_id, "trial": self.status.get("trial", 0),
            "split": self.status["split"], "joint": self.status.get("joint", False),
            "case": case["id"], "character": case["character"], "scenario": scenario["id"],
            "seed": seed, "encounter": matchup, "stage": copy.deepcopy(battle["stage"]),
            "cell_size": battle["cell_size"], "tick_seconds": battle["rules"].get("tick_seconds", DEFAULT_TICK_SECONDS),
            "candidate": copy.deepcopy(self.status["candidate"]),
            "definitions": {str(team): {unit["id"]: unit for unit in definitions}
                for team, definitions in enumerate((battle["ally_definitions"], battle["enemy_baseline"]))}}
        self.frames = []
        self.publish(active_episode={key: self.active[key] for key in
            ("id", "trial", "split", "case", "character", "scenario", "seed", "encounter")}, latest_episode=episode_id)

    def on_frame(self, debug, decision):
        units = []
        for original in debug["units"]:
            unit = {field: original[field] for field in UNIT_FIELDS}
            definition = self.active["definitions"][str(unit["team"])][unit["character"]]
            unit["maximum_hp"] = definition["hp"]
            units.append(unit)
        frame = {"tick": debug["tick"], "seconds": debug["tick"] * self.active["tick_seconds"],
                 "decision": decision, "outcome": debug["outcome"], "units": units, "bases": debug["bases"]}
        self.frames.append(frame)
        write_json(self.directory / "frames" / f"episode-{self.active['id']:06d}.json",
            {key: value for key, value in self.active.items() if key != "definitions"} | {"frames": self.frames})
        self.publish(latest_tick=debug["tick"], simulation_seconds=frame["seconds"], frame_count=len(self.frames))

    def on_episode_completed(self, episode):
        record = {key: self.active[key] for key in
            ("id", "trial", "split", "joint", "case", "character", "scenario", "seed", "encounter")}
        record.update(outcome=episode["outcome"], metrics=episode["metrics"], frame_count=len(self.frames))
        with (self.directory / "episodes.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        recent = [*self.status["recent_episodes"], record][-RECENT_EPISODE_LIMIT:]
        self.publish(episode_count=self.status["episode_count"] + 1,
            phase_completed=self.status["phase_completed"] + 1, recent_episodes=recent)

    def on_trial_completed(self, trial):
        compact = {key: trial[key] for key in ("index", "candidate", "score")}
        compact["train_loss"] = trial["evaluation"]["loss"]
        compact["distribution"] = trial.get("proposal_after")
        compact["update"] = trial.get("update")
        history = [*self.status["history"], compact]
        self.publish(completed_trials=len(history), updates=compact["distribution"]["updates"] if compact["distribution"] else 0,
            history=history, distribution=compact["distribution"], best_score=min(item["score"] for item in history))

    def on_completed(self, report):
        self.publish(state="completed", report=report, finished_at=datetime.now(timezone.utc).isoformat())
