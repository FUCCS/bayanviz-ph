# Deployment

The frontend is hosted at [bayanviz.netlify.app](https://bayanviz.netlify.app/) and the FastAPI backend at [bayanviz-ph-api.onrender.com](https://bayanviz-ph-api.onrender.com/api/health). Netlify builds from `FUCCS/bayanviz-ph` with `VITE_API_BASE_URL` set to the Render HTTPS origin. Render runs the `bayanviz-ph-api` Blueprint with `FRONTEND_ORIGINS=https://bayanviz.netlify.app`.

1. Push this project to the GitHub repository connected to Netlify and Render.
2. Check the Render health endpoint and wait for the new backend deployment to finish. Free instances may take time to wake after inactivity.
3. Verify a fresh session starts with no dataset. Upload a weather file, fetch a seven-day weather period, and switch between them using the Dataset dropdown.
4. Validate a mixed upload/API multi-source selection, then inspect charts, maps, code, reports, and exports. The first request may wait for a free Render instance to wake; repeated provider requests can use the in-memory cache.

Anonymous sessions and uploads are stored in process memory for three hours; a restart erases them. Multiple workers would need shared session storage. No observations are bundled with the application.
