"""AGNTCY transport: each advisor is a separate A2A agent on a SLIM node.

- `run_node()` starts a SLIM data-plane node inside Python (no Docker needed).
- `serve_advisor()` serves one advisor as an A2A agent over SLIM patterns.
- `SlimCouncil.gather()` sends concurrent A2A requests over addressed SLIM
  sessions, retaining the destination identity separately from the signed payload.
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
from sumer.protocol import Brief, Delivery, Reply, endpoint

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
        description=f"{a.title}. Mandate: {a.mandate} Concerns: {a.red_line}",
        defaultInputModes=["application/json"],
        defaultOutputModes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="deliberate",
                name="Deliberate on a council brief",
                description="Proposes, objects, revises, or returns a signed explicit YES/NO ballot.",
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
    """The clerk retains the addressed peer for each independently signed response."""

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

    async def gather(self, brief: Brief) -> list[Delivery]:
        from a2a.types import Message, MessageSendParams, Part, Role, SendMessageRequest, TextPart
        from agntcy_app_sdk.semantic.a2a.client.utils import message_translator

        if self._client is None:
            await self.connect()
        t0 = time.monotonic()
        async def one(peer: str) -> Delivery:
            try:
                req = SendMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(
                    message=Message(role=Role.user, messageId=brief.message_id,
                                    parts=[Part(root=TextPart(text=brief.model_dump_json()))])))
                message = message_translator(request=req.model_dump(mode="json", exclude_none=True))
                message.headers.update({"sumer-run-id": brief.run_id,
                                        "sumer-phase": brief.phase})
                response = await self._client.transport.request(
                    endpoint(peer), message, timeout=self.timeout_s)
                if response is None:
                    raise ValueError("Peer did not respond before the transport deadline.")
                payload = json.loads(response.payload)
                if payload.get("id") != req.id:
                    raise ValueError("Wrong A2A request identifier.")
                result = payload["result"]
                reply = Reply.model_validate_json(result["parts"][0]["text"])
                return Delivery(peer, endpoint(peer), reply=reply)
            except Exception as exc:  # noqa: BLE001 - peer failures are retried by the phase collector
                return Delivery(peer, endpoint(peer), error=f"{type(exc).__name__}: {exc}")
        deliveries = list(await asyncio.gather(*(one(p) for p in brief.targets)))
        self.last = {
            "transport": "AGNTCY SLIM (addressed A2A sessions)",
            "recipients": [endpoint(p) for p in brief.targets],
            "bytes_out": len(brief.model_dump_json()),
            "replies": sum(d.reply is not None for d in deliveries),
            "elapsed_s": round(time.monotonic() - t0, 2),
        }
        return deliveries
