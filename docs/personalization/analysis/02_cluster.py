"""Stage 3a: similarity clustering over unique normalized texts, mapped back to rows.
Produces clusters.parquet with a similarity-cluster id per activity, at default threshold."""
import pandas as pd, hashlib
from cluster import build_clusters

nd = pd.read_parquet("normalized.parquet")
# unique texts (collapse exact dupes first for speed)
uniq = nd.core_norm.drop_duplicates().reset_index(drop=True)
texts = uniq.tolist()
print(f"rows={len(nd)}  unique normalized texts={len(texts)}")

K, THR = 4, 0.5
labels = build_clusters(texts, k=K, threshold=THR, perms=64, bands=16)
text2clu = dict(zip(texts, labels))
nd['sim_clu'] = nd.core_norm.map(text2clu)

g = nd.groupby('sim_clu').size()
in_fam = g[g>=2].sum(); fam3 = g[g>=3].sum()
print(f"sim-clusters (k={K}, thr={THR}): {g.size} clusters")
print(f"  in-family (>=2): {in_fam} ({in_fam/len(nd)*100:.1f}%)")
print(f"  >=3 recipients : {fam3} ({fam3/len(nd)*100:.1f}%)")
print(f"  largest cluster: {g.max()}")
print(f"  singletons     : {(g==1).sum()}")

# compare to exact
ex = nd.groupby('k_exact').transform('size')
sm = nd.groupby('sim_clu').transform('size')
gained = ((ex<3) & (sm>=3)).sum()
print(f"\n rows lifted from sing/<3-exact into a >=3 sim-family: {gained} ({gained/len(nd)*100:.1f}%)")
nd.to_parquet("clusters.parquet", index=False)
print("wrote clusters.parquet")
