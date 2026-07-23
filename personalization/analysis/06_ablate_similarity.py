"""Stage 5a: sweep similarity measure, threshold T, family size N vs ground truth."""
import pandas as pd
from rule import label_corpus, evaluate

nd = pd.read_parquet("clusters.parquet")
gt = pd.read_csv("ground_truth.csv")

print(f"{'measure':8s} {'T':>4s} {'N':>2s} {'acc':>6s} {'fp(bespoke->T)':>15s} "
      f"{'fn(blast->P)':>13s} {'hardBlast':>10s} {'headline%T':>10s} {'fresh%T':>8s}")
best=[]
for measure in ['jac4','jac3','jac1','jac5','difflib']:
    for T in [0.3,0.4,0.45,0.5,0.55,0.6,0.7]:
        for N in [3]:
            fam,size,label = label_corpus(nd, measure=measure, threshold=T, N=N)
            e = evaluate(nd, label, gt)
            head = (pd.Series(label)=='Templated').mean()*100
            fresh_mask = ~nd.is_reply.values
            fresh = (pd.Series(label)[fresh_mask]=='Templated').mean()*100
            print(f"{measure:8s} {T:4.2f} {N:2d} {e['acc']*100:5.1f}% {e['fp']:15d} "
                  f"{e['fn']:13d} {e['hard_blast']:10d} {head:9.1f}% {fresh:7.1f}%")
            best.append((e,measure,T,N,head,fresh))
print("\n--- best by accuracy then fewest hard-fails ---")
for e,m,T,N,head,fresh in sorted(best, key=lambda x:(-x[0]['acc'], x[0]['fp']+x[0]['hard_blast']))[:6]:
    print(f"{m} T={T} N={N}: acc={e['acc']*100:.1f}% fp={e['fp']} fn={e['fn']} hardBlast={e['hard_blast']} head={head:.1f}%")
