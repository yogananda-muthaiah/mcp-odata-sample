"""MCP server exposing the public OData V4 sample service as tools.

Runs over streamable HTTP so it can be deployed to SAP BTP Cloud Foundry.
MCP endpoint:  https://<app-url>/mcp
Health check:  https://<app-url>/health
"""
import os
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

ODATA_BASE = os.environ.get("ODATA_BASE_URL", "https://services.odata.org/V4/OData/OData.svc").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))  # Cloud Foundry injects PORT
TIMEOUT = httpx.Timeout(30.0)
HEADERS = {"Accept": "application/json"}

mcp = FastMCP(
    "odata-sample-server",
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,  # no session affinity needed when running multiple CF instances
    json_response=True,
)


async def _get(path: str = "", params: Optional[dict] = None, accept: str = "application/json"):
    url = f"{ODATA_BASE}/{path}".rstrip("/") if path else ODATA_BASE + "/"
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers={"Accept": accept})
        resp.raise_for_status()
        return resp.json() if "json" in accept else resp.text


def _error(e: Exception) -> dict:
    if isinstance(e, httpx.HTTPStatusError):
        return {"error": f"OData service returned {e.response.status_code}", "details": e.response.text[:500]}
    return {"error": str(e)}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


@mcp.tool()
async def list_entity_sets() -> dict:
    """List all entity sets (tables) available in the OData service, e.g. Products, Categories, Suppliers."""
    try:
        data = await _get()
        return {"entity_sets": [v["name"] for v in data.get("value", []) if v.get("kind") == "EntitySet"]}
    except Exception as e:
        return _error(e)


@mcp.tool()
async def describe_entity(entity_set: str) -> dict:
    """Describe the fields (name and type) and key of an entity set, parsed from $metadata.
    Use this before building a $filter or $select so the property names are correct."""
    try:
        xml_text = await _get("$metadata", accept="application/xml")
        root = ET.fromstring(xml_text)
        ns = {"edm": "http://docs.oasis-open.org/odata/ns/edm"}
        set_el = next((s for s in root.iter("{%s}EntitySet" % ns["edm"]) if s.get("Name") == entity_set), None)
        if set_el is None:
            return {"error": f"Unknown entity set '{entity_set}'. Call list_entity_sets first."}
        type_name = set_el.get("EntityType", "").split(".")[-1]
        type_el = next((t for t in root.iter("{%s}EntityType" % ns["edm"]) if t.get("Name") == type_name), None)
        if type_el is None:
            return {"error": f"Entity type for '{entity_set}' not found in metadata."}
        return {
            "entity_set": entity_set,
            "key": [r.get("Name") for r in type_el.findall("edm:Key/edm:PropertyRef", ns)],
            "properties": [{"name": p.get("Name"), "type": p.get("Type")} for p in type_el.findall("edm:Property", ns)],
            "navigation_properties": [n.get("Name") for n in type_el.findall("edm:NavigationProperty", ns)],
        }
    except Exception as e:
        return _error(e)


@mcp.tool()
async def query_entities(
    entity_set: str,
    filter: Optional[str] = None,
    select: Optional[str] = None,
    orderby: Optional[str] = None,
    expand: Optional[str] = None,
    top: int = 10,
    skip: int = 0,
    count: bool = False,
) -> dict:
    """Query an entity set with OData system query options.

    Examples:
      entity_set="Products", filter="Price gt 20", orderby="Price desc", top=5
      entity_set="Products", filter="contains(Name,'Milk')", select="Name,Price"
      entity_set="Products", expand="Category"
    """
    params = {"$top": max(1, min(top, 100)), "$skip": max(0, skip)}
    if filter:
        params["$filter"] = filter
    if select:
        params["$select"] = select
    if orderby:
        params["$orderby"] = orderby
    if expand:
        params["$expand"] = expand
    if count:
        params["$count"] = "true"
    try:
        data = await _get(entity_set, params)
        result = {"count": len(data.get("value", [])), "value": data.get("value", [])}
        if "@odata.count" in data:
            result["total_count"] = data["@odata.count"]
        return result
    except Exception as e:
        return _error(e)


@mcp.tool()
async def get_entity(entity_set: str, key: str, expand: Optional[str] = None) -> dict:
    """Fetch one entity by key, e.g. entity_set="Products", key="0". Use quotes for string keys: key="'abc'"."""
    params = {"$expand": expand} if expand else None
    try:
        return await _get(f"{entity_set}({key})", params)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def search_products(text: str, top: int = 10) -> dict:
    """Convenience search: find Products whose Name or Description contains the given text (case-sensitive)."""
    safe = text.replace("'", "''")
    flt = f"contains(Name,'{safe}') or contains(Description,'{safe}')"
    return await query_entities("Products", filter=flt, top=top)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")