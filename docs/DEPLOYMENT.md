# Deployment

The React frontend is configured for Netlify and the FastAPI backend for Render. No public deployment has been performed.

1. Push this project directory to a GitHub repository you control. The ignored virtual environment, node_modules, secrets, generated builds, and large prepared files must remain uncommitted.
2. Create a Render Blueprint from render.yaml at repository root. It uses Python 3.14.3, pinned backend requirements, Uvicorn, and /api/health. Set FRONTEND_ORIGINS to the exact Netlify HTTPS origin after assignment.
3. Create a Netlify site from the same repository. netlify.toml sets frontend as the base, runs npm run build, publishes dist, and configures the SPA fallback. Set VITE_API_BASE_URL to the verified Render HTTPS origin and rebuild.
4. Verify the public health endpoint, curated date bounds, Negros Oriental date filtering, climate charts, code/interpretation dialogs, weather API fetch, private upload, map, and exports.
5. Review current [Open-Meteo terms](https://open-meteo.com/en/terms), [Render FastAPI guidance](https://render.com/docs/deploy-fastapi), [Netlify configuration](https://docs.netlify.com/build/configure-builds/file-based-configuration/), [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/), and [GitHub file-size guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) before launch.

The backend keeps sessions in process memory. Multiple workers need shared temporary storage and distributed rate limits. Live requests are bounded to three locations and 367 dates per request, with 12 requests per session; shared provider responses are cached for one hour. Add edge-level abuse controls before broad public launch. Render free instances may sleep, and temporary data disappears after restart. The current five-point weather snapshot is not national coverage.
