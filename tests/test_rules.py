"""
Phase 3 rule tests — every dedup/attribution rule from docs/spec.md §3,
anchored on REAL validated cases (tests/fixtures/*.json are live rows pulled
from the raw tables on 2026-07-16, truncated bodies; each fixture's expected
answer was verified by hand — several with the rep themselves — before the
rules were written).

Run:  python -m pytest tests/ -q      (from the repo root)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))
import rules

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        rows = json.load(f)["rows"]
    for r in rows:
        if isinstance(r.get("hs_timestamp"), str):
            r["hs_timestamp"] = datetime.fromisoformat(r["hs_timestamp"])
        if isinstance(r.get("start_date"), str):
            r["start_date"] = datetime.fromisoformat(r["start_date"])
    return rows


def prep_and_cluster(rows):
    prepped = [rules.prep_email(r) for r in rows]
    return prepped, rules.cluster_emails(prepped)


# ---------------------------------------------------------- subject stripping
def test_subject_stripping():
    # Apollo tool prefix + reply prefix both go; case/whitespace normalize
    assert rules.norm_subject("[Apollo] [Email] [<<] Re: VLMs in identity") == "vlms in identity"
    # AmpleMarket's synced copy ADDS 'Re: ' to the same send (verified pair)
    assert rules.norm_subject("Re: Real-time video effects") == \
           rules.norm_subject("Real-time video effects")
    # a colon-less 'R.e.' typed by a rep is a reply marker too
    assert rules.norm_subject("R.e. Encord enquiry") == "encord enquiry"
    # ...but ordinary words that happen to start like markers must survive
    assert rules.norm_subject("VS Code integration") == "vs code integration"
    assert rules.norm_subject("Reaching out about Encord") == "reaching out about encord"


# ------------------------------------------------- invite classification (r5)
def test_invite_classification():
    assert rules.subject_is_invite("Invitation: Camtek X Encord Introduction @ Thu 23 Jul 2026")
    assert rules.subject_is_invite("[Apollo] [Email] [<<] Updated invitation: Encord x Bosch | Sync")
    assert rules.subject_is_invite("Accepted: Encord <> Mobileye | 3D Annotation Intro")
    # a HUMAN replying about a decline is an email, not calendar noise
    assert not rules.subject_is_invite("Re: Declined: Encord <> Mobileye | 3D Annotation Intro")
    assert not rules.subject_is_invite("Re: Invitation: Encord - Bytedance Sync")
    assert not rules.subject_is_invite("Encord Introduction - data annotation")


# --------------------------------------- Andrew Bell 2026-07-14 (end-to-end)
def test_andrew_full_day():
    """The roadmap's named fixture: Andrew's 17 raw sender rows must resolve
    to exactly 7 real sent emails + 6 invite/notification rows (validated
    live with Andrew + by body inspection 2026-07-16: the 13:06:25 meet-link
    email and the 13:10 intro email are DIFFERENT sends to the same person
    minutes apart — bodies differ, so they must NOT merge)."""
    prepped, clusters = prep_and_cluster(load("andrew_jul14_emails.json"))
    assert len(prepped) == 17
    invite_rows = [p for p in prepped if p["is_invite"]]
    assert len(invite_rows) == 6
    email_clusters = [c for c in clusters if not any(r["is_invite"] for r in c)]
    assert len(email_clusters) == 7          # the day's real sent emails
    # 3 solo sends + the four verified cross-tool pairs, each collapsed to one
    assert sorted(len(c) for c in email_clusters) == [1, 1, 1, 2, 2, 2, 2]
    # every copy in every cluster shares the sender
    for c in clusters:
        assert len({r["sender"] for r in c}) == 1


def test_andrew_meet_link_vs_intro_not_merged():
    """Same sender, same recipient, same stripped subject, 4 min apart —
    but different bodies = different sends. Body must win over subject."""
    rows = [r for r in load("andrew_jul14_emails.json")
            if (r["subject"] or "").endswith("Encord (Andrew) | Introduction")
            and "Invitation" not in (r["subject"] or "")]
    assert len(rows) == 3                     # 13:06:25 + 13:10:00 + 13:10:04
    _, clusters = prep_and_cluster(rows)
    assert sorted(len(c) for c in clusters) == [1, 2]


# ------------------------------------- cross-tool duplicate collapse (r3)
def test_falconer_cross_tool_pair_collapses():
    """One send logged by BOTH Amplemarket and Apollo in the same second."""
    _, clusters = prep_and_cluster(load("falconer_dup_pair.json"))
    assert len(clusters) == 1 and len(clusters[0]) == 2


def test_reply_prefix_mutation_pair_collapses():
    """The verified pair where the synced copy ADDED 'Re: ' to the subject."""
    rows = load("reply_prefix_pair.json")
    assert len(rows) == 2
    _, clusters = prep_and_cluster(rows)
    assert len(clusters) == 1 and len(clusters[0]) == 2


# ------------------------------ sequence blasts must NOT merge (r3, the 7.8%)
def test_sequence_blast_never_merges():
    """Will Sawyer's 'Internal resource allocation': one subject+body sent to
    49 DISTINCT recipients within minutes. Without the recipient-overlap
    component these false-merge (36% of his outbound). Each send must
    survive as its own activity."""
    rows = load("sequence_blast.json")
    prepped, clusters = prep_and_cluster(rows)
    assert len(clusters) == len(rows)         # zero merging
    # sanity: the trap is real — one identical subject across all 49 sends
    assert len({p["subject_norm"] for p in prepped}) == 1
    # harder variant: force every body byte-identical (templates measured live
    # ARE byte-identical across recipients — §3) — must STILL not merge,
    # because the recipient sets are disjoint
    same_body = [dict(r, body_preview="identical template body across every send")
                 for r in rows]
    _, clusters2 = prep_and_cluster(same_body)
    assert len(clusters2) == len(rows)


# ------------------------------------------- invite fan-out collapse (r6)
def test_invite_fanout_recipientless_collapse():
    """Per-attendee fan-out: the SAME invite logged once per attendee, seconds
    apart, DIFFERENT recipients. Invite-classified rows are the only place
    recipient-less subject+time collapse is allowed."""
    a, b = load("invite_fanout.json")
    for r in (a, b):
        assert rules.subject_is_invite(r["subject"])
    # the real Camtek rows are 35 min apart -> two calendar actions, no merge
    _, clusters = prep_and_cluster([a, b])
    assert len(clusters) == 2
    # the same two rows within the window -> one calendar action, merged,
    # even though the recipients differ (the fan-out case)
    b2 = dict(b, hs_timestamp=a["hs_timestamp"] + timedelta(seconds=8))
    _, clusters = prep_and_cluster([a, b2])
    assert len(clusters) == 1 and len(clusters[0]) == 2


def test_recipientless_collapse_forbidden_for_normal_email():
    """The same subject+time-window WITHOUT recipient overlap must NOT merge
    when the rows are not invites (that's exactly the sequence-blast trap).
    (The live Camtek rows share a recipient, so disjoint sets are synthetic.)"""
    a, b = load("invite_fanout.json")
    a = dict(a, subject="Quarterly infra update", to_email="alice@prospect-a.com")
    b = dict(b, subject="Quarterly infra update", to_email="bob@prospect-b.com",
             hs_timestamp=a["hs_timestamp"] + timedelta(seconds=8))
    _, clusters = prep_and_cluster([a, b])
    assert len(clusters) == 2


# ---------------------------------------- body normalization robustness (r3)
def test_gmail_banner_stripped_before_body_compare():
    """The live Lucid Bots pair (2026-07-14 16:10): Apollo's copy of a prospect
    reply starts with Gmail's first-contact banner; the Gmail copy doesn't.
    Same send — must merge."""
    base = {"id": "503503813838", "hs_timestamp": datetime(2026, 7, 14, 16, 10, 16, tzinfo=timezone.utc),
            "subject": "[Apollo] [Email] [<<] Re: Encord x Lucid Bots | Data labeling",
            "from_email": "scanton@lucidbots.com", "to_email": "Andrew Bell <andrew@encord.com>",
            "cc_email": "James Watson <james.watson@encord.com>", "bcc_email": None,
            "body_preview": "This is the first time you're receiving an email from this person. "
                            "Make sure you check the email address to confirm their identity "
                            "before interacting with the email. "   # exact live banner bytes
                            "We're not in a place to start talking labelling right now. Thanks. Sean",
            "activity_date": "2026-07-14"}
    copy = dict(base, id="503504491711", subject="Re: Encord x Lucid Bots | Data labeling",
                hs_timestamp=base["hs_timestamp"] + timedelta(seconds=12),
                to_email="andrew@encord.com", cc_email="james.watson@encord.com",
                body_preview="We're not in a place to start talking labelling right now. Thanks. Sean")
    _, clusters = prep_and_cluster([base, copy])
    assert len(clusters) == 1 and len(clusters[0]) == 2


def test_signature_suffix_drift_still_merges():
    """Live pattern (Satchel, 2026-07): one tool's preview carries the trailing
    signature block, the other's stops before it. Same send — must merge."""
    a = rules.norm_body_prefix("Appreciate your patience - I updated the permissions, try now.")
    b = rules.norm_body_prefix("Appreciate your patience - I updated the permissions, try now. "
                               "-- Kind regards Satchel Sevenau Commercial Associate")
    assert rules.bodies_match(a, b)
    # ...but a short pleasantry being a prefix of a longer real email is NOT
    # proof of sameness (two consecutive real replies must not merge)
    assert not rules.bodies_match(rules.norm_body_prefix("Thanks!"),
                                  rules.norm_body_prefix("Thanks! One more thing before Tuesday…"))


def test_different_bodies_same_subject_never_merge():
    """The Andrew 13:06/13:10 case in miniature: same sender, same recipient,
    same stripped subject, minutes apart — different bodies = different sends."""
    assert not rules.bodies_match(
        rules.norm_body_prefix("Encord (Andrew) | Introduction Hi Agu Join with Google Meet "
                               "Meeting link meet.google.com/aqo-gbpg-dmb Join by phone"),
        rules.norm_body_prefix("Encord (Andrew) | Introduction Hi Agustin - good to chat just now, "
                               "As mentioned, I met Yassine at ICRA"))


# ------------------------------- cluster-level decisions (review 2026-07-16)
CA_ADDR = {"andrew@encord.com": "CA_ANDREW"}


def test_inbound_attribution_uses_all_copies_recipients():
    """Copies of one send carry DIFFERENT recipient lists (Apollo drops
    internal/self recipients) — a CA visible only on the non-canonical copy
    must still attribute + count."""
    t0 = datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
    a = rules.prep_email({"id": "1", "hs_timestamp": t0, "subject": "Re: Encord intro",
                          "from_email": "jane@prospect.com",
                          "to_email": "sales@encord.com", "cc_email": None, "bcc_email": None,
                          "body_preview": "Happy to meet next week, send times please.",
                          "activity_date": "2026-07-14"})
    b = rules.prep_email({"id": "2", "hs_timestamp": t0 + timedelta(seconds=15),
                          "subject": "[Apollo] [Email] [>>] Re: Encord intro",
                          "from_email": "jane@prospect.com",
                          "to_email": "sales@encord.com;andrew@encord.com",  # CA only here
                          "cc_email": None, "bcc_email": None,
                          "body_preview": "Happy to meet next week, send times please.",
                          "activity_date": "2026-07-14"})
    clusters = rules.cluster_emails([a, b])
    assert len(clusters) == 1
    s = rules.summarize_email_cluster(clusters[0], CA_ADDR)
    assert s["ca_id"] == "CA_ANDREW" and s["counts"]


def test_reply_prefixed_invite_copy_folds_into_invite_cluster():
    """A synced copy can ADD 'Re: ' to an invite's subject, knocking it out of
    the invite classification — the strict key (recipients+body+time) must
    still fold it into the invite cluster, and the cluster stays classified
    as an invite (else the calendar action counts as a manual email AND the
    meeting object counts: the exact double-count rule 5 prevents)."""
    t0 = datetime(2026, 7, 14, 11, 0, 0, tzinfo=timezone.utc)
    body = "Encord x Bosch | Sync Join with Google Meet meet.google.com/xyz"
    a = rules.prep_email({"id": "1", "hs_timestamp": t0,
                          "subject": "Invitation: Encord x Bosch | Sync @ Tue 14 Jul",
                          "from_email": "andrew@encord.com",
                          "to_email": "tim@de.bosch.com", "cc_email": None, "bcc_email": None,
                          "body_preview": body, "activity_date": "2026-07-14"})
    b = rules.prep_email({"id": "2", "hs_timestamp": t0 + timedelta(seconds=8),
                          "subject": "Re: Invitation: Encord x Bosch | Sync @ Tue 14 Jul",
                          "from_email": "andrew@encord.com",
                          "to_email": "tim@de.bosch.com", "cc_email": None, "bcc_email": None,
                          "body_preview": body, "activity_date": "2026-07-14"})
    assert a["is_invite"] and not b["is_invite"]
    clusters = rules.cluster_emails([a, b])
    assert len(clusters) == 1
    s = rules.summarize_email_cluster(clusters[0], CA_ADDR)
    assert s["channel"] == "meeting" and not s["counts"]
    # ...while a genuine HUMAN reply about an invite (different body) stays
    # a separate, counted email
    c = rules.prep_email({"id": "3", "hs_timestamp": t0 + timedelta(seconds=30),
                          "subject": "Re: Invitation: Encord x Bosch | Sync @ Tue 14 Jul",
                          "from_email": "andrew@encord.com",
                          "to_email": "tim@de.bosch.com", "cc_email": None, "bcc_email": None,
                          "body_preview": "Looking forward to it - agenda attached ahead of time.",
                          "activity_date": "2026-07-14"})
    clusters = rules.cluster_emails([a, b, c])
    assert len(clusters) == 2
    human = next(cl for cl in clusters if any(r["id"] == "3" for r in cl))
    assert rules.summarize_email_cluster(human, CA_ADDR)["counts"]


def test_bridge_row_cannot_chain_distinct_sends():
    """A multi-recipient row overlapping two disjoint single-recipient sends
    must not transitively chain all three into one activity (the cluster-wide
    recipient intersection must stay non-empty)."""
    t0 = datetime(2026, 7, 14, 9, 0, 0, tzinfo=timezone.utc)
    body = "identical sequence template body used for every send"
    mk = lambda i, to, secs: rules.prep_email({
        "id": str(i), "hs_timestamp": t0 + timedelta(seconds=secs),
        "subject": "Scaling your vision pipeline", "from_email": "andrew@encord.com",
        "to_email": to, "cc_email": None, "bcc_email": None,
        "body_preview": body, "activity_date": "2026-07-14"})
    a = mk(1, "a@x.com", 0)
    bridge = mk(2, "a@x.com;b@y.com", 60)
    c = mk(3, "b@y.com", 120)
    clusters = rules.cluster_emails([a, bridge, c])
    # never one giant cluster; the disjoint pair (a, c) must stay apart
    assert len(clusters) >= 2
    for cl in clusters:
        ids = {r["id"] for r in cl}
        assert not {"1", "3"} <= ids


# --------------------------------------------- inbound noise filtering (r11)
def test_noise_detection():
    prepped = [rules.prep_email(r) for r in load("noise_samples.json")]
    flagged = [p for p in prepped if p["is_noise"]]
    assert len(flagged) == len(prepped)        # every sampled row is noise
    # an org-tagged auto-reply is still noise (review 2026-07-16)
    assert rules.subject_is_autoreply("EXT: Automatic reply: Re: Sports video data")
    # a real prospect reply is NOT noise
    real = rules.prep_email({"id": "x", "hs_timestamp": None, "subject": "Re: pricing question",
                             "from_email": "jane.doe@prospect.com", "to_email": "andrew@encord.com",
                             "cc_email": None, "bcc_email": None, "body_preview": "Thanks!",
                             "activity_date": "2026-07-14"})
    assert not real["is_noise"] and real["direction"] == "inbound"


def test_inbound_dedup_uses_all_recipients():
    """A prospect reply logged twice (Apollo + Gmail copies): its recipients
    are OUR reps (internal), so overlap must be computed on ALL recipients."""
    base = {"id": "1", "hs_timestamp": datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc),
            "subject": "Re: Encord intro", "from_email": "jane.doe@prospect.com",
            "to_email": "andrew@encord.com", "cc_email": None, "bcc_email": None,
            "body_preview": "Sounds great, let's talk Tuesday.", "activity_date": "2026-07-14"}
    copy = dict(base, id="2", subject="[Apollo] [Email] [>>] Re: Encord intro",
                hs_timestamp=base["hs_timestamp"] + timedelta(seconds=20),
                to_email="Andrew Bell <andrew@encord.com>")
    _, clusters = prep_and_cluster([base, copy])
    assert len(clusters) == 1 and len(clusters[0]) == 2


# ------------------------------------- internal-only exclusion basis (r9)
def test_internal_only_computed_over_to_cc_bcc():
    """Live CA emails whose 'to' is internal-only carry the prospect on cc —
    they must still show external recipients (and so still count)."""
    for r in load("internal_only_to.json"):
        p = rules.prep_email(r)
        assert p["external_recipients"], f"row {r['id']} lost its cc'd prospect"


def test_truly_internal_only_has_no_externals():
    p = rules.prep_email({"id": "x", "hs_timestamp": None, "subject": "internal sync",
                          "from_email": "andrew@encord.com", "to_email": "kat@encord.com",
                          "cc_email": "ops@encord.ai", "bcc_email": None,
                          "body_preview": "internal", "activity_date": "2026-07-14"})
    assert p["external_recipients"] == []


# ----------------------------------- sender attribution, never owner_id (r1)
def test_direction_ignores_owner_id():
    """George Lim's owner_id 538916758 is shared across 5 reps — the rules
    never read owner_id; direction and identity come from the sender."""
    row = {"id": "x", "hs_timestamp": None, "subject": "s",
           "owner_id": "538916758",               # present — and ignored
           "from_email": "jane.doe@prospect.com", "to_email": "george.lim@encord.com",
           "cc_email": None, "bcc_email": None, "body_preview": None,
           "activity_date": "2026-07-14"}
    assert rules.prep_email(row)["direction"] == "inbound"
    row["from_email"] = "george.lim@encord.com"
    assert rules.prep_email(row)["direction"] == "outbound"


# --------------------------------------------------- automation lean (r4)
def test_cluster_automation_lean():
    mk = lambda src, det: {"object_source": src, "object_source_detail": det}
    # tool-synced only -> leans automated
    assert rules.cluster_is_automated([mk("INTEGRATION", "Amplemarket")])
    assert rules.cluster_is_automated([mk("INTEGRATION", "Apollo Integration"),
                                       mk("INTEGRATION", "Amplemarket")])
    # any human-mailbox copy proves a person sent it
    assert not rules.cluster_is_automated([mk("INTEGRATION", "Apollo Integration"),
                                           mk("EMAIL", None)])
    assert not rules.cluster_is_automated([mk("CRM_UI", None)])
    # unknown source: treated as manual, never dropped
    assert not rules.cluster_is_automated([mk("INTEGRATION", "SomeNewTool")])


# -------------------------------------------------------------- calls (r8)
def test_call_grouping_attempts_vs_conversations():
    """Real fixture: 3 dials at one contact within 2 minutes + 1 more three
    hours later = 2 pursuit groups, 4 attempts, 0 conversations (all dials
    answered=True but human=False — voicemail/IVR, not a conversation)."""
    calls = load("call_group.json")
    groups = rules.group_calls(calls)
    assert len(calls) == 4
    assert len(set(groups.values())) == 2
    assert sum(1 for c in calls if c["human"]) == 0   # answered != conversation
    # task_id is not even an input to grouping — it's usually null (spec §9)


def test_null_contact_calls_stay_separate():
    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    calls = [{"id": "1", "user_id": "u", "contact_id": None, "start_date": t0, "human": True},
             {"id": "2", "user_id": "u", "contact_id": None,
              "start_date": t0 + timedelta(seconds=30), "human": True}]
    groups = rules.group_calls(calls)
    assert groups["1"] != groups["2"]          # unattributable ≠ mergeable


# ----------------------------------------------------------- meetings (r10)
def test_duplicate_meetings_collapse():
    """The 3 live duplicate meeting objects (same title+start+owner under two
    HubSpot ids) each collapse to one."""
    pairs = load("meeting_dups.json")
    assert len(pairs) == 3
    for p in pairs:
        a = {"id": p["id_a"], "title": p["title"], "start_time": p["start_time"],
             "owner_id": p["owner_a"], "outcome": p["outcome_a"]}
        b = {"id": p["id_b"], "title": p["title"], "start_time": p["start_time"],
             "owner_id": p["owner_b"], "outcome": p["outcome_b"]}
        groups = rules.dedupe_meetings([a, b])
        assert len(groups) == 1 and len(groups[0]) == 2


def test_meetings_missing_key_fields_never_merge():
    rows = [{"id": "1", "title": None, "start_time": None, "owner_id": "o"},
            {"id": "2", "title": None, "start_time": None, "owner_id": "o"}]
    assert len(rules.dedupe_meetings(rows)) == 2


def test_meeting_ca_attribution_via_attendees():
    """Meeting→rep attribution reads attendee_owner_ids (plus owner), never
    owner_id alone."""
    row = {"id": "m", "owner_id": "999", "attendee_owner_ids": "111;222; 333"}
    assert rules.meeting_ca_ids(row, {"222", "333", "444"}) == ["222", "333"]
    assert rules.meeting_ca_ids(row, {"999"}) == ["999"]   # owner counts too


# ------------------------------------------------- task channels (r7)
def test_task_channels_and_shadows():
    assert rules.task_channel("email") == "email_task"          # send counted from HubSpot copy
    assert rules.task_channel("phone_call") == "call_task"      # dials counted from /calls
    assert rules.task_channel("linkedin_message") == "li_message"
    assert rules.task_channel("linkedin_voice_message") == "li_message"
    assert rules.task_channel("linkedin_connect") == "li_connect"
    assert rules.task_channel("linkedin_visit") == "li_other"
    assert rules.task_channel("linkedin_like_last_post") == "li_other"
    assert rules.task_channel("whatsapp") == "whatsapp"
    assert rules.task_channel("some_future_type") == "other"    # never dropped
