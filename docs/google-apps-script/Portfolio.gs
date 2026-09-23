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
  // 1. 현재 요일 가져오기 (0: 일요일, 1: 월요일, ... 6: 토요일)
  var today = new Date().getDay();
  
  // 2. 주말(일요일 또는 토요일)이면 더 이상 진행하지 않고 종료
  if (today === 0 || today === 6) {
    return; // 테스트가 끝나면 주석을 해제하세요.
  }

  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('퇴직연금DC');
  
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
    if (name === "현금" || String(code) === "0") {
      currentPrice = basePrice; 
      rsi = "-";
      rsi_d1 = "-";
    } else {
      // 일반 주식 실시간 현재가 / RSI 가져오기 (모바일 JSON 우선 조회 + RSI 1회 호출로 동시 계산)
      currentPrice = GET_NAVER_PRICE(code);
      if (isNaN(currentPrice) || currentPrice === "조회 실패") {
         currentPrice = basePrice; // API 실패 시 1원(위험자산)이 되지 않도록 평단가로 임시 대체
      }
      var rsiData = GET_NAVER_RSI_BOTH(code, 14);
      rsi = rsiData.rsi;
      rsi_d1 = rsiData.rsi_d1;
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
  var todayDate = Utilities.formatDate(new Date(), "Asia/Seoul", "yyyy-MM-dd");
  var alertMessage = `📊 DC 4조 진행 현황 (${todayDate})\n`; 
  var arrow = "";

  // NASDAQ 및 이동평균선(20일, 55일) API (💡 최적화: 1회 호출로 당일/20일/55일 동시 계산 + 5분 캐시)
  var nasdaqData = GET_NASDAQ_DATA(); 
  var NASDAQ_today = nasdaqData.today; 
  var NASDAQ_m20 = nasdaqData.m20; 
  var NASDAQ_m55 = nasdaqData.m55; 
  
  // 💡 신호등 로직: 20일선 위(녹색), 20~55일선 사이(노랑), 55일선 아래(빨강)
  if (NASDAQ_today >= NASDAQ_m20) {
    arrow = "🟢";
  } else if (NASDAQ_today >= NASDAQ_m55) {
    arrow = "🟡";
  } else {
    arrow = "🔴";
  }

  alertMessage += `- ${arrow} NASDAQ ${NASDAQ_today}\n`;

  // VIX API (💡 최적화: 1회 호출로 오늘/전일 추출 + 5분 캐시)
  var vixData = GET_VIX_DATA();
  var vix = vixData.today;
  var preVix = vixData.prev;
  var vixArrow = (vix > preVix) ? "↑" : (vix < preVix ? "↓" : "=");
  var vixWarn = (vix >= 30) ? "🚨" : (vix >= 20 ? "⚠️" : "");
  alertMessage += `- ${vixWarn} VIX ${vix}${vixArrow}\n`; 

  // 하이일드 스프레드 API (💡 최적화: 1회 CSV 다운로드로 오늘/전일 추출 + 30분 캐시)
  var hysData = GET_HIGH_YIELD_DATA();
  var hys = hysData.today;
  var prehys = hysData.prev;
  arrow = (hys > prehys) ? "↑" : (hys < prehys ? "↓" : "=");
  alertMessage += `- 하이일드 스프레드 ${hys}${arrow}\n`; 

  // 위험자산 비중  
  alertMessage += `- 위험자산 비중: ${riskAssetRatio}%\n\n`; 

  // [3] 개별 종목 수익률 및 비중 메시지 작성
  portfolio.forEach(item => {
    var currentWeight = item.evalAmount / totalValue;
    
    var change = (((item.currentPrice - item.basePrice) / item.basePrice)*100).toFixed(2);
    var profit = (item.currentPrice - item.basePrice) * item.count;
    
    total_profit += profit;

    if(currentWeight > 0) {
      // 💡 현금일 때와 일반 주식일 때 메시지 포맷 구분
      if (item.name === "현금" || String(item.code) === "0") {
        alertMessage += `[${item.name}]\n. 비중: ${(currentWeight*100).toFixed(1)}%  (잔고: ${formatNumber(item.evalAmount)}원) \n\n`;
      } else {
        var numRsi = Number(item.rsi);
        var numRsiD1 = Number(item.rsi_d1);
        arrow = (numRsi > numRsiD1) ? "↑" : (numRsi < numRsiD1 ? "↓" : "=");
        var displayRsi = isNaN(numRsi) ? item.rsi : numRsi.toFixed(2);

        alertMessage += `[${item.name}]\n. 비중: ${(currentWeight*100).toFixed(1)}%  rsi: ${displayRsi} ${arrow} \n`;
        alertMessage += ` (수익 ${formatNumber(profit)},  ${change}%) \n\n`;
      }
    }
  });

  // [4] 총 자산 및 수익금 표기
  alertMessage +=  `* 총자산 ${formatNumber(totalValue)}\n`; 

  var totalPrincipal = totalValue - total_profit;
  var totalProfitRate = (totalPrincipal > 0) ? ((total_profit / totalPrincipal) * 100).toFixed(2) : 0;
   
  alertMessage +=  `* 총 수익금 ${formatNumber(total_profit)} (${totalProfitRate >= 0 ? '+' : ''}${totalProfitRate}%)`;
    
  sendTelegram(alertMessage);
}

// 기존 실행 함수 호환성
function checkPortfolio() {
  checkPortfolioTEST();
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
    "text": text
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

/** 💡 네이버 현재가 조회 (경량 모바일 JSON API 우선 호출 -> 실패 시 PC HTML 파싱) */
function GET_NAVER_PRICE(code) {
  if (!code) return "";
  var formattedCode = String(code).trim().padStart(6, '0');
  
  // 1. 모바일 JSON API 우선 호출 (다운로드 용량이 1~2KB로 매우 가볍고 빠름)
  try {
    var apiUrl = "https://m.stock.naver.com/api/stock/" + formattedCode + "/integration";
    var apiRes = UrlFetchApp.fetch(apiUrl, {muteHttpExceptions: true});
    if (apiRes.getResponseCode() === 200) {
      var json = JSON.parse(apiRes.getContentText());
      if (json && json.dealTrendInfos && json.dealTrendInfos.length > 0) {
        var closePrice = json.dealTrendInfos[0].closePrice;
        if (closePrice) return Number(String(closePrice).replace(/,/g, ""));
      }
      if (json && json.totalInfos && json.totalInfos.length > 0) {
        var p = json.totalInfos[0].closePrice;
        if (p) return Number(String(p).replace(/,/g, ""));
      }
    }
  } catch (e) {}

  // 2. 실패 시 PC 웹페이지 파싱으로 폴백
  try {
    var pcUrl = "https://finance.naver.com/item/main.naver?code=" + formattedCode;
    var pcRes = UrlFetchApp.fetch(pcUrl, {muteHttpExceptions: true});
    var pcHtml = pcRes.getContentText("EUC-KR");
    
    var pMatch = pcHtml.match(/<p\s+class="no_today"[^>]*>([\s\S]*?)<\/p>/i);
    if (pMatch && pMatch[1]) {
      var spanMatch = pMatch[1].match(/<span\s+class="blind">([0-9,]+)<\/span>/i);
      if (spanMatch && spanMatch[1]) {
        var price = Number(spanMatch[1].replace(/,/g, ""));
        if (!isNaN(price) && price > 0) return price;
      }
    }
  } catch (e) {}

  return "조회 실패";
}

/** 💡 1회 fchart 호출로 오늘 RSI와 전일 RSI를 동시에 계산하는 최적화 함수 */
function GET_NAVER_RSI_BOTH(code, period) {
  if (!code) return { rsi: "-", rsi_d1: "-" };
  code = String(code).padStart(6, '0');
  period = period || 14;
  
  var url = "https://fchart.stock.naver.com/sise.nhn?symbol=" + code + "&timeframe=day&count=100&requestType=0";
  try {
    var response = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
    var items = response.getContentText().match(/<item data="[^"]+" \/>/g);
    
    if (!items || items.length < period + 2) {
      return { rsi: "데이터 부족", rsi_d1: "데이터 부족" };
    }
    
    var allCloses = [];
    for (var i = 0; i < items.length; i++) {
      var parts = items[i].match(/data="([^"]+)"/)[1].split('|');
      allCloses.push(Number(parts[4]));
    }
    
    var computeRsi = function(closes) {
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
      
      if (ad === 0) return 100;
      var rs = au / ad;
      return Number((100 - (100 / (1 + rs))).toFixed(2));
    };

    var rsiToday = computeRsi(allCloses);
    var rsiPrev = computeRsi(allCloses.slice(0, -1));

    return { rsi: rsiToday, rsi_d1: rsiPrev };
  } catch (e) {
    return { rsi: "Error", rsi_d1: "Error" };
  }
}

/** 💡 나스닥 현재가 + 20일선 + 55일선을 1회 호출로 모두 계산하고 5분간 캐싱 */
function GET_NASDAQ_DATA() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get("CACHE_NASDAQ_DATA");
  if (cached) {
    try { return JSON.parse(cached); } catch(e) {}
  }

  var url = "https://query2.finance.yahoo.com/v8/finance/chart/%5EIXIC?range=6mo&interval=1d";
  var options = { muteHttpExceptions: true, headers: { "User-Agent": "Mozilla/5.0" } };
  try {
    var response = UrlFetchApp.fetch(url, options);
    if (response.getResponseCode() !== 200) {
      return { today: "접속차단", m20: 0, m55: 0 };
    }
    var result = JSON.parse(response.getContentText()).chart.result[0];
    var today = Number(result.meta.regularMarketPrice.toFixed(2));
    var closes = result.indicators.quote[0].close.filter(function(val) { return val !== null; });

    var calcSMA = function(period) {
      if (closes.length < period) return 0;
      var recentCloses = closes.slice(-period);
      var sum = 0;
      for (var i = 0; i < recentCloses.length; i++) sum += recentCloses[i];
      return Number((sum / period).toFixed(2));
    };

    var data = {
      today: today,
      m20: calcSMA(20),
      m55: calcSMA(55)
    };
    cache.put("CACHE_NASDAQ_DATA", JSON.stringify(data), 300); // 5분 캐시
    return data;
  } catch(e) {
    return { today: "에러", m20: 0, m55: 0 };
  }
}

/** 💡 VIX 오늘값과 전일값을 1회 호출로 추출하고 5분간 캐싱 */
function GET_VIX_DATA() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get("CACHE_VIX_DATA");
  if (cached) {
    try { return JSON.parse(cached); } catch(e) {}
  }

  var url = "https://query2.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=1d";
  var options = { muteHttpExceptions: true, headers: { "User-Agent": "Mozilla/5.0" } };
  try {
    var response = UrlFetchApp.fetch(url, options);
    if (response.getResponseCode() !== 200) {
      return { today: "접속차단", prev: "접속차단" };
    }
    var result = JSON.parse(response.getContentText()).chart.result[0];
    var today = Number(result.meta.regularMarketPrice.toFixed(2));
    var closes = result.indicators.quote[0].close.filter(function(val) { return val !== null; });
    var prev = closes.length >= 2 ? Number(closes[closes.length - 2].toFixed(2)) : today;
    
    var data = { today: today, prev: prev };
    cache.put("CACHE_VIX_DATA", JSON.stringify(data), 300); // 5분 캐시
    return data;
  } catch(e) {
    return { today: "에러", prev: "에러" };
  }
}

/** 💡 하이일드 스프레드 CSV를 1회만 받아 오늘값/전일값 추출하고 30분간 캐싱 */
function GET_HIGH_YIELD_DATA() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get("CACHE_HY_DATA");
  if (cached) {
    try { return JSON.parse(cached); } catch(e) {}
  }

  var url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2";
  try {
    var csv = UrlFetchApp.fetch(url, {muteHttpExceptions: true}).getContentText();
    var validLines = csv.split('\n').filter(function(line) {
      var parts = line.trim().split(',');
      return parts.length === 2 && parts[0] !== 'DATE' && parts[1] !== '.';
    });
    if (validLines.length === 0) return { today: "데이터 없음", prev: "데이터 없음" };
    if (validLines.length < 2) return { today: Number(validLines[0].split(',')[1]), prev: "데이터 부족" };
    
    var today = Number(validLines[validLines.length - 1].split(',')[1]);
    var prev = Number(validLines[validLines.length - 2].split(',')[1]);
    
    var data = { today: today, prev: prev };
    cache.put("CACHE_HY_DATA", JSON.stringify(data), 1800); // 30분 캐시 (FRED는 1일 1회 업데이트)
    return data;
  } catch(e) {
    return { today: "Error", prev: "Error" };
  }
}
