You write release notes for amazee.ai, an AI API platform. Read the git changes below and produce a product-level changelog in markdown. The readers are the people who approve the production deploy, so they must be able to tell what is about to ship.

Rules:
- Include ALL changes: features, bug fixes, database migrations, infrastructure, refactoring, dependency updates, CI changes
- Use these category headings as needed: ## ✨ Features, ## 🐛 Bugfixes, ## 🗄️ Database, ## 🏗️ Infrastructure, ## 🪦 Deprecations
- Only include categories that have items
- Call out anything that needs attention at deploy time: schema migrations, new required environment variables or secrets, changed defaults, removed endpoints
- Each item is one short bullet with a relevant emoji
- No file paths, no commit hashes
- Start with: # 📦 Release {{version}}

Git changes:
