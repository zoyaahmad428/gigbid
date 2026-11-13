// Third-party API integration layer for a fintech platform

# Dockerfile (excerpt)
# FROM node:20-alpine
# RUN npm ci && npm run build
# CMD ["node", "dist/server.js"]


import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";

const s3 = new S3Client({});
await s3.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: body }));
