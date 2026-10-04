# MCP capability owner contract — v1

**Status:** local restatement of inherited approved direction and existing
implementation seams, dated 2026-10-03. This packet does not ratify new product
scope, certify deployment, or replace executable schemas. Prefix **MCP** is a
local review reference, not an upstream protocol namespace.

Parent: [local vision](../docs/VISION.md). Shared authority at reviewed family
revision `625af2f2832fafc8152f6078061d626f160362a4`:
[HP](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/host-protocol.v1.md),
[AP](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/agent-protocol.v1.md),
[WS](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/working-set.v1.md),
[CB](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/component-boundaries.v1.md),
[CS](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/client-state.v1.md),
[CX](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/client-experience.v1.md),
[CD](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/client-development.v1.md), and
[MA](https://github.com/microsoft/amplifier-unified/blob/625af2f2832fafc8152f6078061d626f160362a4/contracts/maintenance.v1.md).
Upstream AHP/ACP retain wire authority through the host/agent owners; the official
MCP SDK retains transport/OAuth behavior. Local extensions never redefine them.

## Public seam and source of truth

The [README](../README.md) describes composition, action/resource URI families,
callback routing, observation, held maintenance and selected offline import.
Use [TypeScript exports](../src/index.ts), runtime `actionSchemas()` backed by
[definitions](../python/amplifier_unified_mcp/definitions.py), and the
[launcher schema](../schemas/broker-config.v1.json) for exact fields and limits.
Do not copy their definitions into a second schema or infer supported behavior
from this prose alone. Legacy review/import/receipt/source and observation or
maintenance participant methods are trusted composition seams, not advertised
browser/agent actions. Versioned selected-import format and restrictions are in
the README; public resource descriptor semantics remain unchanged.

## Promises, falsifiers and executable checks

| Local promise and inherited basis | Falsifier | Existing acceptance checks |
| --- | --- | --- |
| **MCP1 — Independent optional ownership.** The broker/adaptor run with fixture peers and no private host or native runtime imports; absent support is explicit. CB1–3, HP1/HP5, AP1–2/AP5, CS9, CX7. | Listing MCP state starts a model or requires a host checkout; unsupported actions gain a destructive fallback. | [Broker seam](../test/broker.test.ts), [extracted manager](../python/tests/test_extracted_manager.py), public installed-host receipt in QUALIFICATION.json. |
| **MCP2 — Authenticate scope, origin and grants.** Session resources and saved App calls retain the authorized scope, immutable binding, connector-attested account and exact reviewed authority. HP4, CS1/CS5–8. | A different session reads a document; client args forge agent origin; an account/schema/configuration change executes under an old grant. | [Broker authorization/binding](../python/tests/test_broker.py), [identity](../python/tests/test_connector_identity.py), [OAuth lifecycle](../python/tests/test_connector_lifecycle.py), Node broker seam. |
| **MCP3 — Preserve originals and truthful recovery.** Durable command identity precedes dispatch; unknown effects are not replayed; saved source/results remain available when live authority changes. HP6, CS4/CS7, WS1, CX6. | Restart or retry repeats an effect, replaces missing HTML, loses an old receipt, or local sign-out claims provider revocation. | Broker crash/restart/reconnect cases, OAuth lifecycle, [legacy import](../python/tests/test_legacy_import.py), [packed import seam](../test/legacy-import.test.ts). |
| **MCP4 — Bound selected work.** Metadata/discovery/schema/source reads expose explicit pages/limits; large bytes remain selected resources; receipt updates do not serialize unrelated catalogs. HP2, CS2–3/CS6, WS3–5. | Overview hydrates all result blobs; a tiny receipt serializes an unrelated catalog; an observation reconnects or invokes a model. | Broker metadata-cost/page cases, [observation seam](../python/tests/test_observation_seam.py), import/source-page cases. |
| **MCP5 — Hold real lifetimes and exact recovery proof.** Intake remains held across uncertainty/replacement; active requests/callbacks/threads and unqualified connected peers refuse retirement; passive recovery remains readable. CB7, MA1–3/MA6. | A timed-out caller manufactures idle while work lives; restart clears a fence; changed release proof is accepted. | [Quiescence](../python/tests/test_quiescence.py), [real subprocess/HTTP lifetime](../python/tests/test_real_lifetime.py), [service stop](../python/tests/test_service_stop.py), Node quiescence/service-stop cases. |
| **MCP6 — Reviewed preservation before migration or disposal.** Exact selected bytes, destination and resolved configuration bind review; collisions refuse; imported Apps are read-only. Held reference inspection preserves records and reports partial coverage. WS1, CS7, MA7–11. | Import overwrites identity or adopts credentials/grants; changed symlink passes review; opaque server dependencies are guessed safe for file disposal. | Legacy import/packed import cases, [retention](../python/tests/test_retention.py), [managed-file references](../python/tests/test_managed_files.py). |

## Consumer obligations

The embedding host authenticates the caller and trusted origin, supplies the
actual callback origin and preserves duplicate callback query keys without
logging secrets. It checks session access and serializes administrative policy;
a client ID by itself is not migration approval. Resource composition registers
bounded descriptors idempotently, including recovery after acknowledgement loss.
Only the broker's explicit artifact envelope, or a retained descriptor from a
view-producing open/reconnect/context action, registers an external artifact.
Smart Tool package descriptors and arbitrary tool-result descriptor fields are
inert metadata; they never confer resource identity or registration authority.

Clients use advertised schemas and immutable revisions, preserve private drafts,
render MCP HTML in the README's opaque sandbox, and make unsupported/busy/unknown
outcomes visible. Discovery pages and views confer no new grant. They distinguish
saved configuration, connected transport, confirmed account and completed effect.

For explicit Smart Tool installation, bounded static project metadata supplies
the Python constraint; runtime selection must satisfy it independently of the
host's default Python. Failed preparation must preserve prior installations and
receipts. The additive `pythonRequires` and `problem` read fields are described
in the README; clients use the safe message and keep selected diagnostics
separate. Neither those fields nor successful installation imply an MCP adapter
or usable account. These refine MCP1/3/4 and CX's guided user outcomes without
changing AHP/ACP/MCP wire semantics. Falsifiers include selecting Python 3.12 for
`>=3.13`, executing metadata during inspection, exposing full raw errors as the
normal UI, or retrying successful items. The isolated cases are in
[installation runtime tests](../python/tests/test_installation_runtime.py).

The operations owner rechecks qualified observation source/schema/account/target
bindings and never treats a tool's read-only hint as qualification. The supervisor
holds every configured participant and supplies verified exact release evidence;
this owner does not authenticate platform proof or establish remote job completion.

## Evidence and unqualified gaps

[QUALIFICATION.json](../QUALIFICATION.json) preserves the sealed implementation
packet at `0a17d1206cd6004e57d68dfe802ced69236e6c81` and separately records the
review/apply correction at `c8aadb9ccd3af926808fbb92adc84ef7c004a9a1`.
The predecessor qualified 110 source/installed-wheel Python cases, nine Node
cases, a packed Node import check and synthetic public-host/OAuth checks. The
correction qualified 114 source cases and 22 selected installed import cases;
the accepted corrective handoff additionally records 114 full installed cases,
a packed Node/broker smoke check and Node 22/24 CI. Independent corrective review
was read-only; independent successor execution was unperformed. This documentation
successor is based on main merge `aacbec7335ee65428858be2e168b8c26984254bc` and
introduces no new execution qualification. Normal checks are
`npm run check`, `npm test`, and `python/.venv/bin/python -m pytest -q python/tests`;
[CI](../.github/workflows/contracts.yml) runs Python 3.13 and Node 22/24.

Actual external accounts/provider-side revocation, real browser/device rendering,
actual deployed callback/adoption, complete F11 form/control coverage, root's
administrative migration/export flow and actual legacy user stores are separate
unqualified gates. Generic connected-peer retirement and opaque configured-server
file dependencies remain explicit coverage gaps. Selected imports do not adopt
credentials, packages, missing bytes or mutable legacy UI state. These gaps do
not retire desired behavior or imply that source/package tests prove full parity.

## Changes and proposals

CD1–6 govern behavioral impact, mixed versions and evidence. Identify affected
producer/consumers and contract clauses before changing shared meaning. Source,
installed package, browser/account, release and live adoption receipts stay
separate. New credential migration, remote retirement/task protocols or mutable
legacy adoption are proposals requiring review by their authority owners; this
v1 restatement grants none of them.
