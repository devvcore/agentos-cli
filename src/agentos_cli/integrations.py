"""Thin CLI over AgentOS's authorized MCP integration dispatcher."""
from __future__ import annotations
import argparse
import json
import sys
from .mcp import Client


SERVICE_TOOLS = {
    "semrush": ("compare-domains", "SEMRUSH_DOMAIN_VS_DOMAIN"),
    "notion": ("read-block", "NOTION_FETCH_BLOCK_CONTENTS"),
    "stripe": ("list-invoices", "STRIPE_LIST_INVOICES"),
}


def _bounded(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return number


def _rpc(client, tool, args):
    response = client._post({"jsonrpc": "2.0", "id": next(client._ids), "method": "tools/call",
                            "params": {"name": "workspace_call", "arguments": {
                                "name": tool, "arguments": json.dumps(args)}}})
    if not isinstance(response, dict) or "error" in response:
        raise ValueError("transport failed")
    result = response.get("result", {})
    return result, result.get("structuredContent", result)


def service_main(service, argv=None, *, client_factory=Client):
    """Typed observed operations; exact account enforcement remains server-owned."""
    import uuid
    command, slug = SERVICE_TOOLS[service]
    parser = argparse.ArgumentParser(prog="aos " + service)
    commands = parser.add_subparsers(dest="command", required=True)
    action = commands.add_parser(command)
    action.add_argument("--account", required=True, help="Exact reviewed connection ID; no default account.")
    action.add_argument("--limit", type=_bounded, default=20)
    if service == "semrush":
        action.add_argument("--domains", required=True, help="Reviewed Semrush comparison expression: <sign>|<or/ad>|<domain> entries.")
        action.add_argument("--database", required=True)
        action.add_argument("--offset", type=int, default=0)
    elif service == "notion":
        action.add_argument("--block", required=True, type=lambda value: str(uuid.UUID(value)))
        action.add_argument("--cursor")
    else:
        action.add_argument("--cursor")
        action.add_argument("--customer")
        action.add_argument("--status", choices=["draft", "open", "paid", "uncollectible", "void"])
    opts = parser.parse_args(argv)
    if not opts.account.strip():
        parser.error("--account cannot be empty")
    if service == "semrush":
        if opts.offset < 0:
            parser.error("--offset cannot be negative")
        payload = {"domains": opts.domains, "database": opts.database,
                   "display_limit": opts.limit, "display_offset": opts.offset}
    elif service == "notion":
        payload = {"block_id": opts.block, "page_size": opts.limit}
        if opts.cursor: payload["start_cursor"] = opts.cursor
    else:
        payload = {"limit": opts.limit}
        for flag, field in (("cursor", "starting_after"), ("customer", "customer"), ("status", "status")):
            if getattr(opts, flag): payload[field] = getattr(opts, flag)
    try:
        client = client_factory()
        client.initialize(client_name="agentos-service-cli")
        result, described = _rpc(client, "integration_search", {"q": slug, "tool_slug": slug})
        rows = described.get("tools", []) if isinstance(described, dict) else []
        if result.get("isError") or len(rows) != 1:
            print(json.dumps({"ok": False, "error_code": "operation_unavailable"}))
            return 1
        operation = rows[0]
        if operation.get("slug") != slug:
            print(json.dumps({"ok": False, "error_code": "operation_unavailable"}))
            return 1
        # Old curated descriptors cannot safely select an account. Never let a
        # requested account silently degrade to the dispatcher's default.
        eligible = {row.get("connection_id") for row in operation.get("accounts", []) if isinstance(row, dict)}
        if operation.get("connection_id") != opts.account and not (operation.get("supports_explicit_account") is True and opts.account in eligible):
            print(json.dumps({"ok": False, "error_code": "account_selection_unavailable"}))
            return 1
        schema = operation.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        if any(key not in properties for key in payload) or any(key not in payload for key in schema.get("required", [])):
            print(json.dumps({"ok": False, "error_code": "reviewed_schema_changed"}))
            return 1
        for key, value in payload.items():
            rule = properties[key]
            if ("enum" in rule and value not in rule["enum"]) or (isinstance(value, int) and (value < rule.get("minimum", value) or value > rule.get("maximum", value))):
                print(json.dumps({"ok": False, "error_code": "invalid_typed_argument"}))
                return 1
        result, response = _rpc(client, "integration_call", {
            "tool_slug": operation["slug"], "connection_id": opts.account, "arguments": payload})
        print(json.dumps(response, ensure_ascii=False))
        if result.get("isError") or isinstance(response, dict) and (response.get("ok") is False or response.get("error_code") or response.get("status") in ("failed", "denied", "expired")):
            return 1
        if isinstance(response, dict) and (response.get("queued_for_approval") is True or response.get("status") in ("awaiting_approval", "pending_approval")):
            return 2
        return 0
    except Exception:
        print("AgentOS transport failed; the request was not retried.", file=sys.stderr)
        return 1


def main(argv=None, *, client_factory=Client):
    parser = argparse.ArgumentParser(prog="aos integrations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--offset", type=int, default=0)
    describe = commands.add_parser("describe")
    describe.add_argument("tool")
    call = commands.add_parser("call")
    call.add_argument("tool")
    call.add_argument("--args-file", default="-", help="JSON object file; default stdin. Never put provider credentials here.")
    opts = parser.parse_args(argv)
    if opts.command == "list":
        tool, args = "integration_list", {}
    elif opts.command == "search":
        tool, args = "integration_search", {"q": opts.query, "offset": opts.offset}
    elif opts.command == "describe":
        tool, args = "integration_search", {"q": opts.tool, "tool_slug": opts.tool}
    else:
        try:
            if opts.args_file == "-":
                payload = json.load(sys.stdin)
            else:
                with open(opts.args_file, encoding="utf-8") as handle:
                    payload = json.load(handle)
            if not isinstance(payload, dict):
                raise ValueError("arguments must be an object")
        except (OSError, ValueError):
            print("Invalid arguments: provide a JSON object through stdin or --args-file.", file=sys.stderr)
            return 2
        tool, args = "integration_call", {"tool_slug": opts.tool, "arguments": payload}
    try:
        client = client_factory()
        client.initialize(client_name="agentos-integrations-cli")
        response = client._post({"jsonrpc": "2.0", "id": next(client._ids), "method": "tools/call",
                                 "params": {"name": "workspace_call", "arguments": {
                                     "name": tool, "arguments": json.dumps(args)}}})
        # Preserve domain errors and pending approvals; neither is success.
        if not isinstance(response, dict) or "error" in response:
            print("AgentOS transport failed. Check your AgentOS identity and connection; the request was not retried.", file=sys.stderr)
            return 1
        result = response.get("result", {})
        payload = result.get("structuredContent", result)
        print(json.dumps(payload, ensure_ascii=False))
        if result.get("isError"):
            return 1
        if isinstance(payload, dict):
            if payload.get("ok") is False or payload.get("error_code") or payload.get("status") in ("failed", "denied", "expired"):
                return 1
            if payload.get("queued_for_approval") is True or payload.get("status") in ("awaiting_approval", "pending_approval"):
                return 2
        return 0
    except Exception:
        # Exception messages can contain URLs, headers or upstream bodies.
        print("AgentOS transport failed. Check your AgentOS identity and connection; the request was not retried.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
