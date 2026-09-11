# Agent Instructions

You're working inside the **WAT framework** (Workflows, Agents, Tools).

## The WAT Architecture

**Layer 1: Workflows** — Markdown SOPs in `workflows/`
**Layer 2: Agents** — Your role: read workflows, run tools, handle failures
**Layer 3: Tools** — Python scripts in `tools/` for deterministic execution

## How to Operate

1. Look for existing tools before building new ones
2. Learn and adapt when things fail — update workflows with findings
3. Keep workflows current as the project evolves

## File Structure

```
.tmp/       # Temporary files — regenerated as needed
tools/      # Python scripts
workflows/  # Markdown SOPs
.env        # Credentials — NEVER commit this
```

## Project: Kerger

- **Server:** https://kerger.odoo.com
- **DB:** kerger
- **Design system:** claude.ai/design project "Kerger Design System" (`b14a74a0-f823-4b71-a694-5c83ee794fa8`) — fetch tokens via DesignSync; treat it as the source of truth for brand values
- **Brand colors:** blue `#00B9F2`, orange `#FF5B1E` (only two in source art, flat fields — no gradients); deep navy `#0B2942` for inverse surfaces; ink `#0B1B26` for text; cool blue-grey neutral ramp (`#F4F7F9`…`#0F1720`)
- **Brand fonts:** Outfit (display/headings), Inter (body), IBM Plex Mono (SKUs) — Google Fonts substitutions, exact brand face unknown (logo is outlined paths)
- **Design direction:** formal-but-warm B2B wholesaler (marine/offshore electrotechnical); white surfaces, thin subtle borders, small radii (3–10px), navy-tinted shadows on hover only, duotone-blue marine photography, no emoji/no hype copy
- **Logo:** lockup in design system at `assets/logo/kerger-logo-lockup.png` (white KERGER on blue field, orange payoff band)

> ⚠️ This server has no separate staging instance on record — treat it as live. Always use `--dry-run` before any migration and double-check before publishing.

## Key Workflows

- `workflows/design_page.md` — designing and building pages
- `workflows/push_to_odoo.md` — pushing content to Odoo as a **website page** (`website.page`)
- `workflows/create_blog_post.md` — creating and publishing **blog posts** (`blog.post`) — use this, NOT push_to_odoo, for blog content
- `workflows/css_theming.md` — CSS injection pattern
- `workflows/design_survey.md` — designing and creating Odoo surveys
- `workflows/migrate_staging_to_prod.md` — staging → production migration

## Rules

- Always read a workflow before starting a task that matches it
- Never store secrets anywhere except `.env`
- Validate HTML before pushing: `python3 tools/validate_html.py --input .tmp/draft.html`
- Always dry-run before migrating to production
