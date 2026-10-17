"""Owner companion shell. Reuses the guarded Guild chat; never reads page content."""
from flask import request

DOMAINS = {'/app/german': 'Mein Deutsch', '/app/portuguese': 'Meu Português',
           '/app/curator': 'Curator', '/app/cos': 'Chief of Staff',
           '/app/iotconnect': 'IoT Connect', '/guild': 'Guild', '/dashboard': 'Home'}

def install(app, current_user, environ):
    @app.after_request
    def companion_shell(response):
        if (environ.get('MINIMOI_MC_EVERYWHERE') != '1' or request.method != 'GET'
                or response.status_code != 200 or response.mimetype != 'text/html'
                or response.is_streamed or response.direct_passthrough
                or 'guild_ui_next.companion' not in app.view_functions):
            return response
        user = current_user()
        if not user or user.get('tier') != 'owner':
            return response
        matched = next(((root, label) for root, label in DOMAINS.items()
                       if request.path == root or request.path.startswith(root + '/')), None)
        if matched is None:
            return response
        html = response.get_data(as_text=True)
        # Native Guild pages already carry the same conversation controls.
        if 'data-mc-panel' in html or 'id="minimoi-companion"' in html or '</body>' not in html.lower():
            return response
        from flask import render_template
        shell = render_template('_companion.html', companion_domain=matched[1], companion_key=matched[0].rsplit('/', 1)[-1])
        index = html.lower().rfind('</body>')
        response.set_data(html[:index] + shell + html[index:])
        response.headers['Cache-Control'] = 'no-store'
        # Any upstream validators refer to the original representation.
        response.headers.pop('ETag', None)
        response.headers.pop('Content-MD5', None)
        return response
