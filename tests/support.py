from sumer.game import State
from sumer.protocol import Brief, sign, state_hash
from sumer.transport import inproc


def brief(keys, phase="proposal", **kwargs):
    state = State()
    return Brief(run_id="test-run", year=1, round=0, max_rounds=2, phase=phase,
                 state=state.public(), state_hash=state_hash(state), peer_keys=keys, **kwargs)


async def voters(request, yes=("farmer", "priest", "general")):
    deliveries = await inproc.gather(request)
    if request.phase == "vote":
        for delivery in deliveries:
            delivery.reply = sign(delivery.reply.model_copy(update={
                "choice": "YES" if delivery.peer in yes else "NO",
                "speech": f"{delivery.peer} explicit decision on the frozen candidate.",
            }))
    return deliveries


async def _emit(kind, data):
    pass
