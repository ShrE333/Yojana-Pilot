"""Citizen intent orders exploration; it never changes official eligibility."""
import re

CATEGORIES = {
    'pension': ('pension', 'old age', 'senior citizen', 'वृद्ध', 'पेन्शन', 'पेंशन'),
    'income': ('financial assistance', 'financial help', 'income support', 'subsistence', 'maintenance allowance', 'आर्थिक', 'वित्तीय'),
    'education': ('scholarship', 'student', 'education', 'शिष्यवृत्ती', 'छात्रवृत्ति', 'शिक्षण'),
    'agriculture': ('farmer', 'agricultur', 'kisan', 'fisher', 'शेतकरी', 'किसान'),
    'health': ('health', 'medical', 'hospital', 'ayushman', 'आरोग्य', 'स्वास्थ्य'),
    'housing': ('housing', 'house construction', 'awas', 'घरकुल', 'आवास'),
    'employment': ('employment', 'skill', 'entrepreneur', 'रोजगार'),
}


def intent_from_text(text, previous=None):
    """Explicit topic/life-stage cues only; no demographic or eligibility guesses."""
    result = dict(previous or {})
    text = text.casefold()
    if any(word in text for word in ('retired', 'निवृत्त', 'सेवानिवृत्त')):
        result['life_stage'] = 'retired'
    matches = [key for key, words in CATEGORIES.items() if any(word in text for word in words)]
    if re.search(r'not (?:a )?student|no scholarships?|not looking for (?:a )?scholarship',text):
        matches = [category for category in matches if category != 'education']
    if re.search(r'not retired|no longer retired',text):
        result.pop('life_stage',None)
    if matches:
        result['categories'] = matches
    return result


def categories(scheme):
    text = ' '.join(str(scheme.get(k) or '') for k in ('name', 'name_hi', 'name_mr', 'benefit_summary')).casefold()
    return {key for key, words in CATEGORIES.items() if any(word in text for word in words)}


def relevance(scheme, profile):
    context = profile.get('_intent', {})
    requested = set(context.get('categories', []))
    tags = categories(scheme)
    senior = (profile.get('age') or 0) >= 60 or profile.get('occupation') == 'retired' or context.get('life_stage') == 'retired'
    if senior and 'education' in tags and 'education' not in requested:
        return 0
    if requested:
        relevant = requested | ({'pension'} if 'income' in requested and senior else set())
        if tags & relevant:
            return 4 if senior and 'pension' in tags else 3
        return 0
    if senior and 'pension' in tags:
        return 3
    if profile.get('occupation') == 'student' and 'education' in tags:
        return 2
    if ('agriculture' in tags and profile.get('occupation')
            and profile['occupation'] not in ('farmer','agricultural_labourer','fisher')
            and not profile.get('owns_cultivable_land')):
        return 0
    if senior and 'agriculture' in tags and profile.get('occupation') == 'retired' and not profile.get('owns_cultivable_land'):
        return 0
    if profile.get('occupation') in ('farmer', 'agricultural_labourer', 'fisher') and 'agriculture' in tags:
        return 2
    return 1
