// The one D1 table (migrations/0001_handles.sql). The daily count moves in the same statement that
// checks the secret, so two pushes at once can never both take the 500th place.

export const DAILY_LIMIT = 500;

export interface Target {
  apns_token: string;
  sandbox: boolean;
}

export type Counted = { kind: 'ok'; target: Target } | { kind: 'unauthorized' } | { kind: 'over_limit' };

export async function addHandle(
  db: D1Database,
  handle: string,
  apnsToken: string,
  secretHash: string,
  sandbox: boolean,
): Promise<void> {
  await db
    .prepare('INSERT INTO handles (handle, apns_token, secret_hash, sandbox) VALUES (?1, ?2, ?3, ?4)')
    .bind(handle, apnsToken, secretHash, sandbox ? 1 : 0)
    .run();
}

/** Counts one push for `day` when the secret is right and the day is not full. */
export async function countPush(
  db: D1Database,
  handle: string,
  secretHash: string,
  day: string,
): Promise<Counted> {
  const row = await db
    .prepare(
      `UPDATE handles
          SET count = CASE WHEN day = ?3 THEN count + 1 ELSE 1 END, day = ?3
        WHERE handle = ?1 AND secret_hash = ?2 AND (day <> ?3 OR count < ?4)
        RETURNING apns_token, sandbox`,
    )
    .bind(handle, secretHash, day, DAILY_LIMIT)
    .first<{ apns_token: string; sandbox: number }>();
  if (row) return { kind: 'ok', target: { apns_token: row.apns_token, sandbox: row.sandbox === 1 } };
  const known = await db
    .prepare('SELECT 1 AS ok FROM handles WHERE handle = ?1 AND secret_hash = ?2')
    .bind(handle, secretHash)
    .first();
  return known ? { kind: 'over_limit' } : { kind: 'unauthorized' };
}

/** True when the handle existed with this secret. */
export async function deleteHandle(db: D1Database, handle: string, secretHash: string): Promise<boolean> {
  const result = await db
    .prepare('DELETE FROM handles WHERE handle = ?1 AND secret_hash = ?2')
    .bind(handle, secretHash)
    .run();
  return result.meta.changes > 0;
}

/** APNs says the token is gone: forget the handle, whatever its secret. */
export async function dropHandle(db: D1Database, handle: string): Promise<void> {
  await db.prepare('DELETE FROM handles WHERE handle = ?1').bind(handle).run();
}
