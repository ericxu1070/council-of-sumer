"""Phase-specific model decisions. No policy scores or forecasts enter live prompts."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from sumer import llm
from sumer.advisors import ADVISORS
from sumer.config import settings
from sumer.game import State
from sumer.observability import context_headers, span
from sumer.protocol import Brief, Certificate, Plan, Reply, sign, verify_certificate

RULES = """Hamurabi: ten years; 20 bushels feed one resident; each resident tends 10 acres.
Planting consumes floor(acres/2) bushels. Trade first, then feed, then plant.
Rats eat remaining stores before the new harvest is added. Yield is 1..5 per acre.
Rats roll uniformly from 1..5: an even roll eats floor(stores/roll), otherwise none.
Land costs the known current price (17..26). Immigration is computed before starvation,
as floor(factor*(20*acres+ending grain)/(starting population*100))+1, with a hidden
uniform factor of 1..5. Unfed residents starve; partial starvation still permits arrivals.
Then arrivals and plague appear at the following report. Plague probability is 15%;
it leaves floor(population/2) survivors. More than 45% starvation overthrows the council.
Buying and selling use one signed land_delta. Never overspend or exceed land/workforce.
Appraisal uses average annual starvation (including zero years) and final acres per survivor.
The final rating is Nero if average starvation >33% or acres/person <7; Henry VIII if
>10% or <9; fair if >3% or <10; otherwise Jefferson. Zero population ends the reign.
Apply the final year's arrivals and plague before rating. Hidden events remain unknown.
No advisor has a veto. Every candidate requires all four YES/NO ballots; three YES adopt.
An unresolved bounded negotiation pauses for a human. Explain your own arithmetic and
assumptions, address objections, and decide according to your mandate and the public facts."""


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speech: str = Field(min_length=1)
    proposal: Plan | None = None
    assumed_price: StrictInt | None = None
    choice: Literal["YES", "NO"] | None = None
    arithmetic: str = ""
    assumptions: str = ""
    expected_effects: str = ""
    objections_addressed: list[str] = Field(default_factory=list)
    cited_message_ids: list[str] = Field(default_factory=list)


def clean(text: object, limit: int = 2000) -> str:
    return str(text or "").replace("—", " - ").replace("–", "-").strip()[:limit]


def messages(peer: str, brief: Brief) -> list[dict]:
    mandates = [{"id": a.id, "mandate": a.mandate, "concerns": a.red_line}
                for a in ADVISORS.values()]
    schema = {
        "proposal": 'Required keys: proposal (an object with integer land_delta, feed, plant), '
                    'assumed_price (current integer quote), speech, arithmetic, assumptions, '
                    'expected_effects (strings). Choose all budget amounts independently.',
        "revision": 'Required keys: proposal (an object with integer land_delta, feed, plant), '
                    'assumed_price (current integer quote), speech, arithmetic, assumptions, '
                    'expected_effects (strings), objections_addressed (array of recorded objection '
                    'or failed-ballot message IDs). '
                    'Choose all budget amounts independently; explain why changed or retained.',
        "objection": '{"speech":"Concrete objections to the proposed budgets; identify the author '
                     'and arithmetic/assumption you dispute","cited_message_ids":["public message ID"]}',
        "vote": '{"choice":"YES or NO","speech":"reason for this exact budget",'
                '"cited_message_ids":["public message ID"]}',
    }[brief.phase]
    system = (f"You are {ADVISORS[peer].title}. Mandate: {ADVISORS[peer].mandate}. "
              f"Your concerns: {ADVISORS[peer].red_line}. These guide your own YES/NO judgment; "
              "they are not application-enforced vetoes.\n" + RULES +
              f"\nAll public mandates: {json.dumps(mandates)}\n"
              "Return only one JSON object for the requested phase: " + schema)
    public = {
        "phase": brief.phase, "year": brief.year, "cycle": brief.round,
        "city": brief.state, "historical_ledger": brief.history,
        "negotiation_transcript": brief.transcript,
        "candidates": [c.model_dump() for c in brief.table],
        "candidate_to_vote_on": brief.candidate.model_dump() if brief.candidate else None,
        "correction_required": brief.errors.get(peer),
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(public)}]


def fixture(peer: str, brief: Brief) -> Output:
    """Explicit offline fixture; never invoked as a fallback for a model failure."""
    from sumer.advisors import Beliefs, Scorer
    from sumer.baselines import dictator
    state = dict(brief.state)
    state.pop("acres_per_person", None)
    state = State(**state)
    references = [r["message_id"] for r in brief.transcript if r.get("phase") == "objection"]
    if not references:
        references = [r["message_id"] for r in brief.transcript if r.get("kind") == "failed_ballot"]
    if brief.phase in ("proposal", "revision"):
        d = dictator(peer)(state, Beliefs())
        return Output(
            proposal=Plan(**d.as_dict()), assumed_price=state.land_price,
            speech=f"Offline fixture for {ADVISORS[peer].title}: {d.as_dict()}.",
            arithmetic=f"Trade {d.land_delta * state.land_price}, food {d.feed}, seed {d.plant // 2}.",
            assumptions="Expected harvest is uncertain.", expected_effects="Mandate-specific budget.",
            objections_addressed=references[-4:] if brief.phase == "revision" else [],
        )
    if brief.phase == "objection":
        return Output(speech=f"Consider {ADVISORS[peer].mandate} when revising these budgets.",
                      cited_message_ids=[r["message_id"] for r in brief.transcript[-4:]])
    score = Scorer(state, Beliefs(), [brief.candidate.plan.decision()])
    yes = score.norm(peer, brief.candidate.plan.decision()) >= 0.4
    return Output(choice="YES" if yes else "NO",
                  speech=f"Offline fixture {'supports' if yes else 'opposes'} this budget.")


async def decide(peer: str, brief: Brief) -> Reply:
    # Every peer can independently verify previously adopted certificates.
    if brief.history and brief.history[-1].get("certificate"):
        previous = brief.history[-1]
        old = dict(previous["start_state"])
        old.pop("acres_per_person", None)
        verify_certificate(Certificate.model_validate(previous["certificate"]),
                           State(**old), brief.run_id, brief.peer_keys)
    with span("agent.phase", parent=brief.traceparent, run_id=brief.run_id, year=brief.year,
              phase=brief.phase, peer=peer,
              candidate_hash=brief.candidate.hash if brief.candidate else None):
        metadata = {}
        error = None
        if settings.llm_mode == "bayleaf":
            with span("model.call", run_id=brief.run_id, year=brief.year,
                      phase=brief.phase, peer=peer, model=settings.llm_model) as model_span:
                try:
                    answer = await llm.chat(messages(peer, brief))
                    metadata = answer.meta()
                    data = Output.model_validate(llm.parse_json(answer.content, strict=True))
                except (llm.LLMUnavailable, ValueError, TypeError) as exc:
                    # Keep failures and their reported spend in the signed response journal.
                    metadata = getattr(exc, "usage", None) or metadata
                    error = f"{type(exc).__name__}: {exc}"
                    if model_span is not None:
                        model_span.record_exception(exc)
                        model_span.set_attribute("sumer.error", error)
                    data = Output(speech="Unable to produce a valid phase response.")
            source = "llm"
        elif settings.llm_mode == "mock":
            data = fixture(peer, brief)
            source = "policy"
        else:
            raise llm.LLMUnavailable(f"Unknown LLM_MODE: {settings.llm_mode}")
        context = context_headers()
        return sign(Reply(
            run_id=brief.run_id, year=brief.year, round=brief.round, phase=brief.phase,
            advisor=peer, state_hash=brief.state_hash, reply_to=brief.message_id,
            candidate_hash=brief.candidate.hash if brief.candidate else None,
            source=source, trace={"llm": metadata, **context} if metadata else context,
            traceparent=context.get("traceparent", ""), error=error, **data.model_dump(),
        ))
