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
    localStorage: {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v))},
    fetch: (url, opts) => { fetches.push({url, opts, body: JSON.parse(opts.body)}); return new Promise(() => {}); },
  });
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
  applyTheme('bogus');
  assert.equal(html.dataset.theme, undefined, 'unknown → follow the system');
  assert.deepEqual(posted, ['dark', 'navy-orange', 'eclipse-almond-light', 'system']);
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

test('updates: «auto» schedules a check, «manual» schedules nothing', () => {
  const timers = [];
  const {armUpdates, ctx} = load(['armUpdates']);
  ctx.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };
  vm.runInContext(`prefs.update_mode = 'manual'`, ctx); armUpdates();
  assert.deepEqual(timers, [], 'manual: no background checks');
  vm.runInContext(`prefs.update_mode = 'auto'`, ctx); armUpdates();
  assert.deepEqual(timers, [15000]);
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
