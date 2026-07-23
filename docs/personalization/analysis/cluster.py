"""Deterministic MinHash-LSH near-duplicate clustering over unique normalized texts.
Reusable: build_clusters(texts, k, threshold, perms, bands) -> labels list aligned to texts.
Stdlib only (hashlib for deterministic permutations)."""
import re, hashlib
from collections import defaultdict

def shingles(text, k=4):
    w = text.split()
    if len(w) < k:
        return frozenset([' '.join(w)]) if w else frozenset()
    return frozenset(' '.join(w[i:i+k]) for i in range(len(w)-k+1))

def _hh(i, s):
    return int(hashlib.blake2b(f"{i}\x00{s}".encode(), digest_size=8).hexdigest(), 16)

def minhash(shset, perms):
    if not shset:
        return tuple([0]*perms)
    return tuple(min(_hh(i, s) for s in shset) for i in range(perms))

def jaccard(a, b):
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)

class UF:
    def __init__(self, n): self.p=list(range(n))
    def find(self,x):
        while self.p[x]!=x: self.p[x]=self.p[self.p[x]]; x=self.p[x]
        return x
    def union(self,a,b):
        ra,rb=self.find(a),self.find(b)
        if ra!=rb: self.p[ra]=rb

def build_clusters(texts, k=4, threshold=0.5, perms=64, bands=16):
    """texts: list of unique normalized strings. Returns cluster label per index."""
    rows = perms // bands
    shs = [shingles(t, k) for t in texts]
    sigs = [minhash(s, perms) for s in shs]
    # LSH banding -> candidate buckets
    cand = defaultdict(list)
    for idx, sig in enumerate(sigs):
        for b in range(bands):
            band = sig[b*rows:(b+1)*rows]
            cand[(b, band)].append(idx)
    uf = UF(len(texts))
    checked = set()
    for bucket in cand.values():
        if len(bucket) < 2: continue
        for i in range(len(bucket)):
            for j in range(i+1, len(bucket)):
                a, b = bucket[i], bucket[j]
                key = (a, b)
                if key in checked: continue
                checked.add(key)
                if jaccard(shs[a], shs[b]) >= threshold:
                    uf.union(a, b)
    # relabel to compact ids
    roots = {}
    labels = []
    for i in range(len(texts)):
        r = uf.find(i)
        if r not in roots: roots[r] = len(roots)
        labels.append(roots[r])
    return labels
