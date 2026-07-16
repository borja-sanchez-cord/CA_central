#!/usr/bin/env python3
"""
CA Activity Visibility — Phase 3: dedup / attribution / classification rules.

PURE LOGIC ONLY — no database, no network. model/build_activity.py feeds it
raw rows and writes the results; tests/ replay the validated real-world cases
(tests/fixtures/*.json) through these exact functions. If you change a rule,
a fixture test must prove the known-right answer still comes out.

The authoritative statements of these rules live in docs/spec.md §3 (with the
evidence for every constant: the ±180s window, the ~150-char body prefix, why
recipient overlap is mandatory, why subject is only a tiebreak). Do not tune
a constant here without reading §3 first.

Rule index (spec §3 / roadmap Phase 3 checklist):
  1  attribute by SENDER address, never hubspot_owner_id     -> prep_email + caller
  2  sent vs received by sender ∈ CA addresses               -> prep_email
  3  cross-tool duplicate collapse (corrected key)           -> cluster_emails
  4  four email sources; unknown = manual, never dropped     -> cluster_is_automated
  5  invite/notification emails -> meeting channel           -> subject_is_invite
  6  invite fan-out collapse (subject+time, invites only)    -> cluster_emails
  7  AmpleMarket email task ≠ send (HubSpot copy counts)     -> task_channel
  8  calls: attempts vs conversations, never task_id         -> group_calls
  9  internal-only-recipient exclusion over to ∪ cc ∪ bcc    -> prep_email
 10  duplicate meeting objects: (title, start_time, owner)   -> dedupe_meetings
 11  inbound: same dedup + bounce/auto-reply noise filter    -> sender_is_noise etc.
"""
import re
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestion"))
from ingest import _extract_emails, INTERNAL_DOMAINS

# ---------------------------------------------------------------- constants
# Dedup window: copies of one send land seconds-to-minutes apart (max measured
# 160s across 139 live duplicate groups) — spec §3, do not narrow.
DEDUP_WINDOW_SECONDS = 180
# Body-prefix length for the dedup key (~150 chars, whitespace-normalized).
BODY_PREFIX_CHARS = 150
# Repeated dials at one contact within this gap chain into ONE conversation
# group (validated: 4 dials in ~90s = 1 conversation; a redial hours later is
# a new pursuit).
CALL_GROUP_GAP_SECONDS = 1800

# Calendar invite / calendar-notification subjects (checked AFTER stripping
# tool prefixes but BEFORE stripping reply prefixes — "Re: Declined: X" is a
# human writing back about a decline, not calendar noise).
INVITE_SUBJECT_PREFIXES = (
    "invitation:", "updated invitation:", "canceled event", "cancelled event",
    "accepted:", "declined:", "tentatively accepted:", "tentative:",
    "new event:", "event confirmed:", "event canceled:", "event cancelled:",
)

# Auto-generated senders (bounces, platform notifications) — engagement noise.
NOISE_SENDER_LOCAL_MARKERS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "notification", "bounce",
)

# Auto-reply / OOO / delivery-failure subjects, incl. the localized variants
# measured live (spec §3: 287 automated-sender rows + 22 auto-replies in 5
# days ≈ 2x inflation of reply metrics if kept).
AUTOREPLY_SUBJECT_PREFIXES = (
    "automatic reply", "auto reply", "auto-reply", "autoreply",
    "out of office", "respuesta autom", "automatische antwort",
    "reponse automatique", "réponse automatique", "risposta automatica",
    "resposta autom", "autosvar", "abwesenheit",
    "undeliverable", "undelivered mail", "delivery status notification",
    "mail delivery", "delivery failure", "failure notice", "returned mail",
)

# AmpleMarket task type -> channel (spec §2). email/phone_call tasks are
# SHADOWS: the countable record of the send is the HubSpot copy, and the
# countable record of the dial is /calls — the task rows are kept visible but
# never counted, so nothing is double-counted and nothing is dropped.
TASK_CHANNEL = {
    "email": "email_task",
    "phone_call": "call_task",
    "linkedin_message": "li_message",
    "linkedin_voice_message": "li_message",
    "linkedin_video_message": "li_message",
    "linkedin_connect": "li_connect",
    "linkedin_visit": "li_other",
    "linkedin_follow": "li_other",
    "linkedin_like_last_post": "li_other",
    "whatsapp": "whatsapp",
    "sms": "sms",
}

# HubSpot email-copy sources that prove a HUMAN mailbox sent it (manual Gmail
# via the Sales extension, or typed into the HubSpot UI).
HUMAN_EMAIL_SOURCES = ("EMAIL", "CRM_UI")


def id_key(rid):
    """Numeric-aware sort for source ids ('99' > '100' as text — the same
    pitfall identity/resolve.py guards; duplicated here to keep this module
    import-light and pure)."""
    return (0, int(rid)) if rid.isdigit() else (1, rid)


# ------------------------------------------------------------ normalization
_TOOL_PREFIX_RE = re.compile(r"^\[[^\]]{1,40}\]\s*")           # [Apollo] [Email] [<<] ...
# Reply markers need their colon ('VS Code…' must survive); 'R.e.' is a live
# colon-less variant reps type by hand.
_REPLY_PREFIX_RE = re.compile(r"^(?:(?:re|aw|fw|fwd|sv|vs|wg|antw|tr|rv)\s*:|r\.e\.:?)\s*", re.I)


def strip_tool_prefixes(subject):
    """Remove leading bracketed tool tags: '[Apollo] [Email] [<<] X' -> 'X'."""
    s = (subject or "").strip()
    while True:
        m = _TOOL_PREFIX_RE.match(s)
        if not m:
            return s
        s = s[m.end():]


def strip_reply_prefixes(subject):
    """Remove leading reply/forward markers, repeatedly ('Re: Fwd: X' -> 'X')."""
    s = (subject or "").strip()
    while True:
        m = _REPLY_PREFIX_RE.match(s)
        if not m:
            return s.strip()
        s = s[m.end():]


def norm_subject(subject):
    """The dedup-key form: tool AND reply prefixes stripped, lowercased,
    whitespace collapsed. Subject is a TIEBREAK only, never primary (§3)."""
    s = strip_reply_prefixes(strip_tool_prefixes(subject))
    return re.sub(r"\s+", " ", s).strip().lower()


# Mail-client banners injected at the TOP of one tool's copy but not the
# other's (measured live 2026-07-16: the Gmail first-contact banner made a
# prospect reply's two copies look like different emails). Stripped before
# the body prefix is taken.
_BODY_BANNER_RES = [
    re.compile(r"^this is the first time you'?re receiving an email from this person\.?\s*"
               r"(make sure you check the email address[^.]*\.\s*)?"),
    re.compile(r"^you don'?t often get email from \S+\.?\s*(learn why this is important\.?)?\s*"),
    re.compile(r"^caution:? this email originated from outside[^.]*\.\s*"),
    re.compile(r"^\[?external( email)?\]?:?\s+"),
]


def norm_body_prefix(body, n=BODY_PREFIX_CHARS):
    """Whitespace-normalized body prefix — raw copies drift by whitespace/nbsp
    in 23% of duplicate groups (§3), so normalize (and drop injected client
    banners) before comparing."""
    if not body:
        return None
    t = re.sub(r"\s+", " ", body.replace("\xa0", " ")).strip().lower()
    for banner in _BODY_BANNER_RES:
        t = banner.sub("", t, count=1)
    return t[:n] or None


# Prefix-of matches need enough signal to be safe: 'thanks!' being a prefix
# of 'thanks! one more thing…' must not merge two real consecutive replies.
_BODY_PREFIX_MATCH_MIN = 20


def bodies_match(a, b):
    """Are two normalized body prefixes the same send's body? Exact match, or
    one is a prefix of the other (>=20 chars) — copies of one send differ by
    a trailing signature block that only some tools include in the preview
    (measured live 2026-07-16: 'try now.' vs 'try now. -- kind regards …')."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= _BODY_PREFIX_MATCH_MIN and long_.startswith(short)


# ------------------------------------------------------------ classification
# External-mail tags some orgs prepend ('EXT: Automatic reply: …') — stripped
# for CLASSIFICATION only, so a tagged auto-reply doesn't count as engagement.
_EXTERNAL_TAG_RE = re.compile(r"^(ext|external)\s*:\s*", re.I)


def _classification_subject(subject):
    s = strip_tool_prefixes(subject)
    return _EXTERNAL_TAG_RE.sub("", s, count=1).lower().lstrip()


def subject_is_invite(subject):
    """Calendar invite/notification? Checked on the TOOL-stripped subject —
    reply prefixes intact, so a human's 'Re: Declined: X' stays an email."""
    return _classification_subject(subject).startswith(INVITE_SUBJECT_PREFIXES)


def subject_is_autoreply(subject):
    return _classification_subject(subject).startswith(AUTOREPLY_SUBJECT_PREFIXES)


def sender_is_noise(sender):
    """Auto-generated sender (bounces/platform notifications)?"""
    if not sender:
        return False
    local = sender.rsplit("@", 1)[0].lower()
    return any(m in local for m in NOISE_SENDER_LOCAL_MARKERS)


def is_internal_address(addr):
    return addr.rsplit("@", 1)[1] in INTERNAL_DOMAINS


def prep_email(row):
    """Parse one raw_hubspot_emails row (dict) into the fields every rule
    reads. Direction comes from the SENDER (rule 1/2): internal-domain sender
    = outbound rep effort, anything else = inbound. hubspot_owner_id is never
    consulted. Recipient sets are computed over to ∪ cc ∪ bcc (rule 9)."""
    senders = _extract_emails(row.get("from_email"))
    sender = senders[0] if senders else None
    recipients = []          # ordered: to, then cc, then bcc (contact pick order)
    for f in ("to_email", "cc_email", "bcc_email"):
        for a in _extract_emails(row.get(f)):
            if a not in recipients:
                recipients.append(a)
    external = [a for a in recipients if not is_internal_address(a)]
    outbound = sender is not None and is_internal_address(sender)
    return {
        "id": row["id"],
        "ts": row.get("hs_timestamp"),
        "activity_date": row.get("activity_date"),
        "subject": row.get("subject"),
        "subject_norm": norm_subject(row.get("subject")),
        "body_norm": norm_body_prefix(row.get("body_preview")),
        "body_preview": row.get("body_preview"),
        "body_html": row.get("body_html"),
        "object_source": row.get("object_source"),
        "object_source_detail": row.get("object_source_detail"),
        "sender": sender,
        "recipients": recipients,
        "external_recipients": external,
        "direction": "outbound" if outbound else "inbound",
        "is_invite": subject_is_invite(row.get("subject")),
        "is_noise": (not outbound) and (sender_is_noise(sender)
                                        or subject_is_autoreply(row.get("subject"))),
    }


# ----------------------------------------------------------------- dedup key
def emails_mergeable(a, b):
    """The corrected cross-tool duplicate key (spec §3, audit 2026-07-15).

    Callers guarantee same sender, same direction, and a timestamp gap within
    DEDUP_WINDOW_SECONDS. This decides the rest:

    - BOTH rows INVITE-classified: subject+time is enough (the ONLY
      recipient-less collapse allowed — per-attendee fan-out means copies
      genuinely differ in recipient).
    - Everything else — including an invite row against a non-invite row
      (a synced copy can ADD 'Re: ' to an invite's subject, knocking it out
      of the invite classification; the strict key below still catches it —
      review 2026-07-16): non-empty overlap of recipients is MANDATORY
      (sequence blasts share sender+subject+body across DISTINCT recipients —
      7.8% of all CA outbound would false-merge without this). Overlap is
      computed on EXTERNAL recipients for outbound (a rep self-cc'd on every
      blast send must not create overlap); on ALL recipients for inbound (a
      prospect reply's recipients are our own reps — an all-external rule
      would find nothing).
    - When both copies carry a body: the normalized prefixes must match
      (bodies distinguish e.g. a meet-link email from the intro email sent 4
      minutes later to the same person). When either body is missing (rows
      predating body capture): stripped-subject equality is the tiebreak.
    """
    if not a["sender"]:
        return False
    if a["is_invite"] and b["is_invite"]:
        return bool(a["subject_norm"]) and a["subject_norm"] == b["subject_norm"]
    ovl_field = "external_recipients" if a["direction"] == "outbound" else "recipients"
    if not set(a[ovl_field]) & set(b[ovl_field]):
        return False
    if a["body_norm"] is not None and b["body_norm"] is not None:
        return bodies_match(a["body_norm"], b["body_norm"])
    return a["subject_norm"] == b["subject_norm"]


def cluster_emails(rows, window=DEDUP_WINDOW_SECONDS):
    """Group prepped email rows into duplicate clusters (rules 3 + 6 + 11).

    Partition by (sender, direction), sort by time, union-find over pairs
    within the window. Merging is transitive on purpose: copies A-B and B-C
    within the window pull A-C into one cluster even if A-C exceeds it
    (duplicate groups are N-way across a varying set of sources). Two guards
    on transitivity (review 2026-07-16):
    - The CLUSTER-WIDE recipient intersection must stay non-empty for
      non-invite merges — a multi-recipient 'bridge' row must not chain two
      distinct single-recipient sends into one activity.
    - Invite-with-invite merges (subject+time) widen the tracked set instead
      (their copies genuinely differ in recipient).
    Rows with no timestamp or no sender never merge.
    """
    partitions = {}
    solo = []
    for r in rows:
        if r["ts"] is None or r["sender"] is None:
            solo.append([r])
            continue
        partitions.setdefault((r["sender"], r["direction"]), []).append(r)

    clusters = list(solo)
    for (_sender, direction), part in partitions.items():
        ovl_field = "external_recipients" if direction == "outbound" else "recipients"
        part.sort(key=lambda r: (r["ts"], id_key(r["id"])))
        parent = list(range(len(part)))
        shared = [set(r[ovl_field]) for r in part]  # per-root recipient intersection

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            ri, rj = find(i), find(j)
            if ri == rj:
                return
            if part[i]["is_invite"] and part[j]["is_invite"]:
                merged = shared[ri] | shared[rj]      # fan-out: recipients differ
            else:
                merged = shared[ri] & shared[rj]      # bridge guard
                if not merged:
                    return
            parent[rj] = ri
            shared[ri] = merged

        for i in range(len(part)):
            for j in range(i + 1, len(part)):
                if (part[j]["ts"] - part[i]["ts"]).total_seconds() > window:
                    break
                if emails_mergeable(part[i], part[j]):
                    union(i, j)

        grouped = {}
        for i, r in enumerate(part):
            grouped.setdefault(find(i), []).append(r)
        clusters.extend(grouped.values())
    return clusters


def pick_canonical(cluster):
    """Deterministic representative of a duplicate cluster: prefer the copy
    with a body (richest record), then one with recipients, then earliest,
    then lowest id."""
    never = datetime.max.replace(tzinfo=timezone.utc)
    return min(cluster, key=lambda r: (r["body_norm"] is None,
                                       not r["recipients"],
                                       r["ts"] or never,
                                       id_key(r["id"])))


def summarize_email_cluster(cluster, ca_addresses):
    """Every judgment call for one duplicate cluster -> the activity row's
    decision fields. Pure (testable); build_activity.py just maps the result
    onto table columns.

    Decisions are made over the WHOLE cluster, never the canonical copy alone
    (review 2026-07-16): copies of one send carry different recipient lists
    (Apollo drops internal/self recipients), so a CA present only on a
    non-canonical copy must still attribute; a mutated-subject copy must not
    hide the cluster's invite/noise classification.
    """
    can = pick_canonical(cluster)
    never = datetime.max.replace(tzinfo=timezone.utc)
    ordered = sorted(cluster, key=lambda r: (r["ts"] or never, id_key(r["id"])))
    recipients, external = [], []          # ordered union across all copies
    for r in ordered:
        for a in r["recipients"]:
            if a not in recipients:
                recipients.append(a)
                if not is_internal_address(a):
                    external.append(a)
    is_invite = any(r["is_invite"] for r in cluster)
    is_noise = any(r["is_noise"] for r in cluster)

    direction = can["direction"]
    ca_id = reason = is_auto = auto_conf = None
    counts = False
    if can["sender"] is None:
        channel, direction, contact_email = "inbound_email", "inbound", None
        reason = "no_sender"
    elif direction == "outbound":
        ca_id = ca_addresses.get(can["sender"])
        contact_email = external[0] if external else None
        if is_invite:
            channel, reason = "meeting", "invite_email"
        else:
            is_auto, auto_conf = cluster_is_automated(cluster), "low"
            channel = "auto_email" if is_auto else "manual_email"
            if ca_id is None:
                reason = "non_ca_sender"
            elif not external:
                reason = "internal_only_recipients"
            else:
                counts = True
    else:  # inbound
        contact_email = can["sender"]
        ca_id = next((ca_addresses[a] for a in recipients if a in ca_addresses), None)
        if is_invite:
            channel, reason = "meeting", "invite_email"
        elif is_noise:
            channel, reason = "inbound_email", "noise_auto_generated"
        elif ca_id is None:
            channel, reason = "inbound_email", "inbound_no_ca_recipient"
        else:
            channel, counts = "inbound_email", True

    return {"canonical": can, "channel": channel, "direction": direction,
            "ca_id": ca_id, "counts": counts, "excluded_reason": reason,
            "contact_email": contact_email, "is_automated": is_auto,
            "automated_confidence": auto_conf}


def cluster_is_automated(cluster):
    """Automation lean for a HubSpot email cluster (rule 4, spec §4) — LOW
    confidence always. Any copy logged by a human mailbox path (manual Gmail
    'EMAIL' or typed-in-HubSpot 'CRM_UI') proves a human sent it; a cluster
    seen ONLY as tool-synced copies (Amplemarket / Apollo) leans automated.
    Unknown sources count as human (treat as manual, never drop — §3)."""
    known_tool = ("Amplemarket", "Apollo Integration")
    for r in cluster:
        if r["object_source"] in HUMAN_EMAIL_SOURCES:
            return False
        if r["object_source_detail"] not in known_tool:
            return False   # unknown source: treat as manual
    return True


# ---------------------------------------------------------------------- calls
def group_calls(calls, gap=CALL_GROUP_GAP_SECONDS):
    """Assign each dial a conversation-group id (rule 8): same user + same
    contact, chained while consecutive dials are within `gap`. task_id is
    NEVER the key (usually null). Dials with no contact can't be grouped —
    each is its own group (still counted; ~25% of real conversations carry no
    contact and are surfaced, not dropped). Returns {call_id: group_id}."""
    out = {}
    keyed = {}
    for c in calls:
        if c.get("contact_id") is None or c.get("start_date") is None:
            out[c["id"]] = f"solo:{c['id']}"
            continue
        keyed.setdefault((c.get("user_id"), c["contact_id"]), []).append(c)
    for (uid, cid), items in keyed.items():
        items.sort(key=lambda c: (c["start_date"], id_key(c["id"])))
        gid = last = None
        for c in items:
            if last is None or (c["start_date"] - last).total_seconds() > gap:
                gid = f"{uid}:{cid}:{c['id']}"
            out[c["id"]] = gid
            last = c["start_date"]
    return out


# ------------------------------------------------------------------- meetings
def dedupe_meetings(rows):
    """Collapse duplicate meeting OBJECTS (rule 10): same (title, start_time,
    owner) = one meeting under several HubSpot ids (~2% inflation measured).
    A meeting missing title or start_time never merges."""
    groups = {}
    for r in rows:
        if r.get("title") is None or r.get("start_time") is None:
            groups[("__solo__", r["id"])] = [r]
        else:
            groups.setdefault((r["title"], r["start_time"], r.get("owner_id")), []).append(r)
    return list(groups.values())


def pick_canonical_meeting(group):
    """Prefer the copy that knows its outcome; then lowest id."""
    return min(group, key=lambda r: (r.get("outcome") is None, id_key(r["id"])))


def meeting_ca_ids(row, ca_roster_ids):
    """All CAs on a meeting: attendee_owner_ids (semicolon-separated internal
    owner ids — the reliable path) plus owner_id, intersected with the
    roster. Sorted for determinism."""
    ids = set()
    for tok in (row.get("attendee_owner_ids") or "").split(";"):
        tok = tok.strip()
        if tok:
            ids.add(tok)
    if row.get("owner_id"):
        ids.add(row["owner_id"])
    return sorted(ids & ca_roster_ids, key=id_key)


# ---------------------------------------------------------------------- tasks
def task_channel(task_type):
    """AmpleMarket task type -> channel (rule 7: email/phone_call tasks are
    non-counted shadows; unknown types land as 'other', counted, never
    dropped)."""
    return TASK_CHANNEL.get(task_type, "other")
