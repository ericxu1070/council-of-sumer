"""AGNTCY transport: each advisor is a separate A2A agent on a SLIM node.

- `run_node()` starts a SLIM data-plane node inside Python (no Docker needed).
- `serve_advisor()` serves one advisor as an A2A agent over SLIM patterns.
- `SlimCouncil.gather()` broadcasts a Brief to all four advisors in one SLIM
  group session (MLS-encrypted with the shared secret) and collects ballots.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

from sumer.advisors import ADVISOR_IDS, ADVISORS
from sumer.agent import decide
from sumer.config import settings
from sumer.protocol import Ballot, Brief

log = logging.getLogger(__name__)
NAMESPACE = "sumer/council"


def _env() -> None:
    # The SDK reads the shared secret from the environment when serving.
    os.environ.setdefault("SLIM_SHARED_SECRET", settings.slim_secret)


def agent_name(advisor_id: str) -> str:
    return f"{NAMESPACE}/{advisor_id}"


def agent_card(advisor_id: str):
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
    from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

    a = ADVISORS[advisor_id]
    return AgentCard(
        name=advisor_id,
        url="",
        version="1.0.0",
        description=f"{a.title}. Mandate: {a.mandate} Red line: {a.red_line}",
        defaultInputModes=["application/json"],
        defaultOutputModes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="deliberate",
                name="Deliberate on a council brief",
                description="Reads a council Brief and returns a signed Ballot.",
                tags=["council", "negotiation", "hamurabi"],
            )
        ],
        preferredTransport=InterfaceTransport.SLIM_PATTERNS,
        additional_interfaces=[
            AgentInterface(
                transport=InterfaceTransport.SLIM_PATTERNS,
                url=f"slim://{settings.slim_host}:{settings.slim_port}/{agent_name(advisor_id)}",
            )
        ],
    )


async def run_node() -> None:
    import slim_bindings as sb

    sb.initialize_with_defaults()
    svc = sb.get_global_service()
    endpoint = f"{settings.slim_host}:{settings.slim_port}"
    await svc.run_server_async(sb.new_insecure_server_config(endpoint))
    log.info("SLIM node listening on %s", endpoint)
    print(f"SLIM node listening on {endpoint}", flush=True)
    await asyncio.Event().wait()


def _executor(advisor_id: str):
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.utils import new_agent_text_message

    class AdvisorExecutor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            brief = Brief.model_validate_json(context.get_user_input())
            ballot = await decide(advisor_id, brief)
            await event_queue.enqueue_event(new_agent_text_message(ballot.model_dump_json()))

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            return None

    return AdvisorExecutor()


async def serve_advisor(advisor_id: str) -> None:
    _env()
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from agntcy_app_sdk.factory import AgntcyFactory

    factory = AgntcyFactory()
    session = factory.create_app_session(max_sessions=10)
    handler = DefaultRequestHandler(
        agent_executor=_executor(advisor_id), task_store=InMemoryTaskStore()
    )
    print(f"advisor {advisor_id} serving as {agent_name(advisor_id)}", flush=True)
    await session.add_a2a_card(agent_card(advisor_id), handler).with_factory(factory).start(
        keep_alive=True
    )


class SlimCouncil:
    """The clerk's side: broadcast a Brief, collect one Ballot per advisor."""

    def __init__(self, timeout_s: float = 90.0):
        self.timeout_s = timeout_s
        self._client = None

    async def connect(self) -> None:
        _env()
        from agntcy_app_sdk.factory import AgntcyFactory
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig, SlimTransportConfig

        cfg = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint=settings.slim_endpoint,
                name=f"{NAMESPACE}/clerk-{uuid.uuid4().hex[:6]}",
                shared_secret_identity=settings.slim_secret,
            )
        )
        self._client = await AgntcyFactory().a2a(cfg).create(agent_card(ADVISOR_IDS[0]))

    async def gather(self, brief: Brief) -> list[Ballot]:
        from a2a.types import Message, MessageSendParams, Part, Role, SendMessageRequest, TextPart

        if self._client is None:
            await self.connect()
        req = SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(
                message=Message(
                    role=Role.user,
                    messageId=str(uuid.uuid4()),
                    parts=[Part(root=TextPart(text=brief.model_dump_json()))],
                )
            ),
        )
        topic = f"council-y{brief.year}-r{brief.round}-{uuid.uuid4().hex[:8]}"
        responses = await self._client.broadcast_message(
            req,
            broadcast_topic=topic,
            recipients=[agent_name(a) for a in ADVISOR_IDS],
            timeout=self.timeout_s,
        )
        ballots = []
        for r in responses:
            try:
                result = r.model_dump(mode="json")["result"]
                ballots.append(Ballot.model_validate(json.loads(result["parts"][0]["text"])))
            except (KeyError, IndexError, TypeError, ValueError) as e:
                log.warning("unreadable ballot: %s", e)
        if len(ballots) < len(ADVISOR_IDS):
            got = {b.advisor for b in ballots}
            log.warning("missing ballots from %s", [a for a in ADVISOR_IDS if a not in got])
        return ballots
