// Real-time messaging feature for a e-commerce marketplace

import { createClient } from "redis";

const redis = createClient({ url: process.env.REDIS_URL });
export async function once(key: string) {
  return (await redis.set(key, "1", { NX: true, EX: 86400 })) === "OK";
}


import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";

const s3 = new S3Client({});
await s3.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body }));
