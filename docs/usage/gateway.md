# Gateway

What the Trino Gateway has registered, and how those backends map to the
clusters TMS knows about.

Read-only in TMS, with one exception: the [safe restart
sequence](safe-restart.md) deactivates and reactivates a backend as steps 1 and
6. There is no standalone toggle, because that toggle would be the way around
the drain.

---

## Setup

```yaml
gateway:
  enabled: true
  base_url: https://gateway.internal:8080
  user: tms-gateway
  poll_interval_seconds: 30
```

Password from `TMS_GATEWAY_PASSWORD` or `config.secret.yaml`.

> ⚠️ **The Gateway has no read-only role.** The `API` role that can list
> backends can also change them. Protect this credential exactly like the Trino
> one.

While disabled, health check H-08 is removed from the catalogue rather than
reported as permanently UNKNOWN, and the nav link is hidden rather than leading
to a page that can only say "off".

---

## What it shows

| | |
|---|---|
| **Backends** | Every backend, whether it is active, its routing group, and which TMS cluster it maps to |
| **Unmonitored backends** | Registered in the Gateway but matching no configured cluster — TMS is blind to those |
| **Unrouted clusters** | Configured in TMS but not registered in the Gateway. Queries cannot reach them |
| **Routing rules** | Read-only. Priority, condition, actions |

The mapping is by **URL**, not by name. A Gateway backend called
`trino-prod-a-1` and a TMS cluster called `prod-a` are matched because they
point at the same coordinator — a console that guessed from names would
deactivate the wrong backend during a restart.

---

## Known limits

**Cache state is not observable.** The Gateway's `databaseCache` does not emit
a hit signal, so TMS cannot report whether it is working. The screen says what
it observed, not what it inferred.

That matters operationally: if the Gateway loses its database, routing keeps
working from cache until `expireAfterWrite` elapses — ten minutes on a common
setting — and then stops. TMS cannot warn you during those ten minutes.

**Routing rules are read-only.** Editing them is a Gateway concern and belongs
in the Gateway's own configuration.

**Cluster registration is a Gateway concern too.** TMS has no create, update or
delete for clusters. The list of clusters has one owner, and duplicating it
here would produce two sources of truth that drift.

---

## During a restart

When a safe restart is running, the affected backend shows as inactive here.
That is the sequence working, and the console draws a banner on every screen
while it lasts — a cluster held out of rotation is otherwise invisible, and the
remaining clusters look healthy while traffic is being refused.
