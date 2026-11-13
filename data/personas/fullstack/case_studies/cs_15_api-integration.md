# Third-party API integration layer for a fintech platform

Short version: data from several partner systems was copied across by hand every day. Removed about 11 hours of manual copying per week.

The core of it was simple: I normalised partner data into one schema. The piece that mattered most:

```typescript
# Dockerfile (excerpt)
# FROM node:20-alpine
# RUN npm ci && npm run build
# CMD ["node", "dist/server.js"]
```

Also kept sync failures below 2%. Ran 16 weeks for a fintech client in 2026.
