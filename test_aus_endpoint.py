import urllib.request, json

# Login
req = urllib.request.Request(
    'http://localhost:50504/api/auth/login',
    data=json.dumps({"legajo":"1110","password":"1110"}).encode(),
    headers={'Content-Type':'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req) as r:
        login = json.loads(r.read())
        token = login.get('token','')
        print('Login OK, token:', token[:20]+'...')
except Exception as e:
    print('Login error:', e)
    token = ''

if not token:
    # Intentar con admin
    req2 = urllib.request.Request(
        'http://localhost:50504/api/auth/login',
        data=json.dumps({"legajo":"admin","password":"admin123"}).encode(),
        headers={'Content-Type':'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req2) as r:
            login = json.loads(r.read())
            token = login.get('token','')
            print('Login admin OK')
    except Exception as e:
        print('Login admin error:', e)

# Test ausentismo endpoint para persona 1110 (Arispe Matias)
if token:
    req3 = urllib.request.Request(
        'http://localhost:50504/api/personal/1110/ausentismo',
        headers={'Authorization': f'Bearer {token}'}
    )
    try:
        with urllib.request.urlopen(req3) as r:
            data = json.loads(r.read())
            print(f'Ausentismo OK: {len(data)} registros')
    except urllib.error.HTTPError as e:
        print(f'HTTP Error {e.code}: {e.read().decode()}')
    except Exception as e:
        print(f'Error: {e}')
