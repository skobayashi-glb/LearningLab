"""Online score-function gradients for bounded character-stat proposal distributions."""
import copy
import math

import torch

from .config import canonical_hash
from .parameter_space import finite

ADAPTIVE_STRATEGY = "adaptive-distribution"
RANDOM_STRATEGY = "random-grid"
STATE_VERSION = 1
DTYPE = torch.float64
INITIAL_FRACTION_MARGIN = 0.1
MINIMUM_SCORE_SCALE = 1.0
ADVANTAGE_LIMIT = 10.0
DEFAULTS = {"learning_rate": 0.05, "initial_std": 1.0, "min_std": 0.15,
            "max_std": 2.5, "max_abs_mean": 6.0, "reference_rate": 0.1,
            "entropy_coefficient": 0.01, "max_grad_norm": 5.0}


class AdaptiveProposalLearner:
    """Sample once, evaluate its full train cases, then update before the next sample."""
    def __init__(self, space, options, seed):
        unknown = set(options) - DEFAULTS.keys()
        if unknown:
            raise ValueError(f"Unknown adaptive options: {sorted(unknown)}")
        self.options = dict(DEFAULTS, **options)
        for key, value in self.options.items():
            if not finite(value) or value < 0 or (value == 0 and key != "entropy_coefficient"):
                raise ValueError(f"Invalid adaptive option: {key}")
        cfg = self.options
        if not cfg["min_std"] <= cfg["initial_std"] <= cfg["max_std"] or cfg["reference_rate"] > 1:
            raise ValueError("Invalid adaptive scale bounds or reference rate")
        self.space = space
        fraction = torch.tensor(space.baseline_fractions(), dtype=DTYPE).clamp(
            INITIAL_FRACTION_MARGIN, 1 - INITIAL_FRACTION_MARGIN)
        initial_mean = torch.logit(fraction)
        if initial_mean.abs().max().item() > cfg["max_abs_mean"]:
            raise ValueError("max_abs_mean is too small for the initial distribution")
        self.mean = torch.nn.Parameter(initial_mean)
        self.log_std = torch.nn.Parameter(torch.full_like(fraction, math.log(cfg["initial_std"])))
        self.optimizer = torch.optim.Adam([self.mean, self.log_std], lr=cfg["learning_rate"])
        self.rng = torch.Generator().manual_seed(seed)
        self.updates = 0
        self.reference_score = None
        self.pending = None
        self.signature = canonical_hash({"axes": space.axes, "bounds": space.bounds, "options": self.options})

    def initialize_score(self, score):
        if self.reference_score is not None or not finite(score):
            raise ValueError("Initialize once with a finite baseline train score")
        self.reference_score = float(score)

    def distribution(self):
        return torch.distributions.Normal(self.mean, self.log_std.exp())

    def ask(self):
        if self.pending is not None or self.reference_score is None:
            raise RuntimeError("Each proposal needs baseline initialization and exactly one feedback update")
        with torch.no_grad():
            latent = self.mean + self.log_std.exp() * torch.randn(self.mean.shape, generator=self.rng, dtype=DTYPE)
            candidate = self.space.decode(torch.sigmoid(latent).tolist())
        self.pending = {"latent": latent.detach(), "candidate": copy.deepcopy(candidate)}
        return candidate

    def tell(self, candidate, score):
        if self.pending is None or candidate != self.pending["candidate"] or not finite(score):
            raise ValueError("Feedback must match the pending proposal and have a finite train score")
        distribution = self.distribution()
        # Lower cost is better: gradient descent increases probability of negative-advantage samples.
        reference = self.reference_score
        advantage = (score - reference) / max(abs(reference), MINIMUM_SCORE_SCALE)
        advantage = max(-ADVANTAGE_LIMIT, min(ADVANTAGE_LIMIT, advantage))
        log_probability = distribution.log_prob(self.pending["latent"]).sum()
        latent_entropy = distribution.entropy().sum()
        loss = advantage * log_probability - self.options["entropy_coefficient"] * latent_entropy
        if not torch.isfinite(loss):
            raise FloatingPointError("Non-finite adaptive loss")
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_([self.mean, self.log_std], self.options["max_grad_norm"], error_if_nonfinite=True)
        self.optimizer.step()
        with torch.no_grad():
            self.mean.clamp_(-self.options["max_abs_mean"], self.options["max_abs_mean"])
            self.log_std.clamp_(math.log(self.options["min_std"]), math.log(self.options["max_std"]))
        self.reference_score += self.options["reference_rate"] * (score - reference)
        self.updates += 1
        self.pending = None
        return {"update": self.updates, "train_score": score, "reference_before": reference,
                "advantage": advantage, "proposal_loss": loss.item(), "gradient_norm": gradient_norm.item(),
                "latent_entropy": latent_entropy.item()}

    def describe(self):
        return {"updates": self.updates, "latent_mean": self.mean.detach().tolist(),
                "latent_std": self.log_std.detach().exp().tolist(), "reference_score": self.reference_score,
                "center_candidate": self.space.decode(torch.sigmoid(self.mean.detach()).tolist())}

    def state_dict(self):
        if self.pending is not None:
            raise RuntimeError("Save only after proposal feedback")
        return {"version": STATE_VERSION, "signature": self.signature,
                "mean": self.mean.detach().clone(), "log_std": self.log_std.detach().clone(),
                "optimizer": copy.deepcopy(self.optimizer.state_dict()), "rng": self.rng.get_state(),
                "updates": self.updates, "reference_score": self.reference_score}

    def load_state_dict(self, state):
        if state["version"] != STATE_VERSION or state["signature"] != self.signature or self.pending is not None:
            raise ValueError("Adaptive checkpoint version/domain/options mismatch")
        with torch.no_grad():
            self.mean.copy_(state["mean"])
            self.log_std.copy_(state["log_std"])
        self.optimizer.load_state_dict(copy.deepcopy(state["optimizer"]))
        self.rng.set_state(state["rng"])
        self.updates, self.reference_score = state["updates"], state["reference_score"]
