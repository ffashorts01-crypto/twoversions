import json
def S(summary, *quotes): return {"summary": summary, "quotes": [{"id": i, "text": t} for i, t in quotes]}
def C(key, ids, title, place, region, tags, ru, ua, ind, diverge, unc, unc_r, contra, uk, en):
    return dict(key=key, ids=ids, title=title, place=place, region=region, tags=tags, ru=ru, ua=ua, ind=ind,
                diverge=diverge, figures=[], contradiction=contra, uncertainty=unc, uncertainty_reason=unc_r,
                uk=dict(zip(['t','p','d','ru','ua','ind'], uk)), en=dict(zip(['t','p','d','ru','ua','ind'], en)))
cards = []
