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
import sys
import time
import uuid

from sumer.advisors import ADVISOR_IDS, ADVISORS, Advisor, advisors_for
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


def agent_card(advisor_id: str, advisor: Advisor | None = None):
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
    from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

    a = advisor or ADVISORS[advisor_id]
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
                    raise ValueError("Brief was delivered to the wrong advisor endpoint.")
                ballot = await decide(advisor_id, brief)
                await event_queue.enqueue_event(new_agent_text_message(ballot.model_dump_json()))

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            return None

    return AdvisorExecutor()


async def serve_advisor(advisor_id: str, advisor: Advisor | None = None) -> None:
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
    await session.add_a2a_card(agent_card(advisor_id, advisor), handler).with_factory(factory).start(
        keep_alive=False
    )
    print(f"advisor {advisor_id} ready", flush=True)
    await session.start_all_sessions(keep_alive=True)


class SlimCouncil:
    """The clerk's side: send each turn's Brief to its speaker, collect its Ballot."""

    def __init__(self, timeout_s: float = 90.0):
        self.timeout_s = timeout_s
        self._client = None
        self._workers: dict[str, asyncio.subprocess.Process] = {}
        self._worker_output: dict[str, asyncio.Task] = {}
        self._worker_lock = asyncio.Lock()
        self.last: dict = {}

    async def _ensure_worker(self, advisor: Advisor) -> None:
        """Custom seats are A2A peers; presets keep their existing external workers."""
        if advisor.id in ADVISORS or advisor.id in self._workers:
            return
        async with self._worker_lock:
            if advisor.id in self._workers:
                return
            _env()
            # SDK 0.5.5 stores a single SLIM identity per process. Each custom
            # peer therefore needs its own process, just like the preset peers.
            worker_code = (
                "import asyncio,json,sys; "
                "from sumer.advisors import resolve_council; "
                "from sumer.transport.slim import serve_advisor; "
                "advisor=resolve_council([json.loads(sys.argv[1])]); "
                "aid=next(iter(advisor)); "
                "asyncio.run(serve_advisor(aid, advisor[aid]))"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", worker_code, json.dumps(advisor.public()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            self._workers[advisor.id] = process
            ready = f"advisor {advisor.id} ready"

            async def wait_ready():
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        raise RuntimeError(f"Custom advisor {advisor.short} failed to start.")
                    if line.decode(errors="replace").strip() == ready:
                        return

            await asyncio.wait_for(wait_ready(), timeout=30)
            self._worker_output[advisor.id] = asyncio.create_task(
                _drain_worker_output(process, advisor.id)
            )

    async def close(self) -> None:
        """Release this run's custom peers and its clerk connection."""
        async with self._worker_lock:
            workers, self._workers = list(self._workers.values()), {}
            for process in workers:
                if process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            output_tasks, self._worker_output = list(self._worker_output.values()), {}
            for task in output_tasks:
                task.cancel()
            await asyncio.gather(*output_tasks, return_exceptions=True)
            client, self._client = self._client, None
            if client is not None:
                try:
                    await client.transport.close()
                finally:
                    await _cancel_listener(client.transport)
                    # Clear SDK's cached identity after disconnect so a later
                    # game creates a fresh clerk connection.
                    from agntcy_app_sdk.transport.slim import common
                    common.global_slim = None
                    common.global_slim_service = None
                    common.global_connection_id = None

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
            roster = advisors_for(brief.advisors)
            if speaker not in roster:
                raise ValueError("The speaker is not seated in this council.")
            await self._ensure_worker(roster[speaker])
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


async def _cancel_listener(transport) -> None:
    task = getattr(transport, "_listener_task", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _drain_worker_output(process: asyncio.subprocess.Process, advisor_id: str) -> None:
    """Keep an agent's logs from filling its stdout pipe during a long run."""
    while line := await process.stdout.readline():
        log.debug("custom advisor %s: %s", advisor_id, line.decode(errors="replace").rstrip())
