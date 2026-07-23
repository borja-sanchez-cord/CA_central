"""Deterministic family-rule + evaluation harness. Reusable across ablation configs."""
import pandas as pd, difflib
from collections import defaultdict
import hashlib
from cluster import shingles, jaccard, UF

def _hh(i, s):
    return int(hashlib.blake2b(f"{i}\x00{s}".encode(), digest_size=8).hexdigest(), 16)

def build_families(texts, measure='jac4', threshold=0.5, perms=64, bands=16, length_aware=False):
    """Return family label per text index. measure in {jac1,jac3,jac4,jac5,difflib}.
    LSH blocking uses 4-shingles regardless; scoring uses the chosen measure."""
    k_block = 4
    blk = [shingles(t, k_block) for t in texts]
    # scoring shingles
    kmap = {'jac1':1,'jac3':3,'jac4':4,'jac5':5}
    if measure in kmap:
        sc = [shingles(t, kmap[measure]) for t in texts]
    else:
        sc = None
    rows = perms // bands
    sigs = []
    for s in blk:
        sigs.append(tuple(min(_hh(i, sh) for sh in s) if s else 0 for i in range(perms)))
    cand = defaultdict(list)
    for idx, sig in enumerate(sigs):
        for b in range(bands):
            cand[(b, sig[b*rows:(b+1)*rows])].append(idx)
    uf = UF(len(texts)); checked=set()
    for bucket in cand.values():
        if len(bucket)<2: continue
        for i in range(len(bucket)):
            for j in range(i+1,len(bucket)):
                a,b = bucket[i],bucket[j]
                if (a,b) in checked: continue
                checked.add((a,b))
                thr = threshold
                if length_aware:
                    # shorter of the two texts relaxes threshold slightly (few-sentence emails)
                    wa, wb = len(texts[a].split()), len(texts[b].split())
                    if min(wa,wb) < 25: thr = max(0.3, threshold-0.1)
                if measure=='difflib':
                    ok = difflib.SequenceMatcher(None, texts[a], texts[b]).ratio() >= thr
                else:
                    ok = jaccard(sc[a], sc[b]) >= thr
                if ok: uf.union(a,b)
    roots={}; lab=[]
    for i in range(len(texts)):
        r=uf.find(i)
        if r not in roots: roots[r]=len(roots)
        lab.append(roots[r])
    return lab

def label_corpus(nd, measure='jac4', threshold=0.5, N=3, length_aware=False):
    """Attach family + Templated/Personalized label to nd (needs core_norm)."""
    uniq = nd.core_norm.drop_duplicates().tolist()
    fam = build_families(uniq, measure=measure, threshold=threshold, length_aware=length_aware)
    t2f = dict(zip(uniq, fam))
    famid = nd.core_norm.map(t2f)
    size = famid.map(famid.value_counts())
    label = pd.Series(['Templated' if s>=N else 'Personalized' for s in size], index=nd.index)
    return famid.values, size.values, label.values

def evaluate(nd, label, gt):
    """gt: DataFrame with activity_id,label. nd indexed by activity_id-> label array aligned."""
    lab = pd.Series(label, index=nd.activity_id.values)
    g = gt.set_index('activity_id')
    pred = lab.reindex(g.index)
    truth = g.label
    tp = int(((pred=='Templated')&(truth=='Templated')).sum())
    tn = int(((pred=='Personalized')&(truth=='Personalized')).sum())
    fp = int(((pred=='Templated')&(truth=='Personalized')).sum())  # bespoke->Templated HARD FAIL
    fn = int(((pred=='Personalized')&(truth=='Templated')).sum())  # blast->Personalized
    n = len(g)
    acc = (tp+tn)/n
    # hard fails: a *pure blast* (big family in GT) called Personalized, or *bespoke* called Templated.
    # bespoke->Templated = fp ; blast->Personalized = fn where GT fam_sim large
    hard_bespoke = fp
    blastmiss = g[(truth=='Templated')&(pred=='Personalized')]
    hard_blast = int((blastmiss.fam_sim>=10).sum())  # a genuinely big blast missed = hard fail
    return dict(acc=acc, tp=tp, tn=tn, fp=fp, fn=fn,
                hard_bespoke=hard_bespoke, hard_blast=hard_blast, n=n)
