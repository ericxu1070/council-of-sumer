"""AGNTCY transport: each advisor is a separate A2A agent on a SLIM node.

- `run_node()` starts a SLIM data-plane node inside Python (no Docker needed).
- `serve_advisor()` serves one advisor as an A2A agent over SLIM patterns.
- `SlimCouncil.ask()` sends one turn's Brief to the one advisor whose turn it
  is, over a SLIM point-to-point session to that advisor's own name, and
  returns its Ballot. If unicast fails it falls back to a one-member
  group session (A2A broadcast).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from sumer.advisors import ADVISOR_IDS, ADVISORS
from sumer.agent import decide
from sumer.config import settings
from sumer.observability import initialize, span
from sumer.protocol import Ballot, Brief, Delivery, endpoint

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
                description="Reads a council Brief and returns a Ballot.",
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
            with span("transport.receive", parent=brief.traceparent, run_id=brief.run_id,
                      year=brief.year, phase=brief.phase, peer=advisor_id):
                if brief.speaker and brief.speaker != advisor_id:
                    log.warning("brief for %s delivered to %s", brief.speaker, advisor_id)
                ballot = await decide(advisor_id, brief)
                await event_queue.enqueue_event(new_agent_text_message(ballot.model_dump_json()))

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            return None

    return AdvisorExecutor()


async def serve_advisor(advisor_id: str) -> None:
    _env()
    initialize(f"sumer-{advisor_id}")
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
    """The clerk's side: send each turn's Brief to its speaker, collect its Ballot."""

    def __init__(self, timeout_s: float = 90.0):
        self.timeout_s = timeout_s
        self._client = None
        self.last: dict = {}

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

    def _request(self, brief: Brief):
        from a2a.types import Message, MessageSendParams, Part, Role, SendMessageRequest, TextPart

        return SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(
                message=Message(
                    role=Role.user,
                    messageId=brief.message_id,
                    parts=[Part(root=TextPart(text=brief.model_dump_json()))],
                )
            ),
        )

    async def ask(self, brief: Brief) -> Delivery:
        with span("transport.request", parent=brief.traceparent, run_id=brief.run_id, year=brief.year, phase=brief.phase, peer=brief.speaker):
            from agntcy_app_sdk.semantic.a2a.client.utils import message_translator

            if self._client is None:
                await self.connect()
            speaker = brief.speaker
            req = self._request(brief)
            t0 = time.monotonic()
            mode = "A2A unicast, SLIM point-to-point session"
            topic = agent_name(speaker)
            payload = None
            message = message_translator(request=req.model_dump(mode="json", exclude_none=True))
            message.headers.update({"traceparent": brief.traceparent, "sumer-run-id": brief.run_id, "sumer-phase": brief.phase})
            try:
                reply = await self._client.transport.request(
                    topic,
                    message,
                    timeout=int(self.timeout_s),
                )
                payload = json.loads(reply.payload.decode("utf-8")) if reply else None
            except Exception as e:  # noqa: BLE001 - any SDK failure falls back to a group session
                log.warning("unicast to %s failed (%s), falling back to a group session", speaker, e)
            if payload is None:
                mode = "A2A broadcast, one-member MLS group session"
                topic = f"council-y{brief.year}-r{brief.round}-{speaker}-{uuid.uuid4().hex[:8]}"
                responses = await self._client.broadcast_message(
                    req, broadcast_topic=topic, recipients=[agent_name(speaker)],
                    timeout=self.timeout_s,
                )
                payload = responses[0].model_dump(mode="json") if responses else None
                topic = f"{NAMESPACE}/{topic}"
            ballot = None
            try:
                if payload is not None:
                    result = payload["result"]
                    ballot = Ballot.model_validate(json.loads(result["parts"][0]["text"]))
            except (KeyError, IndexError, TypeError, ValueError) as e:
                log.warning("unreadable ballot from %s: %s", speaker, e)
            self.last = {
                "transport": f"AGNTCY SLIM ({mode})",
                "topic": topic,
                "recipients": [agent_name(speaker)],
                "bytes_out": len(brief.model_dump_json()),
                "replies": int(ballot is not None),
                "elapsed_s": round(time.monotonic() - t0, 2),
            }
            if ballot is None:
                log.warning("no ballot from %s", speaker)
            return Delivery(speaker, endpoint(speaker), reply=ballot)
