"""Stage 6+7: temporal label-stability replay + per-CA results."""
import pandas as pd
nd = pd.read_parquet("labeled_final.parquet").copy()
nd['day']=pd.to_datetime(nd.occurred_at).dt.date
nd=nd.sort_values('occurred_at').reset_index(drop=True)
N=3

# --- per-email temporal labels (family membership stable; count-to-date grows) ---
nd['arr_rank']=nd.groupby('fam_win').cumcount()+1          # arrival order within family
famsize=nd.groupby('fam_win').activity_id.transform('size')
nd['final_templated']=famsize>=N
# date the N-th member of each family arrives (stability date for that family)
nth=nd[nd.arr_rank==N].set_index('fam_win').day
nd['stab_day']=nd.fam_win.map(nth)                          # NaT if family never reaches N
nd['born_templated']=nd.arr_rank>=N                         # at own arrival, cum>=N
nd['flips']=nd.final_templated & ~nd.born_templated         # Personalized -> Templated later

flip_rate=nd.flips.mean()*100
print("=== TEMPORAL LABEL STABILITY ===")
print(f"emails whose final label != day-1 label: {int(nd.flips.sum())} ({flip_rate:.1f}%)")
print(f"  (all flips are Personalized->Templated; families only grow)")
# days to stability for flippers
fl=nd[nd.flips].copy()
fl['dts']=(pd.to_datetime(fl.stab_day)-pd.to_datetime(fl.day)).dt.days
print(f"  median days-to-stability (flippers): {fl.dts.median():.0f} | mean {fl.dts.mean():.1f} | p90 {fl.dts.quantile(.9):.0f}")
print(f"  flippers stable same-day: {(fl.dts==0).mean()*100:.0f}%")

# daily convergence: if metric computed with data-to-date each day
days=sorted(nd.day.unique())
print("\n  daily 'Personalized %' if frozen each day (to-date rule):")
rows=[]
for d in days:
    sub=nd[nd.day<=d]
    fs=sub.groupby('fam_win').activity_id.transform('size')
    templ=(fs>=N)
    p=(~templ).mean()*100
    rows.append((str(d), len(sub), round(p,1)))
for r in rows: print(f"    {r[0]}  n={r[1]:5d}  personalized%={r[2]}")
final_p=(~nd.final_templated).mean()*100
print(f"  final personalized% (full corpus): {final_p:.1f}%")

# --- per-CA (Stage 7): fresh vs replies separated ---
print("\n=== PER-CA (winning rule) ===")
def tp(g): return (g=='Templated').mean()*100
per=nd.groupby('ca_name').agg(
    n=('activity_id','size'),
    templated_pct=('pred', tp),
    fresh_n=('is_reply', lambda s:(~s).sum()),
)
fresh=nd[~nd.is_reply].groupby('ca_name').pred.apply(tp).rename('fresh_templ%')
repl=nd[nd.is_reply].groupby('ca_name').pred.apply(tp).rename('reply_templ%')
per=per.join(fresh).join(repl).sort_values('n',ascending=False)
per['personalized%']=(100-per.templated_pct).round(1)
per['templated_pct']=per.templated_pct.round(1)
per['fresh_templ%']=per['fresh_templ%'].round(1)
pd.set_option('display.width',160)
print(per[['n','fresh_n','templated_pct','personalized%','fresh_templ%','reply_templ%']].to_string())
per.to_csv("per_ca.csv")
nd.to_parquet("labeled_final.parquet", index=False)
