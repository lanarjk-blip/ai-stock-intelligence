"""Diagnostic abstention evaluation; never promotes a production model.

The archive has already been inspected. Threshold 0.60 is a diagnostic probe,
not a newly untouched model-selection test or evidence of tradable accuracy.
"""
import json
import math
from pathlib import Path


def evaluate(rows, model, threshold=0.60):
    selected=[]
    for row in rows:
        p=row['probabilities'][model]
        if len(p)!=3 or any(not math.isfinite(v) or v<0 or v>1 for v in p) or abs(sum(p)-1)>1e-6:
            raise ValueError('Invalid probability vector')
        prediction=max(range(3),key=lambda k:p[k])
        if prediction!=1 and p[prediction]>=threshold:
            selected.append((row,prediction))
    n=len(selected);correct=sum(row['actual_label']==pred for row,pred in selected)
    by_ticker={}
    for ticker in sorted({row['ticker'] for row in rows}):
        subset=[(row,pred) for row,pred in selected if row['ticker']==ticker]
        by_ticker[ticker]={'signals':len(subset),'correct':sum(row['actual_label']==pred for row,pred in subset)}
    precision={}
    for label,name in [(2,'UP'),(0,'DOWN')]:
        subset=[(r,p) for r,p in selected if p==label]
        precision[name]={'n':len(subset),'accuracy':sum(r['actual_label']==p for r,p in subset)/len(subset) if subset else None}
    return dict(threshold=threshold,total_outcomes=len(rows),signals=n,correct=correct,
        coverage=n/len(rows) if rows else 0,accuracy=correct/n if n else None,per_ticker=by_ticker,precision=precision,
        decision='NO PROMOTION; confidence filtering has not established an edge',
        diagnostic_only=True,production_enabled=False)


def run():
    root=Path(__file__).parent
    ledger=json.loads((root/'conditional_replay.json').read_text())
    previous=json.loads((root/'conditional_results.json').read_text())
    report=evaluate(ledger,previous['selected_on_validation'])
    report.update(model=previous['selected_on_validation'],test_days=previous['test_days'],
        limitations=['Previously inspected two-ticker historical replay, not untouched live evidence',
        'No fees/slippage model; no synchronized IHSG/sector data',
        'No model or probability threshold promoted to production'])
    (root/'selective_results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':run()
