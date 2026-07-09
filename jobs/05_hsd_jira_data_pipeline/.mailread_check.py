import json
import requests
from Meeting_agenda_OneNote import resolve_graph_client_secret, get_graph_token_delegated_with_secret

try:
    secret = resolve_graph_client_secret()
    token = get_graph_token_delegated_with_secret(secret, scopes=['Mail.Read'])
    headers = {'Authorization': 'Bearer ' + token}
    url = 'https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=id,subject,receivedDateTime'
    resp = requests.get(url, headers=headers, timeout=30)
    print('HTTP_STATUS', resp.status_code)
    body = resp.text or ''
    print('BODY_SNIPPET', body[:800])
except Exception as e:
    print('EXCEPTION', str(e))
