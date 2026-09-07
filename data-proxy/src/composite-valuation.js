const PRICE_MEDIAN_DAYS = 756;
const PERCENTILE_DAYS = 1260;
const MIN_PRICE_DAYS = 504;
const MIN_PERCENTILE_DAYS = 504;
const CASH_RETURN_DAYS = 63;
const DIVIDEND_LOOKBACK_MS = 365 * 86400000;

function lowerBound(values, target) {
  let low = 0, high = values.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (values[middle] < target) low = middle + 1;
    else high = middle;
  }
  return low;
}

function upperBound(values, target) {
  let low = 0, high = values.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (values[middle] <= target) low = middle + 1;
    else high = middle;
  }
  return low;
}

function rollingSorted(values, window, minimum, selector) {
  const sorted = [], output = Array(values.length).fill(NaN);
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (Number.isFinite(value)) sorted.splice(lowerBound(sorted, value), 0, value);
    const expired = index >= window ? values[index - window] : NaN;
    if (Number.isFinite(expired)) sorted.splice(lowerBound(sorted, expired), 1);
    if (Number.isFinite(value) && sorted.length >= minimum) output[index] = selector(sorted, value);
  }
  return output;
}

function rollingMedian(values, window, minimum) {
  return rollingSorted(values, window, minimum, (sorted) => {
    const middle = sorted.length >> 1;
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  });
}

function rollingPercentile(values) {
  return rollingSorted(
    values,
    PERCENTILE_DAYS,
    MIN_PERCENTILE_DAYS,
    (sorted, value) => upperBound(sorted, value) / sorted.length,
  );
}

function alignedSeries(primary, secondary) {
  const sorted = [...secondary].sort((left, right) => left.Date.localeCompare(right.Date));
  const output = [];
  let index = 0, last = null;
  for (const row of primary) {
    while (index < sorted.length && sorted[index].Date <= row.Date) {
      last = sorted[index];
      index += 1;
    }
    output.push(last);
  }
  return output;
}

export function addCompositeValuationScore(data) {
  if (!data.QQQ?.length || !data.SPY?.length || !data.BIL?.length) {
    throw new Error("합성 밸류에이션에는 QQQ, SPY, BIL 데이터가 필요합니다.");
  }
  const qqq = [...data.QQQ].sort((left, right) => left.Date.localeCompare(right.Date));
  const spy = alignedSeries(qqq, data.SPY);
  const bil = alignedSeries(qqq, data.BIL);
  const qClose = qqq.map((row) => Number(row.Close));
  const spyClose = spy.map((row) => Number(row?.Close));
  const bilClose = bil.map((row) => Number(row?.Close));
  const relative = qClose.map((value, index) => Math.log(value / spyClose[index]));
  const relativeMedian = rollingMedian(relative, PRICE_MEDIAN_DAYS, MIN_PRICE_DAYS);
  const qqqMedian = rollingMedian(qClose, PRICE_MEDIAN_DAYS, MIN_PRICE_DAYS);
  const relativePremium = relative.map((value, index) => value - relativeMedian[index]);
  const stretch = qClose.map((value, index) => value / qqqMedian[index] - 1);
  const cashYield = bilClose.map((value, index) => index < CASH_RETURN_DAYS
    ? NaN
    : (value / bilClose[index - CASH_RETURN_DAYS]) ** 4 - 1);

  const dividendYield = Array(qqq.length).fill(NaN);
  let dividendStart = 0, dividendSum = 0;
  for (let index = 0; index < qqq.length; index += 1) {
    dividendSum += Number(qqq[index].Dividends || 0);
    const cutoff = Date.parse(`${qqq[index].Date}T00:00:00Z`) - DIVIDEND_LOOKBACK_MS;
    while (dividendStart < index && Date.parse(`${qqq[dividendStart].Date}T00:00:00Z`) <= cutoff) {
      dividendSum -= Number(qqq[dividendStart].Dividends || 0);
      dividendStart += 1;
    }
    const rawClose = Number(qqq[index].RawClose ?? qqq[index].Close);
    dividendYield[index] = rawClose > 0 ? dividendSum / rawClose : NaN;
  }

  const relativeRank = rollingPercentile(relativePremium);
  const stretchRank = rollingPercentile(stretch);
  const rateRank = rollingPercentile(cashYield);
  const dividendRank = rollingPercentile(dividendYield);
  const composite = qqq.map((_, index) => (
    relativeRank[index] * 0.25
    + stretchRank[index] * 0.25
    + rateRank[index] * 0.35
    + (1 - dividendRank[index]) * 0.15
  ) * 100);

  let month = "", previousMonthScore = NaN, activeScore = NaN;
  data.QQQ = qqq.map((row, index) => {
    const nextMonth = row.Date.slice(0, 7);
    if (nextMonth !== month) {
      activeScore = previousMonthScore;
      month = nextMonth;
    }
    previousMonthScore = composite[index];
    return {...row, VALUATION_SCORE: activeScore};
  });
  return data;
}
