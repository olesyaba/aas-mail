// Web UI logic tests: pure helpers are lifted straight out of web/index.html's
// inline <script> and evaluated in a sandbox, so the tests exercise the shipped code.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const plain = v => JSON.parse(JSON.stringify(v));  // vm objects have foreign prototypes
const html = readFileSync(new URL('../../web/index.html', import.meta.url), 'utf8');
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

/** Permissive DOM stand-in: any property is another stub, any call returns a stub. */
function stub() {
  const own = {};
  return new Proxy(function () {}, {
    get(_, k) {
      if (k in own) return own[k];
      if (k === 'then') return undefined;
      if (k === Symbol.toPrimitive) return () => '';
      if (k === Symbol.iterator) return [][Symbol.iterator];
      if (k === 'querySelectorAll') return () => [];
      return (own[k] = stub());
    },
    set(_, k, v) { own[k] = v; return true; },
    apply: () => stub(),
    construct: () => stub(),
  });
}

/**
 * Run the whole inline script in a sandbox (fake DOM, recorded fetch) and
 * export the named top-level bindings (`const`/`let` are not globals in vm).
 */
function load(names, extra = {}) {
  const store = new Map(), fetches = [];
  const document = stub();
  document.querySelector = sel => sel.startsWith('meta') ? {content: 'TKN'} : stub();
  document.querySelectorAll = () => [];
  document.getElementById = () => stub();
  document.hidden = false;
  const ctx = vm.createContext({
    URL, Map, Set, Promise, JSON, Date, Math, RegExp, Error, Array, Object, String, Number,
    console, setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    document, location: {hash: '', reload() {}}, CSS: {escape: s => s},
    addEventListener: () => {}, innerWidth: 1200, history: {state: null, pushState() {}, replaceState() {}},
    localStorage: {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v))},
    fetch: (url, opts) => { fetches.push({url, opts, body: JSON.parse(opts.body)}); return new Promise(() => {}); },
  });
  ctx.window = ctx;
  vm.runInContext(script + `\n;globalThis.__x = {${names.join(',')}};`, ctx);
  for (const [k, v] of Object.entries(extra)) vm.runInContext(`${k} = ${JSON.stringify(v)}`, ctx);
  return Object.assign(ctx.__x, {ctx, fetches});
}

test('whole inline script parses', () => {
  assert.doesNotThrow(() => new vm.Script(script));
});

test('boot: loads prefs, about and accounts in parallel with the page token', () => {
  const {fetches} = load([]);
  assert.deepEqual(fetches.map(f => f.url).sort(), ['/api/about', '/api/accounts', '/api/prefs']);
  for (const f of fetches) {
    assert.equal(f.opts.method, 'POST');
    assert.equal(f.opts.headers['X-Tok'], 'TKN');
    assert.equal(f.body.acct, 'main');
  }
});

test('esc escapes every HTML-significant character', () => {
  const {esc} = load(['esc']);
  assert.equal(esc(`<a href="x" onclick='y'>&</a>`), '&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
  assert.equal(esc(null), '');
});

test('joinURL prefers meeting hosts, strips HTML and trailing punctuation', () => {
  const {joinURL} = load(['joinURL']);
  assert.equal(joinURL('Room 5', 'Agenda https://wiki.test/page. Join: <a href="https://teams.microsoft.com/l/meetup-join/abc">link</a>'),
    'https://teams.microsoft.com/l/meetup-join/abc');
  assert.equal(joinURL('https://telemost.yandex.ru/j/123,', ''), 'https://telemost.yandex.ru/j/123');
  assert.equal(joinURL('', 'see https:\\/\\/zoom.us\\/j\\/9?pwd=a&amp;b'), 'https://zoom.us/j/9?pwd=a&b');
  assert.equal(joinURL('', 'only https://docs.test/x'), 'https://docs.test/x');
  assert.equal(joinURL(null, null), '');
});

test('splitAddrs keeps commas inside <…> and addrOnly extracts SMTP', () => {
  const {splitAddrs, addrOnly, formatPerson} = load(['splitAddrs', 'addrOnly', 'formatPerson']);
  assert.deepEqual(plain(splitAddrs('"Petrov, Ivan" <ivan@x.ru>; b@y.ru,\n c@z.ru, ')),
    ['"Petrov, Ivan" <ivan@x.ru>', 'b@y.ru', 'c@z.ru']);
  assert.equal(addrOnly('"Petrov, Ivan" <ivan@x.ru>'), 'ivan@x.ru');
  assert.equal(addrOnly(' "b@y.ru" '), 'b@y.ru');
  assert.equal(formatPerson({name: 'Ivan', address: 'i@x.ru'}), 'Ivan <i@x.ru>');
  assert.equal(formatPerson({name: 'i@x.ru', address: 'I@x.ru'}), 'I@x.ru');
  // GAL names like "Petrov, Ivan" must survive the pick → split → send round-trip.
  const picked = formatPerson({name: 'Petrov, Ivan', address: 'ivan@x.ru'}) + ', ' + formatPerson({name: 'B', address: 'b@y.ru'});
  assert.deepEqual(plain(splitAddrs(picked).map(addrOnly)), ['ivan@x.ru', 'b@y.ru']);
});

test('conversation timeline: letters by day, quiet days between them, day headings', () => {
  const {dayGroups, dayHead} = load(['dayGroups', 'dayHead']);
  const at = x => x.received;
  const g = plain(dayGroups([
    {received: '2026-09-04 16:35'}, {received: '2026-09-04 17:02'},
    {received: '2026-09-05 09:48'}, {received: '2026-09-10 10:35'}, {received: '2026-09-29 13:08'},
  ], at));
  assert.deepEqual(g.map(d => [d.day, d.items.length, d.quiet]),
    [['2026-09-04', 2, 0], ['2026-09-05', 1, 0], ['2026-09-10', 1, 4], ['2026-09-29', 1, 18]]);
  // Newest first (the list tree) counts the same quiet days.
  assert.deepEqual(plain(dayGroups([{received: '2026-09-10 10:00'}, {received: '2026-09-05 10:00'}], at)).map(d => d.quiet), [0, 4]);
  const now = new Date('2026-09-29T15:00');
  assert.equal(dayHead('2026-09-29', now), 'Сегодня');
  assert.equal(dayHead('2026-09-28', now), 'Вчера');
  assert.equal(dayHead('2026-09-04', now), 'пт, 4 сент.');
  assert.equal(dayHead('2025-12-31', now), 'ср, 31 дек. 2025');
});

test('thread grouping strips reply prefixes and sorts newest first', () => {
  const {groupItems, ctx} = load(['groupItems'], {prefs: {threads: true}});
  const items = [
    {item_id: '1', subject: 'Отчёт', received: '2026-09-20 10:00'},
    {item_id: '2', subject: 'RE: Fwd: отчёт', received: '2026-09-21 10:00'},
    {item_id: '3', subject: 'Другое', received: '2026-09-19 10:00'},
  ];
  const g = groupItems(items);
  assert.equal(g.length, 2);
  assert.deepEqual(plain(g[0].items.map(m => m.item_id)), ['2', '1']);
  vm.runInContext('prefs.threads = false', ctx);
  assert.equal(groupItems(items).length, 3);
});

test('fmtDate shows time for today, day.month otherwise, year when different', () => {
  const {fmtDate, isoDay} = load(['isoDay', 'fmtDate']);
  const today = isoDay(new Date());
  assert.equal(fmtDate(`${today} 09:05`), '09:05');
  const y = new Date().getFullYear();
  const other = today.endsWith('-01-02') ? '01-03' : '01-02';
  assert.equal(fmtDate(`${y}-${other} 10:00`), `${other.slice(3)}.${other.slice(0, 2)} 10:00`);
  assert.equal(fmtDate('2001-03-04 10:00'), '04.03.2001 10:00');
  assert.equal(fmtDate(''), '');
});

test('linkify only wraps http(s) and drops trailing punctuation', () => {
  const {linkify} = load(['linkify']);
  assert.equal(linkify('go https://a.test/x.'), 'go <a href="https://a.test/x" target="_blank" rel="noopener noreferrer">https://a.test/x</a>.');
  assert.equal(linkify('javascript:alert(1)'), 'javascript:alert(1)');
});

test('parseLinks accepts "name | url" and bare urls, rejects others', () => {
  const {parseLinks} = load(['parseLinks']);
  assert.deepEqual(plain(parseLinks('Room | https://telemost.yandex.ru/j/1\nhttps://zoom.us/j/2\nnot a link\n')), [
    {name: 'Room', url: 'https://telemost.yandex.ru/j/1'},
    {name: 'zoom.us/j/2', url: 'https://zoom.us/j/2'},
  ]);
});

test('recent people: ranked by use, case-insensitive merge, per-account key', () => {
  const x = load(['rememberPeople', 'rememberAddrsFromFields', 'filterRecentPeople']);
  x.rememberAddrsFromFields('Ivan Petrov <ivan@x.ru>, b@y.ru');
  x.rememberPeople([{address: 'IVAN@x.ru'}]);
  assert.deepEqual(plain(x.filterRecentPeople('').map(p => p.address)), ['IVAN@x.ru', 'b@y.ru']);
  assert.equal(x.filterRecentPeople('petrov')[0].name, 'Ivan Petrov');
  vm.runInContext("ACCT = 'seller'", x.ctx);
  assert.deepEqual(plain(x.filterRecentPeople('')), []);
});

test('calendar week starts on Monday', () => {
  const {mondayOf} = load(['mondayOf']);
  const sun = new Date(2026, 8, 27, 15, 0);  // Sunday
  assert.equal(mondayOf(sun).getDate(), 21);
  assert.equal(mondayOf(new Date(2026, 8, 21, 0, 1)).getDate(), 21);
});

test('fmtSize units', () => {
  const {fmtSize} = load(['fmtSize']);
  assert.equal(fmtSize(512), '512 Б');
  assert.equal(fmtSize(2048), '2 КБ');
  assert.equal(fmtSize(3 * 1048576), '3.0 МБ');
});

test('refreshUnread is always called with an account id first', () => {
  // refreshUnread(acct, force): a bare boolean first arg writes counts under key "true".
  const bad = [...script.matchAll(/refreshUnread\((true|false)\b/g)];
  assert.deepEqual(bad.map(m => m[0]), []);
});

test('every api() domain has a backend route', () => {
  const routes = new Set(['prefs', 'about', 'accounts', 'account-config', 'update', 'unread', 'message', 'retry', 'sync', 'invite', 'warm',
    'mail', 'folders', 'events', 'people', 'settings']);
  const used = new Set([...script.matchAll(/api\('([\w-]+)'/g)].map(m => m[1]));
  assert.deepEqual([...used].filter(d => !routes.has(d)), []);
});

test('category colours: distinct for real categories, stable once assigned, readable ink', () => {
  const {catColor, inkOn, ctx} = load(['catColor', 'inkOn']);
  ctx.prefs_main = () => vm.runInContext('prefs.category_colors', ctx);
  vm.runInContext('prefs = {category_colors: {}}', ctx);
  const names = ['--', '121', 'бизнес', 'дирекция', 'команда', 'статус', 'B2B', 'Ecom', 'My events'];
  const cols = names.map(n => catColor(n));
  assert.equal(new Set(cols).size, names.length);
  assert.equal(catColor('статус'), cols[5], 'same colour next time');
  vm.runInContext("prefs.category_colors['статус'] = '#fff100'", ctx);
  assert.equal(catColor('статус'), '#fff100', 'user choice wins');
  assert.equal(inkOn('#fff100'), '#1f2328');
  assert.equal(inkOn('#C19C00'), '#1f2328', 'mustard gets dark text');
  assert.equal(inkOn('#004E8C'), '#fff');
  vm.runInContext("prefs.category_colors_seller = {'статус': '#123456'}", ctx);
  assert.equal(catColor('статус', 'seller'), '#123456', 'Seller has its own colours');
  assert.equal(catColor('статус', 'main'), '#fff100', 'Bank colour untouched');
  catColor('только-селлер', 'seller');
  assert.ok(!('только-селлер' in ctx.prefs_main()), 'a Seller tag never lands in the Bank map');
});

test('theme: applied to <html>, remembered, forwarded to the native shell', () => {
  const posted = [];
  const {applyTheme, ctx} = load(['applyTheme']);
  const html = {dataset: {}};
  ctx.document.documentElement = html;
  ctx.window = {webkit: {messageHandlers: {aasTheme: {postMessage: v => posted.push(v)}}}};
  applyTheme('dark');
  assert.equal(html.dataset.theme, 'dark');
  applyTheme('navy-orange');
  assert.equal(html.dataset.theme, 'navy-orange');
  applyTheme('eclipse-almond-light');
  assert.equal(html.dataset.theme, 'eclipse-almond-light');
  applyTheme('mist-auto');  // no dark scheme in the sandbox → the light variant
  assert.equal(html.dataset.theme, 'mist-light');
  applyTheme('bogus');
  assert.equal(html.dataset.theme, undefined, 'unknown → follow the system');
  assert.deepEqual(posted, ['dark', 'navy-orange', 'eclipse-almond-light', 'mist-light', 'system']);
});

test('theme picker: palette × mode ↔ stored id', () => {
  const {themeId, themeParts, THEME_IDS, THEME_PALETTES} = load(['themeId', 'themeParts', 'THEME_IDS', 'THEME_PALETTES']);
  assert.equal(themeId('', 'auto'), 'system');
  assert.equal(themeId('', 'light'), 'light');
  assert.equal(themeId('mist', 'dark'), 'mist');
  assert.equal(themeId('mist', 'light'), 'mist-light');
  assert.equal(themeId('coral-mint', 'auto'), 'coral-mint-auto');
  for (const p of THEME_PALETTES) for (const m of ['auto', 'light', 'dark'])
    assert.deepEqual(plain(themeParts(themeId(p.id, m))), {pal: p.id, mode: m}, `${p.id || 'default'}/${m} round-trips`);
  assert.deepEqual(plain(themeParts('bogus')), {pal: '', mode: 'auto'});
  assert.equal(THEME_IDS.length, THEME_PALETTES.length * 3 - 1, 'every palette × mode, minus «system»');
});

test('themes: every palette is registered everywhere and readable (≥ 4.5:1)', () => {
  const read = p => readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');
  const tokens = read('web/ui-kit/tokens.css').replace(/\/\*[\s\S]*?\*\//g, '');
  const all = plain(load(['THEME_IDS']).THEME_IDS);
  for (const id of all.filter(t => t.endsWith('-auto'))) assert.ok(read('webapp.py').includes(`"${id}"`), `${id} accepted by webapp.py`);
  const ids = all.filter(t => !t.endsWith('-auto'));  // auto resolves to one of these
  const py = read('webapp.py'), swift = read('app/main.swift');
  const vars = sel => {
    const out = {};
    for (const m of tokens.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
      if (!m[1].split(',').some(s => s.trim() === sel)) continue;
      for (const d of m[2].matchAll(/(--aas-[\w-]+):\s*(#[0-9a-f]{6})/gi)) out[d[1]] = d[2];
    }
    return out;
  };
  const lum = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255)
    .map(x => x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
    .reduce((s, x, i) => s + x * [0.2126, 0.7152, 0.0722][i], 0);
  const cr = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const weak = [];
  for (const id of ids.filter(t => t !== 'light' && t !== 'dark')) {
    assert.ok(py.includes(`"${id}"`), `${id} accepted by webapp.py`);
    assert.ok(swift.includes(`"${id}"`), `${id} mapped in main.swift`);
    const root = vars(`:root[data-theme="${id}"]`);
    assert.ok(root['--aas-bg'], `${id} has a tokens.css block`);
    for (const [acct, sel] of [['bank', 'body.theme-bank'], ['seller', 'body.theme-seller']]) {
      const v = {...root, ...vars(`:root[data-theme="${id}"] ${sel}`)};
      const pairs = [['text', v['--aas-text'], v['--aas-bg']], ['muted', v['--aas-muted'], v['--aas-panel']],
        ['on-accent', v['--aas-on-accent'], v[`--aas-${acct}-fill`]], ['ink', v[`--aas-${acct}-ink`], v['--aas-panel']],
        ['danger', v['--aas-danger'], v['--aas-panel']]];
      for (const [n, f, b] of pairs) if (cr(f, b) < 4.5) weak.push(`${id}/${acct} ${n} ${f} on ${b} = ${cr(f, b).toFixed(2)}`);
    }
  }
  assert.deepEqual(weak, [], 'text/fill pairs below WCAG AA');
});

test('themePalette: tray gets the theme colours, dark text on light fills', () => {
  const {themePalette, ctx} = load(['themePalette']);
  const css = vars => { ctx.getComputedStyle = () => ({getPropertyValue: n => vars[n.slice(6)] ?? ''}); };
  const forestLight = {bg: '#eaf3e7', panel: '#ffffff', line: '#d3e0cf', text: '#0f1f16', muted: '#55705f',
    danger: '#c4314b', ok: '#107c41', 'warning-text': '#8a5a00',
    'bank-fill': '#e76f51', 'bank-ink': '#b4492f', 'bank-bg': '#fbe9e3',
    'seller-fill': '#a7f432', 'seller-ink': '#2e6b3a', 'seller-bg': '#e3f5cf'};
  css(forestLight);
  let p = plain(themePalette());
  assert.equal(p.sellerOn, '#0f1f16', 'lime needs dark text');
  assert.equal(p.bankOn, '#0f1f16', 'terracotta needs dark text');
  assert.equal(p.dark, false);
  css({...forestLight, bg: ' #181A1D', text: '#e6e8ea', 'bank-fill': '#8c2f3d', 'seller-fill': '#1e6b57'});
  p = plain(themePalette());
  assert.equal(p.bg, '#181a1d', 'trimmed + lower-cased');
  assert.equal(p.dark, true);
  assert.deepEqual([p.bankOn, p.sellerOn], ['#ffffff', '#ffffff']);
  css({...forestLight, muted: 'var(--x)'});
  assert.equal(themePalette(), null, 'unresolved token → keep the tray as it is');
});

test('layoutLanes: width comes from the conflict group, not the whole day', () => {
  const {layoutLanes} = load(['layoutLanes']);
  const t = (h, m = 0) => new Date(2026, 8, 24, h, m);
  const ev = (a, b, id) => ({id, s: t(...a), e: t(...b)});
  // 09–10 alone; 11–13 overlaps 11:30–12 and 12:30–14 (chain), 12:30 overlaps 11–13 only; 15–16 alone.
  const evs = [ev([9], [10], 'solo1'), ev([11], [13], 'A'), ev([11, 30], [12], 'B'),
               ev([12, 30], [14], 'C'), ev([13, 30], [14], 'D'), ev([15], [16], 'solo2')];
  layoutLanes(evs);
  const got = Object.fromEntries(evs.map(e => [e.id, [e._l, e._n]]));
  assert.deepEqual(got.solo1, [0, 1]);
  assert.deepEqual(got.solo2, [0, 1]);
  for (const id of ['A', 'B', 'C', 'D']) assert.equal(got[id][1], 2, id);
  assert.equal(got.A[0], 0); assert.equal(got.B[0], 1); assert.equal(got.C[0], 1); assert.equal(got.D[0], 0);
  // Back-to-back meetings don't conflict.
  const bb = [ev([9], [10], 'x'), ev([10], [11], 'y')];
  layoutLanes(bb);
  assert.deepEqual(bb.map(e => [e._l, e._n]), [[0, 1], [0, 1]]);
});

test('entering the calendar tab forces a calendar delta sync', () => {
  const {showView, fetches, ctx} = load(['showView']);
  vm.runInContext('drawCal = () => { calDrawnFor = ACCT; }', ctx);  // the stub DOM has no grid
  fetches.length = 0;
  showView('cal');
  const sync = fetches.filter(f => f.url === '/api/sync');
  assert.equal(sync.length, 1);
  assert.equal(sync[0].body.calendar, 'delta');
  assert.ok(fetches.some(f => f.url === '/api/events'), 'cached week drawn first');
});

test('folder list: per-folder badges, «N+» while loading, account total on top', () => {
  const x = load(['renderFolders', 'setUnread']);
  let html = '';
  const qs = x.ctx.document.querySelector;
  x.ctx.document.querySelector = sel => sel === '#folders' ? {set innerHTML(v) { html = v; }} : sel === '#newfolder' ? null : qs(sel);
  x.setUnread('main', {folders: {i: 3, p: 120, s: 4}, total: 123, partial: ['i']});
  x.renderFolders([{folder_id: 'i', name: 'Входящие', type: '2', parent_id: '0'},
                   {folder_id: 'p', name: 'Проекты', type: '12', parent_id: '0'},
                   {folder_id: 's', name: 'Отправленные', type: '5', parent_id: '0'}]);
  assert.match(html, /aas-folder-total[^]*?>123\+</);
  assert.match(html, /data-id="i"[^]*?>3\+</);
  assert.match(html, /data-id="p"[^]*?>99\+</);
});

test('Inbox badge says 1 but the letter is not listed → the open list reloads', () => {
  const x = load(['setUnread']);
  vm.runInContext("view = 'mail'; curFolder = 'inbox'; searchQ = ''; allItems = [{item_id: 'a', is_read: true}]", x.ctx);
  x.fetches.length = 0;
  x.setUnread('main', {folders: {inbox: 0}, total: 0});
  assert.equal(x.fetches.length, 0, 'counts agree: no reload');
  x.setUnread('main', {folders: {inbox: 1}, total: 1});
  const lists = x.fetches.filter(f => f.url === '/api/mail' && f.body.action === 'list');
  assert.equal(lists.length, 1);
  assert.equal(lists[0].body.folder, 'inbox');
  x.setUnread('main', {folders: {inbox: 1}, total: 1});
  assert.equal(x.fetches.filter(f => f.url === '/api/mail').length, 1, 'no reload storm');
});

test('switching Bank ⇄ Seller calendar never leaves the other mailbox on screen', () => {
  const x = load(['showView']);
  vm.runInContext("accounts = [{id: 'main', name: 'Bank'}, {id: 'seller', name: 'Seller', green: true}]; view = 'cal';" +
    "events = [{subject: 'bank-only', s: new Date(), e: new Date(), _acct: 'main'}]; calDrawnFor = 'main';" +
    "drawCal = () => { calDrawnFor = ACCT; }", x.ctx);  // the stub DOM has no grid
  x.fetches.length = 0;
  x.showView('cal', 'seller');
  assert.equal(vm.runInContext('calDrawnFor', x.ctx), 'seller', 'grid redrawn for Seller before its fetch returns');
  assert.equal(vm.runInContext('events.length', x.ctx), 0, 'Bank meetings are not Seller meetings');
  assert.ok(x.fetches.some(f => f.url === '/api/events' && f.body.acct === 'seller'));
});

test('event card: long description has no inner scroller, participants collapse', () => {
  const x = load(['attSummary']);
  const st = {accepted: 'принял', declined: 'отклонил', none: 'без ответа'};
  assert.match(x.attSummary([{status: 'accepted'}, {status: 'accepted'}, {status: 'declined'}, {}], st),
               /2 приняли · 1 отклонил · 1 без ответа/);
  const html = readFileSync(new URL('../../web/index.html', import.meta.url), 'utf8');
  assert.doesNotMatch(html, /max-height:180px/);
});

test('incoming mail: long To/Cc lists collapse with expand', () => {
  const {recipBlock} = load(['recipBlock']);
  const many = Array.from({length: 5}, (_, i) => ({name: `u${i}`, address: `u${i}@x.ru`}));
  const collapsed = recipBlock('Кому', many);
  assert.match(collapsed, /<details class="aas-mc__recips">/);
  assert.match(collapsed, /и ещё 3/);
  assert.match(collapsed, /aas-mc__recips-full/);
  const short = recipBlock('Кому', many.slice(0, 2));
  assert.doesNotMatch(short, /<details/);
  assert.match(short, /^<div class="aas-mc__recips">Кому:/);
  assert.equal(recipBlock('Копия', []), '');
});

test('reply form shows the original under an Outlook header block', () => {
  const {quotedOriginal} = load(['quotedOriginal']);
  const html = quotedOriginal({from: [{name: 'Егор', address: 'e@b.ru'}], to: [{address: 'me@b.ru'}], cc: [],
    subject: 'Backend гильдия', date: 'Thu, 24 Sep 2026 09:30:00 +0300', text: 'исходный <текст>'});
  for (const s of ['<b>От:</b> Егор &lt;e@b.ru&gt;', '<b>Отправлено:</b>', '<b>Кому:</b> me@b.ru', '<b>Тема:</b> Backend гильдия', 'исходный &lt;текст&gt;'])
    assert.ok(html.includes(s), s);
  assert.ok(!html.includes('Копия'), 'no empty Cc line');
});

test('Dock badge = Inbox unread of all accounts, sent on change (AAS-24-05)', () => {
  const sent = [];
  const {paintTabBadges, ctx} = load(['paintTabBadges']);
  ctx.window = {webkit: {messageHandlers: {aasBadge: {postMessage: v => sent.push(v)}}}};
  vm.runInContext(`accounts = [{id: 'main'}, {id: 'seller'}];
    inboxIds.main = '14'; inboxIds.seller = 'i/1';
    unreadByAcct = {main: {folders: {'14': 3, '20': 50}, total: 53}, seller: {folders: {'i/1': 2}, total: 2}};`, ctx);
  paintTabBadges(); paintTabBadges();
  vm.runInContext(`unreadByAcct.main.folders['14'] = 0; unreadByAcct.seller.folders['i/1'] = 0;`, ctx);
  paintTabBadges();
  assert.deepEqual(sent, [5, 0], 'other folders do not count; repeats are not re-sent; 0 clears');
});

test('mail list sort: newest first by default, threads by latest message (AAS-24-10)', () => {
  const {sortGroups} = load(['sortGroups']);
  const g = (key, received, name, subject) => ({key, items: [{received, from: {name}, subject}]});
  const mk = () => [g('a', '2026-09-20T10:00', 'Борис', 'Отчёт'), g('b', '2026-09-24T09:00', 'Анна', 'Re: Бюджет'),
    g('c', '2026-09-22T12:00', 'Анна', 'Встреча')];
  const keys = list => list.map(x => x.key).join('');
  assert.equal(keys(sortGroups(mk())), 'bca');
  assert.equal(keys(sortGroups(mk(), 'date_asc')), 'acb');
  assert.equal(keys(sortGroups(mk(), 'from')), 'bca', 'same sender → newest first');
  assert.equal(keys(sortGroups(mk(), 'subject')), 'bca', 'Re: is ignored');
});

test('↓/↑ walk the mail list and stop at the ends (AAS-24-07)', () => {
  const {listStep, ctx} = load(['listStep']);
  const rows = ['b', 'c', 'a'].map(k => ({dataset: {key: 'i:' + k}}));
  ctx.document.querySelectorAll = sel => sel === '#rows .aas-row' ? rows : [];
  vm.runInContext(`prefs.threads = false; allItems = [
    {item_id: 'a', received: '1'}, {item_id: 'b', received: '3'}, {item_id: 'c', received: '2'}];
    openGroup = g => { curKey = g.key; };`, ctx);
  assert.equal(listStep(1), 'i:b', 'nothing open → first row');
  assert.equal(listStep(1), 'i:c');
  assert.equal(listStep(1), 'i:a');
  assert.equal(listStep(1), null, 'last row: stays');
  assert.equal(listStep(-1), 'i:c');
});

test('attachments: open / save-as per file and «save all» only inside the app', () => {
  const {attachmentsHtml, ctx} = load(['attachmentsHtml']);
  const m = {item_id: 'x'}, atts = [{ref: 'r1', name: 'a.pdf', size: 10}, {ref: 'r2', name: 'b.xlsx', size: 20}];
  ctx.window = {};
  const plainHtml = attachmentsHtml(m, atts);
  assert.match(plainHtml, /download/);
  assert.doesNotMatch(plainHtml, /data-att-as|data-att-all/, 'a plain browser only gets the download link');
  ctx.window = {webkit: {messageHandlers: {aasSave: {postMessage() {}}}}};
  const app = attachmentsHtml(m, atts);
  assert.equal((app.match(/data-att-as=/g) || []).length, 2);
  assert.equal((app.match(/data-att-open=/g) || []).length, 2);
  assert.match(app, /Сохранить все \(2\)/);
  assert.doesNotMatch(attachmentsHtml(m, atts.slice(0, 1)), /data-att-all/, 'one file: no «save all»');
});

test('updates: both modes check in the background (manual only shows the header pill)', () => {
  const timers = [];
  const {armUpdates, ctx} = load(['armUpdates']);
  ctx.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };
  vm.runInContext(`prefs.update_mode = 'manual'`, ctx); armUpdates();
  vm.runInContext(`prefs.update_mode = 'auto'`, ctx); armUpdates();
  assert.deepEqual(timers, [15000, 15000]);
});

test('update pill: hidden without a newer version; Mac opens Settings, Android links the release page', () => {
  const {updatePill} = load(['updatePill']);
  assert.equal(updatePill(null, false), null);
  assert.equal(updatePill({available: false, latest: '1.2.20'}, false), null);
  const u = {available: true, latest: '1.2.21', can_install: true, page: 'https://github.com/o/r/releases/latest'};
  assert.deepEqual(plain(updatePill(u, false)), {label: '↑ 1.2.21', href: '', title: 'Доступна версия 1.2.21 — открыть «Обновления»'});
  assert.deepEqual(plain(updatePill({...u, can_install: false}, true)),
    {label: '↑ 1.2.21', href: 'https://github.com/o/r/releases/latest', title: 'Доступна версия 1.2.21 — скачать APK со страницы релиза'});
});

test('fmtWhen: today → time, вчера, weekday, «24 сент.», full date for other years', () => {
  const {fmtWhen} = load(['fmtWhen']);
  const now = new Date(2026, 8, 25, 14, 0);  // Fri 25 Sep 2026
  assert.equal(fmtWhen('2026-09-25 11:11', false, now), '11:11');
  assert.equal(fmtWhen('2026-09-24 10:07', false, now), 'вчера');
  assert.equal(fmtWhen('2026-09-24 10:07', true, now), 'вчера, 10:07');
  assert.equal(fmtWhen('2026-09-21 09:00', false, now), 'пн');
  assert.equal(fmtWhen('2026-09-15 16:47', true, now), '15 сент., 16:47');
  assert.equal(fmtWhen('2025-12-31 23:00', false, now), '31.12.2025');
  assert.equal(fmtWhen('', false, now), '');
});

test('cleanPreview drops mailto, cid stubs, separator lines and quoted headers', () => {
  const {cleanPreview} = load(['cleanPreview']);
  assert.equal(cleanPreview('@Вера<mailto:v@corp.ru> посмотри'), '@Вера посмотри');
  assert.equal(cleanPreview('Добрый день [cid:image002.png@01DD] From: Иван Sent: вчера'), 'Добрый день');
  assert.equal(cleanPreview('Да, надо убирать ----------------------------- ещё'), 'Да, надо убирать ещё');
  assert.equal(cleanPreview('Тема: встреча завтра'), 'Тема: встреча завтра', 'a leading label is text, not a quote');
  assert.equal(cleanPreview(null), '');
});

test('event description: forwarded «Original Appointment» header folds away', () => {
  const {evBodyHtml} = load(['evBodyHtml']);
  const body = 'Коллеги, актуально\n\n-----Original Appointment-----\nFrom: Иван\nSent: вчера\nSubject: Синк\n\nСсылка https://ktalk.ru/x';
  const html = evBodyHtml(body);
  assert.match(html, /^<div class="aas-evbody">Коллеги, актуально<\/div><details class="aas-evorig">/);
  assert.match(html, /<summary>Исходное приглашение<\/summary><div class="aas-evbody">From: Иван/);
  assert.match(html, /<\/details><div class="aas-evbody">Ссылка <a href="https:\/\/ktalk.ru\/x"/);
  assert.equal(evBodyHtml('просто текст'), '<div class="aas-evbody">просто текст</div>');
});

test('layoutLanes numbers conflict groups so a crowded slot folds into «+N» per group', () => {
  const {layoutLanes} = load(['layoutLanes']);
  const t = h => new Date(2026, 8, 25, h);
  const evs = [{s: t(9), e: t(10)}, {s: t(9), e: t(10)}, {s: t(12), e: t(13)}];
  layoutLanes(evs);
  assert.equal(evs[0]._g, evs[1]._g);
  assert.notEqual(evs[0]._g, evs[2]._g);
});

test('evTitle drops the FW:/RE: of a forwarded invitation', () => {
  const {evTitle} = load(['evTitle']);
  assert.equal(evTitle({subject: 'FW: Синк продукта'}), 'Синк продукта');
  assert.equal(evTitle({subject: 'RE: Fwd: Daily'}), 'Daily');
  assert.equal(evTitle({}), '(без темы)');
});

test('eventQuoteText builds Outlook-style appointment block for mail reply', () => {
  const {eventQuoteText} = load(['eventQuoteText']);
  const e = {
    s: new Date(2026, 9, 1, 10, 0), e: new Date(2026, 9, 1, 11, 0), is_all_day: false,
    subject: 'План', location: 'Байкал', body: 'повестка',
    organizer: {name: 'Org', address: 'org@bank.test'},
    attendees: [{name: 'Me', address: 'me@bank.test'}],
  };
  const t = eventQuoteText(e);
  assert.match(t, /^-----Исходная встреча-----/);
  assert.match(t, /От: Org <org@bank\.test>/);
  assert.match(t, /Где: Байкал/);
  assert.match(t, /Тема: План/);
  assert.match(t, /повестка/);
});

test('fmtRange: one wording for meeting times, year only when not this one', () => {
  const {fmtRange} = load(['fmtRange']);
  const now = new Date(2026, 8, 25, 12);
  assert.equal(fmtRange(new Date(2026, 8, 21, 10, 5), new Date(2026, 8, 21, 10, 50), false, now), 'пн, 21 сент., 10:05–10:50');
  assert.equal(fmtRange(new Date(2026, 8, 21), null, true, now), 'пн, 21 сент., весь день');
  assert.equal(fmtRange(new Date(2025, 11, 31, 23), new Date(2026, 0, 1, 1), false, now), 'ср, 31 дек. 2025, 23:00 – чт, 1 янв., 01:00');
});

test('calendar: empty hours outside the working day fold, busy and current hours stay', () => {
  const {calScale, ctx} = load(['calScale']);
  vm.runInContext('prefs.work_start = 9; prefs.work_end = 18', ctx);
  const day = new Date(2026, 8, 21), at = (h, m = 0) => new Date(2026, 8, 21, h, m);
  const sc = calScale([day], 48, [{s: at(7, 30), e: at(8, 15)}], new Date(2026, 8, 25, 12));
  assert.equal(sc.H[7], 48, 'a meeting at 7:30 keeps 7 and 8 open');
  assert.equal(sc.H[8], 48);
  assert.equal(sc.H[3], 6);
  assert.equal(sc.H[12], 48, 'working hours never fold');
  assert.deepEqual(JSON.parse(JSON.stringify(sc.runs)), [[0, 7], [18, 24]]);
  assert.equal(sc.yOf(9), sc.Y[9]);
  assert.equal(Math.round(sc.minsAt(sc.Y[10] + 24)), 10 * 60 + 30, 'a click maps back to 10:30');
});

test('avatar: two initials from the name, a stable hue from the address', () => {
  const {avatar} = load(['avatar']);
  assert.match(avatar({name: 'Орлов Павел', address: 'p@x.ru'}), />ОП</);
  assert.match(avatar({name: '"Karpov, Denis"', address: 'd@x.ru'}), />KD</);
  assert.match(avatar({address: 'sd@x.ru'}), />S</);
  assert.equal(avatar({name: 'A B', address: 'q@x.ru'}).match(/--h:(\d+)/)[1], avatar({name: 'C', address: 'q@x.ru'}).match(/--h:(\d+)/)[1]);
});

test('list filter: unread / with attachments narrow what is on screen', () => {
  const {groupsOnScreen, ctx} = load(['groupsOnScreen']);
  vm.runInContext(`prefs.threads = false; allItems = [
    {item_id: 'a', is_read: false}, {item_id: 'b', is_read: true, has_attachments: true}, {item_id: 'c', is_read: true}]`, ctx);
  assert.equal(groupsOnScreen().length, 3);
  vm.runInContext(`listFilter = 'unread'`, ctx); assert.deepEqual(plain(groupsOnScreen().map(g => g.key)), ['i:a']);
  vm.runInContext(`listFilter = 'att'`, ctx); assert.deepEqual(plain(groupsOnScreen().map(g => g.key)), ['i:b']);
});

test('no server: the list says so, counts down and retries by itself', () => {
  const timers = [];
  const x = load(['scheduleListRetry', 'listRetry']);
  x.ctx.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };
  let html = '';
  const qs = x.ctx.document.querySelector;
  x.ctx.document.querySelector = sel => sel === '#rows' ? {set innerHTML(v) { html = v; }} : qs(sel);
  vm.runInContext("accounts = [{id: 'main', name: 'Alfa-Bank'}]", x.ctx);
  x.scheduleListRetry({message: 'Сервер Alfa-Bank не отвечает.'});
  x.scheduleListRetry({message: ''});
  assert.match(html, /Нет связи с «Alfa-Bank»/);
  assert.match(html, /Повторить сейчас/);
  assert.deepEqual(timers, [10000, 20000], 'backs off: 10 s, then 20 s');
});

test('opening a folder asks for the saved letters first, then the real (delta) list', () => {
  const {loadList, fetches, ctx} = load(['loadList']);
  vm.runInContext("curFolder = '14'; searchQ = ''", ctx);
  fetches.length = 0;
  loadList(true);
  const lists = fetches.filter(f => f.url === '/api/mail' && f.body.action === 'list');
  assert.equal(lists.length, 2);
  assert.equal(lists[0].body.cache_only, true, 'cache first: no Exchange round-trip');
  assert.equal(lists[1].body.cache_only, undefined);
  assert.equal(lists[1].body.folder, '14');
});

test('auto-sync keeps its interval when WebKit holds timers back (native nudge asks «due?»)', () => {
  const {autoSyncDue, ctx} = load(['autoSyncDue']);
  let ticks = 0;
  vm.runInContext('prefs.auto_sync = 2', ctx);
  ctx.__tick = () => ticks++;
  vm.runInContext('autoSyncTick = () => { __tick(); lastTick = Date.now(); }', ctx);
  vm.runInContext('lastTick = Date.now() - 30000', ctx);
  assert.equal(autoSyncDue(), false, '30 s after a tick: not yet');
  vm.runInContext('lastTick = Date.now() - 9 * 60000', ctx);
  assert.equal(autoSyncDue(), true, '9 min late: runs now');
  assert.equal(ticks, 1);
  assert.equal(autoSyncDue(), false, 'and not twice');
  vm.runInContext('prefs.auto_sync = 0; lastTick = 0', ctx);
  assert.equal(autoSyncDue(), false, 'switched off in Settings');
});

test('the last sync time is one for the app, not swapped per account', () => {
  const {snapshot} = load(['snapshot']);
  assert.ok(!('lastSync' in snapshot()));
});

test('conversation: short recipients line — «мне» first, surname + initial, «и ещё N»', () => {
  const {shortRecips, ctx} = load(['shortRecips']);
  vm.runInContext("accounts = [{id: 'main', email: 'me@bank.test'}]; ACCT = 'main'", ctx);
  const p = (name, address) => ({name, address});
  assert.equal(shortRecips({to: [p('Балкаров Ислам Валерьевич', 'b@bank.test'), p('Я', 'me@bank.test')], cc: []}), 'мне, Балкаров И.');
  const many = Array.from({length: 6}, (_, i) => p(`Фамилия${i} Имя`, `u${i}@bank.test`));
  assert.equal(shortRecips({to: many, cc: []}), 'Фамилия0 И., Фамилия1 И., Фамилия2 И. и ещё 3');
});

test('plain-text reply: the quoted history folds, a letter that is only a quote stays open', () => {
  const {foldQuoteText} = load(['foldQuoteText']);
  const folded = foldQuoteText('Коллеги, ок.\n\n-----Original Message-----\nFrom: Иван\nSent: вчера\n\nстарое');
  assert.match(folded, /Коллеги, ок\.<\/pre><details class="aas-q"><summary>Показать историю переписки<\/summary>/);
  assert.match(folded, /-----Original Message-----/);
  const onlyQuote = foldQuoteText('-----Original Message-----\nFrom: Иван\nSent: вчера');
  assert.doesNotMatch(onlyQuote, /<details/);
  assert.doesNotMatch(foldQuoteText('Просто письмо'), /<details/);
  assert.match(foldQuoteText('Да.\nОт: Иван Петров\nОтправлено: пн\nКому: мне'), /<details/);
});

test('one pane below 600px: list until a letter is open, then the letter', () => {
  const {paneFor} = load(['paneFor']);
  assert.equal(paneFor(900, false), 'both');
  assert.equal(paneFor(900, true), 'both');
  assert.equal(paneFor(599, false), 'list');
  assert.equal(paneFor(412, true), 'reader');
});

test('back needs one history entry per opened letter on the narrow screen', () => {
  const {needsBackEntry} = load(['needsBackEntry']);
  assert.equal(needsBackEntry('reader', null), true);          // folded with a letter open
  assert.equal(needsBackEntry('reader', {reading: 1}), false); // already pushed
  assert.equal(needsBackEntry('list', null), false);
  assert.equal(needsBackEntry('both', null), false);
});

test('notification deep link #open=acct:item_id (the id may contain colons)', () => {
  const {parseOpenHash} = load(['parseOpenHash']);
  assert.deepEqual(plain(parseOpenHash('#open=seller%3AaS8%3A1')), {acct: 'seller', id: 'aS8:1'});
  assert.equal(parseOpenHash('#cal'), null);
  assert.equal(parseOpenHash('#open=nocolon'), null);
});

test('new-mail bridge names the account id and the letter, so Android can open it', () => {
  const sent = [];
  const {noteIncoming, ctx} = load(['noteIncoming']);
  ctx.window = {webkit: {messageHandlers: {aasNewMail: {postMessage: v => sent.push(v)}}}};
  vm.runInContext(`accounts = [{id: 'main', name: 'Alfa-Bank'}, {id: 'seller', name: 'Seller'}];`, ctx);
  noteIncoming('seller', [{item_id: 'old', is_read: false}]);
  noteIncoming('seller', [{item_id: 'new1', is_read: false, subject: 'Счёт', from: {name: 'Бухгалтерия'}}, {item_id: 'old'}]);
  assert.equal(sent.length, 1);
  assert.deepEqual(plain(sent[0]), {count: 1, from: 'Бухгалтерия', subject: 'Счёт', preview: '', account: 'Seller',
    acct: 'seller', item_id: 'new1'});
});

test('railGrid: 6 weeks from the Monday on/before the 1st', () => {
  const {railGrid} = load(['railGrid']);
  const g = railGrid(new Date(2026, 8, 1));  // 1 Sep 2026 is a Tuesday
  assert.equal(g.length, 42);
  assert.equal(g[0].getDay(), 1);
  assert.equal(g[0].getDate(), 31);
  assert.equal(g[1].getDate(), 1);
});

test('railAgenda: all-day first, timed by start, free gaps ≥1h, cancelled neither busy nor gap-splitting', () => {
  const {railAgenda} = load(['railAgenda']);
  const at = (h, m = 0) => new Date(2026, 8, 28, h, m);
  const ev = (subject, s, e, x = {}) => ({subject, s, e, ...x});
  const list = [
    ev('late', at(14), at(14, 30)),
    ev('early', at(9, 30), at(10)),
    ev('now', at(11), at(11, 45)),
    ev('gone', at(12, 30), at(13), {meeting_status: 'cancelled'}),
    ev('holiday', new Date(2026, 8, 28), new Date(2026, 8, 29), {is_all_day: true}),
    ev('tomorrow', new Date(2026, 8, 29, 10), new Date(2026, 8, 29, 11)),
  ];
  const out = plain(railAgenda(list, new Date(2026, 8, 28), at(11, 10)));
  assert.deepEqual(out.map(x => x.gap ?? x.e.subject), ['holiday', 'early', 60, 'now', 'gone', 135, 'late']);
  assert.deepEqual(out.filter(x => x.now).map(x => x.e.subject), ['now']);
  assert.deepEqual(plain(railAgenda(list, new Date(2026, 8, 27))), []);
});

test('stripRefresh drops meta refresh in any spelling, keeps other meta and text', () => {
  const {stripRefresh} = load(['stripRefresh']);
  assert.equal(stripRefresh('<META HTTP-EQUIV="Refresh" CONTENT="0;url=https://x">a'), 'a');
  assert.equal(stripRefresh("<meta content='1;url=https://x' http-equiv=refresh>b"), 'b');
  assert.equal(stripRefresh('<meta http-equiv = " refresh" content="0">c'), 'c');
  assert.equal(stripRefresh('<meta charset="utf-8"><p>refresh me</p>'), '<meta charset="utf-8"><p>refresh me</p>');
});

test('scheduler: fbSlice, ranking, constraints, no data, past, too long', () => {
  const {fbSlice, rankSlots, explainSlot} = load(['fbSlice', 'rankSlots', 'explainSlot']);
  const day = w => '0'.repeat(18) + w;   // 09:00 is code 18; working day 9..12 = 6 half-hours
  assert.deepEqual(plain(fbSlice('0'.repeat(48) + day('21'), 1, 9, 10)), ['2', '1']);
  assert.deepEqual(plain(fbSlice('', 0, 9, 10)), ['4', '4']);
  const A = {address: 'a@x', role: 'req', fb: day('220000')};
  const B = {address: 'b@x', role: 'req', fb: day('000010')};
  const O = {address: 'o@x', role: 'opt', fb: day('002200')};
  const base = {days: 1, dur: 2, ws: 9, we: 12, k: 6};
  const r = plain(rankSlots({...base, people: [A, B, O]}));
  assert.deepEqual(r.map(o => o.s), [2, 4, 0]);            // scores 2, 3, 10; 3 and 1 overlap picks
  assert.deepEqual(r[0].optBusy, ['o@x']);
  assert.deepEqual(r[1].reqTent, ['b@x']);
  assert.deepEqual(r[2].reqBusy, ['a@x']);
  const c = plain(rankSlots({...base, people: [A, B, O], cons: {not_before_10: true}}));
  assert.deepEqual(c.find(o => o.s === 0).pen, ['раньше 10:00']);
  const N = {address: 'n@x', role: 'req', fb: ''};
  const nd = plain(rankSlots({...base, people: [A, B, O, N]}));
  assert.deepEqual(nd.map(o => o.s), [2, 4, 0]);            // no data is not busy
  assert.deepEqual(nd[0].noData, ['n@x']);
  const R = {address: 'r@x', role: 'room', fb: day('002222')};
  const rm = plain(rankSlots({...base, people: [A, B, O, R]}));
  assert.ok(rm.find(o => o.s === 2).pen.includes('переговорка занята'));
  const past = plain(rankSlots({...base, people: [A, B, O], past: (d, s) => s < 3}));
  assert.deepEqual(past.map(o => o.s), [4]);
  assert.deepEqual(plain(rankSlots({...base, dur: 7, people: [A]})), []);
  assert.equal(explainSlot({reqBusy: ['a', 'b', 'c', 'd'], reqTent: [], optBusy: ['o'], oof: [], noData: [], pen: ['раньше 10:00']}, x => x.toUpperCase()),
    'Заняты обязательные: A, B, C и ещё 1; опциональные заняты: O; раньше 10:00');
});

test('agenda: numbered text block, empty rows skipped', () => {
  const {agendaText, AGENDA_TPL} = load(['agendaText', 'AGENDA_TPL']);
  assert.equal(agendaText([{t: 'Что получилось', m: 15, who: 'Соколова М.'}, {t: ' ', m: 5}, {t: 'Итоги', m: 0}]),
    'Повестка:\n1. Что получилось — 15 мин (Соколова М.)\n2. Итоги');
  assert.equal(agendaText([]), '');
  assert.equal(AGENDA_TPL.retro.length, 4);
});

test('scheduler: one person away outranks nobody, however many are tentative', () => {
  const {rankSlots} = load(['rankSlots']);
  const day = w => '0'.repeat(18) + w;
  const tent = Array.from({length: 5}, (_, i) => ({address: `t${i}@x`, role: 'req', fb: day('11' + '00')}));
  const away = {address: 'a@x', role: 'req', fb: day('00' + '33')};
  const r = plain(rankSlots({people: [...tent, away], days: 1, dur: 2, ws: 9, we: 11, k: 2}));
  assert.deepEqual(r.map(o => o.s), [0, 2]);   // 5 tentative at 09:00 beats one away at 10:00
});

test('scheduler: meeting length in half-hours rounds up, never down', () => {
  const {halfHours} = load(['halfHours']);
  assert.deepEqual([15, 30, 45, 60, 0].map(m => halfHours(m * 6e4)), [1, 1, 2, 2, 1]);
});

test('joinURL: dedicated link field, then location, then body; a meeting host wins anywhere', () => {
  const {joinURL} = load(['joinURL']);
  assert.equal(joinURL('Переговорная 5', 'https://wiki.test/a', 'https://alfaseller.ktalk.ru/vladena'), 'https://alfaseller.ktalk.ru/vladena');
  assert.equal(joinURL('https://docs.test/x', '', 'https://zoom.us/j/1'), 'https://zoom.us/j/1');
  assert.equal(joinURL('https://alfabank.ktalk.ru/a', '', 'https://intranet.test/room'), 'https://alfabank.ktalk.ru/a');
  assert.equal(joinURL('', '', 'https://intranet.test/room'), 'https://intranet.test/room');
  assert.equal(joinURL('Room', 'no links'), '');
});

test('external participants: outside the account domain (subdomains are ours)', () => {
  const {isExternal} = load(['isExternal']);
  assert.equal(isExternal('kamil@advantshop.net', 'OBabakaeva@alfabank.ru'), true);
  assert.equal(isExternal('ASChekanov@AlfaBank.ru', 'OBabakaeva@alfabank.ru'), false);
  assert.equal(isExternal('x@msk.alfabank.ru', 'me@alfabank.ru'), false);
  assert.equal(isExternal('Иванов', 'me@alfabank.ru'), false);
  assert.equal(isExternal('a@x.ru', ''), false);
});

test('Seller → Alfa-Bank twin: same login first, else the one full-name match', () => {
  const {pickTwin} = load(['pickTwin']);
  const vg = {name: 'Петрова Анна Сергеевна', address: 'ASPetrova@alfabank.ru'};
  const other = {name: 'Петрова Мария', address: 'MPetrova@alfabank.ru'};
  assert.equal(pickTwin('aspetrova@alfaseller.ru', '', [other, vg], 'alfabank.ru').address, 'ASPetrova@alfabank.ru');
  // Login differs: the name decides, word order and ё do not matter.
  const vg2 = {name: 'Петрова Анна', address: 'Petrova.A@alfabank.ru'};
  assert.equal(pickTwin('apetrova@alfaseller.ru', 'Анна Петрова', [other, vg2], 'alfabank.ru').address, 'Petrova.A@alfabank.ru');
  assert.equal(pickTwin('a@alfaseller.ru', 'Фёдоров Иван', [{name: 'Федоров Иван', address: 'IF@alfabank.ru'}], 'alfabank.ru').address, 'IF@alfabank.ru');
  // Two namesakes, no name, or only non-bank hits: no guess.
  assert.equal(pickTwin('x@alfaseller.ru', 'Петрова Мария', [other, {...other, address: 'MPetrova2@alfabank.ru'}], 'alfabank.ru'), null);
  assert.equal(pickTwin('x@alfaseller.ru', '', [vg], 'alfabank.ru'), null);
  assert.equal(pickTwin('aspetrova@alfaseller.ru', '', [{name: 'В', address: 'aspetrova@alfaseller.ru'}], 'alfabank.ru'), null);
});

test('meeting recurrence: form choice → create params and words', () => {
  const {repeatParams, repeatText} = load(['repeatParams', 'repeatText']);
  const P = r => plain(repeatParams(r));
  assert.equal(repeatParams({kind: ''}), null);
  assert.deepEqual(P({kind: 'weekdays'}), {repeat: 'weekly', repeat_days: ['mon', 'tue', 'wed', 'thu', 'fri']});
  assert.deepEqual(P({kind: 'daily', end: 'count', count: '5'}), {repeat: 'daily', repeat_count: 5});
  assert.deepEqual(P({kind: 'custom', unit: 'weekly', n: '2', days: ['fri', 'mon'], end: 'until', until: '2026-12-31'}),
    {repeat: 'weekly', repeat_interval: 2, repeat_days: ['mon', 'fri'], repeat_until: '2026-12-31T23:59'});
  assert.deepEqual(P({kind: 'custom', unit: 'monthly', n: '0', days: ['mon']}), {repeat: 'monthly'}, 'days only for weekly; interval ≥ 1');
  assert.equal(repeatText(repeatParams({kind: 'weekdays', end: 'count', count: '10'})), 'По будням (пн–пт), 10 раз');
  assert.equal(repeatText(repeatParams({kind: 'custom', unit: 'weekly', n: '2', days: ['tue', 'thu']})), 'Каждые 2 недели: вт, чт, без окончания');
  assert.equal(repeatText(repeatParams({kind: 'yearly', end: 'until', until: '2030-01-15'})), 'Каждый год, до 15.01.2030');
});

test('delete from the keyboard: the next letter below opens, else the one above', () => {
  const {keyAfterRemoval} = load(['keyAfterRemoval']);
  const order = ['a', 'b', 'c', 'd'];
  assert.equal(keyAfterRemoval(order, new Set(['b'])), 'c');
  assert.equal(keyAfterRemoval(order, new Set(['b', 'c'])), 'd');
  assert.equal(keyAfterRemoval(order, new Set(['d'])), 'c', 'last row: the one above');
  assert.equal(keyAfterRemoval(order, new Set(['c', 'd'])), 'b');
  assert.equal(keyAfterRemoval(['a'], new Set(['a'])), null);
});

test('calendar drag: only own movable meetings, same length at the new time', () => {
  const {canDragEvent, shiftEvent} = load(['canDragEvent', 'shiftEvent']);
  const me = 'me@bank.test';
  assert.equal(canDragEvent({item_id: '1', response_type: 'organizer', attendees: [{address: 'x@bank.test'}]}, me), true);
  assert.equal(canDragEvent({item_id: '1', attendees: []}, me), true, 'a plain appointment');
  assert.equal(canDragEvent({item_id: '1', organizer: {address: 'boss@bank.test'}, response_type: 'accepted'}, me), false);
  assert.equal(canDragEvent({item_id: '1', response_type: 'organizer', is_recurring: true}, me), false, 'Exchange refuses to move one occurrence');
  assert.equal(canDragEvent({item_id: '1', response_type: 'organizer', is_all_day: true}, me), false);
  assert.equal(canDragEvent({item_id: '1', response_type: 'organizer', meeting_status: 'cancelled'}, me), false);
  const s = new Date(2026, 9, 1, 10, 0), e = new Date(2026, 9, 1, 11, 30);
  const to = shiftEvent(s, e, new Date(2026, 9, 2), 14 * 60 + 15);
  assert.equal(to.start.getDate(), 2); assert.equal(to.start.getHours(), 14); assert.equal(to.start.getMinutes(), 15);
  assert.equal(to.end - to.start, 90 * 60e3);
});

test('cancelled meetings: by status or by a server-prefixed subject', () => {
  const {isCancelled, evTitle} = load(['isCancelled', 'evTitle']);
  assert.equal(isCancelled({meeting_status: 'cancelled', subject: 'Backend гильдия'}), true, 'series exception: plain subject');
  assert.equal(isCancelled({meeting_status: 'meeting', subject: 'Отменено: Daily'}), true);
  assert.equal(isCancelled({subject: 'Canceled: B2B IT leads sync'}), true);
  assert.equal(isCancelled({meeting_status: 'meeting', subject: 'Демо отменённых фич'}), false);
  assert.equal(evTitle({subject: 'FW: Отменено: Daily'}), 'Отменено: Daily');
});

test('own meetings: an alias in Settings does not hide them; others get a reason', () => {
  const {myAddrs, dragBlock} = load(['myAddrs', 'dragBlock']);
  const list = [{_acct: 'main', response_type: 'organizer', organizer: {address: 'IIvanov@bank.test'}}];
  const mine = myAddrs('main', list);
  assert.equal(mine.has('iivanov@bank.test'), true, 'learned from a meeting the server marks as mine');
  // Exchange answers «none» for some own meetings (Lunch): the organizer address decides.
  assert.equal(dragBlock({item_id: '1', response_type: 'none', organizer: {address: 'iivanov@BANK.test'}, attendees: [{address: 'x@bank.test'}]}, mine), '');
  assert.match(dragBlock({item_id: '1', response_type: 'accepted', organizer: {address: 'boss@bank.test'}}, mine), /организатор/);
  assert.match(dragBlock({item_id: '1', response_type: 'organizer', meeting_status: 'cancelled'}, mine), /отменена/);
  assert.match(dragBlock({item_id: '1', response_type: 'organizer', is_recurring: true}, mine), /Outlook или OWA/);
});

test('people field: the next name is searched even without a comma after «…>»', () => {
  const {peopleToken} = load(['peopleToken']);
  const P = v => plain(peopleToken(v));
  assert.equal(P('Мамаджанов Мирзохид <MMamajanov@alfabank.ru> гре').token, 'гре');
  assert.equal(P('Мамаджанов Мирзохид <MMamajanov@alfabank.ru> гре').pre, 'Мамаджанов Мирзохид <MMamajanov@alfabank.ru>');
  assert.equal(P('a@x.ru, гре').token, 'гре');
  assert.equal(P('"Иванов, Иван" <i@x.ru>, пет').token, 'пет', 'a comma inside quotes is not a separator');
  assert.equal(P('Иванов <i@x').token, 'Иванов <i@x', 'an unfinished address is still being typed');
});

test('mail selection: Shift ranges in list order, either direction', () => {
  const {keyRange} = load(['keyRange']);
  const order = ['a', 'b', 'c', 'd', 'e'];
  assert.deepEqual(plain(keyRange(order, 'b', 'd')), ['b', 'c', 'd']);
  assert.deepEqual(plain(keyRange(order, 'd', 'b')), ['b', 'c', 'd'], 'Shift+↑ selects upwards');
  assert.deepEqual(plain(keyRange(order, 'c', 'c')), ['c']);
  assert.deepEqual(plain(keyRange(order, 'gone', 'e')), ['e'], 'an anchor that left the list: just the row');
});

test('letter editor: pasted styles and links keep only what mail clients render', () => {
  const {rteStyle, rteHref, rteTableHtml} = load(['rteStyle', 'rteHref', 'rteTableHtml']);
  assert.equal(rteStyle('mso-bidi-font-size:11pt; COLOR: #c00000;font-family:"Calibri";background:url(x);font-size: 14pt'),
    'color:#c00000;font-size:14pt');
  assert.equal(rteStyle('border:1px solid #a6a6a6;padding:4px 8px;position:absolute;width:expression(1)'), 'border:1px solid #a6a6a6;padding:4px 8px');
  assert.equal(rteHref(' https://ya.ru/a?b=1 '), 'https://ya.ru/a?b=1');
  assert.equal(rteHref('mailto:a@b.ru'), 'mailto:a@b.ru');
  assert.equal(rteHref('javascript:alert(1)'), '');
  assert.equal(rteHref('https://x" onclick="y'), '');
  const t = rteTableHtml(2, 3);
  assert.equal(t.match(/<tr>/g).length, 2);
  assert.equal(t.match(/<td /g).length, 6);
});
