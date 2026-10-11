from sumer.protocol import bind, sign
from sumer.transport import inproc


async def voters(brief):
    return await inproc.ask(brief)


async def unanimous(brief):
    from sumer.advisors import safe_plan
    from sumer.game import State
    from sumer.protocol import Ballot, Plan
    state = State(**{k: v for k, v in brief.state.items() if k not in ("starved", "acres_per_person")})
    plan = Plan(**safe_plan(state).as_dict())
    matches = [t.id for t in brief.table if t.plan == plan]
    return sign(bind(Ballot(advisor=brief.speaker, year=brief.year, round=brief.round,
        phase=brief.phase, proposal=plan, accept=matches, speech="We agree on this budget."), brief))
