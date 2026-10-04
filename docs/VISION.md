# MCP owner vision

This repository makes optional MCP capabilities useful in Amplifier Unified
without coupling clients or the generic host to MCP transport, credentials,
package installation or saved App internals.

This is a local application of the [approved family direction](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/docs/architecture/VISION.md),
not a new ratification. The [owner contract](../contracts/mcp-owner.v1.md)
records inherited promises, existing seams and acceptance gaps separately.

## Desired outcomes

- An authorized person or agent can discover and use the same advertised
  capability actions, with caller origin and saved grants enforced at dispatch.
- Installed tools retain selected source-exact setup instructions. CLI capability,
  installed package, prerequisites, connected MCP and confirmed accounts remain
  distinct; reading author instructions never executes them.
- Registrations and installation metadata are cheap to inspect. Connections,
  authorization, large results and App documents load only through explicit,
  bounded requests.
- A saved App keeps its identity and immutable source after disconnect, restart,
  configuration change or account change. Old grants never silently authorize
  new code, schemas or accounts.
- An uncertain effect remains inspectable without replay. Maintenance holds
  real intake and accounts for requests, callbacks, filesystem threads and peer
  lifetimes before it claims safe replacement.
- Explicit migration preserves selected originals and identities. Configuration
  adoption, credential authorization and execution adoption remain distinct.
- An information worker can add a tool without selecting a Python version or
  interpreting installer logs. The owner prepares declared prerequisites in an
  isolated environment and supplies actionable summaries with selected details.
  Installed, connected, account-confirmed and ready remain distinct evidence.

## Ownership and scope

This owner contains both the TypeScript composition adapter and independently
installed Python broker. It owns MCP connections, OAuth/account bindings,
Smart Tool installation/lifecycle metadata, saved MCP App bindings and immutable
source/results, qualified observation reads and its durable maintenance gates.
The resources owner indexes its existing public descriptors; it does not copy
or become the authority for these MCP bytes.

The host owns authenticated admission, AHP sequencing and administrative
composition. Clients own forms, drafts, iframe rendering and device permissions.
The native ACP adapter owns full Core/Foundation execution and canonical native
history. Provider services own their remote grants and durable jobs. Distribution
owns release composition, supervisor integration and deployment qualification.

## Scope limits

This capability does not replace upstream AHP, ACP or MCP semantics, native
`tool-mcp`, the agent runtime, resources authority or supervisor. It does not
start models for catalog inspection, claim remote revocation after local sign-out,
infer remote job completion from disconnect, replay unknown work, or transfer
legacy credentials/package ownership implicitly.

Real-account, actual browser/device, host administrative migration and live
adoption evidence remain required for those desired outcomes. Fixture coverage
is useful evidence, not permission to weaken the vision or retire an unqualified
control. New retirement protocols, credential migration or expanded mutation
rights require an explicit proposal to the relevant owners; none is ratified here.
