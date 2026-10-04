import "server-only";

import path from "node:path";

// Share the Python project's .env (Groq key, service URLs) instead of duplicating it.
// Variables already set in the environment win.
try {
  process.loadEnvFile(path.resolve(process.cwd(), "..", ".env"));
} catch {
  // No ../.env: rely on the environment.
}
