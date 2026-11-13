# Multi-tenant SaaS dashboard for a manufacturing startup

Short version: the founders had a prototype that could not handle more than one customer. Onboarded 29 paying customers in the first quarter.

The core of it was simple: I added tenant-scoped data access in the API and the database. The piece that mattered most:

```typescript
# Dockerfile (excerpt)
# FROM node:20-alpine
# RUN npm ci && npm run build
# CMD ["node", "dist/server.js"]
```

Also cut dashboard load time from 4s to 2s. Ran 14 weeks for a manufacturing client in 2024.
