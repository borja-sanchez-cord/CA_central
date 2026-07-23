"""Stage 5c: draw a FRESH held-out sample (not in ground truth) for a clean gut-check."""
import pandas as pd, json
from cluster import shingles, jaccard

nd = pd.read_parquet("labeled_final.parquet")
gt = pd.read_csv("ground_truth.csv")
seen = set(gt.activity_id)
pool = nd[~nd.activity_id.isin(seen)].copy()

# stratify by predicted label x family-size band x reply, deterministic
pool['band'] = pd.cut(pool.fam_win_size, [0,1,2,9,10**9], labels=['1','2','3-9','10+'])
picks=[]
for (pred,band), n in {('Templated','3-9'):18,('Templated','10+'):18,('Personalized','1'):30,
                       ('Personalized','2'):8,('Templated','2'):4}.items():
    sub=pool[(pool.pred==pred)&(pool.band==band)]
    picks.append(sub.sample(min(n,len(sub)), random_state=42))
hold=pd.concat(picks).drop_duplicates('activity_id')

# nearest family rep for context
fam=nd[nd.fam_win_size>=3]
reps=fam.groupby('fam_win').apply(lambda g:g.loc[g.core_len.idxmax()]).reset_index(drop=True)
rl=list(zip(reps.core_norm,[shingles(t) for t in reps.core_norm]))
def near(t,others=rl):
    s=shingles(t);best=0.0
    for rt,rs in others:
        j=jaccard(s,rs)
        if j>best:best=j
        if best>=0.6:break
    return round(best,3)

rows=[]
for _,r in hold.iterrows():
    rows.append(dict(activity_id=r.activity_id, ca=r.ca_name, pred=r.pred,
        fam_size=int(r.fam_win_size), is_reply=bool(r.is_reply),
        subject=(r.subject_norm or '')[:70], core=r.core[:340], nearest_j=near(r.core_norm)))
with open("holdout_sample.jsonl","w") as f:
    for x in rows: f.write(json.dumps(x,ensure_ascii=False)+"\n")
print("held-out n:", len(rows), "| pred dist:", hold.pred.value_counts().to_dict())
EOF_=None
