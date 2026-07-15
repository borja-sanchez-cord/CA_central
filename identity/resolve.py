#!/usr/bin/env python3
"""
CA Activity Visibility — Phase 2: identity resolution.

Reads ONLY the raw landing tables (never calls source APIs) and materializes
the resolved-identity tables everything downstream queries:

  dim_ca                 the CA roster (owners in a config_ca_teams team minus
                         that team's parent members) + AmpleMarket user id
  dim_ca_address         every sending address linked to a CA (the basis for
                         the sender->rep attribution rule in spec §3)
  company_crosswalk      every HubSpot company -> one resolved (canonical)
                         company id; duplicates collapsed by normalized domain
  contact_crosswalk      every known contact email -> HubSpot contact id ->
                         resolved company
  amplemarket_contact_map  AmpleMarket contact id -> HubSpot contact id (via email)
  identity_unresolved    the human-review list (no-domain target companies,
                         unmatched activity emails, parked addresses)

Design (see docs/decisions.md "Authoritative CA roster" + "Sending-address
linking" and docs/spec.md §2/§3):
- FULL REBUILD each run: every table is derived deterministically from the raw
  tables, so we delete+reinsert rather than patch. Idempotent; safe to re-run.
- Kept separate from ingestion/ingest.py on purpose: ingestion is "pull, don't
  think"; this module is where identity judgment lives, so bugs here can never
  break the daily raw pull.
- Address matching is dot-insensitive on the local part (laurazhu@encord.ai is
  laura.zhu@encord.com) and NEVER attributes by domain alone: tryencord.com is
  shared by non-sales senders, so only roster-linked addresses count.
- tryencord.com addresses are PARKED (not linked) until the PM decides whether
  that domain carries real prospect outreach — see decisions.md. They land in
  identity_unresolved so the open question stays visible.
- Company duplicate collapse: by normalized domain only (lowercase, strip
  scheme/path/www). Free-email domains never collapse. No fuzzy-name merges —
  no-domain TARGET companies go to the review list instead of guessing.

Run:  python identity/resolve.py
"""
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestion"))
from ingest import load_env, require, INTERNAL_DOMAINS, _extract_emails

# Domains a company record can never be collapsed on (shared mail providers).
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "qq.com", "163.com", "126.com", "yandex.com",
    "mail.com", "zoho.com",
}

# Sending addresses that may be linked to a CA. tryencord.com is deliberately
# absent: shared by non-sales senders + counting decision still open (parked).
LINKABLE_DOMAINS = ("encord.com", "encord.ai")

DDL = """
create table if not exists dim_ca (
    ca_id text primary key,            -- HubSpot owner id
    name text not null,
    primary_email text not null,
    ca_teams text not null,            -- '; '-joined CA sub-team names
    resolved_at timestamptz not null default now()
);
alter table dim_ca drop column if exists amplemarket_user_id;  -- moved to ca_amplemarket_user (a CA can have >1 account)
-- A CA can hold MULTIPLE AmpleMarket accounts (e.g. an @encord.ai one and an
-- @encord.com one, calls split across both — found live: Callum 17 + Nico 7
-- calls sat under second accounts). Many-to-one link, never a single column.
create table if not exists ca_amplemarket_user (
    amplemarket_user_id text primary key,
    ca_id text not null,
    resolved_at timestamptz not null default now()
);
create table if not exists dim_ca_address (
    address text primary key,
    ca_id text not null,
    source text not null,              -- owner_primary | amplemarket_mailbox | observed_sender
    resolved_at timestamptz not null default now()
);
create table if not exists company_crosswalk (
    hubspot_company_id text primary key,
    resolved_company_id text not null, -- canonical HubSpot company id for the group
    domain_norm text,                  -- null = no usable domain (group of one)
    is_canonical boolean not null,
    resolved_at timestamptz not null default now()
);
create table if not exists contact_crosswalk (
    email text primary key,            -- lowercased
    hubspot_contact_id text not null,
    resolved_company_id text,          -- via associatedcompanyid -> company_crosswalk
    resolved_at timestamptz not null default now()
);
create table if not exists amplemarket_contact_map (
    amplemarket_contact_id text primary key,
    email text,
    hubspot_contact_id text,           -- null = email not in the contact mirror
    resolved_at timestamptz not null default now()
);
create table if not exists identity_unresolved (
    kind text not null,                -- what needs a human (see report)
    key text not null,
    detail text,
    resolved_at timestamptz not null default now(),
    primary key (kind, key)
);
"""


def norm_local(email):
    """Dot-insensitive local part: laura.zhu -> laurazhu."""
    return email.split("@")[0].replace(".", "").lower()


# Legal/generic suffixes that alone don't prove two names are the same company.
NAME_STOPWORDS = {
    "inc", "ltd", "llc", "gmbh", "co", "corp", "corporation", "company",
    "group", "holdings", "holding", "technologies", "technology", "tech",
    "ai", "labs", "io", "the", "plc", "sa", "ab", "bv", "oy", "limited",
}


def name_tokens(name):
    toks = set(re.findall(r"[a-z0-9]+", (name or "").lower()))
    return (toks - NAME_STOPWORDS) or toks


def norm_domain(d):
    """Company website domain -> comparable key; None if unusable."""
    if not d:
        return None
    d = d.strip().lower()
    d = d.split("//")[-1].split("/")[0].strip().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if "." not in d or " " in d or not d:
        return None
    return d


def rebuild(conn, table, columns, rows):
    """Full deterministic rebuild: delete everything, insert the derived rows."""
    with conn.cursor() as cur:
        cur.execute(f"delete from {table}")
        if rows:
            cols = ", ".join(columns)
            execute_values(cur, f"insert into {table} ({cols}) values %s",
                           rows, page_size=5000)
    conn.commit()


# --------------------------------------------------------------------- reps
def derive_cas(conn):
    """The roster: owners in a CA team minus that team's parent members."""
    with conn.cursor() as cur:
        cur.execute("""
            select o.id, o.first_name, o.last_name, lower(o.email),
                   array_agg(c.ca_team_name order by c.ca_team_name)
            from raw_hubspot_owners o
            join config_ca_teams c
              on exists (select 1 from jsonb_array_elements(o.teams) t
                         where t->>'name' = c.ca_team_name)
             and not exists (select 1 from jsonb_array_elements(o.teams) t
                             where t->>'name' = c.parent_team_name)
            where coalesce(o.archived, false) = false
            group by o.id, o.first_name, o.last_name, o.email
        """)
        return [{"ca_id": r[0], "name": f"{r[1] or ''} {r[2] or ''}".strip(),
                 "email": r[3], "teams": "; ".join(r[4])}
                for r in cur.fetchall()]


def link_addresses(conn, cas):
    """Every sending address that belongs to a CA, plus their AmpleMarket id.

    Match key = dot-insensitive local part, restricted to LINKABLE_DOMAINS.
    Sources, in order: the owner's primary email; AmpleMarket user email +
    mailboxes (also yields the uid->ca links — plural: a CA can have several
    AmpleMarket accounts); addresses observed as senders in raw_hubspot_emails.
    """
    by_local = {norm_local(ca["email"]): ca for ca in cas}
    if len(by_local) != len(cas):
        sys.exit("FATAL: two CAs share a dot-insensitive local part — "
                 "address matching is ambiguous; fix before resolving.")

    addresses = {}   # address -> (ca_id, source); first source wins
    parked = []      # tryencord + other unlinkable internal senders, for the report

    def link(addr, source):
        addr = addr.lower()
        domain = addr.rsplit("@", 1)[1]
        ca = by_local.get(norm_local(addr))
        if ca and domain in LINKABLE_DOMAINS:
            addresses.setdefault(addr, (ca["ca_id"], source))
            return True
        return False

    for ca in cas:
        link(ca["email"], "owner_primary")

    ample_users = {}  # amplemarket_user_id -> ca_id (a CA may own several accounts)
    with conn.cursor() as cur:
        cur.execute("""select id, lower(email),
                              (select array_agg(lower(m->>'email'))
                               from jsonb_array_elements(mailboxes) m)
                       from raw_amplemarket_users""")
        for uid, email, boxes in cur.fetchall():
            candidates = [email] + [b for b in (boxes or []) if b]
            ca = next((by_local[norm_local(a)] for a in candidates
                       if a and norm_local(a) in by_local
                       and a.rsplit("@", 1)[1] in INTERNAL_DOMAINS), None)
            if ca:
                ample_users[uid] = ca["ca_id"]
                for a in candidates:
                    if a and not link(a, "amplemarket_mailbox"):
                        parked.append(("address_parked", a,
                                       f"AmpleMarket mailbox of {ca['name']}, unlinkable domain"))

        cur.execute("""select lower(from_email), count(*) from raw_hubspot_emails
                       where from_email is not null group by 1""")
        for raw_from, n in cur.fetchall():
            for a in _extract_emails(raw_from):
                d = a.rsplit("@", 1)[1]
                if d in INTERNAL_DOMAINS and a not in addresses:
                    if not link(a, "observed_sender"):
                        parked.append(("address_parked", a,
                                       f"internal-domain sender, no CA match ({n} emails)"))

    return addresses, ample_users, parked


# ---------------------------------------------------------------- companies
def resolve_companies(conn):
    """Collapse HubSpot duplicate companies by normalized domain.

    Canonical pick within a domain group (deterministic): a row with a real
    target_account_owner beats one without; then earliest created; then lowest
    id. Companies with no usable domain form groups of one (never merged);
    the TARGET ones among them go to the review list.

    Name-agreement guard: a shared domain is necessary but NOT sufficient —
    HubSpot holds junk domains (found live: 'Kärcher' and 'SKV Consulting'
    both carrying domain google.com). A member only merges if its name shares
    a meaningful token with the canonical's name; otherwise it keeps its own
    id and is flagged for human review. Never silent-merge on domain alone.
    """
    with conn.cursor() as cur:
        cur.execute("""select id, domain, name,
                              nullif(target_account_owner, '') is not null,
                              hs_created
                       from raw_hubspot_companies""")
        companies = cur.fetchall()

    groups = {}
    no_domain = []
    for cid, domain, name, has_owner, created in companies:
        d = norm_domain(domain)
        if d is None or d in FREE_EMAIL_DOMAINS:
            no_domain.append((cid, name, has_owner))
            groups[("__solo__", cid)] = [(cid, name, has_owner, created, None)]
        else:
            groups.setdefault(("domain", d), []).append((cid, name, has_owner, created, d))

    never = datetime.max.replace(tzinfo=timezone.utc)  # missing created -> loses ties
    rows, merged_groups, merged_dupes = [], 0, 0
    conflicts = []
    for _key, members in groups.items():
        can_id, can_name = min(members,
                               key=lambda m: (not m[2], m[3] or never, m[0]))[:2]
        can_tokens = name_tokens(can_name)
        merged_any = False
        for cid, name, _own, _created, d in members:
            if cid == can_id or name_tokens(name) & can_tokens:
                rows.append((cid, can_id, d, cid == can_id))
                merged_any = merged_any or cid != can_id
            else:
                rows.append((cid, cid, d, True))  # shares domain, name disagrees
                conflicts.append(("company_domain_conflict", cid,
                                  f"'{name}' shares domain {d} with '{can_name}' "
                                  f"({can_id}) but names don't match — not merged"))
        if merged_any:
            merged_groups += 1
            merged_dupes += sum(1 for r in rows[-len(members):] if r[1] == can_id) - 1

    rebuild(conn, "company_crosswalk",
            ["hubspot_company_id", "resolved_company_id", "domain_norm", "is_canonical"],
            rows)
    review = [("company_no_domain", cid, name) for cid, name, has_owner in no_domain if has_owner]
    return {"total": len(companies), "merged_groups": merged_groups,
            "merged_dupes": merged_dupes, "conflicts": len(conflicts),
            "no_domain": len(no_domain),
            "no_domain_target": len(review)}, review + conflicts


# ----------------------------------------------------------------- contacts
def resolve_contacts(conn):
    """Email -> HubSpot contact -> resolved company; AmpleMarket contact map."""
    with conn.cursor() as cur:
        cur.execute("""select lower(c.email), min(c.id)
                       from raw_hubspot_contacts c
                       where c.email is not null group by 1""")
        email_to_contact = dict(cur.fetchall())

        cur.execute("""select c.id, x.resolved_company_id
                       from raw_hubspot_contacts c
                       join company_crosswalk x on x.hubspot_company_id = c.associated_company_id""")
        contact_company = dict(cur.fetchall())

    contact_rows = [(email, cid, contact_company.get(cid))
                    for email, cid in email_to_contact.items()]
    rebuild(conn, "contact_crosswalk",
            ["email", "hubspot_contact_id", "resolved_company_id"], contact_rows)

    with conn.cursor() as cur:
        cur.execute("""select contact_id, min(lower(contact_email))
                       from (select contact_id, contact_email from raw_amplemarket_tasks
                             union all
                             select contact_id, contact_email from raw_amplemarket_calls) s
                       where contact_id is not null and contact_email is not null
                       group by contact_id""")
        ample_rows = []
        for acid, raw_email in cur.fetchall():
            emails = _extract_emails(raw_email)
            email = emails[0] if emails else None
            ample_rows.append((acid, email, email_to_contact.get(email)))
    rebuild(conn, "amplemarket_contact_map",
            ["amplemarket_contact_id", "email", "hubspot_contact_id"], ample_rows)

    # activity emails that have no HubSpot contact -> review list
    with conn.cursor() as cur:
        cur.execute("""select distinct lower(e) from (
                         select contact_email e from raw_amplemarket_tasks where contact_email is not null
                         union all select contact_email from raw_amplemarket_calls where contact_email is not null
                         union all select from_email from raw_hubspot_emails where from_email is not null
                         union all select to_email from raw_hubspot_emails where to_email is not null
                         union all select cc_email from raw_hubspot_emails where cc_email is not null) s""")
        activity = set()
        for (blob,) in cur.fetchall():
            activity.update(_extract_emails(blob))
    activity = {a for a in activity if a.rsplit("@", 1)[1] not in INTERNAL_DOMAINS}
    unmatched = sorted(a for a in activity if a not in email_to_contact)
    review = [("contact_email_unmatched", a, "seen in activity, no HubSpot contact") for a in unmatched]
    ample_unmatched = sum(1 for _, _, hs in ample_rows if hs is None)
    return {"contact_emails": len(contact_rows), "activity_emails": len(activity),
            "activity_matched": len(activity) - len(unmatched),
            "ample_contacts": len(ample_rows),
            "ample_matched": len(ample_rows) - ample_unmatched}, review


# ------------------------------------------------------------------- report
def coverage(conn):
    """How much raw activity the resolved identities can now attribute."""
    with conn.cursor() as cur:
        cur.execute("""select count(*),
                              count(*) filter (where m.ca_id is not null)
                       from raw_amplemarket_calls c
                       left join ca_amplemarket_user m on m.amplemarket_user_id = c.user_id""")
        calls, calls_ca = cur.fetchone()
        cur.execute("""select count(*),
                              count(*) filter (where m.ca_id is not null)
                       from raw_amplemarket_tasks t
                       left join ca_amplemarket_user m on m.amplemarket_user_id = t.user_id""")
        tasks, tasks_ca = cur.fetchone()
        cur.execute("select lower(from_email), count(*) from raw_hubspot_emails "
                    "where from_email is not null group by 1")
        sent_internal = sent_ca = 0
        with conn.cursor() as c2:
            c2.execute("select address from dim_ca_address")
            ca_addrs = {r[0] for r in c2.fetchall()}
        for raw_from, n in cur.fetchall():
            addrs = _extract_emails(raw_from)
            if any(a.rsplit("@", 1)[1] in INTERNAL_DOMAINS or
                   a.rsplit("@", 1)[1] == "tryencord.com" for a in addrs):
                sent_internal += n
                if any(a in ca_addrs for a in addrs):
                    sent_ca += n
    return {"calls": calls, "calls_ca": calls_ca,
            "tasks": tasks, "tasks_ca": tasks_ca,
            "sent_internal": sent_internal, "sent_ca": sent_ca}


def main():
    load_env()
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    cas = derive_cas(conn)
    addresses, ample_users, parked = link_addresses(conn, cas)
    rebuild(conn, "dim_ca", ["ca_id", "name", "primary_email", "ca_teams"],
            [(c["ca_id"], c["name"], c["email"], c["teams"]) for c in cas])
    rebuild(conn, "dim_ca_address", ["address", "ca_id", "source"],
            [(a, ca_id, src) for a, (ca_id, src) in sorted(addresses.items())])
    rebuild(conn, "ca_amplemarket_user", ["amplemarket_user_id", "ca_id"],
            sorted(ample_users.items()))

    co_stats, co_review = resolve_companies(conn)
    ct_stats, ct_review = resolve_contacts(conn)
    rebuild(conn, "identity_unresolved", ["kind", "key", "detail"],
            sorted(set(parked + co_review + ct_review)))

    cov = coverage(conn)

    accounts_per_ca = {}
    for _uid, ca_id in ample_users.items():
        accounts_per_ca[ca_id] = accounts_per_ca.get(ca_id, 0) + 1
    print(f"=== Phase 2 identity resolution ===")
    print(f"CAs: {len(cas)}  (no AmpleMarket account: "
          f"{sum(1 for c in cas if c['ca_id'] not in accounts_per_ca)})")
    for c in sorted(cas, key=lambda c: c["name"]):
        n_addr = sum(1 for _, (cid, _) in addresses.items() if cid == c["ca_id"])
        n_acct = accounts_per_ca.get(c["ca_id"], 0)
        flag = "" if n_acct else "  [no AmpleMarket user]"
        multi = f"  [{n_acct} AmpleMarket accounts]" if n_acct > 1 else ""
        print(f"   {c['name']:28} {c['email']:28} addresses={n_addr}{flag}{multi}")
    print(f"linked addresses: {len(addresses)}  |  parked/unlinked internal senders: {len(parked)}")
    print(f"companies: {co_stats['total']}  -> {co_stats['merged_groups']} domain groups had duplicates, "
          f"{co_stats['merged_dupes']} rows collapsed; same-domain-different-name conflicts "
          f"(NOT merged, review): {co_stats['conflicts']}; no-domain: {co_stats['no_domain']} "
          f"(target accounts among them -> review list: {co_stats['no_domain_target']})")
    print(f"contacts: {ct_stats['contact_emails']} emails mapped; activity emails "
          f"{ct_stats['activity_matched']}/{ct_stats['activity_emails']} matched; "
          f"AmpleMarket contacts {ct_stats['ample_matched']}/{ct_stats['ample_contacts']} matched")
    print(f"coverage: calls {cov['calls_ca']}/{cov['calls']} attributable to a CA; "
          f"tasks {cov['tasks_ca']}/{cov['tasks']}; "
          f"internally-sent emails {cov['sent_ca']}/{cov['sent_internal']} from a CA address")
    print(f"(non-CA remainder is expected: AEs/Growth/marketing send too — excluded by design)")
    print("=== done ===")
    conn.close()


if __name__ == "__main__":
    main()
