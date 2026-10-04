# Amplifier Unified MCP capabilities

This owner retains the real MCP SDK transport, OAuth, connector-attested account identity, progressive discovery, resources, isolated Smart Tool installation, and MCP App bindings. It has no private host or whole application imports. It is separate from native Amplifier's `tool-mcp` module. Node package: `@amplifier/unified-mcp-capabilities`; independently installable Python service: `amplifier-unified-mcp`. Node 22.16+/Python 3.13+ required. Lockfiles record exact tested dependencies. Neither package starts a model.

## Direction and contracts

Start with the [local vision](docs/VISION.md), [owner contract v1](contracts/mcp-owner.v1.md),
and [contributor entry](AGENTS.md). The packet applies the approved shared family
direction, maps executable checks, and keeps unqualified acceptance gates explicit.
It adds no runtime scope or new ratification. [QUALIFICATION.json](QUALIFICATION.json)
remains evidence for the sealed implementation baseline, not a new documentation-run result.

## Run and compose

```sh
uv sync --directory python
npm ci
npm run build
python/.venv/bin/amplifier-unified-mcp --config /absolute/owned/broker.json
```

Launcher configuration: `{ "dataDir": "/absolute/owned/mcp", "server": { "port": 8941, "public_origins": ["http://127.0.0.1:8941"] } }`. Supply the host's actual configured origins. The broker takes an exclusive process lock and uses a private SQLite WAL database. Do not reuse another owner's data directory.

```ts
const mcp = createMCPCapabilities({
  broker: {command: '/absolute/python', args: ['-m', 'amplifier_unified_mcp.server', '--config', '/absolute/broker.json']},
  inspectSession: (uri, context) => host.inspectSession(uri, context),
  registerExternal: (context, descriptor) => resources.registerExternal(context, descriptor),
  onInvalidate: (topic, scope) => invalidate(topic, scope),
});
```

`.actionSchemas()` exposes exact action descriptions and validated JSON schemas for agent discovery. `.manifest`, `.read`, `.action` implement the public capability-owner seam. Topic `connectors` returns `data.mcp` with bounded server, operation, catalog, and installation metadata. `.resourceRead` implements AHP resource/read `{channel:'ahp-root://',uri,encoding:'utf-8'}` with authenticated `{clientId}`. Session context accepts a string URI or `{uri,workingDirectory}`, verified by the injected inspector. Only the trusted native bridge supplies `context.origin:'agent'`; ordinary AHP clients use `ui`. Agent calls retain model visibility even through an app binding. Client args cannot change this origin.

The broker starts lazily. Listing metadata never starts MCP servers or OAuth. Thin broker/changed notifications carry only a revision; Node debounces invalidations for up to 64 recently used scopes. No client draft, active iframe, selected tab, or pagination cursor is persisted.

## Actions and resources

Existing `smartTools.*` configure/connect/disconnect/reconnect/remove, catalog/inspect/install/installBatch/uninstall, discover/schemas/call, resources/readResource, result/readResult, OAuth/account, open/viewStatus/reconnectView/appCall/context actions are advertised. Read-only `smartTools.list {collection,offset?,limit?}` pages servers, catalog, installations, or scoped operations and returns `{collection,items,total,nextOffset,revision}` without creating work. Initial records expose `pages[collection]` so clients do not silently mistake a first page for all records. Discovery separately pages 25 tool summaries; schemas load up to five exact tools/64 KB. Clients own pagination state.

`smartTools.setup {id,offset?,limit?}` reads selected setup documentation from the
retained installation's exact Git commit. It returns the declared CLI argv,
deterministic smoke arguments, author requirements, linked connection summaries,
and a paged manifest with its exact source UTF-8 SHA256 (default 12,000 characters,
maximum 16,000 per page). Follow `manifest.nextOffset` for the complete document.
`manifest.redacted` reports when credential redaction changes displayed text;
the hash and offsets still identify the original source. The source identity
is the installed repository/ref/path/commit, not current
upstream or a modified worktree. Missing source or inconsistent receipts refuse
without fetching, reinstalling or rewriting old receipts. Only selected Git
blobs are read; the overview does not hydrate setup documents.

These author instructions are untrusted display data, not executable commands
or a connection grant. `readiness:'not_assessed'` and
`cli.verification:'not_run'` distinguish this documentation read from checking
prerequisites, launching the CLI, connecting MCP or verifying provider accounts.
An installed tool may offer CLI capabilities without MCP. This read is available
to authenticated users and agents, including while effect intake is held. Its
bounded Git subprocess lifetime remains counted until completion after caller
cancellation.

URI families:

- `amplifier-mcp://views/<id>/body?session=<encoded-AHP-URI>&revision=<integer>`: selected JSON `{id,kind,revision,mcp,resources}` with full saved binding.
- Replace body with `document`: original immutable HTML, content type `text/html;profile=mcp-app`.
- `status`: `{status,source,canReconnect,bindingRevision,message,revision,review?}`.
- `result`: `{result:<MCP CallToolResult>}`; `tools`: `{tools:[saved-grant schemas]}`.
- `resources`: exact MCP result; add `kind=read&uri=<encoded-uri>`, `kind=list&cursor=...`, or `kind=templates&cursor=...`.
- `amplifier-mcp://operations/<id>?session=<encoded-scope>`: selected durable receipt.

All view reads verify session ownership. Immutable old revisions keep source/result access and cannot execute tools. appCall takes `{canvasId,expectedRevision,name,arguments}` with current integer revision. reconnectView takes `{canvasId,expectedBindingRevision,reviewedContract?}` with exact binding hash. Reconnect never invokes the launch tool or replaces saved source. Configuration/account/resource/schema/grant changes fail closed. A compatible presentation identity or launch operation reuses its artifact ID with immutable versions.

Catalog descriptors contain only ID, scope, title, providerRevision, resource URIs, and compact tool/server IDs (<8 KiB). HTML/results/arguments/grants/account bindings stay here. registerExternal records references only. context is bounded untrusted display data intentionally shared with agents, not an instruction or automatic model turn.

### Smart Tool installation prerequisites and diagnostics

Inspection reads bounded static `pyproject.toml` metadata without running the
package. Its additive `pythonRequires` field is the declared Python version
constraint or `null` when unknown. A workspace-only folder is rejected with a
package-subdirectory explanation. Explicit installation gives the constraint to
`uv venv --python`; uv selects or downloads a compatible isolated interpreter.
Without a declared constraint it uses the broker's interpreter. It does not
change the host Python or inherit a parent project's runtime selection. Package
builds still validate transitive dependencies; static inspection is not proof
that every operating system, external command or account prerequisite is met.

Failed installations expose optional `problem: {code, message, details}` on the
operation or individual batch item. `message` is suitable for the normal UI;
`details` is redacted and bounded to 1,500 characters for expandable diagnostics.
Codes are `invalid_package`, `installer_unavailable`, `source_unavailable`,
`runtime_unavailable`, and `package_install_failed`. Existing `error` remains for
older consumers. Mixed-version clients should use a generic failure summary and
show the legacy error only on request. An explicit batch retry selects unfinished
items only, clears stale diagnostics and preserves successful installations and
the original receipt. Interrupted work remains interrupted and is never replayed.

Installing a package does not connect an MCP server, supply provider credentials,
or establish readiness. A CLI descriptor is not an MCP launch command. The client
uses documented setup and observed connection/account/tool evidence; absent
setup support remains explicit.

The renderer must use an opaque iframe without allow-same-origin, validate source-window/current-view bindings, deny camera/microphone/location/clipboard, and prepend CSP before tool bytes: `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src data: blob:; object-src 'none'; form-action 'none'; base-uri 'none'`. requestedCsp/requestedPermissions are requests, not grants. Original stored source remains unchanged.

## Durability, authorization, and cost

Command scope/fingerprint is durable before execution. Tool dispatch marks and commits unknown immediately before transport invocation. Explicit responses confirm outcome; post-dispatch loss becomes `status:'interrupted',outcome:'unknown'`. Duplicate identical commands return receipts after restart; conflicting reuse rejects. Nothing reconnects/repeats a call automatically. Result blobs stay on disk, hydrate only for selected operations, and are not deleted by UI overview rollover.

Indexed receipt updates do not serialize unrelated catalogs or installations. Initial records are bounded to 768 KB. MCP source/results retain 2 MB bounds. Limits: 64 registrations, eight explicitly active transports. Identity comes only from connector-attested resource over SDK-authenticated transport, never decoded tokens or browser claims. Exact review revision/issuer/subject remain required. Tokens use private files; environment config stores variable names; returned diagnostics are redacted.

Host-only GET `/oauth/mcp/callback` invokes `.oauthCallback(queryPairs, exactOrigin)`. Preserve duplicate query keys for validation and never log callback queries. SDK PKCE/issuer/resource/state/expiry/single-consumption checks remain. No extra HTTP server is started.

## Validation and remaining integration

`npm test` includes real Node → Python stdio → official MCP SDK subprocess calls; the test visibly skips if Python environment is unavailable. `python/.venv/bin/python -m pytest -q python/tests` covers effects/resources, post-effect crashes, binding/review, local synthetic OAuth/PKCE/refresh, secret redaction, catalog changes, install/update rollback, saved-source recovery, scoped receipts, bounded reads, and agent/app visibility. These isolated fixtures do not prove real external accounts, deployed callbacks, or browser rendering.

Compositor registration and opaque-frame rendering require separate integration acceptance. Legacy Unified MCP registrations/artifacts have an explicit selected offline import seam described below. Import is never automatic; live user-data migration and the host-owned administrative flow remain separate acceptance. Managed-update implementation/tests are retained in Python; generic maintenance UI still needs public inventory/stage/activate binding. This package does not claim those integration checks are complete.

## Qualified observation reads

`qualifiedObservation(session, operation, args)` is a private composition seam for the
operations owner, with `qualify`, `check`, and `read` operations. It is not an agent or
client action. Operator qualification binds exact installed source digests, schemas,
connection configuration, account, and reviewed read-only behavior. An MCP
`readOnlyHint` by itself is insufficient. Qualification is trust in reviewed code,
not an OS sandbox or proof of a remote implementation.

Every read rechecks that binding and the exact target/scope. It uses an already
connected transport, suppresses sampling/root access, has a bounded deadline, and
validates the structured outcome. It never reconnects a server, invokes a model,
or presents an artifact automatically. File verification is streaming and bounded;
an LRU of 256 unchanged file identities avoids repeatedly hashing unchanged code.

The seven observation cases use an actual SDK stdio server and cover pending and
actionable results, source/schema/account/target changes, unavailable connections,
and refused remote schema references. The source-qualified suite has 114 Python cases
and nine Node cases. Real provider accounts and remote source enforcement remain
separate acceptance work.

## Held restart/recovery participation

`owner.quiescenceParticipant('mcp')` returns the public host participant shape:
`acquire({fenceId,commandId,purpose,instanceId,dataScope})` returns a held
`{ownerId,fenceId,release(outcome,proof)}` lease or `null` for a known busy/coverage
refusal. `reconcileRelease({...context,outcome,proof})` releases an exact saved
fence after process replacement. `inspectQuiescence()` returns aggregate request,
OAuth, background-I/O and transport coverage plus the compact gate receipt. These
are trusted composition APIs, never browser actions. The broker's private stdio
methods are `quiescence/acquire`, `quiescence/inspect`, `quiescence/release`.

The broker stores its gate in SQLite before returning a held lease. After restart,
that gate is unknown and all effects remain blocked until the host supplies its
verified exact release proof. A repeated old fence never reacquires or repeats
work. Only the current process can unwind a confirmed held lease using the host's
`{kind:'admission-refused'}` proof; unknown/restarted gates require verified
fence/command/data-scope/running-instance/receipt identity. Callback or transport
uncertainty does not clear the gate. A private RPC timeout leaves the owner alive
and keeps the bounded outstanding request slot until its late result or process
exit; it never kills uncertain work to manufacture idleness.

Effects include all mutating broker actions, OAuth HTTP callbacks, qualified
observation reads, remote resource reads, Node artifact registration, and install
I/O. Cancelled callers do not hide a filesystem thread that is still running.
Detached OAuth tasks are counted until they settle. Selected saved documents,
results, metadata and receipts remain readable. The exact safe capability action
map is `owner.quiescenceAccess`; composition may forward it to the host. Remote
resource reads are execution requests and are not in that map.

An idle connected MCP transport is reported separately as
`connected-peer-retirement-unverified`, **not** as active work. This generic owner
has no atomic remote retirement guarantee and does not kill an idle stdio server
that might own background jobs. Explicitly disconnect the peer to qualify; the
owner does not automatically reconnect after release. Historical unknown receipts
alone do not block admission when no execution or transport lifetime remains.
The receipts and all source/result blobs are preserved.

The installed MCP 2.2.0 client negotiates UI and account-identity extensions only.
It does not negotiate asynchronous Tasks. The current official
[Tasks extension](https://modelcontextprotocol.github.io/ext-tasks/specification/2026-07-28/tasks.html)
requires explicit per-request support and durable task IDs; it supplies task
status/control, not an atomic generic peer-retirement guarantee. Task-aware or
reviewed peer retirement is a future explicit capability. Connected generic peers
therefore remain a documented limitation of automatic update/recovery admission,
not evidence of complete automatic-updater compatibility.

The optional `onMayBeIdle()` callback coalesces advisory wakeups after complete Node effects and private broker OAuth, SDK connection, and detached filesystem completions. It carries no state or idle proof: the caller must acquire the held participant again. Notification failures cannot change an effect result.

Release retry proof is immutable: the owner atomically retains the canonical
outcome and entire proof with its released receipt. After acknowledgement loss
or restart, only the identical context, outcome and proof are accepted; changing
the receipt identity, authority scope or outcome is rejected. JSON object key
order is irrelevant. Older released receipts without exact proof evidence are
not inferred or rewritten into verified release evidence.

## Persistent service stop and resume

The trusted `quiescenceParticipant()` advertises `serviceStop: {version: 1}`.
For `purpose: 'service-stop'`, the host supplies the normal fence context plus
`serviceIdentity: {installationId, dataScope, ownerId, instanceId, releaseDigest}`.
The owner copies and retains the complete identity with its durable intake fence;
restarting a process does not open intake or replay work. The host authenticates
platform evidence. This participant validates its exact binding, not a PID,
caller-provided claim of idleness, or socket presence.

A conclusive release needs `kind: 'service-lifecycle'`, the retained `expected`
identity, the exact `observed` identity, and the ordinary verified fence receipt.
`outcome: 'ready'` requires a distinct instance, `serviceOutcome: 'resumed'`,
`resumeCommandId`, `exitReceiptId`, and `readyReceiptId`. `outcome: 'unchanged'`
requires the same instance, `serviceOutcome: 'stop-refused'`, and
`refusalReceiptId`. The two receipt sets cannot be mixed. A changed release
identity, proof, or outcome refuses; exact completed retries are passive.
Generic update/recovery evidence cannot release a service fence.

`{kind: 'admission-refused'}` is restricted to the original live lease's partial
acquisition rollback. It is not a restart reconciliation mechanism. An unknown
outcome keeps intake closed, and passive inspection/receipt routes stay usable.
No service stop, platform process signaling, authenticated supervisor, or remote
provider safety is implemented by this participant itself.

The broker must explicitly negotiate service-stop support; an older broker returns a known pre-acquisition refusal. The Python broker owns the durable gate. MCP SDK connections are not automatically treated as owned execution, but generic peers lacking proof of remote task completion still require explicit safe disconnect before retirement. Existing in-flight tool, callback, OAuth, and task coverage remains unchanged.


## Held retention inspection

The actual quiescence participant advertises `retentionHide: {version: 1}`.
Acquire it with purpose `retention-hide` and the exact coordinator fence context.
The returned live lease exposes
`inspectRetentionReferences({sessions: ["ahp-session:/..."], limit: 101})`.
The family must contain 1–101 distinct explicit session URIs. The result is
`{coverage: "complete" | "partial", protected: [{session, reasons}], omissions}`.
Only the original held lease can inspect; release, uncertainty or replacement
invalidates that authority. Release reconciliation requires the same authenticated
coordinator proof and never retries effects. A pre-effect admission rollback is
allowed only on the original live lease. The purpose grants no mutation rights.

Inspection reads bounded indexed owner metadata while intake remains closed.
It never starts a native worker, scans canonical histories, deletes product records,
or interprets absence of a runtime as absence of deferred work. `complete` describes
this owner's reference coverage, not permission to hide or delete a conversation.
The coordinator must hold every configured owner, check all results, and use the
native history owner's separate preservation-first hide boundary.

Active work blocks acquisition. Retained uncertain owner commands protect their explicitly associated conversations; unscoped uncertainty cannot produce a false absence proof.

## Managed-file disposal protection

`quiescenceParticipant.managedFiles = {version: 1, preservesCanonical: true}` is a separate contract from `retentionHide`. It holds the real owner intake with purpose `managed-files-disposal`, preserving every owner record. The acquired lease exposes:

```ts
inspectManagedFilesReferences({
  sessions: [rootSession, ...descendants], // explicit AHP URIs, at most 101
  limit: 101,
  allocation: {allocationId, executionDirectory, allocationHash, treeHash, entryCount, bytes}
}) // {coverage: 'complete' | 'partial', protected: [{session, reasons}], omissions: [...] }
```

The allocation comes from the trusted host's native-reviewed managed allocation, never a browser path. It is bound with the exact selected family on the first inspection of the live lease; a different review is refused. This read does not grant file deletion. The coordinator must bind release to its exact durable managed-files effect receipt, keep unknown fences held, and refuse partial/protected coverage. Lease inspectors expire before release begins, including unknown release; admission-refused rollback is available only on the original live lease. Restart reconciliation requires the existing exact verified release proof.

Unsettled MCP effects and selected retained MCP views protect files. Configured server dependencies lack a complete file-reference declaration; any configured server therefore returns explicit partial coverage (mcp-server-file-dependencies-unclassified). No peer is connected, no tool is called, and remote or local dependencies are not guessed safe.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.

## Selected offline legacy import

Trusted composition methods `legacyReview(args, {clientId})`,
`legacyImport(args, {clientId})`, `legacyReceipt(commandId, scope, {clientId})`
and `legacySource({commandId, scope, offset?, limit?}, {clientId})` are not
advertised client/agent actions. The embedding migration administrator must
supply authenticated operator authority, source-store exclusion and authorized
native-to-AHP session mapping; a client ID alone is not administrative approval.
The importer never reads source paths or changes source stores. The host owns
administrative composition and resource-index registration, including idempotent
re-registration after a lost acknowledgement.

Supply one selected JSON string as `source`, its exact UTF-8 `sourceSha256`,
`sourceId`, `scope`, and durable `commandId`. Review returns `reviewDigest`;
import requires that same digest against the same source and destination mapping.
The selected envelope has format `amplifier-unified-mcp-legacy-selection/v1`:

- Registration: `{format, kind:'registration', registration:<original row>}`.
  Scope must be `host`. Original ID/configuration survive. Environment/header
  values must remain variable references. Original credential records and
  installation ownership are refused. The imported registration is disconnected;
  old account bindings, grants, catalog readiness and OAuth tokens confer no
  authority. Packages and external files remain in the original owner's store.
- Artifact: `{format, kind:'artifact', artifact:<original saved row>,
  body:<hydrated body>, binding:<hydrated mcpState>, result:<hydrated savedResult>}`.
  Supply the exact original `legacySessionId` and authorized destination AHP URI
  as `scope`. The original MCP App ID and integer revision survive. Hydrated
  body/binding/result must match their original canonical content-addressed
  `$resource` references. No savedResult reference means result must be null;
  no mcpState reference means binding must be empty. Missing original bytes are
  refused, preserving the source reference for recovery rather than fabricating
  an empty document.

Review is passive. Source strings are capped at 2.5 MB, documents/results at
2 MB, bindings at 128 KB, and source pages at 16,000 characters; the private RPC
frame retains its 4 MB ceiling. Duplicate JSON keys/nonfinite numbers refuse.
Registration limits remain 64. Destination identity collisions refuse globally;
imports never overwrite or silently rename. Source bytes, destination bytes and
receipt commit atomically in the existing owner database. A changed command
binding refuses. Exact retries return the retained receipt; the same source under
another command returns the original import command's receipt.

Imported documents/results retain the existing immutable resource descriptors.
`status:'imported_read_only'` denies reconnect, app calls, remote resource reads
and mutable context even if an identically named server is later connected.
Original source and historical bindings remain retained in private owner blobs;
`legacySource` pages the exact selected source for administrative recovery.
Import transfers no grant and launches no work. Open a separately reviewed new
view to acquire current runtime authority.

Held/unknown owner gates block import effects in both Node and Python. Passive
review, receipt and selected source reads remain available. After compositor
failure, recover the already committed receipt and retry the exact reviewed
import to re-register its descriptor; never replay its legacy launch operation.

The selected format is based on retained Unified `resource_files`,
`canvas_library`, `smart_canvas`, and `mcp_view_recovery` at extraction revision
`828d2bff97a77fbdbd0f06429369f7ff1f4abf42`. It supports one hydrated immutable
version per selection, not full-store scanning, credential migration, package
adoption or mutable legacy UI state. Source selection/export, browser rendering,
actual legacy user stores and the host administrative migration flow need their
own qualification.

Review/apply serialization correction: a new import revalidates the complete
source, digest, mapping and normalized configuration only after acquiring the
manager lock. Working-directory review also binds the directory's device/inode,
so same-path replacement invalidates approval. The exact validated row is saved
without a second normalization or an intervening await. This is admission-time
identity validation, not a filesystem lease or a promise about a later connection.
Uncommitted predecessor cwd reviews need a fresh review. Exact already committed
receipts remain passive/recoverable without resolving or adopting a new directory;
retained sources, receipts and registrations are not rewritten by this correction.
The successor's installed import checks are separate from the sealed predecessor's
full installed/public-host evidence; see correctiveQualification in QUALIFICATION.json.
