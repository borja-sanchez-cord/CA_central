"""Controlled vocabulary — the ONE place the category labels are written down.

Every `channel` and `excluded_reason` the model can emit is listed here. The
build (`build_activity.py`) checks its output against these sets and aborts if
it ever produces a value not listed — so a typo ("non_ca_sendr") or a new,
undeclared category can't silently ship and quietly drop out of the scorecard
counts (which filter on these exact strings).

Add a value here FIRST, deliberately, when you introduce a new category. This
file is not logic — changing a string here does not change how anything is
classified; it only widens/narrows what the guard will accept.

Kept in sync with: model/rules.py (which produces the values), the header of
model/build_activity.py (plain-English meanings), and docs/ontology.md.
"""

# Outbound rep effort + inbound engagement, one per real activity row.
CHANNELS = frozenset({
    "auto_email",      # sequence/tool-sent email to an external recipient
    "manual_email",    # human-sent email (Gmail / typed in HubSpot)
    "inbound_email",   # a reply received from a prospect
    "call",            # a phone dial (AmpleMarket dialer)
    "meeting",         # a HubSpot meeting object with a CA attending
    "li_connect",      # LinkedIn connection request (AmpleMarket task)
    "li_message",      # LinkedIn direct / voice / video message
    "li_other",        # LinkedIn profile visit, follow, post like
    "whatsapp",        # AmpleMarket WhatsApp step (defined; none seen yet)
    "sms",             # AmpleMarket SMS step (defined; none seen yet)
    "other",           # AmpleMarket step of an unrecognised type (custom task)
    "email_task",      # AmpleMarket email to-do — non-counted shadow of a send
    "call_task",       # AmpleMarket phone to-do — non-counted shadow of a dial
})

# Why a row is kept but not counted (counts=false). Every non-counted row
# carries exactly one of these.
EXCLUDED_REASONS = frozenset({
    "email_task_shadow",        # the HubSpot-synced send is the counted record
    "call_task_shadow",         # the /calls record is the counted dial
    "non_ca_sender",            # outbound email from an internal, non-CA address
    "non_ca_user",              # AmpleMarket task/call by a non-CA user
    "non_ca_meeting",           # meeting with no CA among internal attendees
    "internal_only_recipients", # outbound with no external recipient
    "noise_auto_generated",     # inbound bounce / auto-reply / notification
    "inbound_no_ca_recipient",  # inbound where no CA is a recipient
    "invite_email",             # calendar invite — counted once as the meeting
    "no_sender",                # email row with missing/unparseable sender
})
