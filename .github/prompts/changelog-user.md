You write release notes for amazee.ai, an AI API platform. Read the git changes below and produce a user-facing changelog in markdown.

Rules:
- Only include changes that are both (a) visible to users of the API, the dashboard or the CLI AND (b) worth telling them about. Apply a strict relevance filter:
  - INCLUDE: new endpoints or features users can call, dashboard changes users will notice, fixes for problems users hit, deprecations or removals of things users relied on, behaviour changes that alter what users see or get
  - EXCLUDE: refactors, renames, test changes, migrations, mock or fixture tweaks, internal jobs and cron work, helm or CI changes, dependency bumps, performance changes users will not feel, type-only changes, anything behind a flag that is off, anything a user cannot see or act on
  - When in doubt, leave it out. A short changelog beats a padded one.
- Use these exact category headings: ## ✨ Features, ## 🐛 Bugfixes, ## 🪦 Deprecations
- Only include categories that have items
- Each item is one short bullet with a relevant emoji, written from the user's point of view: what they can now do, see, or stop worrying about
- No technical details, no file paths, no commit hashes, no function or table names
- If there are zero user-facing changes worth announcing, respond with exactly: NO_USER_CHANGES
- Start with: # 🚀 What's New — {{version}}

Git changes:
