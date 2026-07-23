"""Stage 3b: variation taxonomy — how copies of a template actually differ, with prevalence.
Also quantifies the text-alone ceiling (ambiguous zone). Writes reports/personalization/variation_taxonomy.md"""
import pandas as pd, re, hashlib
from cluster import shingles, jaccard

nd = pd.read_parquet("clusters.parquet")
N = len(nd)
def h(s): return hashlib.md5(s.encode()).hexdigest()[:16]

# --- per-row descriptors ---
nd['has_merge_tok'] = nd.core_norm.str.contains(r'<(fn|ln|acct|title|url|email)>', regex=True)
nd['fam_exact'] = nd.groupby('k_exact').transform('size')      # exact-family size
nd['fam_sim']   = nd.groupby('sim_clu').transform('size')      # sim-cluster size

# body minus first sentence key (dynamic-first-line detector)
def nofirst(t):
    p = re.split(r'(?<=[.!?])\s+', t, maxsplit=1)
    return p[1] if len(p)>1 and len(p[1])>20 else t
nd['k_nofirst'] = nd.core_norm.map(lambda t: h(nofirst(t)))
nd['fam_nofirst'] = nd.groupby('k_nofirst').transform('size')

# representative (longest core) per sim-cluster
rep_idx = nd.groupby('sim_clu').core_len.idxmax()
rep_text = nd.loc[rep_idx].set_index('sim_clu').core_norm.to_dict()
nd['rep_text'] = nd.sim_clu.map(rep_text)

# --- variation type per row (mutually exclusive, ordered) ---
def vtype(r):
    if r.fam_sim < 3:                                   # not in a >=3 family
        return 'singleton_or_pair'
    if r.fam_exact >= 3:                                # identical text shared by >=3
        return 'exact_merge_template' if r.has_merge_tok else 'exact_pure_blast'
    if r.fam_nofirst >= 3:                              # body shared, first line swapped
        return 'dynamic_first_line'
    return 'near_dup_reorder_cta'                       # in sim-fam but text drifts more
nd['vtype'] = nd.apply(vtype, axis=1)

# within-family drift: jaccard of each family member's shingles vs rep
def drift(r):
    if r.fam_sim < 2 or not isinstance(r.rep_text,str): return None
    return round(jaccard(shingles(r.core_norm), shingles(r.rep_text)), 3)
nd['drift_vs_rep'] = nd.apply(drift, axis=1)

tax = nd.vtype.value_counts()
print("=== VARIATION TAXONOMY (row prevalence) ===")
for k,v in tax.items():
    print(f"  {k:24s} {v:5d}  {v/N*100:5.1f}%")

# distinct exact texts per sim-cluster => within-template variation intensity
clu = nd[nd.fam_sim>=3].groupby('sim_clu')
per = clu.agg(size=('activity_id','size'), n_exact=('k_exact','nunique'),
              min_drift=('drift_vs_rep','min'))
per['variants_ratio'] = per.n_exact/per['size']
print("\n=== within-template variation intensity (>=3 families) ===")
print(f"  families: {len(per)}")
print(f"  families that are 1 exact text (pure repeat): {(per.n_exact==1).sum()} ({(per.n_exact==1).mean()*100:.0f}%)")
print(f"  families with >=5 distinct exact texts (high variation): {(per.n_exact>=5).sum()}")
print(f"  median distinct-exact-texts per family: {per.n_exact.median():.0f}")
print(f"  median min drift(worst member vs rep): {per.min_drift.median():.2f}")

# --- CEILING: text-alone ambiguous zone ---
print("\n=== TEXT-ALONE CEILING (ambiguous zone) ===")
# a) singletons that are near-misses to a family (0.35<=jaccard<thr to nearest family rep)
sing = nd[nd.fam_sim<3].copy()
reps = list({t for t in nd[nd.fam_sim>=3].rep_text.dropna().unique()})
rep_sh = [shingles(t) for t in reps]
def nearest(t):
    s = shingles(t); best=0.0
    for rs in rep_sh:
        j = jaccard(s, rs)
        if j>best: best=j
        if best>=0.5: break
    return best
sing['near'] = sing.core_norm.map(nearest)
nearmiss = ((sing.near>=0.35)&(sing.near<0.5)).sum()
print(f"  a) singleton near-misses to a family (0.35<=J<0.5): {nearmiss} ({nearmiss/N*100:.1f}%)  <- maybe dynamic-line members")
# b) very short generic cores (<15 words) not in a clear family
shortgen = ((nd.core_words<15)&(nd.fam_sim<3)).sum()
print(f"  b) short generic singletons (<15 words): {shortgen} ({shortgen/N*100:.1f}%)  <- 'thanks, will check' logistics")
# c) dynamic-first-line families: the first line itself is inherently ambiguous text
dfl = (nd.vtype=='dynamic_first_line').sum()
print(f"  c) dynamic-first-line family rows: {dfl} ({dfl/N*100:.1f}%)  <- first sentence unclassifiable by text alone")
amb = nearmiss + shortgen
print(f"  => rows in the genuinely ambiguous zone (a+b): ~{amb} ({amb/N*100:.1f}%)")

nd.to_parquet("clusters.parquet", index=False)

# write report fragment
with open("variation_taxonomy.md","w") as f:
    f.write("# Variation taxonomy (Stage 3b)\n\n")
    f.write(f"Corpus: {N} counted outbound emails.\n\n## Row prevalence by variation type\n\n")
    f.write("| variation type | rows | % |\n|---|---:|---:|\n")
    for k,v in tax.items():
        f.write(f"| {k} | {v} | {v/N*100:.1f}% |\n")
    f.write(f"\n## Within-template variation intensity (>=3 families: {len(per)})\n\n")
    f.write(f"- Pure repeat (1 exact text): {(per.n_exact==1).sum()} ({(per.n_exact==1).mean()*100:.0f}%)\n")
    f.write(f"- High variation (>=5 exact variants): {(per.n_exact>=5).sum()}\n")
    f.write(f"- Median distinct exact texts / family: {per.n_exact.median():.0f}\n")
    f.write(f"\n## Text-alone ceiling\n\n")
    f.write(f"- Singleton near-misses to a family (0.35<=J<0.5): {nearmiss} ({nearmiss/N*100:.1f}%)\n")
    f.write(f"- Short generic singletons (<15 words): {shortgen} ({shortgen/N*100:.1f}%)\n")
    f.write(f"- Dynamic-first-line family rows: {dfl} ({dfl/N*100:.1f}%)\n")
    f.write(f"- Ambiguous zone (near-miss + short-generic): ~{amb} ({amb/N*100:.1f}%)\n")
print("\nwrote variation_taxonomy.md")
