# OData MCP Server on SAP BTP Cloud Foundry

An MCP (Model Context Protocol) server, written in Python, that lets an AI client ask questions about the public OData V4 sample service and get answers from live data.

- Data source: https://services.odata.org/V4/OData/OData.svc/
- Transport: streamable HTTP
- MCP endpoint: `https://<your-app-route>/mcp`
- Health check: `https://<your-app-route>/health`

---

## 1. How it works

```
User question
     |
     v
MCP client (Claude Desktop, MCP Inspector, your own agent)
     |   the LLM decides which tool to call
     v
MCP server (this project, running on BTP Cloud Foundry)
     |   translates the tool call into an OData request
     v
OData V4 service (services.odata.org)
     |
     v
JSON result -> back to the client -> answer for the user
```

The server does not answer questions by itself. It exposes **tools**. The LLM in your MCP client reads the user's question, picks tools (for example "list entity sets", then "query Products where Price > 20") and builds the answer from the returned data.

---

## 2. Project files

| File | Purpose |
|------|---------|
| `server.py` | The MCP server and its tools |
| `requirements.txt` | Python dependencies (`mcp<2`, `httpx`) |
| `manifest.yml` | Cloud Foundry deployment settings |
| `runtime.txt` | Requested Python version for the buildpack |

---

## 3. What each part of `server.py` does

1. **Configuration**: reads `PORT` (set by Cloud Foundry) and `ODATA_BASE_URL` (defaults to the sample service).
2. **FastMCP instance**: created with `host="0.0.0.0"` so Cloud Foundry can reach it, and `stateless_http=True` so you can run several instances without session affinity.
3. **`/health` route**: simple endpoint used by Cloud Foundry to check the app is alive.
4. **Tools** the LLM can call:

| Tool | What it does |
|------|--------------|
| `list_entity_sets` | Lists the available tables (Products, Categories, Suppliers, ...) |
| `describe_entity` | Shows fields, types and key of an entity set (read from `$metadata`) |
| `query_entities` | Queries data with `$filter`, `$select`, `$orderby`, `$expand`, `$top`, `$skip`, `$count` |
| `get_entity` | Fetches one record by key |
| `search_products` | Quick text search on product name or description |

5. **Startup**: `mcp.run(transport="streamable-http")` serves the MCP endpoint at `/mcp`.

---

## 4. Prerequisites

- A BTP subaccount with Cloud Foundry enabled, and a space where you can deploy
- Cloud Foundry CLI (`cf`) installed: https://docs.cloudfoundry.org/cf-cli/install-go-cli.html
- Python 3.10+ (only needed if you want to test locally)

---

## 5. Test locally (optional)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py
```

The server starts on port 8080. Check it:

```bash
curl http://localhost:8080/health
```

Test the tools with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

Choose transport **Streamable HTTP** and URL `http://localhost:8080/mcp`.

---

## 6. Deploy to BTP Cloud Foundry

```bash
# 1. Log in (use your region's API endpoint, shown in the BTP cockpit)
cf login -a https://api.cf.<region>.hana.ondemand.com --sso

# 2. Select org and space
cf target -o <org> -s <space>

# 3. Deploy (run inside the project folder)
cf push

# 4. Get the route
cf app odata-mcp-server
```

The `routes:` line in the output is your base URL. Your MCP URL is that route plus `/mcp`.

Useful commands:

```bash
cf logs odata-mcp-server --recent   # view logs
cf restart odata-mcp-server         # restart
cf scale odata-mcp-server -i 2      # run 2 instances (works because the server is stateless)
```

---

## 7. Connect a client and ask questions

Open the MCP Inspector (or any MCP client that supports streamable HTTP), use `https://<route>/mcp`, then try questions like:

- "Which entity sets are available?"
- "Show the 5 most expensive products."
- "Find products with 'Milk' in the name."
- "Which category does product 0 belong to?"

The LLM turns each question into tool calls such as `query_entities(entity_set="Products", orderby="Price desc", top=5)`.

---

## 8. Problems we hit and how they were fixed

### Error: `No module named 'mcp.server.fastmcp'` (mcp 2.x)

- **Cause**: `requirements.txt` had `mcp>=1.9.0` with no upper limit, so the buildpack installed mcp 2.x, which renamed `FastMCP` to `MCPServer` and changed other APIs.
- **Fix**: pin the version to `mcp>=1.9.0,<2` in `requirements.txt`, then run `cf push` again.
- **Alternative**: port the code to the mcp 2.x API (`from mcp.server.mcpserver import MCPServer`). See https://py.sdk.modelcontextprotocol.io/v2/migration/

### Python version differs from `runtime.txt`

- The log showed Python 3.14 although `runtime.txt` asked for 3.12. The buildpack may ignore or override it.
- If you get further dependency errors, check which Pythons your buildpack supports with `cf buildpacks` and adjust `runtime.txt`.

---

## 9. Notes and next steps

- **Security**: the endpoint is public. Before using it beyond a demo, add authentication, for example XSUAA (BTP's OAuth service) or an API key checked in middleware.
- **Read-only**: the sample OData service does not allow changes, so there are no write tools.
- **Case-sensitive search**: the sample service's `contains()` filter is case-sensitive, so `search_products` is too.
- **Your own OData service**: set `ODATA_BASE_URL` in `manifest.yml` (or with `cf set-env odata-mcp-server ODATA_BASE_URL <url>` followed by `cf restage odata-mcp-server`). If it needs authentication, add the headers in the `_get` function in `server.py`.