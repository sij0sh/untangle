# Repository mapping

Map two **independent views** of the same code. Neither hierarchy requires every level, and their boundaries may overlap.

**Structural (largest → smallest):** repository → project/application → service → package/library → module → file → class/function → statement/expression.

**Functional (largest → smallest):** domain → subdomain → capability → feature → component → function.

Structural levels answer *where code lives and how it runs*. Functional levels answer *what business or system outcome it enables*. Map only meaningful levels; never invent a service, package, or domain to fill a slot.

## For each unit

Record:

- **Purpose:** what problem does this unit solve; what observable outcome does it own?
- **Consumers:** who calls, imports, invokes, subscribes to, deploys, or relies on it; **why**?
- **Owner / contract:** single authority for rules and state; inputs, outputs, errors, invariants, exports.
- **Connections:** structural location, functional capability, incoming and outgoing dependencies.
- **Flow:** entry → owner → side effect → failure/retry/cleanup, across files or services if necessary.
- **Evidence:** `path:line` or explicit unknown; label observation separately from inference.

For every scoped path, associate at least one owner/capability or label it `unclassified`. For every significant entry point, follow the call chain both toward consumers and toward effects. Inspect tests and configuration to establish externally visible contracts. An import search alone cannot prove a module unused; account for reflection, routing, registration, data/config references, and external callers.

## Mapping loop

Parallelize independent discovery or preliminary flow evidence, not model updates. Give workers distinct questions and the current map revision. Reconcile ownership, consumers, exposure checks and evidence into one model before submitting the current action. Revalidate outstanding results after each completion; semantic corrections invalidate prior dispositions. Follow `SKILL.md` coordination rules.

1. Draft a coarse structural map and capability map from instructions, manifests, directories, and entry points.
2. Choose a concrete entry or consumer; trace its calls, effects, errors, resources, and cleanup.
3. Correct any misassigned responsibility, missing consumer, hidden dependency, or false boundary.
4. Repeat until every scoped file has an accounted-for role and every material flow is **traced** or **unknown with a reason**. Unknown is an honest stopping state, not evidence of safety.

Example correspondence, **not** a mandated structure:

`apps/shop/src/refunds/calculate.ts` → app `shop` / module `refunds` / function `calculate` ↔ commerce / payments / refund processing / partial refunds / calculator. Its consumer might be the refund API, which needs a refundable amount; verify that relationship in code, not from the filename.

Output a compact map: `unit | structural home | capability | owner/contract | consumers + why | traced flows | evidence/gaps`. Highlight cross-boundary dependencies and duplicate owners. This map is a working model, not a generated directory tree.

## Engine JSON protocol

Every completion includes printed `run`, `phase`, `rev`; never guess the active artifact folder. Below omit those guard fields for clarity. Static-only until SCOPE approval.

Evidence: nonempty list of `{"kind":"observed|inferred","where":"path:line[-line]"}` or `{"kind":"unknown","reason":"..."}`. Engine attaches source hashes. Evidence must refer to inventory-authorized source; external contracts use an unknown record, not invented source lines. `owner`, `contract`, and trace claims accept text or `{"unknown":"reason"}`. Truth/adequacy still require source review.

### Seed / replace model

Send `action: seed` plus a complete model. May replace a model during remapping; cumulative counters do not reset.

```json
{
  "action": "seed",
  "mapping": {
    "capabilities": [{"id":"C1","purpose":"calculate refunds"}],
    "units": [{
      "id":"M1", "purpose":"compute refundable amount",
      "structural_home":["app shop","module refunds"], "capabilities":["C1"],
      "owner":"refund calculator", "contract":"amount in; refundable amount or validation error out",
      "consumers":[{"who":"refund API","why":"needs refundable amount"}],
      "incoming":["external:refund API"], "outgoing":[],
      "evidence":[{"kind":"observed","where":"refunds.py:1-20"}]
    }],
    "path_roles":{"refunds.py":{"units":["M1"]}},
    "exposure_checks": {
      "cli_http_jobs":{"status":"found","entries":["E1"],"evidence":[{"kind":"observed","where":"refunds.py:1"}]},
      "exports":{"status":"unknown","entries":[],"reason":"external use undocumented","evidence":[{"kind":"unknown","reason":"external callers unavailable"}]},
      "registration_reflection_routing":{"status":"none","entries":[],"reason":"inspected registration/config","evidence":[{"kind":"observed","where":"refunds.py:1-20"}]},
      "configuration_data":{"status":"none","entries":[],"reason":"inspected configuration","evidence":[{"kind":"observed","where":"refunds.py:1-20"}]},
      "migrations":{"status":"not_applicable","entries":[],"reason":"no schema/write paths","evidence":[{"kind":"observed","where":"refunds.py:1-20"}]},
      "external_callers":{"status":"unknown","entries":[],"reason":"caller deployment unavailable","evidence":[{"kind":"unknown","reason":"external boundary"}]}
    }
  },
  "units":[{"id":"U1","responsibility":"review refund calculation","paths":["refunds.py"],"map_units":["M1"]}],
  "entry_points":[{"id":"E1","where":"refunds.py:1","flow":"API → calculator → result/error","map_units":["M1"],"evidence":[{"kind":"observed","where":"refunds.py:1-20"}]}],
  "conventions":[],
  "observables":[{"what":"refund amount/errors","status":"contractual"}],
  "exclusions":[]
}
```

Adapt all claims/evidence to real source; example is not permission or a preapproved map. Optional capability `parent` references another capability ID; omit meaningless levels. Mapping memberships overlap freely. Audit `units` must partition every candidate file/range exactly once. Directory specs expand to candidate files. Empty files use whole-file specs.

Unknown role: `path_roles[path] = {"unclassified":"reason"}`. Unknown capability: unit `capabilities: []`, `capabilities_unknown: "reason"`. Unknown consumer: `consumers: [{"unknown":"reason"}]`. Work units/entries with `map_units: []` require `unclassified: "reason"`. Dependencies name mapping IDs or `external:boundary`.

All six exposure keys required. `found` needs entry IDs; `none|not_applicable|unknown` needs reason and empty entries. Absence/not-applicable needs source evidence. Every declared entry must occur in the checklist. Entry with `excluded:"reason"` instead of `flow` is proposed for exclusion; user sees it at SCOPE. No executable flows is valid for source-backed docs/schema-only assessments, not an excuse to skip discovery.

### Trace / correct / repeat

Follow the current mapping flow, not audit queue. Send:

```json
{
  "action":"trace", "flow":"E1", "disposition":"traced",
  "consumers":"refund API needs amount", "calls":"API → calculator",
  "effects":"returns amount; no writes", "failure_cleanup":"validation error; no acquired resources",
  "evidence":[{"kind":"observed","where":"refunds.py:1-20"}],
  "reopen":[]
}
```

`disposition: unknown` requires `reason`; do not omit other claims: use explicit unknowns. `pending` + reason keeps the current flow queued (still spends a step). `reopen` names earlier flow IDs. Optional `correction` contains the entire seed model above without guards/action; correct ownership, hidden consumers, connections, or add discovered flows. Use seed to remove the current flow.

A semantic correction increments `map_rev`, reopening **all prior mapping dispositions** (including unknowns). The current trace can resolve at the new revision. Evidence/status-only progress does not reopen others. Source drift requires reinventory. All accepted seed/trace/finish actions spend approved `max_map_steps`; no resets through revision/reinventory.

### Finish

Send `{"action":"finish"}` only when every candidate file has an accounted-for role and every material flow is traced or unknown with reason **at the current map revision**. Checklist, audit partition and evidence must reconcile. Engine then requests explicit SCOPE approval for this `map_rev`; mapping does not approve scope.

Unknown is honest closure, never safety evidence. Every material non-excluded flow still needs a separate audit review. Missing required source/flow inspection is blocked → INCOMPLETE. Available-source review with unverified external/runtime behavior may finish a static assessment only with named limitations. Map corrections discovered in audit return to MAP and invalidate scope/downstream evidence; history and cumulative effort remain.
