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

function requestEvaluations_(subscriptions) {
  const settings = PropertiesService.getScriptProperties();
  const url = requiredProperty_(settings, 'EVALUATION_API_URL');
  const key = requiredProperty_(settings, 'EVALUATION_API_KEY');
  const lastDates = Object.fromEntries(subscriptions.map(item => [item.strategyId, item.lastDate]));
  const privateStrategies = privateStrategies_(subscriptions);
  const response = UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: {Authorization: `Bearer ${key}`},
    payload: JSON.stringify({strategy_ids: subscriptions.map(item => item.strategyId), last_evaluated_dates: lastDates, private_strategies: privateStrategies}),
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
  const response = UrlFetchApp.fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'post', payload: {chat_id: chatId, text: text}, muteHttpExceptions: true,
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
  sheet.appendRow([new Date(), eventKey_(evaluation), evaluation.market_data_at, evaluation.strategy_id, evaluation.strategy_name, formatStates_(evaluation.state_values, evaluation.state), evaluation.reason_text || evaluation.reason || '', evaluation.execution_days || '', formatWeights_(evaluation.target_weights), result || 'SENT', evaluation.type || 'REBALANCE', formatMarket_(evaluation.market)]);
}

function updateSubscription_(evaluation) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.SUBSCRIPTIONS);
  const rows = sheet.getDataRange().getValues();
  rows.slice(1).forEach((row, index) => { if (String(row[1]) === evaluation.strategy_id) sheet.getRange(index + 2, 4, 1, 2).setValues([[evaluation.market_data_at, formatStates_(evaluation.state_values, evaluation.state) || '정상']]); });
}

function logRun_(strategies, sent, result, detail) {
  const sheet = spreadsheet_()?.getSheetByName(SHEETS.LOG);
  if (sheet) sheet.appendRow([new Date(), strategies, sent, result, detail]);
  Logger.log(`${result}: ${detail}`);
}
function formatWeights_(weights) { return Object.entries(weights || {}).map(([ticker, weight]) => `${ticker} ${(Number(weight) * 100).toFixed(1)}%`).join('\n'); }
function formatWeightsInline_(weights) { return Object.entries(weights || {}).map(([ticker, weight]) => `${ticker} ${(Number(weight) * 100).toFixed(1)}%`).join(' / '); }
function formatWeightChanges_(event) {
  const current = event.current_weights || {}, target = event.target_weights || {};
  return Object.keys(target).map(ticker => {
    const before = Number(current[ticker] || 0) * 100, after = Number(target[ticker] || 0) * 100, change = after - before;
    return `${ticker} ${before.toFixed(1)}% → ${after.toFixed(1)}% (${change >= 0 ? '+' : ''}${change.toFixed(1)}%p)`;
  }).join('\n');
}
function formatStates_(states, legacy) {
  const labels = {trend_mode: '추세', market_mode: '추세', defense_mode: '밸류에이션', stage: '하락 단계', valuation_level: '평가 단계'};
  const order = ['trend_mode', 'market_mode', 'defense_mode', 'stage', 'valuation_level'];
  const text = order.filter(key => Object.prototype.hasOwnProperty.call(states || {}, key)).map(key => `${labels[key]} ${states[key]}`).join(' / ');
  return text || legacy || '';
}
function formatConfiguredValue_(item) {
  const value = Number(item.value), digits = item.decimals === undefined ? 1 : Number(item.decimals);
  if (!Number.isFinite(value)) return '-';
  if (item.format === 'ratio_percent') return `${(value * 100).toFixed(digits)}%`;
  if (item.format === 'percent') return `${value.toFixed(digits)}%`;
  return value.toFixed(digits);
}
function formatConfiguredStates_(event) {
  const items = event.notification_display?.states || [];
  if (event.notification_display) return items.map(item => `${item.label} ${item.value}`).join(' / ');
  return formatStates_(event.state_values, event.state);
}
function percent_(value, digits) { return Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits === undefined ? 1 : digits)}%` : '-'; }
function formatMarket_(market) {
  const qqq = market?.qqq || {};
  const parts = [`1일 ${percent_(qqq.roc1)}`, `5일 ${percent_(qqq.roc5)}`, `20일 ${percent_(qqq.roc20)}`];
  if (Number.isFinite(Number(qqq.drawdown120))) parts.push(`120일 고점 대비 ${percent_(Number(qqq.drawdown120) * 100)}`);
  return `QQQ ${parts.join(' / ')}`;
}
function formatConfiguredMarket_(event) {
  const items = event.notification_display?.market || [];
  if (!event.notification_display) return formatMarket_(event.market);
  if (!items.length) return '';
  const groups = {};
  items.forEach(item => { if (!groups[item.ticker]) groups[item.ticker] = []; groups[item.ticker].push(`${item.label} ${formatConfiguredValue_(item)}`); });
  return Object.entries(groups).map(([ticker, values]) => `${ticker} ${values.join(' / ')}`).join('\n');
}
function formatSignals_(event) {
  const configured = event.notification_display?.variables || [];
  if (event.notification_display) return configured.map(item => `${item.label} ${formatConfiguredValue_({...item, format: 'number'})}${item.max === null || item.max === undefined ? '' : `/${item.max}`}`).join(' / ');
  const variables = event.variables || {}, qqq = event.market?.qqq || {}, parts = [];
  if (Number.isFinite(Number(variables.risk_off_score))) parts.push(`약세 ${Number(variables.risk_off_score)}/6`);
  if (Number.isFinite(Number(variables.recovery_score))) parts.push(`회복 ${Number(variables.recovery_score)}/6`);
  if (Number.isFinite(Number(qqq.valuation_score))) parts.push(`밸류에이션 ${Number(qqq.valuation_score).toFixed(1)}`);
  return parts.join(' / ');
}
function formatConfirmations_(confirmations) {
  return (confirmations || []).map(item => `${item.name} → ${item.desired} ${item.days}/${item.required_days}일`).join('\n');
}
function messageFor_(event) {
  const type = event.type || 'REBALANCE';
  const summaryTitles = {daily: '[일간 시장 브리핑]', weekly: '[주간 시장 브리핑]', monthly: '[월간 시장 브리핑]'};
  const titles = {REBALANCE: '[리밸런싱 예정]', PREALERT: '[사전주의 · 매매 없음]', SUMMARY: summaryTitles[event.summary_schedule] || summaryTitles.weekly};
  const lines = [titles[type] || '[투자 전략 알림]', '', `전략: ${event.strategy_name}`, `시장 기준일: ${event.market_data_at}`];
  if (event.mapped_products && event.source_strategy_id) lines.push(`기준 전략: ${event.source_strategy_id}`);
  lines.push(`상태: ${formatConfiguredStates_(event) || '-'}`, formatConfiguredMarket_(event));
  const signals = formatSignals_(event); if (signals) lines.push(`신호: ${signals}`);
  if (event.reason_text || event.reason) lines.push(`판단: ${event.reason_text || event.reason}`);
  const confirmations = formatConfirmations_(event.confirmations); if (confirmations) lines.push(`확인 진행\n${confirmations}`);
  lines.push(`목표 괴리: ${(Number(event.target_deviation || 0) * 100).toFixed(1)}%p`);
  if (type === 'REBALANCE') {
    if (event.target_changed && Object.keys(event.previous_target_weights || {}).length) {
      lines.push(
        '',
        '목표 비중',
        formatWeightsInline_(event.previous_target_weights),
        `→ ${formatWeightsInline_(event.target_weights)}`,
        '',
        '현재 비중',
        formatWeightsInline_(event.current_weights),
      );
    } else {
      lines.push('', '현재 → 목표 비중', formatWeightChanges_(event));
    }
    lines.push(`실행 예정: 다음 거래일 시가부터 ${event.execution_days || 1}일`);
  } else {
    lines.push('', '현재 비중', formatWeights_(event.current_weights), '목표 비중', formatWeights_(event.target_weights), '현재 행동: 리밸런싱 없음');
  }
  if (event.includes_summary) lines[0] += ' · 정기 요약 포함';
  return lines.filter(line => line !== undefined && line !== null).join('\n');
}
function requiredProperty_(properties, key) { const value = properties.getProperty(key); if (!value) throw new Error(`Script Properties에 ${key}를 설정하세요.`); return value; }
function ensureSheet_(spreadsheet, name, headers) { const sheet = spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name); if (sheet.getLastRow() === 0) sheet.appendRow(headers); else sheet.getRange(1, 1, 1, headers.length).setValues([headers]); return sheet; }
