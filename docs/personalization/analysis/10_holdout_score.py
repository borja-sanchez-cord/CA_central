"""Stage 5c: score frozen rule vs my held-out adjudication (clean gut-check number)."""
import pandas as pd, json
rows=[json.loads(l) for l in open("holdout_sample.jsonl")]
h=pd.DataFrame(rows)
# my adjudication = rule pred, except the 2 dinner-invite variants clustered below N=3:
MY_TEMPLATED_OVERRIDE={"hs_email:503778769087","hs_email:504547489001"}  # #67,#68 genuine dinner templates
h['my_label']=h.apply(lambda r:'Templated' if r.activity_id in MY_TEMPLATED_OVERRIDE else r.pred, axis=1)
agree=(h.pred==h.my_label).sum()
fp=int(((h.pred=='Templated')&(h.my_label=='Personalized')).sum())
fn=int(((h.pred=='Personalized')&(h.my_label=='Templated')).sum())
print(f"HELD-OUT gut-check (n={len(h)}): agreement {agree}/{len(h)} = {agree/len(h)*100:.1f}%")
print(f"  bespoke->Templated hard fails: {fp}")
print(f"  Templated->Personalized (recall miss): {fn}  (both dinner-invite variants clustered at fam-size 2)")
h[['activity_id','ca','pred','my_label','fam_size','nearest_j']].to_csv("holdout_labeled.csv",index=False)
print("wrote holdout_labeled.csv")
