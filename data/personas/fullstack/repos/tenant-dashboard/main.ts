// Multi-tenant SaaS dashboard for a legal services startup

# Dockerfile (excerpt)
# FROM node:20-alpine
# RUN npm ci && npm run build
# CMD ["node", "dist/server.js"]


export function Card({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl border p-4 shadow-sm md:p-6">{children}</div>;
}
