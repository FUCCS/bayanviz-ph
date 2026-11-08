# Deployment

The repository is configured for a React static build on Netlify and a separate Python FastAPI service on Render. No public deployment has been performed.

1. Push this project directory as a repository to an account you control. Do not commit virtual environments, node_modules, secrets, or the full national extract.
2. Create a Render Blueprint from render.yaml in the repository root. It uses Python 3.14.3, pinned backend requirements, Uvicorn, and /api/health. The build runs at repository root so the packaged `data/` snapshots remain available. Set FRONTEND_ORIGINS to the exact Netlify HTTPS origin after it is assigned. Add only deliberately approved preview origins.
3. Create a Netlify site from the same repository. netlify.toml sets base frontend, build command npm run build, publish dist, and the SPA fallback. Set VITE_API_BASE_URL to the verified Render HTTPS URL and rebuild.
4. Set FRONTEND_ORIGINS on Render to the verified Netlify origin, then verify health, fetch, upload, chart, code, interpretation, and download from the public site.
5. Before launch, review current [Open-Meteo terms](https://open-meteo.com/en/terms), [Render FastAPI instructions](https://render.com/docs/deploy-fastapi), [Netlify configuration](https://docs.netlify.com/build/configure-builds/file-based-configuration/), [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/), and [GitHub file-size guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

The backend uses process-local temporary sessions. Multiple workers require shared temporary storage and distributed limits before scaling. Public API fetches are bounded to three locations and 367 dates per request, and 12 requests per session lifetime. Shared provider responses are cached for one hour in process memory. Add edge-level rate limiting before a broad public launch. Render free instances can sleep, and temporary data disappears on restart. The current OSM sample is not a nationwide inventory.
