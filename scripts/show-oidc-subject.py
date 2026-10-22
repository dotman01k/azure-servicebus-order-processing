"""Print only the claims needed for federation; never print the JWT itself."""
import base64
import json
import os
import urllib.parse
import urllib.request

url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
separator = '&' if '?' in url else '?'
url += separator + urllib.parse.urlencode({'audience': 'api://AzureADTokenExchange'})
request = urllib.request.Request(url, headers={
    'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
with urllib.request.urlopen(request, timeout=30) as response:
    token = json.load(response)['value']
payload = token.split('.')[1]
claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
print(json.dumps({key: claims[key] for key in ('iss', 'aud', 'sub')}, indent=2))
