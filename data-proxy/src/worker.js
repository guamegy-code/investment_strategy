import {DurableObject} from "cloudflare:workers";
import worker, {notificationEvaluation, notificationStore,refreshNotificationTicker,loadSharedValuation,recoverValuationState,initializeValuationState} from "./index.js";
import {NotificationLedger, nextRecoveryTime} from "./notification-ledger.js";
import {buildValuationTable} from './valuation-basis.js';
import {rebuildNotificationBasis,strategySnapshot,runStrategyIncremental,addIndicators,buildTdf2050Proxy} from './strategy-runtime.js';

export class ValuationData extends DurableObject {
  table(refresh=false) {
    if(!this.tableWork)this.tableWork=loadSharedValuation({...this.env,VALUATION_DATA:null},refresh).finally(()=>{this.tableWork=null;});
    return this.tableWork;
  }
  recover(definitions,definition,previous,basis) {return recoverValuationState({...this.env,VALUATION_DATA:null},definitions,definition,previous,basis);}
  initializeModel(definitions,definition){return initializeValuationState({...this.env,VALUATION_DATA:null},definitions,definition);}
  async refresh(ticker,options){
    if(!this.refreshWork)this.refreshWork=refreshNotificationTicker({...this.env,VALUATION_DATA:null},ticker,options).finally(()=>{this.refreshWork=null;});
    const result=await this.refreshWork;
    // Full history remains in shared storage; ordinary evaluations transfer
    // only recent, already prepared observations across the RPC boundary.
    return {...result,rows:result.rows.slice(-640)};
  }
  indicators(rows) { return addIndicators(rows); }
  proxy(data) { return buildTdf2050Proxy(data); }
  async build(input) {
    const previous=await this.ctx.storage.get('table');
    if(previous?.revision===input.revision)return previous;
    const table=buildValuationTable(input);
    await this.ctx.storage.put('table',table);
    return table;
  }
}

export class NotificationState extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.ledger = new NotificationLedger(ctx.storage); }
  read() { return this.ledger.read(); }
  commit(snapshot, revision) { return this.ledger.commit(snapshot, revision); }
  claim(ids, owner) { return this.ledger.claim(ids, owner); }
  ack(ids, owner, messageId) { return this.ledger.ack(ids, owner, messageId); }
  rebuild(definitions,definition,data,previous,basis) { return rebuildNotificationBasis(definitions,definition,data,previous,basis); }
  initialize(definitions,definition,data) { return strategySnapshot(definitions,definition,data); }
  evaluate(definitions,definition,data,previous) { return runStrategyIncremental(definitions,definition,data,previous); }
}

export class NotificationRecovery extends DurableObject {
  async pause() { await this.ctx.storage.delete("job"); await this.ctx.storage.deleteAlarm(); }
  async ensureScheduled() {
    const job=await this.ctx.storage.get("job");
    if(job && !(await this.ctx.storage.getAlarm())) await this.ctx.storage.setAlarm(Math.max(Date.now()+1000,job.next));
  }
  async register(input, freshness) {
    const previous = await this.ctx.storage.get("job");
    const delivery = input.delivery?.token && input.delivery?.chat_id ? input.delivery : previous?.input.delivery || null;
    if (delivery && (!/^\d+:[\w-]+$/.test(delivery.token) || !/^-?\d+$/.test(delivery.chat_id))) throw new Error("invalid delivery configuration");
    if (input.recovery_schedule && (!Array.isArray(input.recovery_schedule) || input.recovery_schedule.length > 10 || input.recovery_schedule.some(value=>!Number.isFinite(Date.parse(value))))) throw new Error("invalid recovery calendar");
    const scheduled = input.recovery_schedule?.map(Date.parse).filter(value=>value>Date.now()).sort((a,b)=>a-b)[0];
    const next = freshness === "fresh" && scheduled ? scheduled : nextRecoveryTime(freshness === "fresh");
    await this.ctx.storage.put("job", {...previous, version:(previous?.version || 0)+1, input:{...input, delivery}, retries:previous?.retries || 0, next});
    const current = await this.ctx.storage.getAlarm();
    if (!current || current > next) await this.ctx.storage.setAlarm(next);
    const indexKey=`notification-recovery-client:${input.client_id}`;
    if (!(await this.env.MARKET_DATA.get(indexKey))) await this.env.MARKET_DATA.put(indexKey,input.client_id);
  }
  async telegram(delivery, text) {
    const result = await fetch(`https://api.telegram.org/bot${delivery.token}/sendMessage`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({chat_id:delivery.chat_id, text}), signal:AbortSignal.timeout(20_000),
    });
    const payload = await result.json();
    if (!result.ok || !payload.ok) {
      const error = new Error(`Telegram delivery failed (${result.status})`);
      error.retryAfter = Number(payload.parameters?.retry_after || 0);
      throw error;
    }
    return payload.result.message_id;
  }
  async alarm() {
    const job = await this.ctx.storage.get("job");
    if (!job) return;
    if (job.next > Date.now()+1000) { await this.ctx.storage.setAlarm(job.next); return; }
    job.retryAfter = 0;
    let ready = false, errorText = null;
    try {
      const body = {...job.input, background:true};
      delete body.delivery;
      const request = new Request("https://internal/notification-evaluations", {method:"POST", headers:{Authorization:`Bearer ${this.env.NOTIFICATION_API_KEY}`, "Content-Type":"application/json"}, body:JSON.stringify(body)});
      const reply = await notificationEvaluation(request, this.env);
      const payload = await reply.json();
      ready = payload.freshness === "fresh" && payload.evaluations.every(item => item.evaluation_status === "READY");
      for (const evaluation of payload.evaluations) {
        const events = evaluation.alerts || [];
        if (!events.length) continue;
        if (!job.input.delivery) { ready = false; continue; }
        const owner = crypto.randomUUID(), store = notificationStore(this.env, evaluation.strategy_id, job.input.client_id);
        const claimed = new Set(await store.claim(events.map(event => event.event_id), owner));
        ready = false;
        const deliverable = events.filter(event => claimed.has(event.event_id));
        if (deliverable.length) {
          const currentJob=await this.ctx.storage.get("job");
          if(!currentJob || currentJob.version!==job.version)return;
          const event = deliverable.at(-1), current = evaluation.scheduled_summary || event;
          const allocation = Object.entries(current.target_weights || {}).map(([ticker,weight]) => `${event.product_names?.[ticker] || ticker}: ${(weight*100).toFixed(1)}%`).join("\n");
          const history = deliverable.map(item => `${item.market_data_at} ${item.type}: ${item.reason_text || ""}`).join("\n");
          const text = `[자동 복구 · 지연 알림 ${deliverable.length}건]\n${event.strategy_name}\n현재 평가 기준일: ${evaluation.market_data_at}\n안내 시각: ${new Date().toISOString()}\n현재 목표\n${allocation}\n누락된 신호\n${history}\n지연 안내이므로 현재 상태와 실행 가능 시점을 확인하세요.`;
          const messageId = await this.telegram(job.input.delivery, text.slice(0,4000));
          await store.ack(deliverable.map(item => item.event_id), owner, messageId);
        }
      }
      if (!ready) errorText = "필수 시장 자료 또는 미발송 알림의 복구가 진행 중입니다.";
    } catch (error) {
      // Never persist upstream exception strings which may include credential URLs.
      errorText = "알림 자동 복구를 완료하지 못했습니다. 저장된 작업을 다시 시도합니다.";
      job.retryAfter = error.retryAfter || 0;
    }
    if (errorText && job.input.delivery && Date.now() - (job.lastNotice || 0) >= 30*60_000) {
      try { await this.telegram(job.input.delivery, `[투자 전략 오류]\n${errorText}`); job.lastNotice = Date.now(); }
      catch { /* Keep the work and retry independently of the error notice. */ }
    }
    job.retries = ready ? 0 : job.retries + 1;
    job.last_error = errorText;
    const scheduled = job.input.recovery_schedule?.map(Date.parse).filter(value=>value>Date.now()).sort((a,b)=>a-b)[0];
    job.next = ready ? scheduled || nextRecoveryTime(true) : Date.now() + Math.max(job.retryAfter*1000, Math.min(30*60_000, 60_000*2**Math.min(job.retries,5)));
    await this.ctx.storage.transaction(async transaction=>{
      const current=await transaction.get("job");
      if (!current || current.version !== job.version) return;
      await transaction.put("job",job);
      await transaction.setAlarm(job.next);
    });
  }
}

export default worker;
