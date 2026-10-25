# Jar frontend redesign prototype

A standalone React/Vite prototype for Jar's redesigned dashboard. It intentionally lives separately from the production FastAPI interface in `app/static` so the existing application remains functional while the redesign is integrated with live endpoints.

```bash
cd frontend
npm install
npm run dev
```

Current data is representative preview data. The next integration step is mapping the components to Jar's existing API endpoints and replacing the corresponding templates under `app/static`.
