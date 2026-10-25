"""Bind explicit tool/resource schemas and durable jobs to the official MCP SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from functools import partial
import hashlib
import json
import re
import sys

from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListResourcesResult, ListResourceTemplatesResult, ListToolsResult, PaginatedRequestParams, ReadResourceRequestParams, ReadResourceResult, Resource, ResourceTemplate, TextContent, TextResourceContents, Tool, ToolAnnotations

from src.mcp.common.contracts import ServerContext, canonical_bytes, error_response, load_json, make_response, validate_document
from src.mcp.common.files import read_verified_chunk, safe_path
from src.mcp.common.jobs import Cancel, JobManager, Progress, Reconcile, WriteOperation
from src.srim.common.types import JSONObject, JSONValue
from src.srim.common.files import _read_regular_bytes


ReadOperation = Callable[[str, JSONObject, ServerContext], JSONObject]


class ProtocolAdapter:
    """Bind context/catalog/read/write callbacks to one independent local stdio server.

    Construction validates static declarations and initializes its JobManager;
    run owns server lifecycle. SDK context never replaces the trusted workspace.
    """

    def __init__(self: ProtocolAdapter, context: ServerContext, catalog: JSONObject, read: ReadOperation, write: WriteOperation, *, reconcile: Reconcile | None = None) -> None:
        """Store validated context/catalog/read/write and optional reconcile callback; create an unstarted job manager, returning None without execution."""
        if catalog.get("server") != context.name or not isinstance(catalog.get("tools"), dict):
            raise ValueError("Invalid server catalog identity")
        self.context = context
        self.catalog = deepcopy(catalog)
        self.read = read
        self.jobs = JobManager(context, write, reconcile=reconcile)
        self.scheme = "srim" if context.name == "srim_mcp" else "project"

    def input_schema(self: ProtocolAdapter, name: str) -> Mapping[str, JSONValue] | None:
        """Return copied known tool name's input schema or None without side effects."""
        tool = self.catalog["tools"].get(name)
        return None if tool is None else deepcopy(tool["inputSchema"])

    async def list_tools(self: ProtocolAdapter, context: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListToolsResult:
        """Return deterministic SDK tool declarations for params' generation-pinned cursor.

        Context is unused protocol metadata; invalid cursor raises ValueError.
        No native or management operation runs while listing declarations.
        """
        offset = 0
        if params is not None and params.cursor is not None:
            generation, marker, number = params.cursor.partition(":")
            if marker != ":" or generation != self.catalog["generation"] or not number.isdigit():
                raise ValueError("conflict: invalid tool catalog cursor")
            offset = int(number)
        names = sorted(self.catalog["tools"])
        tools = []
        for name in names[offset:offset + 100]:
            definition = self.catalog["tools"][name]
            tools.append(Tool(name=name, description=definition["description"], inputSchema=definition["inputSchema"], outputSchema=definition["outputSchema"], annotations=ToolAnnotations(readOnlyHint=not definition["mutating"] and name != "job_cancel", destructiveHint=False, idempotentHint=True, openWorldHint=False)))
        cursor = self.catalog["generation"] + ":" + str(offset + 100) if offset + 100 < len(names) else None
        return ListToolsResult(tools=tools, nextCursor=cursor)

    async def call_tool(self: ProtocolAdapter, context: ServerRequestContext[object], params: CallToolRequestParams) -> CallToolResult:
        """Validate and serve params using trusted context and bounded structured output.

        Reads/waits run off-loop; accepted mutations outlive request cancellation.
        Improvement-event reads require .local/workflow-improvement ownership.
        Return SDK error envelopes for domain failures, never raw exception stacks.
        """
        try:
            name = params.name
            if name not in self.catalog["tools"]:
                raise ValueError("not-found: unknown tool")
            arguments = {} if params.arguments is None else deepcopy(params.arguments)
            if len(canonical_bytes(arguments)) > self.context.limits["max_request_bytes"]:
                raise ValueError("too-large: request exceeds configured size cap")
            definition = self.catalog["tools"][name]
            validate_document(arguments, definition["inputSchema"])
            for key, schema in definition["inputSchema"]["properties"].items():
                if key not in arguments and "default" in schema:
                    arguments[key] = deepcopy(schema["default"])
            if name == "capabilities_list":
                if arguments["expected_generation"] is not None and arguments["expected_generation"] != self.catalog["generation"]:
                    raise ValueError("conflict: capability catalog generation changed")
                families = {item["family"] for item in self.catalog["capabilities"].values()}
                if arguments["family"] is not None and arguments["family"] not in families:
                    raise ValueError("Invalid capability family")
                query = "" if arguments["query"] is None else arguments["query"].casefold()
                cards = [{key: item[key] for key in ("id", "title", "family", "summary", "tool_names", "implementation", "exposure")} for _, item in sorted(self.catalog["capabilities"].items()) if (arguments["family"] is None or item["family"] == arguments["family"]) and query in (item["id"] + " " + item["title"] + " " + item["summary"]).casefold()]
                data = {"generation": self.catalog["generation"], "total": len(cards), "offset": arguments["offset"], "limit": arguments["limit"], "matches": cards[arguments["offset"]:arguments["offset"] + arguments["limit"]]}
            elif name in ("capability_get", "schema_get", "example_get"):
                collection = {"capability_get": "capabilities", "schema_get": "schemas", "example_get": "examples"}[name]
                identity = arguments[{"capability_get": "capability_id", "schema_get": "schema_id", "example_get": "example_id"}[name]]
                if identity not in self.catalog[collection]:
                    raise ValueError("not-found: unknown catalog identity")
                document = deepcopy(self.catalog[collection][identity])
                if name == "capability_get" and self.context.name == "srim_mcp":
                    environment = await asyncio.to_thread(self.read, "environment_get", {}, self.context)
                    document["compatibility"]["installed"] = environment["installation"]
                data = {"id": identity, "sha256": hashlib.sha256(canonical_bytes(document)).hexdigest(), "schema": document} if name == "schema_get" else document
            elif name == "job_get":
                data = await asyncio.to_thread(self.jobs.wait, arguments["job_id"], arguments["after_sequence"], arguments["wait_seconds"])
            elif name == "jobs_list":
                data = await asyncio.to_thread(partial(self.jobs.list, **arguments))
            elif name == "job_cancel":
                data = await asyncio.to_thread(self.jobs.cancel, arguments["job_id"])
            elif name == "artifact_read":
                reference = arguments["reference"]
                if arguments["length"] > self.context.limits["file_chunk_bytes"] or reference["path"] is None or reference["sha256"] is None or reference["size_bytes"] is None:
                    raise ValueError("Invalid or oversized immutable file reference")
                path = safe_path(self.context.paths.root, reference["path"])
                server_root = self.context.paths.root / "memory" / "mcp" / self.context.name
                owned = False
                for folder, prefix in (("jobs", "job_"), ("objects", "object_"), ("artifacts", "artifact_")):
                    base = server_root / folder
                    if path.is_relative_to(base) and reference["id"].startswith(prefix):
                        owner = safe_path(base, reference["id"])
                        if not path.is_relative_to(owner):
                            continue
                        relative = path.relative_to(owner).as_posix()
                        if folder == "jobs":
                            state = self.jobs.get(reference["id"])
                            if relative in ("request.json", "result.json", "error.json"):
                                if relative == "request.json" or any(isinstance(state.get(key), dict) and state[key] == reference for key in ("result", "error")):
                                    owned = True
                        else:
                            manifest_path = owner / "manifest.json"
                            manifest = load_json(_read_regular_bytes(manifest_path))
                            if relative == "manifest.json":
                                owned = True
                            elif {"path": relative, "sha256": reference["sha256"], "size_bytes": reference["size_bytes"]} in manifest.get("files", manifest.get("outputs", [])):
                                owned = True
                if not owned and self.context.name == "project_mcp":
                    if reference["kind"] in ("case", "analysis") and reference["revision"] is not None:
                        from src.backend.project_records import read_record
                        selected = read_record(self.context.paths, reference["kind"], reference["id"], revision=reference["revision"])
                        base = (self.context.paths.cases if reference["kind"] == "case" else self.context.paths.postprocessing_memory) / reference["id"]
                        if path == base / "revisions" / f"r{reference['revision']:06d}.json" and reference["sha256"] == selected["reference"]["sha256"]:
                            owned = True
                        elif reference["kind"] == "analysis" and path.is_relative_to(base):
                            owned = any(file["path"] == path.relative_to(base).as_posix() and file["sha256"] == reference["sha256"] and file["size_bytes"] == reference["size_bytes"] for file in selected["record"]["artifacts"])
                    elif reference["kind"] == "capability" and reference["id"].startswith("candidate_"):
                        from src.plan.catalog import read_candidate
                        candidate = read_candidate(self.context.paths, reference["id"])
                        base = self.context.paths.root / "memory" / "project" / "candidates" / reference["id"]
                        owned = reference == candidate["reference"] or any(path == base / "files" / file["path"] and reference["sha256"] == file["sha256"] and reference["size_bytes"] == file["size_bytes"] for file in candidate["candidate"]["files"])
                    elif reference["kind"] == "improvement-event":
                        from src.plan.improvement import validate_improvement_event
                        if re.fullmatch(r"[0-9a-f-]{36}", reference["id"]) and path == self.context.paths.root / ".local" / "workflow-improvement" / "events" / (reference["id"] + ".json"):
                            validate_improvement_event(load_json(_read_regular_bytes(path)))
                            owned = True
                    elif reference["kind"] == "proposal":
                        from src.plan.improvement import read_improvement_status
                        if re.fullmatch(r"[0-9a-f-]{36}", reference["id"]):
                            status = read_improvement_status(self.context.paths, proposal_id=reference["id"])
                            owned = not status["issues"] and any(event["kind"] == "proposal" and event["details"]["path"] == reference["path"] and event["details"]["revision"] == reference["sha256"] for event in status["events"])
                if not owned:
                    raise ValueError("unsafe-path: file is not a listed immutable server artifact")
                data = await asyncio.to_thread(partial(read_verified_chunk, path, reference["sha256"], reference["size_bytes"], offset=arguments["offset"], length=arguments["length"]))
            elif definition["mutating"]:
                request_id = arguments.pop("request_id")
                if name == "run_submit" and arguments["prepared"]["engine"] == "sr" and arguments["timeout_seconds"] is None:
                    raise ValueError("SR requires a finite execution timeout")
                if name == "batch_submit":
                    if arguments["engine"] == "sr" and (arguments["timeout_seconds"] is None or arguments["worker_timeouts"] is not None or arguments["cleanup"] is False):
                        raise ValueError("Invalid SR batch timeout/cleanup options")
                    if len({task["simulation_id"] for task in arguments["tasks"]}) != len(arguments["tasks"]) or any(task["engine"] != arguments["engine"] for task in arguments["tasks"]):
                        raise ValueError("Mixed or duplicate prepared batch tasks")
                data = await asyncio.to_thread(self.jobs.submit, name, arguments, request_id)
            else:
                data = await asyncio.to_thread(self.read, name, arguments, self.context)
            response = make_response(data)
        except Exception as error:
            if not isinstance(error, (ValueError, OSError)):
                print(f"Tool failure {params.name}: {error!r}", file=sys.stderr)
            response = error_response(error)
        if len(canonical_bytes(response)) > self.context.limits["max_response_bytes"]:
            response = error_response(ValueError("too-large: response exceeds cap; use table/file pagination and existing references"))
        validate_document(response, self.catalog["tools"].get(params.name, {}).get("outputSchema", {"type": "object"}))
        text = json.dumps({"ok": response["ok"], "error": response["error"], "message": "Read structuredContent for the complete bounded response"}, ensure_ascii=False)
        return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent=response, isError=not response["ok"])

    async def list_resources(self: ProtocolAdapter, context: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListResourcesResult:
        """Return the three stable catalog resource roots; reject unknown cursor.

        Context is protocol metadata only. No files/defaults/engines are mutated.
        """
        if params is not None and params.cursor is not None:
            raise ValueError("Invalid resource-list cursor")
        return ListResourcesResult(resources=[Resource(uri=self.scheme + "://" + category, name=category, mimeType="application/json", description="Bounded index; full entries are available through templates and tools") for category in ("capabilities", "schemas", "examples")])

    async def list_resource_templates(self: ProtocolAdapter, context: ServerRequestContext[object], params: PaginatedRequestParams | None) -> ListResourceTemplatesResult:
        """List declared identity-only URI templates without accessing domain data.

        Invalid cursor raises ValueError; return independent SDK declarations.
        """
        if params is not None and params.cursor is not None:
            raise ValueError("Invalid resource-template cursor")
        templates = [("capabilities/{id}", "capability"), ("schemas/{id}", "schema"), ("examples/{id}", "example"), ("jobs/{id}", "job"), ("artifacts/{id}/manifest", "artifact-manifest")]
        if self.context.name == "project_mcp":
            templates.extend([("cases/{id}/{revision}", "case"), ("analyses/{id}/{revision}", "analysis"), ("collections/{collection}", "collection"), ("capabilities/{collection}/{id}/{version}", "registered-capability"), ("candidates/{id}", "candidate"), ("improvement/{proposal_id}", "improvement")])
        return ListResourceTemplatesResult(resourceTemplates=[ResourceTemplate(uriTemplate=self.scheme + "://" + path, name=name, mimeType="application/json") for path, name in templates])

    async def read_resource(self: ProtocolAdapter, context: ServerRequestContext[object], params: ReadResourceRequestParams) -> ReadResourceResult:
        """Map params.uri to declared tools/readers and return bounded JSON content.

        Validate identities and integer revisions; never interpret a URI as an
        arbitrary filesystem path. Missing/malformed resources raise ValueError.
        """
        uri = str(params.uri)
        prefix = self.scheme + "://"
        if not uri.startswith(prefix) or any(character in uri for character in ("?", "#", "%", "\\")):
            raise ValueError("unsafe-path: unsupported resource URI")
        parts = uri[len(prefix):].rstrip("/").split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("unsafe-path: invalid resource identity")
        if len(parts) == 1 and parts[0] in ("capabilities", "schemas", "examples"):
            if parts[0] == "capabilities":
                data = {"generation": self.catalog["generation"], "ids": sorted(self.catalog["capabilities"])}
            else:
                data = {"generation": self.catalog["generation"], "ids": sorted(self.catalog[parts[0]])}
            document = make_response(data)
        elif len(parts) == 2 and parts[0] in ("capabilities", "schemas", "examples", "jobs"):
            tool, key = {"capabilities": ("capability_get", "capability_id"), "schemas": ("schema_get", "schema_id"), "examples": ("example_get", "example_id"), "jobs": ("job_get", "job_id")}[parts[0]]
            result = await self.call_tool(context, CallToolRequestParams(name=tool, arguments={key: parts[1]}))
            document = result.structured_content
        elif len(parts) == 3 and parts[0] == "artifacts" and parts[2] == "manifest":
            if re.fullmatch(r"artifact_[0-9a-f-]{36}", parts[1]) is None:
                raise ValueError("Invalid artifact identity")
            target = safe_path(self.context.paths.root / "memory" / "mcp" / self.context.name / "artifacts", parts[1] + "/manifest.json")
            if target.stat().st_size > self.context.limits["max_response_bytes"]:
                raise ValueError("too-large: artifact manifest exceeds response cap")
            document = make_response(load_json(_read_regular_bytes(target)))
        elif self.context.name == "project_mcp":
            if len(parts) == 3 and parts[0] in ("cases", "analyses") and parts[2].isdigit():
                tool = "case_get" if parts[0] == "cases" else "analysis_get"
                arguments = {"case_id" if parts[0] == "cases" else "analysis_id": parts[1], "revision": int(parts[2])}
            elif len(parts) == 2 and parts[0] in ("collections", "candidates", "improvement"):
                tool, key = {"collections": ("collection_entries", "collection"), "candidates": ("candidate_get", "candidate_id"), "improvement": ("improvement_status", "proposal_id")}[parts[0]]
                arguments = {key: parts[1]}
            elif len(parts) == 4 and parts[0] == "capabilities":
                collection, identity, selected_version = parts[1:]
                if collection not in ("workflow", "analysis", "skill") or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identity) is None or len(identity) > 64 or re.fullmatch(r"v[1-9][0-9]*", selected_version) is None:
                    raise ValueError("Invalid registered capability resource identity")
                target = safe_path(self.context.paths.root / "memory" / "project" / "capabilities", collection + "/" + identity + "/" + selected_version + "/manifest.json")
                if target.stat().st_size > self.context.limits["max_result_bytes"]:
                    raise ValueError("too-large: registered capability manifest exceeds cap")
                digest = hashlib.sha256(_read_regular_bytes(target)).hexdigest()
                tool, arguments = "collection_entry_get", {"reference": {"collection": collection, "id": identity, "version": selected_version, "manifest_sha256": digest}}
            else:
                raise ValueError("not-found: unknown project resource")
            result = await self.call_tool(context, CallToolRequestParams(name=tool, arguments=arguments))
            document = result.structured_content
        else:
            raise ValueError("not-found: unknown resource")
        encoded = canonical_bytes(document)
        if len(encoded) > self.context.limits["max_response_bytes"]:
            raise ValueError("too-large: resource exceeds response cap")
        return ReadResourceResult(contents=[TextResourceContents(uri=params.uri, mimeType="application/json", text=encoded.decode("utf-8"))])

    async def serve(self: ProtocolAdapter) -> None:
        """Create SDK callbacks, serve stdio and close transport on completion.

        Return None; initialization/transport errors propagate to run's cleanup.
        No HTTP, subscriptions, prompts, sampling or remote access is advertised.
        """
        server = Server(self.context.name, version="1.0.0", instructions=self.catalog["instructions"], get_tool_input_schema=self.input_schema, on_list_tools=self.list_tools, on_call_tool=self.call_tool, on_list_resources=self.list_resources, on_list_resource_templates=self.list_resource_templates, on_read_resource=self.read_resource)
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())

    def run(self: ProtocolAdapter) -> None:
        """Own job/singleton lifetime around asyncio stdio serving, returning None.

        Always attempt graceful job shutdown before propagating startup/SDK errors.
        Diagnostics go to stderr; only SDK protocol messages use stdout.
        """
        self.jobs.start()
        try:
            asyncio.run(self.serve())
        finally:
            self.jobs.shutdown()
