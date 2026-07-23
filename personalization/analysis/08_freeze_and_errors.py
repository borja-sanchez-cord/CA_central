"""Stage 5c: freeze winning rule on full corpus, dump GT disagreements for error analysis."""
import pandas as pd
from rule import label_corpus

nd = pd.read_parquet("clusters.parquet")
gt = pd.read_csv("ground_truth.csv")

MEASURE, T, N = 'jac4', 0.45, 3
fam,size,label = label_corpus(nd, measure=MEASURE, threshold=T, N=N)
nd['fam_win']=fam; nd['fam_win_size']=size; nd['pred']=label
nd.to_parquet("labeled_final.parquet", index=False)

head=(nd.pred=='Templated').mean()*100
fresh=(nd.loc[~nd.is_reply,'pred']=='Templated').mean()*100
repl=(nd.loc[nd.is_reply,'pred']=='Templated').mean()*100
print(f"WINNING RULE: {MEASURE} T={T} N={N}")
print(f"headline Templated%: {head:.1f}%  (fresh {fresh:.1f}% | replies {repl:.1f}%)")
print(f"families: {nd.fam_win.nunique()} | singletons(pred Personalized share): {(nd.pred=='Personalized').mean()*100:.1f}%")

# disagreements vs GT
g = gt.set_index('activity_id')
predm = pd.Series(label, index=nd.activity_id.values).reindex(g.index)
dis = g.copy(); dis['pred']=predm.values
dis = dis[dis.pred!=dis.label]
dis.to_csv("rule_gt_disagreements.csv")
print(f"\n=== {len(dis)} disagreements vs ground truth ===")
print(dis['label'].value_counts().rename('GT label of misses').to_dict())
raw = pd.read_parquet("corpus_raw.parquet").set_index("activity_id")
core = nd.set_index("activity_id")
for aid, r in dis.iterrows():
    print(f"\n[GT={r.label} PRED={r.pred}] {r.ca} | fam_size(win)={int(core.loc[aid,'fam_win_size'])} nearest_j={r.nearest_j}")
    print("  reason(GT):", r.reason[:110])
    print("  core:", core.loc[aid,'core'][:200].replace(chr(10),' / '))
