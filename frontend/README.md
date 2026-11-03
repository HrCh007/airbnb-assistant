# Frontend: Delhi Stays guest chat + host console

Next.js 16 (App Router, TypeScript, Tailwind CSS 4, Lucide icons).

- `/`: mobile-first guest chat (location → property → streaming answers, recommendation cards, quick actions, follow-up chips, guest-PIN unlock)
- `/admin`: host console (dashboard, properties, documents, host picks); JWT stored in `localStorage`

```bash
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev            # http://localhost:3000
npm run lint && npm run typecheck && npm run build
npx playwright test    # E2E: needs the backend running (see ../docs/setup.md#testing)
```

Key files: `hooks/useChat.ts` (session + streaming state), `services/api.ts` (REST client and SSE parser),
`components/chat/*`, `components/admin/*`. See the repository root `README.md` for the full picture.
