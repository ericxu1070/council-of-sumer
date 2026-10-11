"""Bounded private beliefs, authored by each advisor and retained within a run."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrivateBelief(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)

    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$")
    claim: str = Field(min_length=1, max_length=400)
    evidence: str = Field(min_length=1, max_length=500)
    confidence: Literal["low", "medium", "high"]
    uncertainty: str = Field(min_length=1, max_length=300)


class BeliefUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    upsert: list[PrivateBelief] = Field(default_factory=list, max_length=3)
    remove: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [b.id for b in self.upsert] + self.remove
        if len(ids) != len(set(ids)):
            raise ValueError("Each belief id may appear only once in an update.")
        return self


class PrivateMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(default=0, ge=0)
    beliefs: list[PrivateBelief] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({b.id for b in self.beliefs}) != len(self.beliefs):
            raise ValueError("Private beliefs require distinct ids.")
        return self


def revise(memory: PrivateMemory | None, update: BeliefUpdate | None) -> PrivateMemory:
    """Apply an explicit patch; omitted claims survive, and no facts are invented."""
    memory = memory or PrivateMemory()
    if update is None:
        return memory.model_copy(deep=True)
    beliefs = {b.id: b for b in memory.beliefs}
    for belief_id in update.remove:
        if belief_id not in beliefs:
            raise ValueError(f"Cannot remove unknown private belief: {belief_id}")
        del beliefs[belief_id]
    for belief in update.upsert:
        beliefs[belief.id] = belief
    values = list(beliefs.values())
    return PrivateMemory(revision=memory.revision + int(values != memory.beliefs), beliefs=values)
