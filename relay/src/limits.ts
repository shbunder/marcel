// Registration limits (push-relay 0.1.1): the relay's address is public, so registrations are capped
// per client address per hour and for the whole relay per day. Each count moves in one upsert that
// also checks the cap, so a burst can never pass it. Only a SHA-256 of the hour and the address is
// kept, and rows of past hours are deleted as registrations come in.

import { sha256Hex } from './crypto';

export const PER_ADDRESS_PER_HOUR = 20;
export const PER_DAY = 2000;

export type Allowed = { kind: 'ok' } | { kind: 'over'; retryAfterS: number };

async function take(db: D1Database, key: string, period: string, limit: number): Promise<boolean> {
  const row = await db
    .prepare(
      `INSERT INTO register_counts (key, period, count) VALUES (?1, ?2, 1)
       ON CONFLICT (key) DO UPDATE
          SET count = CASE WHEN register_counts.period = excluded.period THEN register_counts.count + 1 ELSE 1 END,
              period = excluded.period
        WHERE register_counts.period <> excluded.period OR register_counts.count < ?3
       RETURNING count`,
    )
    .bind(key, period, limit)
    .first();
  return row !== null;
}

function secondsUntil(now: Date, next: Date): number {
  return Math.max(1, Math.ceil((next.getTime() - now.getTime()) / 1000));
}

/** Counts one registration from `address` (`CF-Connecting-IP`), or says how long to wait. */
export async function allowRegistration(db: D1Database, address: string, now: Date): Promise<Allowed> {
  const hour = now.toISOString().slice(0, 13);
  const day = hour.slice(0, 10);
  await db.prepare("DELETE FROM register_counts WHERE key LIKE 'ip:%' AND period <> ?1").bind(hour).run();
  const nextHour = new Date(`${hour}:00:00Z`);
  nextHour.setUTCHours(nextHour.getUTCHours() + 1);
  if (!(await take(db, `ip:${await sha256Hex(`${hour}|${address}`)}`, hour, PER_ADDRESS_PER_HOUR))) {
    return { kind: 'over', retryAfterS: secondsUntil(now, nextHour) };
  }
  const nextDay = new Date(`${day}T00:00:00Z`);
  nextDay.setUTCDate(nextDay.getUTCDate() + 1);
  if (!(await take(db, 'all', day, PER_DAY))) return { kind: 'over', retryAfterS: secondsUntil(now, nextDay) };
  return { kind: 'ok' };
}
