"""Kaggle's JSON-over-HTTP RPC API.

The official `kaggle` CLI does not expose kernel *sessions*, but the API under
it does. Every method is `POST {BASE}/{service}/{Method}` with a camelCase JSON
body and an OAuth Bearer token (see auth.py). Verified against kagglesdk 0.1.37.

Run any command with `kdev -v` to log each call, its status and its timing.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

BASE = "https://api.kaggle.com/v1"
KERNELS = "kernels.KernelsApiService"
IAM = "security.IamService"
GROUPS = "users.GroupApiService"

#: KaggleResourceType.KAGGLE_RESOURCE_TYPE_KERNELS
RESOURCE_KERNEL = "KAGGLE_RESOURCE_TYPE_KERNELS"
#: CanonicalRole values, as the enum serialises by name.
ROLE_VIEWER, ROLE_EDITOR, ROLE_ADMIN = (
    "CANONICAL_ROLE_VIEWER",
    "CANONICAL_ROLE_EDITOR",
    "CANONICAL_ROLE_ADMIN",
)

# machine_shape values accepted by CreateKernelSession/SaveKernel.
# None == CPU-only. Only the first four are on a normal account; the gated ones
# 403 unless yours has them, and are listed so `--gpu` can pass them through.
SHAPES = {
    "none": None,
    "t4": "NvidiaTeslaT4",
    "p100": "NvidiaTeslaP100",
    "tpu": "Tpu1VmV38",
    "l4": "NvidiaL4",
    "a100": "NvidiaTeslaA100",
    "h100": "NvidiaTeslaH100",
}

#: Shapes every account has. `--gpu` accepts the rest, the picker does not
#: offer them, and Kaggle answers 403 if the account is not entitled.
COMMON_SHAPES = ("none", "t4", "p100", "tpu")


log = logging.getLogger("kdev.api")


class KaggleError(RuntimeError):
    """A Kaggle API call failed. `status` is the HTTP status, 0 if none."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Creds:
    """How one account authenticates: its username and a current OAuth token.

    Kaggle rejects the legacy username+key on the endpoints kdev needs, so a
    token is the only path.
    """

    username: str
    token: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


#: Status codes worth a second attempt. Anything else is a real answer.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


_pool: httpx.Client | None = None
_pool_lock = threading.Lock()


def _post(url: str, **kwargs: Any) -> httpx.Response:
    """POST over one shared keep-alive pool: a fresh TLS handshake per call
    cost ~0.3s each, and a status check makes several calls in a row. Tests
    patch `httpx.post`, so a patched one is honoured."""
    global _pool
    if httpx.post is not _REAL_POST:
        return httpx.post(url, **kwargs)
    with _pool_lock:
        if _pool is None:
            _pool = httpx.Client(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=8))
    return _pool.post(url, **kwargs)


_REAL_POST = httpx.post

#: Extra tries a retryable call gets when Kaggle answers 429.
RATE_LIMIT_EXTRA = 2


def _backoff(r: httpx.Response, attempt: int) -> float:
    """Kaggle's Retry-After when it sends one (capped), else exponential with
    jitter -- so threads that were limited together do not return together."""
    try:
        after = float((getattr(r, "headers", None) or {}).get("retry-after", ""))
    except ValueError:
        after = 0.0
    if after > 0:
        return min(after, 30.0)
    return 2**attempt + random.uniform(0, 1)


def call(
    creds: Creds,
    method: str,
    payload: dict[str, Any] | None = None,
    service: str = KERNELS,
    attempts: int = 3,
) -> dict:
    url = f"{BASE}/{service}/{method}"
    # Two budgets. Failures (no answer, 5xx) get the caller's `attempts`.
    # Rate limiting is Kaggle asking us to slow down, not failing: a call that
    # may be retried at all gets RATE_LIMIT_EXTRA more tries for a 429 only,
    # so being offline still fails fast.
    tries = 0
    limited = 0
    while True:
        tries += 1
        last = tries >= attempts
        started = time.monotonic()
        try:
            r = _post(url, json=payload or {}, headers=creds.headers, timeout=120.0)
        except httpx.HTTPError as e:
            log.debug("%s as %s: %s (attempt %d)", method, creds.username, e, tries)
            if last:
                raise KaggleError(f"{method}: could not reach Kaggle ({e})") from e
            time.sleep(2 ** (tries - 1))
            continue
        log.debug(
            "%s as %s -> %d in %.2fs",
            method,
            creds.username,
            r.status_code,
            time.monotonic() - started,
        )
        if r.status_code == 429 and attempts > 1 and limited < RATE_LIMIT_EXTRA:
            limited += 1
            tries -= 1  # a rate-limited try does not spend the failure budget
            time.sleep(_backoff(r, limited))
            continue
        if r.status_code in RETRY_STATUS and not last:
            time.sleep(_backoff(r, tries - 1))
            continue
        break
    if r.status_code == 401:
        raise KaggleError(f"{method}: {creds.username or 'this account'} is not signed in", 401)
    if r.status_code >= 400:
        raise KaggleError(f"{method}: {_message(r)}", r.status_code)
    try:
        body = r.json() if r.content else {}
    except ValueError as e:
        raise KaggleError(f"{method}: Kaggle sent something that is not JSON", r.status_code) from e
    # The API returns 200 with an `error` field for validation failures.
    if isinstance(body, dict) and body.get("error"):
        raise KaggleError(f"{method}: {body['error']}", r.status_code)
    return body


def _message(r: httpx.Response) -> str:
    """Kaggle's own error message, not the JSON envelope around it."""
    try:
        body = r.json()
    except ValueError:
        body = None
    # A proxy or an outage page can answer with any JSON at all, or none.
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return f"{err['message']} (HTTP {r.status_code})"
    return f"HTTP {r.status_code}: {r.text[:200]}"


def save_kernel(
    creds: Creds,
    *,
    slug: str,
    title: str,
    source: str,
    machine_shape: str | None,
    timeout_seconds: int,
    run: bool = True,
    kernel_type: str = "script",
) -> dict:
    """Create-or-update the notebook and run it top-to-bottom (Save & Run All)."""
    payload: dict[str, Any] = {
        "slug": slug,
        "newTitle": title,
        "text": source,
        "language": "python",
        "kernelType": kernel_type,
        "isPrivate": True,
        "enableInternet": True,
        "kernelExecutionType": "SAVE_AND_RUN_ALL" if run else "QUICK_SAVE",
        "sessionTimeoutSeconds": timeout_seconds,
        "kernelDataSources": [],
        "datasetDataSources": [],
    }
    if machine_shape:
        payload["machineShape"] = machine_shape
    # This creates a run. A lost response is not permission to create another.
    return call(creds, "SaveKernel", payload, attempts=1)


#: The most ListKernelSessionOutput accepts per page (it says so on a 400).
OUTPUT_PAGE = 500
#: The first page of a prefix read: enough for `.kdev/` when it sorts first.
PREFIX_PAGE = 50


def session_output(creds: Creds, slug: str, version: str = "", *, prefix: str = "") -> list[dict]:
    """Every file a saved version left in /kaggle/working, with signed URLs.

    `version` is a label like "v7"; empty means the latest. With `prefix`,
    paging stops once every name under it has been listed (see `_past`). Older versions stay
    readable, which is what lets a restore build on the last good one when the
    newest was cut short. A notebook cannot mount its own output (Kaggle
    rejects the data source), so this listing is the only way back.
    """
    user, _, kslug = slug.partition("/")
    out = []
    token = None
    # A prefix read wants the first names only: a small page answers faster.
    # If that page does not get past the prefix (a `.git/` or `.cache/` at the
    # root sorts before `.kdev/`), start over at the full size rather than
    # walk thousands of names fifty at a time.
    size = PREFIX_PAGE if prefix else OUTPUT_PAGE
    while True:
        payload = {"userName": user, "kernelSlug": kslug, "pageSize": size}
        if version:
            payload["versionLabel"] = version
        if token:
            payload["pageToken"] = token
        resp = call(creds, "ListKernelSessionOutput", payload)
        for f in resp.get("files") or []:
            name = f.get("fileName")
            if name and f.get("url"):
                out.append({"name": name, "url": f["url"]})
        token = resp.get("nextPageToken")
        if not token:
            return out
        if size == PREFIX_PAGE and not _past(resp.get("files") or [], prefix):
            out, token, size = [], None, OUTPUT_PAGE
            continue
        if prefix and _past(resp.get("files") or [], prefix):
            # Only the names under `prefix` must be complete, and Kaggle lists
            # names in order (measured): a sorted page that ends beyond the
            # prefix means no later page holds one. The listing returned is
            # partial -- complete under `prefix`, a sample of the rest.
            return out


def _past(page: list[dict], prefix: str) -> bool:
    """Whether a page is sorted and ends after every name starting `prefix`."""
    names = [str(f.get("fileName") or "") for f in page]
    return bool(names) and names == sorted(names) and names[-1] > prefix + "\U0010ffff"


#: The status of a notebook that has never been run. Kaggle answers that with
#: HTTP 404 "No runs found for this kernel" rather than a state -- measured on a
#: notebook freshly created in the Kaggle editor.
NEVER_RUN = "NEVER_RUN"


def session_status(creds: Creds, slug: str, version: str = "") -> dict:
    """{"status": ..., "failureMessage": ...} -- the second is why it stopped.

    Pass a saved version label such as "v7" to ask about that run instead of
    the latest one.

    A 404 for the latest run means the notebook has never run. For an explicit
    historical version it means Kaggle has no status for that version.
    """
    user, _, kslug = slug.partition("/")
    payload = {"userName": user, "kernelSlug": kslug}
    if version:
        payload["versionLabel"] = version
    try:
        return call(creds, "GetKernelSessionStatus", payload)
    except KaggleError as e:
        if e.status == 404:
            return {"status": "UNKNOWN" if version else NEVER_RUN}
        raise


#: Session states that mean the box is, or is about to be, alive.
LIVE_STATES = frozenset({"RUNNING", "QUEUED"})
#: Stopped, but its files are still being saved. Measured after a cancel: the
#: version listed 0, then 29, then all 54 files while in this state, and was
#: complete only once it moved on. (A crash stays RUNNING until it is saved.)
SAVING = "CANCEL_REQUESTED"


def cancel_session(creds: Creds, kernel_session_id: int) -> dict:
    return call(creds, "CancelKernelSession", {"kernelSessionId": kernel_session_id})


def quota(creds: Creds) -> dict:
    return call(creds, "GetAcceleratorQuotaStatistics", {})


def stream_logs(
    creds: Creds,
    slug: str,
    wait_seconds: int = 300,
    idle: float | None = None,
    *,
    version: str = "",
) -> Iterator[str]:
    """Yield log lines from a running session.

    While the session lives the API returns SSE; once it has terminated it
    returns the persisted JSON log instead, so we branch on content-type
    exactly as the SDK docstring instructs. `idle` ends the stream once no line
    has come for that many seconds: the log so far, without following it.
    """
    user, _, kslug = slug.partition("/")
    payload = {
        "userName": user,
        "kernelSlug": kslug,
        "waitForLogsUrlSeconds": min(wait_seconds, 300),
    }
    if version:
        payload["versionLabel"] = version
    with httpx.stream(
        "POST",
        f"{BASE}/{KERNELS}/GetKernelSessionLogsStream",
        json=payload,
        headers=creds.headers,
        timeout=httpx.Timeout(30.0, read=idle),
    ) as r:
        if r.status_code >= 400:
            r.read()
            raise KaggleError(f"logs: HTTP {r.status_code}: {r.text[:300]}", r.status_code)
        if "text/event-stream" in r.headers.get("content-type", ""):
            try:
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "END_OF_LOG":
                            return
                        yield _log_text(data)
            except httpx.ReadTimeout:
                if idle is None:
                    raise
        else:
            try:
                entries = json.loads(r.read() or b"[]")
            except ValueError as e:
                raise KaggleError(
                    "logs: Kaggle sent something that is not JSON", r.status_code
                ) from e
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                yield _log_text(entry)


def _log_text(entry: Any) -> str:
    """Log entries arrive as JSON objects with a `data` field, or bare strings."""
    if isinstance(entry, str):
        try:
            entry = json.loads(entry)
        except json.JSONDecodeError:
            return entry
    if isinstance(entry, dict):
        return str(entry.get("data", entry.get("text", entry)))
    return str(entry)


# --- sharing ------------------------------------------------------------------


def get_policy(creds: Creds, kernel_id: int) -> dict:
    return call(
        creds,
        "GetIamPolicy",
        {"resourceId": {"type": RESOURCE_KERNEL, "id": kernel_id}},
        service=IAM,
    )


def share_with_group(
    creds: Creds, kernel_id: int, group_slug: str, role: str = ROLE_EDITOR
) -> dict:
    """Grant a Kaggle group a role on a notebook, preserving other bindings.

    Read-modify-write rather than a blind overwrite: SetIamPolicy replaces the
    whole policy, so sending only our binding would drop every collaborator
    already on the notebook.
    """
    policy = get_policy(creds, kernel_id)
    bindings = [dict(b) for b in policy.get("bindings") or []]

    def is_our_group(member: dict) -> bool:
        # GetIamPolicy leaves `group.slug` null and puts it on `group.avatar`
        # (see policy_groups); a binding we wrote ourselves has it at the top.
        g = member.get("group") or {}
        return group_slug in (g.get("slug"), (g.get("avatar") or {}).get("slug"))

    for b in bindings:
        b["members"] = [m for m in (b.get("members") or []) if not is_our_group(m)]
    bindings = [b for b in bindings if b.get("members")]

    target = next((b for b in bindings if b.get("role") == role), None)
    if target is None:
        target = {"role": role, "members": []}
        bindings.append(target)
    target["members"].append({"group": {"slug": group_slug}})

    new_policy = {"bindings": bindings}
    if policy.get("owner"):
        new_policy["owner"] = policy["owner"]
    return call(
        creds,
        "SetIamPolicy",
        {"resourceId": {"type": RESOURCE_KERNEL, "id": kernel_id}, "policy": new_policy},
        service=IAM,
    )


def group_role(policy: dict, group_slug: str) -> str:
    for g in policy_groups(policy):
        if g["slug"] == group_slug:
            return g["role"]
    return ""


def group_members(creds: Creds, group_slug: str) -> list[dict]:
    resp = call(
        creds,
        "ListUserManagedGroupMemberships",
        {"groupSlug": group_slug, "pageSize": 100},
        service=GROUPS,
    )
    return resp.get("memberships") or []


def list_shared_kernels(creds: Creds) -> list[dict]:
    """Notebooks shared with this account -- how a teammate finds the box."""
    resp = call(creds, "ListKernels", {"group": "COLLABORATION", "pageSize": 100})
    return resp.get("kernels") or []


def get_kernel(creds: Creds, slug: str, version: str = "") -> dict:
    """The kernel's metadata. Note the response key is `metadata`, not `kernel`."""
    user, _, kslug = slug.partition("/")
    payload = {"userName": user, "kernelSlug": kslug}
    if version:
        payload["versionLabel"] = version
    resp = call(creds, "GetKernel", payload)
    return resp.get("metadata") or {}


def kernel_source(creds: Creds, slug: str) -> str:
    """The notebook's current source, for checking before overwriting it."""
    user, _, kslug = slug.partition("/")
    resp = call(creds, "GetKernel", {"userName": user, "kernelSlug": kslug})
    return (resp.get("blob") or {}).get("source", "") or ""


def kernel_id(creds: Creds, slug: str) -> int:
    try:
        return int(get_kernel(creds, slug).get("id") or 0)
    except (KaggleError, TypeError, ValueError):
        return 0


def policy_groups(policy: dict) -> list[dict]:
    """Groups a notebook is shared with.

    The identifying fields live on the nested `avatar`, not on the group
    principal itself -- `group.slug` comes back null while `group.avatar.slug`
    is populated.
    """
    out = []
    for b in policy.get("bindings") or []:
        for m in b.get("members") or []:
            g = m.get("group")
            if not g:
                continue
            av = g.get("avatar") or {}
            out.append(
                {
                    "id": g.get("id") or av.get("id"),
                    "slug": g.get("slug") or av.get("slug") or "",
                    "name": av.get("name") or "",
                    "members": av.get("memberCount") or 0,
                    "owner": (av.get("owner") or {}).get("userName", ""),
                    "role": b.get("role", ""),
                }
            )
    return out
