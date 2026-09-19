/** Investment Strategy notification controller for a bound Google Sheet. */
const SHEETS = {SUBSCRIPTIONS: '알림 전략', PERSONAL: '개인 전략', EVENTS: '알림 이력', LOG: '실행 로그'};
const LEGACY_PRIVATE_SHEET = '비공개 전략';
const KST = 'Asia/Seoul';

function spreadsheet_() {
  const active = SpreadsheetApp.getActiveSpreadsheet();
  if (active) return active;
  const id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  return id ? SpreadsheetApp.openById(id) : null;
}

function requireSpreadsheet_() {
  const spreadsheet = spreadsheet_();
  if (!spreadsheet) throw new Error('스프레드시트에 바인딩하거나 Script Properties에 SPREADSHEET_ID를 설정하세요.');
  return spreadsheet;
}

function setupSpreadsheet() {
  const spreadsheet = requireSpreadsheet_();
  const legacy = spreadsheet.getSheetByName(LEGACY_PRIVATE_SHEET);
  if (legacy && !spreadsheet.getSheetByName(SHEETS.PERSONAL)) legacy.setName(SHEETS.PERSONAL);
  ensureSheet_(spreadsheet, SHEETS.SUBSCRIPTIONS,
    ['알림', '전략 ID', '전략명', '마지막 평가일', '상태']);
  ensureSheet_(spreadsheet, SHEETS.PERSONAL,
    ['전략 ID', '전략 YAML']);
  ensureSheet_(spreadsheet, SHEETS.EVENTS,
    ['발송 시각', '이벤트 키', '시장 기준일', '전략 ID', '전략명', '상태', '사유', '실행 일수', '목표 비중', '결과', '알림 유형', '시장 요약']);
  ensureSheet_(spreadsheet, SHEETS.LOG,
    ['실행 시각', '평가 전략 수', '발송 건수', '결과', '상세']);
  const subscriptions = spreadsheet.getSheetByName(SHEETS.SUBSCRIPTIONS);
  subscriptions.getRange('A2:A').insertCheckboxes();
  subscriptions.setFrozenRows(1);
  Object.values(SHEETS).forEach(name => spreadsheet.getSheetByName(name).setFrozenRows(1));
}

function installTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(trigger => trigger.getHandlerFunction() === 'runNotificationCheck')
    .forEach(trigger => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger('runNotificationCheck').timeBased().everyMinutes(10).create();
}

function runNotificationCheck() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    if (!withinNotificationWindow_()) {
      logRun_(0, 0, 'SKIPPED_OUTSIDE_WINDOW', '설정된 알림 시간대 밖입니다.');
      return;
    }
    const subscriptions = enabledSubscriptions_();
    if (!subscriptions.length) {
      logRun_(0, 0, 'SKIPPED_NO_SUBSCRIPTIONS', '알림 전략 시트에서 체크된 전략이 없습니다.');
      return;
    }
    const evaluations = requestEvaluations_(subscriptions);
    let sent = 0;
    evaluations.forEach(evaluation => {
      updateSubscription_(evaluation);
      let alerts = Array.isArray(evaluation.alerts) ? evaluation.alerts.slice() : [];
      if (!alerts.length && evaluation.rebalance_required) alerts.push({...evaluation, type: 'REBALANCE'});
      alerts = alerts.filter(alert => !eventAlreadySent_(eventKey_(alert)));
      const summary = scheduledSummaryDue_(evaluation.scheduled_summary);
      if (summary && !eventAlreadySent_(eventKey_(summary))) {
        const sameDate = alerts.slice().reverse().find(alert => alert.market_data_at === summary.market_data_at);
        if (sameDate) {
          sameDate.includes_summary = true;
          sameDate.coalesced_summary = summary;
        } else {
          alerts.push(summary);
        }
      }
      alerts.forEach(alert => {
        sendTelegram_(messageFor_(alert));
        recordEvent_(alert, 'SENT');
        if (alert.coalesced_summary) recordEvent_(alert.coalesced_summary, 'COALESCED');
        sent++;
      });
    });
    logRun_(subscriptions.length, sent, 'SUCCESS', '');
  } catch (error) {
    logRun_(0, 0, 'FAILED', error.message || String(error));
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function sendTestMessage() {
  try {
    sendTelegram_('[투자 전략] Telegram 알림 연결을 확인했습니다.');
    logRun_(0, 1, 'TEST_SUCCESS', 'Telegram 테스트 메시지를 전송했습니다.');
    Logger.log('Telegram 테스트 메시지 전송 성공');
  } catch (error) {
    logRun_(0, 0, 'TEST_FAILED', error.message || String(error));
    Logger.log(`Telegram 테스트 메시지 전송 실패: ${error.message || String(error)}`);
    throw error;
  }
}

// Sends the current market briefing layout immediately, without changing the
// Worker-side notification snapshot or waiting for the configured schedule.
function sendScheduledSummaryTest() {
  try {
    const subscriptions = enabledSubscriptions_();
    if (!subscriptions.length) throw new Error('알림 전략 시트에서 테스트할 전략을 하나 이상 선택하세요.');
    const evaluations = requestPreviewWithSeed_(subscriptions);
    let sent = 0;
    evaluations.forEach(evaluation => {
      const summary = evaluation.scheduled_summary;
      if (!summary) return;
      sendTelegram_(messageFor_({...summary, test: true}));
      recordEvent_({...summary, test: true}, 'TEST');
      sent++;
    });
    if (!sent) throw new Error('테스트할 시장 브리핑을 만들지 못했습니다.');
    logRun_(subscriptions.length, sent, 'SUMMARY_TEST_SUCCESS', '현재 시장 기준의 정기 브리핑 테스트 전송');
  } catch (error) {
    logRun_(0, 0, 'SUMMARY_TEST_FAILED', error.message || String(error));
    throw error;
  }
}

function requestPreviewWithSeed_(subscriptions) {
  try {
    return requestEvaluations_(subscriptions, true);
  } catch (error) {
    const message = String(error?.message || error);
    if (!message.includes('notification seed is required:')) throw error;
    initializeNotificationStates();
    return requestEvaluations_(subscriptions, true);
  }
}

function initializeNotificationStates() {
  const subscriptions = enabledSubscriptions_();
  if (!subscriptions.length) throw new Error('알림 전략 시트에서 초기화할 전략을 체크하세요.');
  const settings = PropertiesService.getScriptProperties();
  const url = requiredProperty_(settings, 'EVALUATION_API_URL').replace(/\/notification-evaluations$/, '/notification-bootstrap');
  const key = requiredProperty_(settings, 'EVALUATION_API_KEY');
  const privateYamlById = new Map(privateStrategies_(subscriptions).map(item => [item.strategy_id, item.yaml]));
  const seeded = [], failed = [];
  subscriptions.forEach(item => {
    const response = UrlFetchApp.fetch(url, {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: {Authorization: `Bearer ${key}`},
      payload: JSON.stringify({strategy_ids: [item.strategyId], private_strategies: privateYamlById.has(item.strategyId) ? [{strategy_id: item.strategyId, yaml: privateYamlById.get(item.strategyId)}] : []}),
    });
    if (response.getResponseCode() === 200) seeded.push(...(JSON.parse(response.getContentText()).seeded || []));
    else failed.push(`${item.strategyId}: ${response.getContentText()}`);
  });
  seeded.forEach(item => updateSubscription_({strategy_id: item.strategy_id, market_data_at: item.seeded_at}));
  logRun_(subscriptions.length, 0, failed.length ? 'INITIALIZED_WITH_ERRORS' : 'INITIALIZED', [...seeded.map(item => `${item.strategy_id}: ${item.seeded_at}`), ...failed].join(', '));
  if (failed.length) throw new Error(`일부 전략 초기화에 실패했습니다. ${failed.join(' | ')}`);
}

function withinNotificationWindow_() {
  const hour = Number(Utilities.formatDate(new Date(), KST, 'H'));
  return hour >= 7 && hour < 10;
}

function isWeeklySummaryDay_(date) {
  const parts = Utilities.formatDate(date || new Date(), KST, 'yyyy-MM-dd').split('-').map(Number);
  return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay() === 6;
}

function scheduledSummaryDue_(summary) {
  if (!summary) return null;
  const schedule = String(summary.summary_schedule || 'weekly');
  if (schedule === 'daily') return summary;
  if (schedule === 'weekly' && isWeeklySummaryDay_()) return summary;
  if (schedule === 'monthly' && isMonthlySummaryDay_()) return summary;
  return null;
}

function isMonthlySummaryDay_(date) {
  return Utilities.formatDate(date || new Date(), KST, 'd') === '1';
}

function enabledSubscriptions_() {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.SUBSCRIPTIONS);
  return sheet.getDataRange().getValues().slice(1)
    .map((row, index) => ({enabled: row[0] === true, strategyId: String(row[1] || '').trim(), name: String(row[2] || '').trim(), row: index + 2, lastDate: String(row[3] || '').trim()}))
    .filter(item => item.enabled && item.strategyId);
}

function requestEvaluations_(subscriptions, preview) {
  const settings = PropertiesService.getScriptProperties();
  const url = requiredProperty_(settings, 'EVALUATION_API_URL');
  const key = requiredProperty_(settings, 'EVALUATION_API_KEY');
  const lastDates = Object.fromEntries(subscriptions.map(item => [item.strategyId, item.lastDate]));
  const privateStrategies = privateStrategies_(subscriptions);
  const response = UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: {Authorization: `Bearer ${key}`},
    payload: JSON.stringify({strategy_ids: subscriptions.map(item => item.strategyId), last_evaluated_dates: lastDates, private_strategies: privateStrategies, preview: preview === true}),
  });
  if (response.getResponseCode() !== 200) throw new Error(`평가 API 오류 (${response.getResponseCode()}): ${response.getContentText()}`);
  return JSON.parse(response.getContentText()).evaluations || [];
}

function privateStrategies_(subscriptions) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.PERSONAL);
  if (!sheet || sheet.getLastRow() < 2) return [];
  const privateYamlById = new Map();
  sheet.getDataRange().getValues().slice(1).forEach(row => {
    const id = String(row[0] || '').trim(), yaml = String(row[1] || '').trim();
    if (!id && !yaml) return;
    if (!id || !yaml || privateYamlById.has(id)) throw new Error('개인 전략 시트의 전략 ID와 YAML을 확인하세요.');
    privateYamlById.set(id, yaml);
  });
  return subscriptions.filter(item => privateYamlById.has(item.strategyId))
    .map(item => ({strategy_id: item.strategyId, yaml: privateYamlById.get(item.strategyId)}));
}

function sendTelegram_(text) {
  const settings = PropertiesService.getScriptProperties();
  const token = requiredProperty_(settings, 'TELEGRAM_BOT_TOKEN');
  const chatId = requiredProperty_(settings, 'TELEGRAM_CHAT_ID');
  const html = telegramHtml_(text);
  const response = UrlFetchApp.fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'post', payload: {chat_id: chatId, text: html, parse_mode: 'HTML'}, muteHttpExceptions: true,
  });
  if (response.getResponseCode() !== 200) throw new Error('Telegram 메시지 전송에 실패했습니다.');
}

function eventKey_(evaluation) {
  return [evaluation.type || 'REBALANCE', evaluation.strategy_id, evaluation.strategy_version, evaluation.market_data_at, evaluation.reason_text || evaluation.reason || '', JSON.stringify(evaluation.target_weights || {})].join('|');
}

function eventAlreadySent_(key) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.EVENTS);
  return sheet.getLastRow() > 1 && sheet.getRange(2, 2, sheet.getLastRow() - 1, 1).getValues().flat().includes(key);
}

function recordEvent_(evaluation, result) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.EVENTS);
  sheet.appendRow([new Date(), eventKey_(evaluation), evaluation.market_data_at, evaluation.strategy_id, evaluation.strategy_name, formatStatesForSheet_(evaluation), evaluation.reason_text || evaluation.reason || '', evaluation.execution_days || '', formatWeights_(evaluation.target_weights), result || 'SENT', evaluation.type || 'REBALANCE', formatMarketForSheet_(evaluation)]);
}

function updateSubscription_(evaluation) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.SUBSCRIPTIONS);
  const rows = sheet.getDataRange().getValues();
  rows.slice(1).forEach((row, index) => { if (String(row[1]) === evaluation.strategy_id) sheet.getRange(index + 2, 4, 1, 2).setValues([[evaluation.market_data_at, formatStatesForSheet_(evaluation) || '정상']]); });
}

function logRun_(strategies, sent, result, detail) {
  const sheet = spreadsheet_()?.getSheetByName(SHEETS.LOG);
  if (sheet) sheet.appendRow([new Date(), strategies, sent, result, detail]);
  Logger.log(`${result}: ${detail}`);
}
function formatWeights_(weights) { return Object.entries(weights || {}).map(([ticker, weight]) => `${ticker} ${(Number(weight) * 100).toFixed(1)}%`).join('\n'); }
function stateDisplayItems_(event) {
  const configured = event.notification_display?.states;
  if (Array.isArray(configured)) return configured.map(item => ({
    name: item.name,
    label: item.label || item.name,
    value: String(item.value),
    display: String(item.display_value || item.value),
  }));
  return Object.entries(event.state_values || {}).map(([name, value]) => ({name, label: name, value: String(value), display: String(value)}));
}
function formatStatesForSheet_(event) {
  const text = stateDisplayItems_(event).map(item => `${item.label} ${item.display === item.value ? item.value : `${item.display} (${item.value})`}`).join(' / ');
  return text || event.state || '';
}
function formatConfiguredValue_(item) {
  const value = Number(item.value), digits = item.decimals === undefined ? 1 : Number(item.decimals);
  if (!Number.isFinite(value)) return '-';
  if (item.format === 'ratio_percent') return `${(value * 100).toFixed(digits)}%`;
  if (item.format === 'percent') return `${value.toFixed(digits)}%`;
  return value.toFixed(digits);
}
function formatConfiguredStates_(event) {
  return stateDisplayItems_(event).map(item => `• ${item.label}: ${item.display === item.value ? item.value : `${item.display} (${item.value})`}`).join('\n');
}
function marketDisplayItems_(event) {
  const configured = event.notification_display?.market;
  if (Array.isArray(configured)) return configured;
  return Object.entries(event.market || {}).flatMap(([ticker, fields]) => Object.entries(fields || {}).filter(([, value]) => Number.isFinite(Number(value))).map(([field, value]) => ({
    ticker, field, label: field, format: 'number', decimals: 1, value,
  })));
}
function formatMarketForSheet_(event) {
  const groups = {};
  marketDisplayItems_(event).forEach(item => { if (!groups[item.ticker]) groups[item.ticker] = []; groups[item.ticker].push(`${item.label} ${formatConfiguredValue_(item)}`); });
  return Object.entries(groups).map(([ticker, values]) => `${ticker} ${values.join(' / ')}`).join('\n');
}
function formatConfiguredMarket_(event) {
  const items = marketDisplayItems_(event);
  if (!items.length) return '';
  const groups = {};
  items.forEach(item => { if (!groups[item.ticker]) groups[item.ticker] = []; groups[item.ticker].push(`${item.label}: ${formatConfiguredValue_(item)}`); });
  return Object.entries(groups).map(([ticker, values]) => {
    const rows = values.reduce((result, value, index) => {
      if (index % 2 === 0) result.push([]);
      result.at(-1).push(value);
      return result;
    }, []);
    return [`[[B]]📈 시장 지표 (${ticker})[[/B]]`, ...rows.map(row => `• ${row.join(' · ')}`)].join('\n');
  }).join('\n\n');
}
function signalBar_(value, max) {
  const size = 6, filled = Math.round(Math.max(0, Math.min(size, Number(value) / Number(max) * size)));
  return `${'■'.repeat(filled)}${'□'.repeat(size - filled)}`;
}
function formatSignals_(event) {
  const configured = event.notification_display?.variables || [];
  if (event.notification_display) return configured.map(item => {
    const value = formatConfiguredValue_({...item, format: 'number'});
    const suffix = item.max === null || item.max === undefined ? '' : ` ${value}/${item.max}`;
    return `• ${item.label}: ${item.display === 'bar' && Number(item.max) > 0 ? `${signalBar_(item.value, item.max)}${suffix}` : value}`;
  }).join('\n');
  return Object.entries(event.variables || {}).filter(([, value]) => ['number', 'boolean', 'string'].includes(typeof value)).map(([name, value]) => `• ${name}: ${value}`).join('\n');
}
function formatConfirmations_(confirmations) {
  return (confirmations || []).map(item => `${item.name} → ${item.desired} ${item.days}/${item.required_days}일`).join('\n');
}
function formatAllocationTable_(event) {
  const current = event.current_weights || {}, target = event.target_weights || {};
  const tickers = [...Object.keys(target), ...Object.keys(current).filter(ticker => !Object.prototype.hasOwnProperty.call(target, ticker))];
  if (!tickers.length) return '-';
  return tickers.map(ticker => {
    const before = Number(current[ticker] || 0) * 100, after = Number(target[ticker] || 0) * 100, change = after - before;
    const delta = `${change >= 0 ? '+' : ''}${change.toFixed(1)}%p`;
    return `• ${event.product_names?.[ticker] || ticker}\n  [[C]]${before.toFixed(1)}%[[/C]] → [[C]]${after.toFixed(1)}%[[/C]] ([[C]]${delta}[[/C]])`;
  }).join('\n');
}
function telegramHtml_(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\[\[B\]\]([\s\S]*?)\[\[\/B\]\]/g, '<b>$1</b>')
    .replace(/\[\[C\]\]([\s\S]*?)\[\[\/C\]\]/g, '<code>$1</code>');
}
function messageFor_(event) {
  const type = event.type || 'REBALANCE';
  const summaryTitles = {daily: '[일간 시장 브리핑]', weekly: '[주간 시장 브리핑]', monthly: '[월간 시장 브리핑]'};
  const titles = {REBALANCE: '[리밸런싱 예정]', PREALERT: '[사전주의 · 매매 없음]', SUMMARY: summaryTitles[event.summary_schedule] || summaryTitles.weekly};
  const lines = [titles[type] || '[투자 전략 알림]', '', `[[B]]${event.strategy_name}[[/B]]`, `시장 기준일: ${event.market_data_at}`];
  if (event.schedule_disabled) lines.push('정기 브리핑: none 설정 — 테스트로만 전송');
  const states = formatConfiguredStates_(event); if (states) lines.push('', '[[B]]📌 시장 상태[[/B]]', states);
  const signals = formatSignals_(event); if (signals) lines.push('', '[[B]]📊 신호[[/B]]', signals);
  const market = formatConfiguredMarket_(event); if (market) lines.push('', market);
  if (event.reason_text || event.reason) lines.push(`• 판단: ${event.reason_text || event.reason}`);
  const confirmations = formatConfirmations_(event.confirmations); if (confirmations) lines.push(`확인 진행\n${confirmations}`);
  lines.push(`• 목표 괴리: [[C]]${(Number(event.target_deviation || 0) * 100).toFixed(1)}%p[[/C]]`);
  const allocationStatus = type === 'REBALANCE' ? (event.target_changed ? '목표 변경' : '리밸런싱 예정') : '매매 없음';
  lines.push('', `[[B]]📦 포트폴리오 비중 (${allocationStatus})[[/B]]`, formatAllocationTable_(event));
  if (type === 'REBALANCE') lines.push(`[[B]]실행 예정[[/B]]: 다음 거래일 시가부터 ${event.execution_days || 1}일`);
  if (event.test) lines[0] += ' · 테스트';
  if (event.includes_summary) lines[0] += ' · 정기 요약 포함';
  return lines.filter(line => line !== undefined && line !== null).join('\n');
}
function requiredProperty_(properties, key) { const value = properties.getProperty(key); if (!value) throw new Error(`Script Properties에 ${key}를 설정하세요.`); return value; }
function ensureSheet_(spreadsheet, name, headers) { const sheet = spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name); if (sheet.getLastRow() === 0) sheet.appendRow(headers); else sheet.getRange(1, 1, 1, headers.length).setValues([headers]); return sheet; }
