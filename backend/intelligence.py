"""EventPilot decision support, derived from the existing dashboard snapshot."""
from datetime import date

def score(x): return round(max(0,min(100,float(x))),1)

def analyze(d,day):
    tasks=d['tasks']; partners=d['partners']; logistics=d['logistics']; content=d['content']; funnel=d['funnel']
    overdue=[t for t in tasks if t['status']!='Done' and t['due_date']<day.isoformat()]
    pending=[p for p in partners if p['stage']=='Contacted']
    unresolved=[l for l in logistics if l['status']!='Done']
    missed=[c for c in content if c['status']!='Approved' and c['date']<day.isoformat()]
    programme=[t for t in tasks if t['role']=='Programme']
    overdue_programme=[t for t in programme if t in overdue]
    expected_reach=max(1,d['expected']*100)
    values={
      'Registration':(score(d['gap_pct']),f"{d['actual']} actual vs {d['expected']} expected registrations"),
      'Outreach':(score(100*max(0,expected_reach-funnel['Reach'])/expected_reach),f"{funnel['Reach']} reached vs indicative {expected_reach} reach"),
      'Partners':(score(100*len(pending)/len(partners)) if partners else 0,f'{len(pending)} of {len(partners)} partners pending'),
      'Operations':(score(100*len(unresolved)/len(logistics)) if logistics else 0,f'{len(unresolved)} of {len(logistics)} logistics items unresolved'),
      'Content':(score(100*len(missed)/len(content)) if content else 0,f'{len(missed)} missed/unapproved content slots'),
      'Programme':(score(100*len(overdue_programme)/len(programme)) if programme else 0,f'{len(overdue_programme)} programme tasks overdue')
    }
    heatmap=[{'category':k,'score':v,'why':why,'level':'Red' if v>60 else 'Amber' if v>30 else 'Green'} for k,(v,why) in values.items()]
    if d.get('data_state', {}).get('status') == 'no_activity':
        for cell in heatmap:
            if cell['category'] in {'Registration', 'Outreach'}:
                cell.update(score=0, level='Unknown', why='No activity recorded yet; add numbers to assess this area.')
    elif d.get('data_state', {}).get('outreach_recorded') is False:
        next(cell for cell in heatmap if cell['category'] == 'Outreach').update(score=0, level='Unknown', why='Portal registrations are recorded; add outreach metrics to assess reach and clicks.')
    start=date.fromisoformat(d['event']['start_date']); end=date.fromisoformat(d['event']['date'])
    urgency=1+max(0,(day-start).days)/max(1,(end-start).days)
    recs=[{**r,'score':round(r['impact']/max(1,r['effort'])*urgency*r.get('priority_factor',1),1),'why':r['why']+' Impact is a template estimate, not an observed outcome.'} for r in d['recommendations']]
    recs.sort(key=lambda r:r['score'],reverse=True)
    timeline=sorted([{**t,'overdue':t in overdue} for t in tasks],key=lambda t:t['due_date'])
    return {'heatmap':heatmap,'ranked_actions':recs[:3],'timeline':timeline,'overdue_count':len(overdue),'methodology':{'risk_thresholds':'Green <=30; Amber <=60; Red >60','outreach_target':'100 reach per expected registration, illustrative','impact':'Template estimates, not causal proof'}}
