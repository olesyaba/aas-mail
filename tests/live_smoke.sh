#!/bin/bash
# Read-only smoke test against the RUNNING app (real accounts). Nothing is sent,
# moved or changed: folder lists, the newest Inbox mail, calendar, unread, prefs.
#   bash tests/live_smoke.sh
set -uo pipefail
TOK="$(cat ~/.config/eas-bridge/runtime_token 2>/dev/null)" || { echo "app is not running"; exit 1; }
U=http://127.0.0.1:8780
FAIL=0
api() { curl -s -m 300 -H "X-Tok: $TOK" -H "Content-Type: application/json" -d "$2" "$U/api/$1"; }
ok()  { echo "  ✔ $1"; }
bad() { echo "  ✘ $1"; FAIL=1; }

echo "==> server"
V=$(api about '{}' | python3 -c "import sys,json;j=json.load(sys.stdin);print(j['version'] if not j.get('needs_setup') else 'NEEDS_SETUP')")
[ "$V" != "NEEDS_SETUP" ] && ok "about: version $V, logged in" || bad "account needs setup"
ACCTS=$(api accounts '{}' | python3 -c "import sys,json;print(' '.join(a['id'] for a in json.load(sys.stdin)['accounts']))")
ok "accounts: $ACCTS"

TODAY=$(date +%F); TOMORROW=$(date -v+1d +%F)
FIELDS='["subject","from","received","is_read","has_attachments","preview","thread_topic","categories"]'
for A in $ACCTS; do
  echo "==> $A"
  INBOX=$(api folders "{\"acct\":\"$A\",\"action\":\"list\"}" | python3 -c "
import sys,json;j=json.load(sys.stdin);f=j.get('items',[])
print(len(f), next((x['folder_id'] for x in f if x['type']=='2'),''))")
  set -- $INBOX; N=$1; IB=${2:-}
  [ "$N" -gt 3 ] && [ -n "$IB" ] && ok "folders: $N (inbox $IB)" || bad "folders: $INBOX"
  api mail "{\"acct\":\"$A\",\"action\":\"list\",\"folder\":\"$IB\",\"limit\":40,\"filter\":5,\"fields\":$FIELDS}" | python3 -c "
import sys,json,datetime
j=json.load(sys.stdin); it=j.get('items',[])
if not j.get('ok',True): print('  ✘ inbox list:', j.get('message')); sys.exit(1)
r=sorted(x.get('received') or '' for x in it)
newest=r[-1] if r else ''
age=(datetime.datetime.now()-datetime.datetime.fromisoformat(newest.replace(' ','T'))).days if newest else 99
print(('  ✔' if it and all(x.get('from') is not None for x in it) else '  ✘'), f'inbox: {len(it)} letters with sender/preview, newest {newest}')
print(('  ✔' if age <= 7 else '  ✘'), f'newest letter is {age} day(s) old (stale-cache regression guard)')
sys.exit(0 if it and age <= 7 else 1)" || FAIL=1
  api events "{\"acct\":\"$A\",\"action\":\"list\",\"start\":\"$TODAY\",\"end\":\"$TOMORROW\"}" | python3 -c "
import sys,json;j=json.load(sys.stdin)
print(('  ✔' if j.get('ok') else '  ✘'), 'calendar today:', j.get('count'), 'events', j.get('message',''))
sys.exit(0 if j.get('ok') else 1)" || FAIL=1
  api unread "{\"acct\":\"$A\"}" | python3 -c "import sys,json;j=json.load(sys.stdin);print('  ✔ unread total', j.get('total'))"
done
[ $FAIL -eq 0 ] && echo "LIVE SMOKE PASSED" || { echo "LIVE SMOKE FAILED"; exit 1; }
