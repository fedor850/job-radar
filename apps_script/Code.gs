/**
 * Job Radar — bridge between the bot and Google (tracker spreadsheet, Gmail, Calendar).
 * Full guide: docs/google-bridge.md. The sheet and statuses are in Russian (the bot's UI language).
 *
 * Install (once):
 *  1) script.google.com → New project → replace Code.gs with this file → Save.
 *     Easiest: `python3 tools/render_apps_script.py > Code.gs.local` prints this file with your secret filled in.
 *  2) Pick the function "setup" at the top → Run → allow access (Advanced → Go to project → Allow).
 *  3) Deploy → New deployment → type "Web app": Execute as: Me; Who has access: Anyone → Deploy →
 *     copy the web-app URL into secrets.env (APPS_SCRIPT_URL).
 */

var SECRET = '__APPS_SCRIPT_SECRET__';           // shared secret with the bot (= APPS_SCRIPT_SECRET in secrets.env)
var SHEET_NAME = 'Job Radar — трекер откликов';
var TZ = 'Europe/Vienna';

var TRACKER = 'Отклики';
var LOG = 'Лента';
var DASH = 'Сводка';

var TRACKER_HEADERS = ['ID', 'Добавлено', 'Статус', 'Компания', 'Должность', 'Оценка', 'Локация', 'Часы',
  'Зарплата (20 ч)', 'Источник', 'Ссылка для отклика', 'Объявление', 'Дата отклика', 'Follow-up',
  'Последнее письмо', 'Контакт', 'Заметки'];
var TRACKER_KEYS = ['tracker_id', 'added', 'status', 'company', 'title', 'score', 'location', 'hours',
  'salary', 'source', 'apply_url', 'url', 'applied_at', 'followup_due', 'last_email', 'contact', 'notes'];

var LOG_HEADERS = ['Дата', 'Оценка', 'Компания', 'Должность', 'Источник', 'Ссылка', 'Почему подходит', 'Риски', 'ID'];

var STATUSES = ['⭐ Сохранено', '✅ Подался', '📨 Подтверждено', '📝 Тест/задание', '🎤 Интервью', '🏆 Оффер',
  '❌ Отказ', '🔕 Нет ответа', '↩️ Отозвал'];
var STATUS_COLORS = {
  '⭐ Сохранено': '#fff8e1', '✅ Подался': '#e3f2fd', '📨 Подтверждено': '#e8eaf6', '📝 Тест/задание': '#f3e5f5',
  '🎤 Интервью': '#e8f5e9', '🏆 Оффер': '#c8e6c9', '❌ Отказ': '#ffebee', '🔕 Нет ответа': '#f5f5f5',
  '↩️ Отозвал': '#eeeeee'
};

// ------------------------------------------------------------------ entry points

function doPost(e) {
  var out;
  try {
    var req = JSON.parse(e.postData.contents);
    if (req.secret !== SECRET) throw new Error('bad secret');
    var lock = LockService.getScriptLock();
    lock.waitLock(25000);
    try {
      out = {ok: true, result: dispatch_(req.action, req)};
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    out = {ok: false, error: String(err && err.message ? err.message : err)};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return ContentService.createTextOutput(JSON.stringify({ok: true, result: 'Job Radar bridge is running'}))
    .setMimeType(ContentService.MimeType.JSON);
}

function dispatch_(action, req) {
  switch (action) {
    case 'ping': return {sheet_url: ss_().getUrl(), me: Session.getEffectiveUser().getEmail()};
    case 'setup': return setup();
    case 'log_job': return logJob_(req.row);
    case 'upsert_tracker': return upsertTracker_(req.row);
    case 'get_tracker': return getTracker_();
    case 'get_emails': return getEmails_(req.since || 0, req.max || 40);
    case 'get_job_alerts': return getJobAlerts_(req.since || 0, req.max || 30);
    case 'create_event': return createEvent_(req);
    default: throw new Error('unknown action ' + action);
  }
}

// ------------------------------------------------------------------ setup

function setup() {
  var ss = ss_();
  var tr = sheet_(ss, TRACKER, TRACKER_HEADERS);
  var lg = sheet_(ss, LOG, LOG_HEADERS);
  formatTracker_(tr);
  formatLog_(lg);
  buildDashboard_(ss);
  var def = ss.getSheetByName('Sheet1') || ss.getSheetByName('Лист1');
  if (def && ss.getSheets().length > 1) ss.deleteSheet(def);
  // touch Gmail & Calendar so that all permissions are requested at once
  GmailApp.search('newer_than:1d', 0, 1);
  CalendarApp.getDefaultCalendar().getName();
  Logger.log('Таблица: ' + ss.getUrl());
  return {sheet_url: ss.getUrl()};
}

function ss_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SHEET_ID');
  if (id) {
    try { return SpreadsheetApp.openById(id); } catch (e) { /* recreate below */ }
  }
  var ss = SpreadsheetApp.create(SHEET_NAME);
  ss.setSpreadsheetTimeZone(TZ);
  props.setProperty('SHEET_ID', ss.getId());
  return ss;
}

function sheet_(ss, name, headers) {
  var sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  var first = sh.getRange(1, 1, 1, headers.length).getValues()[0];
  if (first.join('') === '') {
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
  sh.setFrozenRows(1);
  sh.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#263238').setFontColor('#ffffff')
    .setWrap(true).setVerticalAlignment('middle');
  return sh;
}

function formatTracker_(sh) {
  var widths = [70, 95, 140, 170, 260, 60, 110, 90, 95, 130, 220, 220, 95, 95, 220, 160, 220];
  for (var i = 0; i < widths.length; i++) sh.setColumnWidth(i + 1, widths[i]);
  var rule = SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(true).build();
  sh.getRange(2, 3, Math.max(sh.getMaxRows() - 1, 1), 1).setDataValidation(rule);
  var rules = [];
  var range = sh.getRange(2, 1, Math.max(sh.getMaxRows() - 1, 1), TRACKER_HEADERS.length);
  for (var s in STATUS_COLORS) {
    rules.push(SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=$C2="' + s + '"')
      .setBackground(STATUS_COLORS[s]).setRanges([range]).build());
  }
  sh.setConditionalFormatRules(rules);
  sh.getRange('B:B').setNumberFormat('dd.mm.yyyy hh:mm');
  sh.getRange('M:N').setNumberFormat('dd.mm.yyyy');
  if (!sh.getFilter()) sh.getRange(1, 1, sh.getMaxRows(), TRACKER_HEADERS.length).createFilter();
}

function formatLog_(sh) {
  var widths = [110, 60, 170, 280, 150, 240, 380, 300, 70];
  for (var i = 0; i < widths.length; i++) sh.setColumnWidth(i + 1, widths[i]);
  sh.getRange('A:A').setNumberFormat('dd.mm.yyyy hh:mm');
  sh.getRange('G:H').setWrap(true);
}

function buildDashboard_(ss) {
  var sh = ss.getSheetByName(DASH);
  if (!sh) sh = ss.insertSheet(DASH, 0);
  sh.clear();
  var T = "'" + TRACKER + "'";
  var L = "'" + LOG + "'";
  var rows = [
    ['Job Radar — сводка', ''],
    ['', ''],
    ['Воронка откликов', 'Кол-во'],
    ['⭐ Сохранено', '=COUNTIF(' + T + '!C:C,"⭐ Сохранено")'],
    ['Откликов всего', '=COUNTA(' + T + '!C2:C)-COUNTIF(' + T + '!C:C,"⭐ Сохранено")'],
    ['📨 Подтверждено', '=COUNTIF(' + T + '!C:C,"📨 Подтверждено")'],
    ['📝 Тест/задание', '=COUNTIF(' + T + '!C:C,"📝 Тест/задание")'],
    ['🎤 Интервью', '=COUNTIF(' + T + '!C:C,"🎤 Интервью")'],
    ['🏆 Оффер', '=COUNTIF(' + T + '!C:C,"🏆 Оффер")'],
    ['❌ Отказ', '=COUNTIF(' + T + '!C:C,"❌ Отказ")'],
    ['🔕 Нет ответа', '=COUNTIF(' + T + '!C:C,"🔕 Нет ответа")'],
    ['Доля ответов (тест/интервью/оффер/отказ)', '=IFERROR((B7+B8+B9+B10)/B5,0)'],
    ['Доля приглашений (тест/интервью/оффер)', '=IFERROR((B7+B8+B9)/B5,0)'],
    ['', ''],
    ['Активность', ''],
    ['Откликов за 7 дней', '=COUNTIFS(' + T + '!M:M,">="&(TODAY()-7))'],
    ['Откликов за 30 дней', '=COUNTIFS(' + T + '!M:M,">="&(TODAY()-30))'],
    ['Вакансий прислано за 7 дней', '=COUNTIFS(' + L + '!A:A,">="&(TODAY()-7))'],
    ['Вакансий прислано всего', '=COUNTA(' + L + '!A2:A)'],
    ['', ''],
    ['Вакансии по источникам', '']
  ];
  sh.getRange(1, 1, rows.length, 2).setValues(rows);
  sh.getRange(rows.length + 1, 1).setFormula(
    '=IFERROR(QUERY(' + L + '!A:E,"select E, count(A) where E is not null group by E order by count(A) desc label count(A) \'Вакансий\'",1),"")');
  sh.getRange('A1').setFontSize(16).setFontWeight('bold');
  sh.getRange('A3:B3').setFontWeight('bold').setBackground('#eceff1');
  sh.getRange('A15:B15').setFontWeight('bold').setBackground('#eceff1');
  sh.getRange('A21:B21').setFontWeight('bold').setBackground('#eceff1');
  sh.getRange('B12:B13').setNumberFormat('0%');
  sh.setColumnWidth(1, 330);
  sh.setColumnWidth(2, 110);
}

// ------------------------------------------------------------------ data

function logJob_(row) {
  var sh = sheet_(ss_(), LOG, LOG_HEADERS);
  sh.appendRow([new Date(row.date || new Date()), row.score, row.company, row.title, row.source, row.url,
    row.why || '', row.risks || '', row.job_id]);
  return true;
}

function upsertTracker_(row) {
  var sh = sheet_(ss_(), TRACKER, TRACKER_HEADERS);
  var data = sh.getDataRange().getValues();
  var id = String(row.tracker_id || '');
  var rowIdx = -1;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === id && id) { rowIdx = i + 1; break; }
  }
  if (rowIdx < 0) {
    if (!id) id = 'm' + new Date().getTime();
    var values = TRACKER_KEYS.map(function (k) {
      if (k === 'tracker_id') return id;
      if (k === 'added') return row.added ? new Date(row.added) : new Date();
      return toCell_(k, row[k]);
    });
    sh.appendRow(values);
    return {tracker_id: id, created: true};
  }
  for (var k in row) {
    var col = TRACKER_KEYS.indexOf(k);
    if (col <= 0 || row[k] === null || row[k] === undefined) continue;
    if (k === 'notes' && row[k]) {
      var old = sh.getRange(rowIdx, col + 1).getValue();
      sh.getRange(rowIdx, col + 1).setValue(old ? old + '\n' + row[k] : row[k]);
      continue;
    }
    sh.getRange(rowIdx, col + 1).setValue(toCell_(k, row[k]));
  }
  return {tracker_id: id, created: false};
}

function toCell_(k, v) {
  if (v === null || v === undefined) return '';
  if ((k === 'applied_at' || k === 'followup_due') && v) {
    var d = new Date(v);
    return isNaN(d.getTime()) ? v : d;
  }
  return v;
}

function getTracker_() {
  var sh = sheet_(ss_(), TRACKER, TRACKER_HEADERS);
  var data = sh.getDataRange().getValues();
  var out = [];
  for (var i = 1; i < data.length; i++) {
    var r = data[i];
    if (!r[2] && !r[3] && !r[4]) continue;  // empty row
    if (!r[0]) {  // a row added by hand → give it an ID
      r[0] = 'm' + new Date().getTime() + i;
      sh.getRange(i + 1, 1).setValue(r[0]);
    }
    var o = {};
    for (var c = 0; c < TRACKER_KEYS.length; c++) {
      var v = r[c];
      if (v instanceof Date) v = Utilities.formatDate(v, TZ, "yyyy-MM-dd'T'HH:mm:ssXXX");
      o[TRACKER_KEYS[c]] = v;
    }
    out.push(o);
  }
  return out;
}

// ------------------------------------------------------------------ Gmail

function getEmails_(sinceEpoch, max) {
  var me = Session.getEffectiveUser().getEmail().toLowerCase();
  var since = Math.floor(Number(sinceEpoch) || (Date.now() / 1000 - 2 * 86400));
  var q = 'after:' + since + ' -in:sent -in:chats -in:spam -category:promotions -category:social -category:forums';
  var threads = GmailApp.search(q, 0, Math.min(max, 50));
  var out = [];
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      var d = m.getDate();
      if (d.getTime() / 1000 < since) return;
      var from = m.getFrom();
      if (from.toLowerCase().indexOf(me) >= 0) return;
      out.push({
        id: m.getId(), thread_id: t.getId(), date: Utilities.formatDate(d, TZ, "yyyy-MM-dd'T'HH:mm:ssXXX"),
        epoch: Math.floor(d.getTime() / 1000), from: from, to: m.getTo(), subject: m.getSubject(),
        body: (m.getPlainBody() || '').substring(0, 6000), link: t.getPermalink()
      });
    });
  });
  return out;
}

// LinkedIn Job Alert e-mails (any Gmail category: they usually land in "Social"/"Promotions", which getEmails_
// skips), full plain body — the bot parses the job links itself.
function getJobAlerts_(sinceEpoch, max) {
  var since = Math.floor(Number(sinceEpoch) || (Date.now() / 1000 - 2 * 86400));
  var q = 'from:(jobalerts-noreply@linkedin.com OR jobs-listings@linkedin.com) after:' + since;
  var threads = GmailApp.search(q, 0, Math.min(max, 50));
  var out = [];
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      var d = m.getDate();
      if (d.getTime() / 1000 < since) return;
      out.push({
        id: m.getId(), thread_id: t.getId(), date: Utilities.formatDate(d, TZ, "yyyy-MM-dd'T'HH:mm:ssXXX"),
        epoch: Math.floor(d.getTime() / 1000), from: m.getFrom(), subject: m.getSubject(),
        body: (m.getPlainBody() || '').substring(0, 60000)
      });
    });
  });
  return out;
}

// ------------------------------------------------------------------ Calendar

function createEvent_(req) {
  var start = new Date(req.start);
  if (isNaN(start.getTime())) throw new Error('bad start');
  var end = new Date(start.getTime() + (Number(req.minutes) || 45) * 60000);
  var ev = CalendarApp.getDefaultCalendar().createEvent(req.title || 'Интервью', start, end, {
    location: req.location || '', description: req.description || ''
  });
  ev.addPopupReminder(60);
  ev.addPopupReminder(24 * 60);
  return {event_id: ev.getId()};
}
