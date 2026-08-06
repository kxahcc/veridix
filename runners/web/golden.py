from __future__ import annotations

from uuid import uuid4

from .models import CandidateFinding, GoldenResult, WebObservation
from .normalizer import build_endpoint_model
from .replay import ReplayEngine


class GoldenWebSlice:
    def __init__(
        self,
        *,
        candidate_path: str,
        mutation_param: str,
        mutation_value: str,
    ) -> None:
        self._candidate_path = candidate_path
        self._mutation_param = mutation_param
        self._mutation_value = mutation_value
        self._replay = ReplayEngine()

    def run(
        self,
        observations: tuple[WebObservation, ...],
        *,
        base_url: str,
    ) -> GoldenResult:
        model = build_endpoint_model(observations)
        candidate = self._pick_candidate(observations)
        baseline = self._replay.baseline(candidate, base_url)
        method, mutated_url, headers, body = self._replay.mutate(
            candidate,
            param=self._mutation_param,
            value=self._mutation_value,
        )
        mutated = self._replay.send(method, mutated_url, headers, body)
        diff = self._replay.diff(
            candidate.endpoint,
            baseline,
            mutated,
            {self._mutation_param: self._mutation_value},
        )
        proof = self._replay.replay_proof(candidate)
        finding = CandidateFinding(
            finding_id=f"finding_{uuid4().hex[:12]}",
            endpoint=candidate.endpoint,
            diff=diff,
            evidence_refs=(candidate.artifact_ref,),
            replay_proof=proof,
            confidence=0.7 if diff.changed else 0.0,
        )
        return GoldenResult(
            endpoint_model=model,
            candidate=finding,
            observations=observations,
        )

    def _pick_candidate(self, observations: tuple[WebObservation, ...]) -> WebObservation:
        for observation in observations:
            if self._candidate_path in observation.url:
                return observation
        raise ValueError(f"no observation for candidate path {self._candidate_path}")
