"""Shared progressive enhancement for backend and standalone public documents."""
def decorate(html, dashboard=False):
    if not dashboard:
        html = html.replace('The right support.', 'Find government schemes').replace('A clearer way forward.', 'you may qualify for.')
    extra = '<link rel="stylesheet" href="/static/citizen.css"><script src="/static/citizen.js" defer></script>'
    return html.replace('</head>',extra+'</head>')
