"""Normalization pipeline for personalization analysis.

normalize_email(body_html, ctx, opts) -> dict with:
  core        : cleaned display text (original case), quoted-chain/sig/greeting removed,
                merge fields neutralized (per `opts`)
  core_norm   : lowercased, whitespace-collapsed matching key
  flags       : has_quote, quote_internal, is_reply, is_selfbump, sig_removed, disc_removed

Every normalization step is a boolean in `opts` so ablation can toggle it.
Reply/quote DETECTION always runs (it is a reporting dimension, not a knob)."""
import re, html

DEFAULT_OPTS = dict(
    strip_quote=True, strip_greeting=True, strip_sig=True,
    strip_disclaimer=True, neutralize_merge=True,
)

# --- markers discovered from the corpus ---
# Strip ONLY the salutation prefix (greeting word + optional name + separator),
# never the rest of the line — bumps put their whole message after "Hi X -".
GREETING = re.compile(r"(?i)^\s*(hi|hey|hello|hiya|dear|good morning|good afternoon|good evening)\b"
                      r"[ \t]*([A-Z][\w'’.\-]*(?:[ \t]+[A-Z][\w'’.\-]*){0,2})?[ \t]*[,\-–—:!.]*[ \t]*")
SIGNOFF = re.compile(r'(?i)^\s*(best regards|kind regards|warm regards|best wishes|all the best|'
                     r'many thanks|thank you|thanks|cheers|best|regards|sincerely|speak soon|'
                     r'talk soon|warmly|looking forward)\s*[,.!]?\s*$')
TITLE_LINE = re.compile(r'(?i)^\s*(commercial associate|technical pm|enterprise ai|ai\s*/\s*ml|'
                        r'account executive|sales development|growth|founder|partnerships)\s*$')
DISCLAIMER = re.compile(r'(?i)^\s*(reg\.?\s*address|registered address|this email|this e-mail|'
                        r'confidential|the information contained)')
SIG_CONTACT = re.compile(r'(?i)(@encord\.com|www\.encord\.com|linkedin|\| encord\b|website\s*:)')
QUOTE_HDR = re.compile(r'(?ims)^\s*On\s.{4,90}\bwrote:\s*$')
QUOTE_EMAIL = re.compile(r'(?is)On\s.{4,120}?<\s*([^>\s]+@[^>\s]+)\s*>\s*wrote:')
URL = re.compile(r'https?://\S+|www\.\S+')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')


def html_to_text(h):
    h = re.sub(r'(?is)<(script|style|head|title)[^>]*>.*?</\1>', ' ', h)
    h = re.sub(r'(?i)<br\s*/?>', '\n', h)
    h = re.sub(r'(?i)</(div|p|tr|li|blockquote|h[1-6]|table)>', '\n', h)
    h = re.sub(r'<[^>]+>', ' ', h)
    h = html.unescape(h)
    h = h.replace('‌', '').replace('​', '').replace('\xa0', ' ')
    h = re.sub(r'[ \t]+', ' ', h)
    h = re.sub(r'\n\s*\n+', '\n', h)
    return h.strip()


def detect_quote(body_html):
    """Return (has_quote, quote_internal_or_None). Runs on raw html."""
    has = bool(re.search(r'(?i)<blockquote', body_html) or
               re.search(r'(?i)gmail_quote', body_html) or
               QUOTE_HDR.search(html_to_text(body_html)))
    internal = None
    m = QUOTE_EMAIL.search(html_to_text(body_html))
    if m:
        internal = m.group(1).lower().endswith('@encord.com')
    return has, internal


def _cut_quote(text):
    text = re.split(r'(?ims)^\s*On\s.{4,90}\bwrote:\s*$', text)[0]
    text = re.split(r'(?im)^\s*-{2,}\s*Original Message\s*-{2,}', text)[0]
    text = re.split(r'(?im)^\s*_{5,}\s*$', text)[0]
    return text


def _esc(s):
    return re.escape(s.strip())


def normalize_email(body_html, ctx, opts=None):
    o = dict(DEFAULT_OPTS)
    if opts:
        o.update(opts)
    has_quote, quote_internal = detect_quote(body_html)

    # 1. strip quoted chain from raw html first (blockquote is the cleanest cut)
    h = body_html
    if o['strip_quote']:
        h = re.split(r'(?i)<blockquote', h)[0]
        h = re.split(r'(?i)<div[^>]*gmail_quote', h)[0]
    text = html_to_text(h)
    if o['strip_quote']:
        text = _cut_quote(text)

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # 2. greeting: remove only the salutation prefix from the first content line
    if o['strip_greeting'] and lines and GREETING.match(lines[0]):
        stripped = GREETING.sub('', lines[0]).strip()
        if stripped:
            lines[0] = stripped
        else:
            lines = lines[1:]

    sig_removed = False
    disc_removed = False

    # 3. signature: cut from earliest sign-off marker in the tail, else drop sig-contact/title lines
    if o['strip_sig'] and lines:
        cut_at = None
        for i in range(len(lines)):
            if SIGNOFF.match(lines[i]):
                cut_at = i
                break
        if cut_at is not None:
            lines = lines[:cut_at]
            sig_removed = True
        # drop residual contact/title/name lines anywhere
        ca = ctx.get('ca_name') or ''
        ca_first = ca.split()[0] if ca else ''
        kept = []
        for l in lines:
            if SIG_CONTACT.search(l) or TITLE_LINE.match(l):
                sig_removed = True
                continue
            if ca and (l.strip() == ca or l.strip() == ca_first) and len(l) < 40:
                sig_removed = True
                continue
            kept.append(l)
        lines = kept

    # 4. disclaimer / legal / reg address
    if o['strip_disclaimer']:
        kept = []
        for l in lines:
            if DISCLAIMER.match(l):
                disc_removed = True
                continue
            kept.append(l)
        lines = kept

    core = '\n'.join(lines).strip()

    # 5. neutralize merge fields using the row's actual values
    if o['neutralize_merge']:
        core = URL.sub(' <URL> ', core)
        core = EMAIL.sub(' <EMAIL> ', core)
        repl = []
        fn = (ctx.get('contact_firstname') or '').strip()
        ln = (ctx.get('contact_lastname') or '').strip()
        acct = (ctx.get('account_name') or '').strip()
        title = (ctx.get('contact_jobtitle') or '').strip()
        if len(acct) >= 4:
            repl.append((re.compile(r'(?i)\b' + _esc(acct) + r'\b'), ' <ACCT> '))
        if len(title) >= 5:
            repl.append((re.compile(r'(?i)\b' + _esc(title) + r'\b'), ' <TITLE> '))
        # names: case-sensitive on the stored (capitalized) form to limit false hits
        if len(fn) >= 2:
            repl.append((re.compile(r'\b' + _esc(fn) + r'\b'), ' <FN> '))
        if len(ln) >= 2:
            repl.append((re.compile(r'\b' + _esc(ln) + r'\b'), ' <LN> '))
        for pat, tok in repl:
            core = pat.sub(tok, core)

    core_norm = re.sub(r'\s+', ' ', core.lower()).strip()
    core_norm = re.sub(r'\s+([,.!?;:])', r'\1', core_norm)

    return dict(
        core=core, core_norm=core_norm,
        has_quote=has_quote, quote_internal=quote_internal,
        is_reply=bool(has_quote and quote_internal is False),
        is_selfbump=bool(has_quote and quote_internal is True),
        sig_removed=sig_removed, disc_removed=disc_removed,
    )


if __name__ == '__main__':
    # smoke test
    sample = ('<div>Hi Jane,</div><div>Loved your work at Acme on vision models.</div>'
              '<div>Best,</div><div>Will Sawyer</div><div>Commercial Associate</div>'
              '<div>Email: will@encord.com | LinkedIn</div>'
              '<blockquote>On July 1, 2026 at 9AM, Will Sawyer &lt;will@encord.com&gt; wrote: earlier</blockquote>')
    ctx = dict(contact_firstname='Jane', contact_lastname='Doe', account_name='Acme',
               contact_jobtitle='VP Eng', ca_name='Will Sawyer')
    import json
    print(json.dumps(normalize_email(sample, ctx), indent=2))
