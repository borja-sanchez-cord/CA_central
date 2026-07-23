"""Stage 4a: build a stratified adjudication sample with family context for reading.
Writes adjudication_sample.jsonl (one row per email to label) + prints nothing sensitive in bulk."""
import pandas as pd, json
from cluster import shingles, jaccard

nd = pd.read_parquet("clusters.parquet").reset_index(drop=True)

# nearest family rep (for singletons) so we can judge 'missed family member?'
fam = nd[nd.fam_sim>=3]
reps = fam.groupby('sim_clu').apply(lambda g: g.loc[g.core_len.idxmax()]).reset_index(drop=True)
rep_list = list(zip(reps.sim_clu, reps.core_norm, [shingles(t) for t in reps.core_norm]))

def nearest_rep(t, self_clu):
    s = shingles(t); best=(0.0,None,None)
    for clu, rtext, rsh in rep_list:
        if clu==self_clu: continue
        j = jaccard(s, rsh)
        if j>best[0]: best=(j, clu, rtext)
    return best

# deterministic per-stratum sampling
def take(df, n, seed):
    return df.sample(min(n,len(df)), random_state=seed)

strata = {
  'exact_pure_blast':     (25, 11),
  'exact_merge_template': (25, 12),
  'near_dup_reorder_cta': (40, 13),
  'dynamic_first_line':   (22, 14),
  'singleton_or_pair':    (75, 15),
}
picks = []
for vt,(n,seed) in strata.items():
    sub = nd[nd.vtype==vt]
    picks.append(take(sub, n, seed))
# ambiguous extras: short generic singletons + reply-heavy
picks.append(take(nd[(nd.core_words<15)&(nd.fam_sim<3)], 15, 16))
picks.append(take(nd[nd.is_reply & (nd.fam_sim<3)], 15, 17))
sample = pd.concat(picks).drop_duplicates('activity_id').reset_index(drop=True)

rows = []
for _, r in sample.iterrows():
    jj, nclu, ntext = nearest_rep(r.core_norm, r.sim_clu)
    rows.append(dict(
        activity_id=r.activity_id, ca=r.ca_name, vtype=r.vtype,
        fam_sim=int(r.fam_sim), fam_exact=int(r.fam_exact),
        is_reply=bool(r.is_reply), is_selfbump=bool(r.is_selfbump),
        subject=(r.subject_norm or '')[:80],
        core=r.core[:360],
        nearest_j=round(jj,3),
        nearest_rep=(ntext or '')[:240],
    ))
with open("adjudication_sample.jsonl","w") as f:
    for x in rows:
        f.write(json.dumps(x, ensure_ascii=False)+"\n")
print(f"sample size: {len(rows)}")
print("by vtype:", sample.vtype.value_counts().to_dict())
print("wrote adjudication_sample.jsonl")
