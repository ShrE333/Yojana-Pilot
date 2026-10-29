"""Deterministic stopping policy. Completion is a useful assessment, not approval."""
import os
from .questions import question_candidates, synthetic_questions
from .loader import load_questions
from .relevance import relevance


def budget():
    return max(1, min(12, int(os.getenv('ASSESSMENT_QUESTION_BUDGET', '7'))))


def relevant_catalog(profile, catalog):
    candidates = []
    for sid, scheme in catalog.items():
        if any(profile.get(field) and scheme.get(field) and profile[field] != scheme[field]
               for field in ('state', 'district')):
            continue
        tier = relevance(scheme, profile)
        if tier:
            fields = {r['field'] for group in ('all','any') for r in scheme.get('eligibility',{}).get(group,[])}
            candidates.append((sid,scheme,tier,len(fields - profile.keys())))
    candidates.sort(key=lambda item:(-item[2],item[3],item[0]))
    return {sid:scheme for sid,scheme,_,_ in candidates[:60]}


def recommendations(results, limit=5):
    seen=set(); cards=[]
    for item in [results.get('start_with')] + results.get('others',[]) + results.get('needs_information',[]) + results.get('near_misses',[]):
        if item and item['id'] not in seen:
            seen.add(item['id']);cards.append(item)
    return cards[:limit]


def important_question(field, profile):
    questions=load_questions()
    for q in list(questions)+synthetic_questions(questions):
        if field in q['fields'] and q['id'] not in profile.get('_asked',[]):
            return {'field':field,'question':q,'score':1,'schemes':['relevant schemes for your situation']}
    return None


def decide(profile, catalog, results, count=0, llm_suggested=False):
    cards=recommendations(results)
    candidates=question_candidates(profile,catalog,limit=30) if catalog else []
    essential=[f for f in ('state','age') if profile.get(f) is None]
    requested=set(profile.get('_intent',{}).get('categories',[]))
    if requested & {'income','pension'} and profile.get('annual_family_income_inr') is None:
        essential.append('annual_family_income_inr')
    next_question=None
    for field in essential:
        next_question=important_question(field,profile)
        if next_question:
            break
    next_question=next_question or (candidates[0] if candidates else None)
    known=[c for c in cards if c['status'] in ('eligible','eligible_check_manually')]
    useful=[c for c in cards if len(c.get('missing_requirements',[]))<=2]
    gain=max((sum(g['field'] in c.get('missing_requirements',[]) for c in cards)/max(1,len(cards))
              for g in candidates),default=0)
    context=not essential and bool(profile.get('_intent') or profile.get('occupation') or len(profile)>=3)
    complete=False; reason='important_uncertainty'
    if count>=budget():
        complete=True;reason='question_budget'
    elif context and known and (len(known)>=3 or not candidates):
        complete=True;reason='useful_coverage'
    elif context and known and len(useful)>=3 and gain<=0.34:
        complete=True;reason='low_marginal_value'
    elif context and len(useful)>=3 and count>=4 and gain<=0.34:
        complete=True;reason='provisional_coverage'
    elif next_question is None:
        complete=True;reason='available_information_exhausted'
    # A model suggestion may trigger only a decision already supported by Python.
    return {'assessment_complete':complete,'reason':reason,'question_count':count,
            'question_budget':budget(),'remaining_uncertainty':sorted({f for c in cards for f in c.get('missing_requirements',[])})[:20],
            'useful_scheme_count':len(useful),'marginal_gain':round(gain,3),
            'llm_completion_validated':bool(llm_suggested and complete and context),
            'recommendations':cards,'next_candidate':None if complete else next_question}


def conclusion(cards):
    if not cards:
        return 'Your assessment is saved. No relevant scheme could be confirmed yet. You can add details or refine your request.'
    return (f'Your assessment is ready with {len(cards)} relevant scheme option'+('s' if len(cards)!=1 else '')+
            '. Review the missing conditions and official sources below. This is guidance, not government approval. You can refine these results.')


def final_answer(decision):
    lines=[conclusion(decision['recommendations'])]
    if decision['reason'] in ('question_budget','available_information_exhausted','provisional_coverage'):
        lines.append('This is a provisional shortlist: unanswered conditions still need checking. Add details to refine it.')
    for index,card in enumerate(decision['recommendations'],1):
        lines.append(f"{index}. {card['name']} — {card['status'].replace('_',' ')}")
        missing=card.get('missing_requirements',[])
        if missing:
            lines.append('Missing: '+', '.join(f.replace('_',' ') for f in missing))
        if card.get('check_yourself'):
            lines.append('Check manually: '+'; '.join(card['check_yourself'][:2]))
        if card.get('next_steps'):
            lines.append('Next: '+card['next_steps'][0])
        if card.get('source_url','').startswith('https://'):
            lines.append(card['source_url'])
    return '\n'.join(lines)[:3400]
