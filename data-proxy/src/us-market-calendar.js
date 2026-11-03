// Equity-market closures. Sources: https://www.nyse.com/markets/hours-calendars
// and https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2024-87 . Early closes
// remain trading sessions; completion checks conservatively wait until 16:15 ET.
const exceptional=new Set(['2004-06-11','2007-01-02','2012-10-29','2012-10-30','2018-12-05','2025-01-09']);
const key=date=>date.toISOString().slice(0,10);
function observed(year,month,day){
  const date=new Date(Date.UTC(year,month-1,day)),weekday=date.getUTCDay();
  date.setUTCDate(date.getUTCDate()+(weekday===6?-1:weekday===0?1:0));return key(date);
}
function nth(year,month,weekday,number){
  const first=new Date(Date.UTC(year,month-1,1));
  return key(new Date(Date.UTC(year,month-1,1+(weekday-first.getUTCDay()+7)%7+7*(number-1))));
}
function easter(year){
  const a=year%19,b=Math.floor(year/100),c=year%100,d=Math.floor(b/4),e=b%4,f=Math.floor((b+8)/25),g=Math.floor((b-f+1)/3);
  const h=(19*a+b-d-g+15)%30,i=Math.floor(c/4),k=c%4,l=(32+2*e+2*i-h-k)%7,m=Math.floor((a+11*h+22*l)/451);
  return new Date(Date.UTC(year,Math.floor((h+l-7*m+114)/31)-1,(h+l-7*m+114)%31+1));
}
export function isUsEquitySession(date){
  const value=new Date(`${date}T12:00:00Z`),year=value.getUTCFullYear();
  if([0,6].includes(value.getUTCDay())||exceptional.has(date))return false;
  const goodFriday=easter(year);goodFriday.setUTCDate(goodFriday.getUTCDate()-2);
  const memorial=new Date(Date.UTC(year,5,0));memorial.setUTCDate(memorial.getUTCDate()-(memorial.getUTCDay()+6)%7);
  const holidays=new Set([nth(year,1,1,3),nth(year,2,1,3),key(goodFriday),key(memorial),observed(year,7,4),nth(year,9,1,1),nth(year,11,4,4),observed(year,12,25)]);
  // Unlike most observed holidays, a Saturday New Year's Day does not close
  // the preceding Friday at NYSE/Nasdaq.
  if(new Date(Date.UTC(year,0,1)).getUTCDay()!==6)holidays.add(observed(year,1,1));
  if(year>=2022)holidays.add(observed(year,6,19));
  return !holidays.has(date);
}
export function previousUsEquitySession(date,includeDate=false){
  const value=new Date(`${date}T12:00:00Z`);
  if(!includeDate)value.setUTCDate(value.getUTCDate()-1);
  while(!isUsEquitySession(key(value)))value.setUTCDate(value.getUTCDate()-1);
  return key(value);
}
