// GraphQL backend for a fintech mobile app

import { createClient } from "redis";

const redis = createClient({ url: process.env.REDIS_URL });
export async function once(key: string) {
  return (await redis.set(key, "1", { NX: true, EX: 86400 })) === "OK";
}


import { Pool } from "pg";

const pool = new Pool({ connectionString: process.env.DATABASE_URL });
export const byTenant = (tenantId: string) =>
  pool.query("SELECT * FROM projects WHERE tenant_id = $1", [tenantId]);
