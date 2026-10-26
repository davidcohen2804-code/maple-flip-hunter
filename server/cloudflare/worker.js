/*
 * Maple Flip Hunter — Friends Edition licence server on Cloudflare Workers + D1 (free plan).
 * Behaviour identical to server/license_server.py (the reference implementation the unit tests run):
 *
 *   POST /v1/activate {machine, version}   → signed token; first call starts the 72 h trial (server clock)
 *   POST /v1/status   {machine, version}   → signed token (trial / licensed / expired, kind trial|activation|lifetime|revoked)
 *   POST /v1/redeem   {machine, code, version} → one-time code bound to this machine; 403 + {error} otherwise
 *   GET  /v1/update                        → signed update manifest, 404 if none published
 *   GET  /v1/health                        → {ok, server_time, public_key}
 *
 *   /admin/* (Authorization: Bearer <ADMIN_TOKEN>) — used by server/cf_admin.py:
 *   POST /admin/code {days}  /admin/list  /admin/check {machine}  /admin/revoke {machine, revoked}
 *   POST /admin/update {version, url, sha256}   GET /admin/usage
 *
 * Secrets: LICENSE_PRIVATE_KEY (64-hex Ed25519 seed), ADMIN_TOKEN. The private key never leaves Cloudflare.
 * Free-quota care: one D1 write per machine per hour at most (last_seen throttled), request counter flushed in
 * batches, 4 KB body cap, 60 req/min per IP via the ratelimit binding.
 */

const TRIAL_SECONDS = 72 * 3600;
const LAST_SEEN_WRITE_EVERY_S = 3600;
const PKCS8_ED25519_PREFIX = "302e020100300506032b657004220420";

// ------------------------------------------------------------------ helpers
const te = new TextEncoder();
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const now = () => Date.now() / 1000;
const hex2bytes = (h) => Uint8Array.from(h.match(/.{2}/g).map((b) => parseInt(b, 16)));
const bytes2hex = (b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** Canonical JSON exactly like Python json.dumps(separators=(",",":"), sort_keys=True) for our flat payloads. */
function canonical(obj) {
  const esc = (v) => JSON.stringify(v).replace(/[\u0080-\uffff]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
  const keys = Object.keys(obj).sort();
  return "{" + keys.map((k) => esc(k) + ":" + esc(obj[k] === undefined ? null : obj[k])).join(",") + "}";
}

let _key = null;
async function privateKey(env) {
  if (_key) return _key;
  const seed = (env.LICENSE_PRIVATE_KEY || "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(seed)) throw new Error("LICENSE_PRIVATE_KEY secret missing or not a 64-hex seed");
  _key = await crypto.subtle.importKey("pkcs8", hex2bytes(PKCS8_ED25519_PREFIX + seed), { name: "Ed25519" }, false, ["sign"]);
  return _key;
}

async function sign(env, payload) {
  const body = te.encode(canonical(payload));
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, await privateKey(env), body);
  return b64url(body) + "." + b64url(sig);
}

async function publicKeyHex(env) {
  const row = await env.DB.prepare("SELECT v FROM meta WHERE k='public_key'").first();
  return row ? row.v : null;
}

function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// ------------------------------------------------------------------ usage counter (approximate, batched)
let _pending = 0;
async function countRequest(env, ctx) {
  _pending += 1;
  if (_pending < 25) return;
  const n = _pending;
  _pending = 0;
  const day = new Date().toISOString().slice(0, 10);
  ctx.waitUntil(
    env.DB.prepare("INSERT INTO usage(day, n) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET n = n + excluded.n").bind(day, n).run().catch(() => {})
  );
}

// ------------------------------------------------------------------ licence logic (mirrors license_server.py)
async function tokenFor(env, machine, version) {
  const t = now();
  const row = await env.DB.prepare("SELECT * FROM machines WHERE machine=?").bind(machine).first();
  let first, licensedUntil;
  if (!row) {
    await env.DB.prepare("INSERT INTO machines(machine, first_seen, last_seen, version, licensed_until) VALUES (?,?,?,?,NULL)")
      .bind(machine, t, t, version).run();
    first = t;
    licensedUntil = null;
  } else {
    first = Number(row.first_seen);
    licensedUntil = row.licensed_until === null ? null : Number(row.licensed_until);
    if (t - Number(row.last_seen) > LAST_SEEN_WRITE_EVERY_S || row.version !== version) {
      await env.DB.prepare("UPDATE machines SET last_seen=?, version=? WHERE machine=?").bind(t, version, machine).run();
    }
  }
  let st, exp, kind;
  if (licensedUntil === -1) [st, exp, kind] = ["expired", null, "revoked"];
  else if (licensedUntil === 0) [st, exp, kind] = ["licensed", null, "lifetime"];
  else if (licensedUntil !== null && licensedUntil > t) [st, exp, kind] = ["licensed", licensedUntil, "activation"];
  else if (licensedUntil !== null) [st, exp, kind] = ["expired", licensedUntil, "activation"];
  else {
    const trialEnd = first + TRIAL_SECONDS;
    [st, exp, kind] = t < trialEnd ? ["trial", trialEnd, "trial"] : ["expired", trialEnd, "trial"];
  }
  const payload = { m: machine, st, exp, iat: t, kind, ver: 1 };
  return { token: await sign(env, payload), server_time: t, status: st, expires_at: exp, kind };
}

async function redeem(env, machine, code) {
  const row = await env.DB.prepare("SELECT * FROM codes WHERE code=?").bind(code).first();
  if (!row) return [false, "unknown activation code"];
  if (row.redeemed_by && row.redeemed_by !== machine) return [false, "this code was already used on another computer"];
  if (row.redeemed_by === machine) return [false, "this code was already used"];
  const t = now();
  const until = Number(row.days) === 0 ? 0 : t + Number(row.days) * 86400;
  const cur = await env.DB.prepare("SELECT licensed_until FROM machines WHERE machine=?").bind(machine).first();
  if (cur && Number(cur.licensed_until) === 0 && cur.licensed_until !== null && until !== 0) return [false, "this computer already has a lifetime licence"];
  if (cur && Number(cur.licensed_until) === -1) return [false, "this computer's licence was revoked — contact the developer"];
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO machines(machine, first_seen, last_seen, version, licensed_until) VALUES (?,?,?,?,?) " +
        "ON CONFLICT(machine) DO UPDATE SET licensed_until=excluded.licensed_until, last_seen=excluded.last_seen"
    ).bind(machine, t, t, "", until),
    env.DB.prepare("UPDATE codes SET redeemed_by=?, redeemed_at=? WHERE code=?").bind(machine, t, code),
  ]);
  return [true, "ok"];
}

function describeRow(r) {
  const t = now();
  const lu = r.licensed_until === null ? null : Number(r.licensed_until);
  let status, kind, expires;
  if (lu === -1) [status, kind, expires] = ["expired", "revoked", null];
  else if (lu === 0) [status, kind, expires] = ["licensed", "lifetime", null];
  else if (lu !== null) [status, kind, expires] = [lu > t ? "licensed" : "expired", "activation", lu];
  else {
    const te_ = Number(r.first_seen) + TRIAL_SECONDS;
    [status, kind, expires] = [t < te_ ? "trial" : "expired", "trial", te_];
  }
  return { machine: r.machine, first_seen: Number(r.first_seen), last_seen: Number(r.last_seen), version: r.version, status, kind, expires_at: expires };
}

function makeCode() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const b = crypto.getRandomValues(new Uint8Array(12));
  const s = [...b].map((x) => alphabet[x % alphabet.length]).join("");
  return `${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8, 12)}`;
}

async function findMachine(env, prefix) {
  const { results } = await env.DB.prepare("SELECT machine FROM machines WHERE machine LIKE ?").bind(prefix + "%").all();
  if (results.length !== 1) return [null, `${results.length} machines match '${prefix}' — give more characters`];
  return [results[0].machine, ""];
}

// ------------------------------------------------------------------ admin
async function admin(req, env, path, body) {
  const auth = req.headers.get("authorization") || "";
  if (!env.ADMIN_TOKEN || !constantTimeEqual(auth, "Bearer " + env.ADMIN_TOKEN)) return json({ error: "unauthorized" }, 401);
  if (path === "/admin/code") {
    const days = Number(body.days);
    if (!Number.isInteger(days) || days < 0 || days > 3650) return json({ error: "days must be 0 (lifetime) or 1..3650" }, 400);
    const code = makeCode();
    await env.DB.prepare("INSERT INTO codes(code, days, created) VALUES (?,?,?)").bind(code, days, now()).run();
    return json({ code, days });
  }
  if (path === "/admin/list") {
    const { results } = await env.DB.prepare("SELECT * FROM machines ORDER BY first_seen").all();
    const codes = await env.DB.prepare("SELECT code, days, created, redeemed_by FROM codes ORDER BY created").all();
    return json({ machines: results.map(describeRow), codes: codes.results });
  }
  if (path === "/admin/check") {
    const [m, err] = await findMachine(env, String(body.machine || ""));
    if (!m) return json({ error: err }, 404);
    const r = await env.DB.prepare("SELECT * FROM machines WHERE machine=?").bind(m).first();
    return json(describeRow(r));
  }
  if (path === "/admin/revoke") {
    const [m, err] = await findMachine(env, String(body.machine || ""));
    if (!m) return json({ error: err }, 404);
    await env.DB.prepare("UPDATE machines SET licensed_until=? WHERE machine=?").bind(body.revoked ? -1 : null, m).run();
    return json({ machine: m, revoked: !!body.revoked });
  }
  if (path === "/admin/update") {
    const { version, url, sha256 } = body;
    if (!/^\d+\.\d+\.\d+$/.test(String(version)) || !/^https:\/\//.test(String(url)) || !/^[0-9a-f]{64}$/i.test(String(sha256)))
      return json({ error: "need version x.y.z, https url, 64-hex sha256" }, 400);
    const payload = { published: Math.floor(now()), sha256: String(sha256).toLowerCase(), url: String(url), version: String(version) };
    const signature = (await sign(env, payload)).split(".")[1];
    const manifest = { ...payload, signature };
    await env.DB.prepare("INSERT INTO meta(k, v) VALUES ('update_manifest', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v")
      .bind(JSON.stringify(manifest)).run();
    return json(manifest);
  }
  if (path === "/admin/usage") {
    const { results } = await env.DB.prepare("SELECT day, n FROM usage ORDER BY day DESC LIMIT 14").all();
    const machines = await env.DB.prepare("SELECT COUNT(*) AS c FROM machines").first();
    return json({ days: results, machines: machines.c, free_limits: { workers_requests_per_day: 100000, d1_writes_per_day: 100000, d1_reads_per_day: 5000000 },
      note: "approximate (counted in batches of 25); exact numbers: Cloudflare dashboard → Workers & Pages → mfh-license → Metrics" });
  }
  return json({ error: "not found" }, 404);
}

// ------------------------------------------------------------------ entry
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const path = url.pathname;
    try {
      if (req.method === "GET") {
        if (path === "/v1/health") return json({ ok: true, server_time: now(), public_key: await publicKeyHex(env) });
        if (path === "/v1/update") {
          const row = await env.DB.prepare("SELECT v FROM meta WHERE k='update_manifest'").first();
          return row ? json(JSON.parse(row.v)) : json({ error: "no update published" }, 404);
        }
        if (path === "/admin/usage") return admin(req, env, path, {});
        return json({ error: "not found" }, 404);
      }
      if (req.method !== "POST") return json({ error: "method" }, 405);

      const ip = req.headers.get("cf-connecting-ip") || "unknown";
      if (env.RL) {
        try {
          const { success } = await env.RL.limit({ key: ip });
          if (!success) return json({ error: "too many requests" }, 429);
        } catch (_) { /* binding unavailable locally */ }
      }
      const len = Number(req.headers.get("content-length") || 0);
      if (len > 4096) return json({ error: "too large" }, 413);
      let body;
      try { body = await req.json(); } catch (_) { return json({ error: "bad json" }, 400); }

      if (path.startsWith("/admin/")) return admin(req, env, path, body || {});

      await countRequest(env, ctx);
      const machine = String(body.machine || "");
      if (!/^[0-9a-f]{64}$/i.test(machine)) return json({ error: "bad machine id" }, 400);
      const version = String(body.version || "").slice(0, 32);
      if (path === "/v1/activate" || path === "/v1/status") return json(await tokenFor(env, machine, version));
      if (path === "/v1/redeem") {
        const [ok, msg] = await redeem(env, machine, String(body.code || "").trim().toUpperCase());
        if (!ok) return json({ error: msg }, 403);
        return json(await tokenFor(env, machine, version));
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: "server error: " + (e && e.message ? e.message : String(e)) }, 500);
    }
  },
};
