"""Stage 4b: write personalization_ground_truth.csv from adjudication.
Labels = my read judgment. Non-singleton classes verified as coherent templates by
reading a broad sample; singletons judged individually with missed-member exceptions."""
import pandas as pd, json

nd = pd.read_parquet("clusters.parquet").set_index("activity_id")
rows = [json.loads(l) for l in open("adjudication_sample.jsonl")]

# Singletons/pairs judged Templated on reading = missed template members (clustering recall gaps).
# Reasons recorded. Everything else in the singleton class = Personalized.
TEMPLATED_SINGLETONS = {
 "hs_email:504203295977": "12th-Aug dinner invite (Ulrik Hansen full-name variant dropped below thr)",
 "hs_email:503822349499": "12th-Aug NYC dinner invite variant",
 "hs_email:505635737818": "'did you see my invite? join July 30' invite bump template",
 "hs_email:504606034144": "dinner confirmation bump ('confirmed X,Y,Z, few seats left')",
 "hs_email:502263083257": "generic weekend bump (j=0.47 to family)",
 "hs_email:506286946499": "2-recipient dup (Oxipital Series A, 120 vision systems; j=0.45)",
 "hs_email:502314174675": "'did you get my email? Nico' bump family",
 "hs_email:505578076405": "'did you read my last email? Nico' bump family",
 "hs_email:502937024708": "'X spots left, save you a seat?' invite-nudge template",
 "hs_email:502683759862": "shared skeleton 'consistent ground truth across ... is the hard part' (ceiling case)",
}
# Borderline generic bumps kept Personalized but flagged (open question for Dillon).
GENERIC_BUMP_FLAG = {"hs_email:504025494732", "hs_email:502989566195"}

out = []
for r in rows:
    aid = r["activity_id"]; vt = r["vtype"]
    if vt != "singleton_or_pair":
        label = "Templated"
        reason = f"{vt}: coherent reuse family (fam_sim={r['fam_sim']})"
        method = "read+cluster-verified"
    else:
        if aid in TEMPLATED_SINGLETONS:
            label = "Templated"; reason = "missed family member — " + TEMPLATED_SINGLETONS[aid]
            method = "read"
        else:
            label = "Personalized"
            reason = "unique substance for one prospect; no shared verbatim block (j=%.2f)" % r["nearest_j"]
            if aid in GENERIC_BUMP_FLAG:
                reason = "generic bump, not provably reused (open question)"
            if r["is_reply"]:
                reason = "unique reply/logistics text, not reused; " + reason
            method = "read"
    out.append(dict(
        activity_id=aid, ca=r["ca"], vtype=vt,
        fam_sim=r["fam_sim"], is_reply=r["is_reply"], is_selfbump=r["is_selfbump"],
        nearest_j=r["nearest_j"], label=label, family_id=(int(nd.loc[aid,"sim_clu"])),
        method=method, reason=reason,
    ))
gt = pd.DataFrame(out)
gt.to_csv("../personalization_ground_truth.csv", index=False)  # reports/ root (gitignored)
gt.to_csv("ground_truth.csv", index=False)  # local copy for downstream stages

print("ground truth n:", len(gt))
print(gt.label.value_counts().to_dict())
print("\nTemplated by vtype:")
print(gt.groupby('vtype').label.value_counts())
print("\nreplies in GT:", int(gt.is_reply.sum()), "| of which Personalized:",
      int(((gt.is_reply)&(gt.label=='Personalized')).sum()))
print("wrote ../personalization_ground_truth.csv")
