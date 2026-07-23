"""Stage 1: apply default normalization to the full corpus -> normalized.parquet"""
import pandas as pd, re
from normalize import normalize_email

df = pd.read_parquet("corpus_raw.parquet")
recs = []
for _, r in df.iterrows():
    ctx = dict(contact_firstname=r.contact_firstname, contact_lastname=r.contact_lastname,
               account_name=r.account_name, contact_jobtitle=r.contact_jobtitle,
               contact_email=r.contact_email, ca_name=r.ca_name)
    n = normalize_email(r.body_html, ctx)
    recs.append(n)
nd = pd.DataFrame(recs)
out = df[['activity_id','ca_name','channel','subject_norm','account_name',
          'contact_firstname','contact_email','occurred_at']].reset_index(drop=True)
out = pd.concat([out, nd], axis=1)
out['core_len'] = out.core_norm.str.len()
out['core_words'] = out.core_norm.str.split().str.len().fillna(0).astype(int)
out.to_parquet("normalized.parquet", index=False)

n = len(out)
print(f"rows: {n}")
print(f"has_quote:   {out.has_quote.mean()*100:5.1f}%  (reply(ext) {out.is_reply.mean()*100:.1f}% | selfbump(int) {out.is_selfbump.mean()*100:.1f}% | quote-sender-unknown {(out.has_quote & out.quote_internal.isna()).mean()*100:.1f}%)")
print(f"sig_removed: {out.sig_removed.mean()*100:5.1f}%")
print(f"disc_removed:{out.disc_removed.mean()*100:5.1f}%")
print(f"empty core (<=2 words after norm): {(out.core_words<=2).mean()*100:.1f}%  (n={(out.core_words<=2).sum()})")
print("\ncore_words quantiles:")
print(out.core_words.describe(percentiles=[.05,.1,.25,.5,.75,.9,.95]).round(1))
print("\nrows with empty core (candidates: image-only / all-quote):", (out.core_len==0).sum())
EOF_MARK = None
