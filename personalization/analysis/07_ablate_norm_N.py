"""Stage 5b: (i) family-size N sweep, (ii) normalization-step ablation, (iii) length-aware.
Fixed similarity: jac4. Re-normalizes corpus per toggle."""
import pandas as pd
from normalize import normalize_email, DEFAULT_OPTS
from rule import label_corpus, evaluate

raw = pd.read_parquet("corpus_raw.parquet")
gt = pd.read_csv("ground_truth.csv")
base = pd.read_parquet("clusters.parquet")[['activity_id','is_reply']]

def renorm(opts):
    recs=[]
    for _, r in raw.iterrows():
        ctx=dict(contact_firstname=r.contact_firstname, contact_lastname=r.contact_lastname,
                 account_name=r.account_name, contact_jobtitle=r.contact_jobtitle,
                 contact_email=r.contact_email, ca_name=r.ca_name)
        recs.append(normalize_email(r.body_html, ctx, opts))
    nd = pd.DataFrame(recs)
    nd['activity_id']=raw.activity_id.values
    nd['is_reply']=nd.has_quote & (nd.quote_internal==False)
    return nd

def run(nd, measure='jac4', T=0.45, N=3, la=False):
    fam,size,label = label_corpus(nd, measure=measure, threshold=T, N=N, length_aware=la)
    e = evaluate(nd, label, gt)
    head=(pd.Series(label)=='Templated').mean()*100
    return e, head

# (i) N sweep on default normalization
ndm = pd.read_parquet("clusters.parquet")
print("=== family-size N sweep (jac4, T=0.45) ===")
print(f"{'N':>2s} {'acc':>6s} {'fp':>3s} {'fn':>3s} {'hardBlast':>9s} {'headline%T':>10s}")
for N in [2,3,4,5]:
    e,head = run(ndm, N=N)
    print(f"{N:2d} {e['acc']*100:5.1f}% {e['fp']:3d} {e['fn']:3d} {e['hard_blast']:9d} {head:9.1f}%")

# (iii) length-aware toggle
print("\n=== length-aware threshold (jac4 T=0.45 N=3) ===")
for la in [False, True]:
    e,head = run(ndm, la=la)
    print(f"length_aware={la!s:5s}: acc={e['acc']*100:.1f}% fp={e['fp']} fn={e['fn']} headline={head:.1f}%")

# (ii) normalization ablation: turn each step OFF (from full default)
print("\n=== normalization-step ablation (jac4 T=0.45 N=3) ===")
print(f"{'config':28s} {'acc':>6s} {'fp':>3s} {'fn':>3s} {'headline%T':>10s}  {'vs base':>8s}")
e0,head0 = run(ndm)
print(f"{'FULL (all steps on)':28s} {e0['acc']*100:5.1f}% {e0['fp']:3d} {e0['fn']:3d} {head0:9.1f}%  {'--':>8s}")
for step in ['strip_quote','strip_greeting','strip_sig','strip_disclaimer','neutralize_merge']:
    opts=dict(DEFAULT_OPTS); opts[step]=False
    nd = renorm(opts)
    e,head = run(nd)
    print(f"{('  OFF: '+step):28s} {e['acc']*100:5.1f}% {e['fp']:3d} {e['fn']:3d} {head:9.1f}%  {head-head0:+7.1f}%")
