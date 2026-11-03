// Pure storage logic; the Durable Object supplies transactional storage.
export class NotificationLedger {
  constructor(storage) { this.storage = storage; }
  async read() {
    const record = await this.storage.get("record");
    return record ? {...record.snapshot, notification_revision: record.revision} : null;
  }
  async commit(snapshot, expectedRevision) {
    return this.storage.transaction(async transaction => {
      const current = await transaction.get("record");
      if ((current?.revision || 0) !== expectedRevision) throw new Error("notification state changed; retry evaluation");
      if(current && snapshot.calculation_basis?.version && (current.snapshot.calculation_basis?.version!==snapshot.calculation_basis.version || snapshot.basis_recovery?.revision!==current.snapshot.basis_recovery?.revision)){
        if(Object.values(current.claims||{}).some(lease=>lease.until>Date.now()))throw new Error('delivery in progress; retry basis recovery after the lease expires');
        const backupKey=`basis-backup:${snapshot.calculation_basis.version}:${snapshot.basis_recovery?.revision||'initial'}`;
        if(!await transaction.get(backupKey))await transaction.put(backupKey,current);
      }
      const next = {snapshot, revision: expectedRevision + 1, claims: current?.claims || {},receipts:current?.receipts||[]};
      await transaction.put("record", next);
      return next.revision;
    });
  }
  async claim(eventIds, owner, now = Date.now()) {
    return this.storage.transaction(async transaction => {
      const current = await transaction.get("record");
      if (!current) return [];
      const ids = new Set(eventIds), claimed = [];
      current.claims ||= {};
      for (const event of current.snapshot.notification_outbox || []) {
        const lease = current.claims[event.event_id];
        if (!ids.has(event.event_id) || lease && lease.until > now && lease.owner !== owner) continue;
        current.claims[event.event_id] = {owner, until: now + 120_000};
        claimed.push(event.event_id);
      }
      await transaction.put("record", current);
      return claimed;
    });
  }
  async ack(eventIds, owner, messageId) {
    return this.storage.transaction(async transaction => {
      const current = await transaction.get("record");
      if (!current) return;
      const ids = new Set(eventIds.filter(id => current.claims?.[id]?.owner === owner));
      current.snapshot.notification_outbox = (current.snapshot.notification_outbox || []).filter(event => !ids.has(event.event_id));
      current.receipts = [...(current.receipts || []), ...[...ids].map(id => ({event_id:id, message_id:messageId, sent_at:new Date().toISOString()}))].slice(-500);
      for (const id of ids) delete current.claims[id];
      current.revision++;
      await transaction.put("record", current);
    });
  }
}

export function nextRecoveryTime(ready, now = Date.now()) {
  if (!ready) return now + 5 * 60_000;
  const local = new Date(now + 9 * 3600_000);
  const next = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate(), 8, 15) - 9 * 3600_000;
  let value = next > now ? next : next + 86400_000;
  while ([0,6].includes(new Date(value + 9 * 3600_000).getUTCDay())) value += 86400_000;
  return value;
}
