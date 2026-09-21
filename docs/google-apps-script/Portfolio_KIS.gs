/**
 * ==============================================================================
 *                        [ 📊 퇴직연금DC 시트 데이터 구조 ]
 * ==============================================================================
 *  [0] 종목명                 [1] Code  [2] 수량  [3] 평단   [4] 위험자산 (1=O,0=X)
 * ------------------------------------------------------------------------------
 *  TIME 나스닥 액티브         426030       100    47574        1
 *  KoAct 나스닥 액티브        0015B0       120    17803        1
 *  TDF 2026                  0082V0      3000    11311        0
 *  TDF 2025                  434060      1000    16818        0
 *   ...
 *  현금                           0       1      1395          0
 * ==============================================================================
 */

function checkPortfolio() {
  var day = Utilities.formatDate(new Date(), 'Asia/Seoul', 'u');
//  if (day === '6' || day === '7') return;
  sendTelegram(DC_buildPortfolioMessage_());
}

function DC_buildPortfolioMessage_() {
  var sheet = DC_sheet_();
  // 5개의 열만 사용하므로 범위를 (2, 1, 7, 5)로 최적화 (A~E열)
  var data = sheet.getRange(2, 1, 7, 5).getValues(); 
  
  var totalValue = 0;
  var total_profit = 0;
  var riskAssetValue = 0; 
  var portfolio = []; 

  // [1] 개별 종목 실시간 가격/RSI API 직접 호출 및 자산 계산
  data.forEach(row => {
    var name = row[0];
    if (name === "") return; // 이름이 빈 칸이면 건너뜀
    
    var code = row[1]; 
    var count = Number(row[2]) || 0;
    var basePrice = Number(row[3]) || 0; // 평단
    var isRiskAsset = (row[4] == 1);     // 위험자산여부

    var currentPrice, rsi, rsi_d1;

    // 💡 현금 예외 처리 (API 조회 스킵)
    if (String(name).trim() === "현금" || DC_normalizeCode_(code) === "000000") {
      currentPrice = basePrice; 
      rsi = "-";
      rsi_d1 = "-";
    } else {
      // 일반 주식 실시간 현재가 / RSI 가져오기
      currentPrice = GET_KIS_PRICE(code);
      rsi = GET_NAVER_RSI(code, 14, 0);
      rsi_d1 = GET_NAVER_RSI(code, 14, 1);
    }

    var evalAmount = currentPrice * count;
    totalValue += evalAmount;
    
    if (isRiskAsset) {
      riskAssetValue += evalAmount;
    }

    portfolio.push({
      name: name,
      code: code,
      count: count,
      currentPrice: currentPrice,
      evalAmount: evalAmount,
      rsi: rsi,
      rsi_d1: rsi_d1,
      basePrice: basePrice
    });
  });

  var riskAssetRatio = (totalValue > 0) ? ((riskAssetValue / totalValue) * 100).toFixed(1) : 0;
  
  // [2] 텔레그램 메시지 시작 (오늘 날짜 포함)
  var todayDate = Utilities.formatDate(new Date(), "Asia/Seoul", "yyyy-MM-dd HH:mm");
  var alertMessage = `📊 재영이 4조 진행 현황 (${todayDate})\n`; 
  alertMessage += "- 현재가: 한국투자증권 KRX (REST 조회 기준)\n";
  var arrow = "";

  // NASDAQ 및 이동평균선(20일, 55일) API
  var NASDAQ_today = GET_NASDAQ(0); 
  var NASDAQ_m20 = GET_NASDAQ_SMA(20); 
  var NASDAQ_m55 = GET_NASDAQ_SMA(55); 
  
  // 💡 신호등 로직: 20일선 위(녹색), 20~55일선 사이(노랑), 55일선 아래(빨강)
  if (NASDAQ_today >= NASDAQ_m20) {
    arrow = "🟢";
  } else if (NASDAQ_today >= NASDAQ_m55) {
    arrow = "🟡";
  } else {
    arrow = "🔴";
  }

  alertMessage += `- ${arrow} NASDAQ ${NASDAQ_today}\n`;

  // VIX API (상하 화살표와 경고 이모지 동시 출력으로 버그 수정)
  var vix = GET_VIX(0);
  var preVix = GET_VIX(1);
  var vixArrow = (vix > preVix) ? "↑" : (vix < preVix ? "↓" : "=");
  var vixWarn = (vix >= 30) ? "🚨" : (vix >= 20 ? "⚠️" : "");
  alertMessage += `- ${vixWarn} VIX ${vix}${vixArrow}\n`; 

  // 하이일드 스프레드 API
  var hys = GET_HIGH_YIELD_SPREAD(0);
  var prehys = GET_HIGH_YIELD_SPREAD(1);
  arrow = (hys > prehys) ? "↑" : (hys < prehys ? "↓" : "=");
  alertMessage += `- 하이일드 스프레드 ${hys}${arrow}\n`; 

  // 위험자산 비중  
  alertMessage += `- 위험자산 비중: ${riskAssetRatio}%\n\n`; 

  // [3] 개별 종목 수익률 및 비중 메시지 작성
  portfolio.forEach(item => {
    var currentWeight = totalValue > 0 ? item.evalAmount / totalValue : 0;
    
    var change = (item.basePrice > 0 ? ((item.currentPrice - item.basePrice) / item.basePrice)*100 : 0).toFixed(2);
    var profit = (item.currentPrice - item.basePrice) * item.count;
    
    total_profit += profit;

    if(currentWeight > 0) {
      // 💡 현금일 때와 일반 주식일 때 메시지 포맷 구분
      if (String(item.name).trim() === "현금" || DC_normalizeCode_(item.code) === "000000") {
        alertMessage += `[${item.name}]\n. 비중: ${(currentWeight*100).toFixed(1)}%  (잔고: ${formatNumber(item.evalAmount)}원) \n\n`;
      } else {
        var numRsi = Number(item.rsi);
        var numRsiD1 = Number(item.rsi_d1);
        arrow = (numRsi > numRsiD1) ? " ⬆️" : (numRsi < numRsiD1 ? "↓" : "=");
        var displayRsi = isNaN(numRsi) ? item.rsi : numRsi.toFixed(2);

        // 💡 수익률에 따라 앞에 붙는 기호 색상 자동 변경 (플러스면 빨강, 마이너스면 파랑)
        var bullet = (change > 0) ? "🔺" : (change < 0 ? "🔹" : "🔸");

        alertMessage += `[${item.name}]\n${bullet} 비중: ${(currentWeight*100).toFixed(1)}%  rsi: ${displayRsi} ${arrow} \n`;
        alertMessage += `     (수익 ${formatNumber(profit)},  ${change}%) \n\n`;
      }
    }
  });

  // [4] 총 자산 및 수익금 표기
  alertMessage +=  `* 총자산 ${formatNumber(totalValue)}\n`; 

  var totalPrincipal = totalValue - total_profit;
  var totalProfitRate = (totalPrincipal > 0) ? ((total_profit / totalPrincipal) * 100).toFixed(2) : 0;
   
  alertMessage +=  `* 총 수익금 ${formatNumber(total_profit)} (${totalProfitRate >= 0 ? '+' : ''}${totalProfitRate}%)`;
    
  return alertMessage;
}

// =======================================================
// [아래부터는 텔레그램 및 지표를 가져오는 필수 유틸리티 함수들입니다]
// =======================================================

function sendTelegram(text) {
  var scriptProperties = PropertiesService.getScriptProperties();
  var token = scriptProperties.getProperty('TELEGRAM_TOKEN');
  var chatId = scriptProperties.getProperty('TELEGRAM_CHAT_ID');
  if (!token || !chatId) {
    throw new Error("설정값이 없습니다. 스크립트 속성을 다시 확인하세요.");
  }
  var url = "https://api.telegram.org/bot" + token + "/sendMessage";
  
  var payload = {
    "chat_id": chatId,
    "text": text // 💡 <pre> 태그 제거 완료 (글자 크기 원래대로 시원하게 나옴)
  };
  
  var options = {
    "method": "post",
    "contentType": "application/json",
    "payload": JSON.stringify(payload)
  };
  UrlFetchApp.fetch(url, options);
}

function formatNumber(num) {
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** KIS 실전 REST 현재가. 시세는 캐시하지 않고 인증 토큰만 재사용합니다. */
function GET_KIS_PRICE(code, trigger) {
  if (code === '' || code === null || code === undefined) return '';
  var symbol = DC_normalizeCode_(code);
  if (symbol === '000000') throw new Error('현금은 현재가 API 조회 대상이 아닙니다.');
  var config = DC_kisConfig_();
  var token = DC_kisToken_(config);
  var url = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price'
    + '?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD=' + encodeURIComponent(symbol);
  var rateRetries = 0;
  var tokenRenewed = false;
  while (true) {
    var response = DC_kisQuoteFetch_(url, {
      muteHttpExceptions: true,
      headers: {
        authorization: 'Bearer ' + token,
        appkey: config.key,
        appsecret: config.secret,
        tr_id: 'FHKST01010100',
        custtype: 'P'
      }
    });
    var body = DC_json_(response, '현재가 ' + symbol);
    if (body.msg_cd === 'EGW00201' || response.getResponseCode() === 429) {
      if (rateRetries >= 3) {
        throw new Error('KIS 호출 한도 초과 [' + symbol
          + '] EGW00201/HTTP ' + response.getResponseCode()
          + ': 재시도 3회 실패. 같은 키를 사용하는 다른 실행/프로그램을 확인하세요.');
      }
      Utilities.sleep(1500 * Math.pow(2, rateRetries++));
      continue;
    }
    // 호출 한도 재시도와 별도로 토큰 만료 시 한 번만 갱신합니다.
    if (!tokenRenewed && body.msg_cd === 'EGW00123') {
      tokenRenewed = true;
      token = DC_kisToken_(config, token);
      continue;
    }
    if (response.getResponseCode() !== 200 || String(body.rt_cd) !== '0') {
      throw new Error('KIS 현재가 조회 실패 [' + symbol + '] HTTP '
        + response.getResponseCode() + ' / ' + String(body.msg_cd || 'UNKNOWN'));
    }
    var price = Number(body.output && body.output.stck_prpr);
    if (!isFinite(price) || price <= 0) throw new Error('KIS 현재가 없음: ' + symbol);
    return price;
  }
}

/** 같은 Apps Script 프로젝트의 동시 실행도 조회 간격을 공유합니다. */
function DC_kisQuoteFetch_(url, options) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var props = PropertiesService.getScriptProperties();
    var last = Number(props.getProperty('DC_KIS_LAST_QUOTE_AT')) || 0;
    // 6종목 보고용의 보수적인 간격. 공식 초당 한도 값이라는 뜻은 아닙니다.
    var wait = Math.min(1100, Math.max(0, 1100 - (Date.now() - last)));
    if (wait > 0) Utilities.sleep(wait);
    try {
      return UrlFetchApp.fetch(url, options);
    } finally {
      props.setProperty('DC_KIS_LAST_QUOTE_AT', String(Date.now()));
    }
  } finally { lock.releaseLock(); }
}

function DC_normalizeCode_(code) {
  var value = String(code).trim().toUpperCase();
  if (/^\d{1,6}$/.test(value)) value = value.padStart(6, '0');
  if (!/^[0-9A-Z]{6}$/.test(value)) throw new Error('종목 코드 형식 오류: ' + value);
  return value;
}

function DC_kisConfig_() {
  var props = PropertiesService.getScriptProperties();
  var key = (props.getProperty('KIS_APP_KEY') || '').trim();
  var secret = (props.getProperty('KIS_APP_SECRET') || '').trim();
  if (!key || !secret) throw new Error('스크립트 속성에 KIS_APP_KEY, KIS_APP_SECRET을 설정하세요.');
  return { key: key, secret: secret };
}

function DC_json_(response, label) {
  try {
    var body = JSON.parse(response.getContentText());
    if (!body || typeof body !== 'object') throw new Error('empty');
    return body;
  } catch (e) {
    // 인증 응답 원문에는 토큰이 포함될 수 있으므로 출력하지 않습니다.
    throw new Error('KIS ' + label + ' 응답 형식 오류 / HTTP ' + response.getResponseCode());
  }
}

function DC_kisToken_(config, rejectedToken) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var props = PropertiesService.getScriptProperties();
    var cached = {};
    try { cached = JSON.parse(props.getProperty('DC_KIS_TOKEN') || '{}'); } catch (e) {}
    if (cached && cached.key === config.key && cached.token && cached.token !== rejectedToken
        && Number(cached.expiresAt) > Date.now() + 60000) return cached.token;
    var response = UrlFetchApp.fetch('https://openapi.koreainvestment.com:9443/oauth2/tokenP', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      payload: JSON.stringify({grant_type: 'client_credentials', appkey: config.key, appsecret: config.secret})
    });
    var body = DC_json_(response, '토큰');
    var expiresIn = Number(body.expires_in);
    if (response.getResponseCode() !== 200 || !body.access_token || !isFinite(expiresIn) || expiresIn <= 60) {
      throw new Error('KIS 토큰 발급 실패 / HTTP ' + response.getResponseCode()
        + ' / ' + String(body.error_code || body.msg_cd || 'UNKNOWN'));
    }
    props.setProperty('DC_KIS_TOKEN', JSON.stringify({
      key: config.key, token: body.access_token, expiresAt: Date.now() + expiresIn * 1000
    }));
    return body.access_token;
  } finally { lock.releaseLock(); }
}

function DC_sheet_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('DC_SPREADSHEET_ID');
  var ss = id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('시트에 연결된 Apps Script를 사용하거나 DC_SPREADSHEET_ID를 설정하세요.');
  var sheet = ss.getSheetByName(props.getProperty('DC_SHEET_NAME') || '퇴직연금DC');
  if (!sheet) throw new Error('퇴직연금DC 시트를 찾을 수 없습니다. DC_SHEET_NAME을 확인하세요.');
  return sheet;
}

/** 주말에도 현재가 6종목과 현금 처리를 검증. 텔레그램 발송 없음. */
function TEST_KIS_PRICES() {
  var rows = DC_sheet_().getRange(2, 1, 7, 5).getValues();
  var result = rows.filter(function(row) { return row[0] !== ''; }).map(function(row) {
    var cash = String(row[0]).trim() === '현금' || DC_normalizeCode_(row[1]) === '000000';
    var price = cash ? Number(row[3]) : GET_KIS_PRICE(row[1]);
    return {name: row[0], code: String(row[1]), price: price,
      source: cash ? '시트 평단(현금)' : 'KIS KRX',
      fetchedAt: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss')};
  });
  console.log(JSON.stringify(result));
  return result;
}

/** 주말에도 전체 보고서를 생성. 텔레그램 발송 없음. */
function PREVIEW_DC_PORTFOLIO() {
  var message = DC_buildPortfolioMessage_();
  console.log(message);
  return message;
}


function GET_NAVER_RSI(code, period, daysAgo) {
  if (!code) return null;
  code = DC_normalizeCode_(code);
  period = period || 14;
  daysAgo = daysAgo || 0; 
  
  var url = "https://fchart.stock.naver.com/sise.nhn?symbol=" + code + "&timeframe=day&count=100&requestType=0";
  try {
    var response = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
    var items = response.getContentText().match(/<item data="[^"]+" \/>/g);
    
    if (!items || items.length < period + 1 + daysAgo) return "데이터 부족";
    
    var closes = [];
    for (var i = 0; i < items.length - daysAgo; i++) {
      var parts = items[i].match(/data="([^"]+)"/)[1].split('|');
      closes.push(Number(parts[4]));
    }
    
    var upSum = 0, downSum = 0;
    for (var i = 1; i <= period; i++) {
      var diff = closes[i] - closes[i - 1];
      if (diff > 0) upSum += diff; else downSum -= diff;
    }
    
    var au = upSum / period, ad = downSum / period;
    for (var i = period + 1; i < closes.length; i++) {
      var diff = closes[i] - closes[i - 1];
      var up = diff > 0 ? diff : 0;
      var down = diff < 0 ? -diff : 0;
      au = (au * (period - 1) + up) / period;
      ad = (ad * (period - 1) + down) / period;
    }
    
    var rs = au / ad;
    return Number((100 - (100 / (1 + rs))).toFixed(2));
  } catch (e) { return "Error"; }
}

function GET_HIGH_YIELD_SPREAD(daysAgo) {
  daysAgo = daysAgo || 0; 
  var url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2";
  try {
    var csv = UrlFetchApp.fetch(url, {muteHttpExceptions: true}).getContentText();
    var validLines = csv.split('\n').filter(function(line) {
      var parts = line.trim().split(',');
      return parts.length === 2 && parts[0] !== 'DATE' && parts[1] !== '.';
    });
    if (validLines.length === 0) return "데이터 없음";
    
    var targetIndex = validLines.length - 1 - daysAgo;
    if (targetIndex < 0) return "데이터 부족";
    
    return Number(validLines[targetIndex].split(',')[1]);
  } catch(e) { return "Error"; }
}

function GET_VIX(daysAgo) {
  daysAgo = daysAgo || 0;
  var url = "https://query2.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=1d";
  var options = { muteHttpExceptions: true, headers: { "User-Agent": "Mozilla/5.0" } };
  try {
    var response = UrlFetchApp.fetch(url, options);
    if (response.getResponseCode() !== 200) return "접속차단";
    
    var result = JSON.parse(response.getContentText()).chart.result[0];
    if (daysAgo === 0) return Number(result.meta.regularMarketPrice.toFixed(2));
    
    var closes = result.indicators.quote[0].close.filter(function(val) { return val !== null; });
    var targetIndex = closes.length - 1 - daysAgo;
    return Number(closes[targetIndex].toFixed(2));
  } catch(e) { return "에러"; }
}

function GET_NASDAQ(daysAgo) {
  daysAgo = daysAgo || 0;
  var url = "https://query2.finance.yahoo.com/v8/finance/chart/%5EIXIC?range=5d&interval=1d";
  var options = { muteHttpExceptions: true, headers: { "User-Agent": "Mozilla/5.0" } };
  try {
    var response = UrlFetchApp.fetch(url, options);
    if (response.getResponseCode() !== 200) return "접속차단";
    
    var result = JSON.parse(response.getContentText()).chart.result[0];
    if (daysAgo === 0) return Number(result.meta.regularMarketPrice.toFixed(2));
    
    var closes = result.indicators.quote[0].close.filter(function(val) { return val !== null; });
    var targetIndex = closes.length - 1 - daysAgo;
    return Number(closes[targetIndex].toFixed(2));
  } catch(e) { return "에러"; }
}

function GET_NASDAQ_SMA(period) {
  period = period || 55;
  var url = "https://query2.finance.yahoo.com/v8/finance/chart/%5EIXIC?range=6mo&interval=1d";
  var options = { muteHttpExceptions: true, headers: { "User-Agent": "Mozilla/5.0" } };
  try {
    var response = UrlFetchApp.fetch(url, options);
    if (response.getResponseCode() !== 200) return "접속차단";
    
    var closes = JSON.parse(response.getContentText()).chart.result[0].indicators.quote[0].close.filter(function(val) { return val !== null; });
    if (closes.length < period) return "데이터 부족";
    
    var recentCloses = closes.slice(-period);
    var sum = 0;
    for (var i = 0; i < recentCloses.length; i++) sum += recentCloses[i];
    return Number((sum / period).toFixed(2));
  } catch(e) { return "에러"; }
}


// 웹 브라우저로 접속했을 때 실행되는 함수
function doGet() {
  // 1. 기존에 작성해두신 포트폴리오 계산 로직 실행 (텔레그램으로 보내던 메시지 내용)
  // 예: var reportText = generatePortfolioReport(); 
  var reportText = DC_buildPortfolioMessage_(); // 기존 메시지 생성 함수 호출
  
  // 2. 모바일 브라우저 화면에 깔끔하게 출력
  var html = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>📊 내 포트폴리오 현황</title>
      <style>
        body { font-family: -apple-system, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }
        .card { background: #1e293b; padding: 18px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        pre { white-space: pre-wrap; word-break: break-all; font-family: inherit; font-size: 14px; line-height: 1.6; }
        .btn { display: block; width: 100%; padding: 12px; margin-top: 15px; background: #3b82f6; color: white; text-align: center; border-radius: 8px; text-decoration: none; font-weight: bold; border: none; }
      </style>
    </head>
    <body>
      <h2>📊 퇴직연금DC 포트폴리오</h2>
      <div class="card">
        <pre>${reportText}</pre>
      </div>
      <button class="btn" onclick="location.reload()">🔄 새로고침</button>
    </body>
    </html>
  `;
  
  return HtmlService.createHtmlOutput(html)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}