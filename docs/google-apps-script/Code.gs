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
    ['발송 시각', '이벤트 키', '시장 기준일', '전략 ID', '전략명', '상태', '사유', '실행 일수', '목표 비중', '결과']);
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
      if (!evaluation.rebalance_required || eventAlreadySent_(eventKey_(evaluation))) return;
      sendTelegram_(messageFor_(evaluation));
      recordEvent_(evaluation);
      sent++;
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
  return [evaluation.strategy_id, evaluation.strategy_version, evaluation.market_data_at, evaluation.reason || '', JSON.stringify(evaluation.target_weights || {})].join('|');
}

function eventAlreadySent_(key) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.EVENTS);
  return sheet.getLastRow() > 1 && sheet.getRange(2, 2, sheet.getLastRow() - 1, 1).getValues().flat().includes(key);
}

function recordEvent_(evaluation) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.EVENTS);
  sheet.appendRow([new Date(), eventKey_(evaluation), evaluation.market_data_at, evaluation.strategy_id, evaluation.strategy_name, evaluation.state, evaluation.reason || '', evaluation.execution_days || '', formatWeights_(evaluation.target_weights), 'SENT']);
}

function updateSubscription_(evaluation) {
  const sheet = requireSpreadsheet_().getSheetByName(SHEETS.SUBSCRIPTIONS);
  const rows = sheet.getDataRange().getValues();
  rows.slice(1).forEach((row, index) => { if (String(row[1]) === evaluation.strategy_id) sheet.getRange(index + 2, 4, 1, 2).setValues([[evaluation.market_data_at, '정상']]); });
}

function logRun_(strategies, sent, result, detail) {
  const sheet = spreadsheet_()?.getSheetByName(SHEETS.LOG);
  if (sheet) sheet.appendRow([new Date(), strategies, sent, result, detail]);
  Logger.log(`${result}: ${detail}`);
}
function formatWeights_(weights) { return Object.entries(weights || {}).map(([ticker, weight]) => `${ticker} ${(Number(weight) * 100).toFixed(1)}%`).join('\n'); }
function messageFor_(event) { return `[리밸런싱 알림]\n\n전략: ${event.strategy_name}\n시장 기준일: ${event.market_data_at}\n상태: ${event.state || '-'}\n사유: ${event.reason || '-'}\n실행 기간: ${event.execution_days || 1}일\n\n목표 비중\n${formatWeights_(event.target_weights)}`; }
function requiredProperty_(properties, key) { const value = properties.getProperty(key); if (!value) throw new Error(`Script Properties에 ${key}를 설정하세요.`); return value; }
function ensureSheet_(spreadsheet, name, headers) { const sheet = spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name); if (sheet.getLastRow() === 0) sheet.appendRow(headers); return sheet; }
