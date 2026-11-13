# Fast marketing site and CMS for a media company

Short version: the old site was slow, hard to edit and ranked poorly. Raised Lighthouse performance from 42 to 98.

The core of it was simple: I connected a headless CMS so the marketing team could publish alone. The piece that mattered most:

```typescript
export type Result<T> = { ok: true; value: T } | { ok: false; error: string };
```

Also grew organic traffic by 28% over three months. Ran 7 weeks for a media client in 2021.
