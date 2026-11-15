# Amplifier Unified MCP Capabilities

Connect a Unified host to Model Context Protocol (MCP) servers, their tools,
resources, and interactive apps. The package handles server connections,
authorization, discovery, and Smart Tool installation.

It has two parts: the `@amplifier/unified-mcp-capabilities` Node.js library and the
`amplifier-unified-mcp` Python broker. It is an optional host capability, separate
from Amplifier's native `tool-mcp` module.

## Get started

Use Node.js 22.16+, Python 3.13+, and `uv`. From a checkout:

```sh
uv sync --directory python
npm ci
npm run build
```

Create a broker configuration with a private, dedicated data directory and the
origins used by your application:

```json
{
  "dataDir": "/absolute/private/mcp",
  "server": {
    "port": 8941,
    "public_origins": ["http://127.0.0.1:8941"]
  }
}
```

On macOS or Linux, start the broker with:

```sh
python/.venv/bin/amplifier-unified-mcp --config /absolute/broker.json
```

For host integration, import `createMCPCapabilities` from the Node package and
configure its `broker.command` and `broker.args` to launch that Python environment.
Provide the host's public session inspector, invalidation callback, and the
Resources capability's external-resource registration callback. The
[owner contract](contracts/mcp-owner.v1.md) and [public entry point](src/index.ts)
describe that composition.

## Connections and permissions

The host supplies authenticated conversation scope; client arguments cannot grant
access to another conversation. Listing connection metadata does not start MCP
servers or begin OAuth. Each service's actual authorization is still required
before its tools can be used.

Smart Tool installation needs `uv`. A deployment can set an explicit
`installer.executable` in the [broker configuration](schemas/broker-config.v1.json)
instead of relying on PATH. Do not reuse another broker's data directory.

## Reference

- [Capability integration and responsibilities](contracts/mcp-owner.v1.md)
- [Broker configuration](schemas/broker-config.v1.json)
- [Exported Node API](src/index.ts)

For development, run `npm run check`, `npm test`, and
`uv run --directory python pytest`.

## Contributing

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
