---
name: browser
description: Use when the user wants to open or control a web browser — navigate to a site, click, fill forms, log in, search, scrape a page, or do any interactive web task that plain HTTP fetch can't (JS-rendered pages, logins, OAuth, CAPTCHA). The user watches this browser live and can take over for steps only a human can do.
version: 1.0.0
keywords:
  - browser
  - 浏览器
  - navigate
  - website
  - login
  - form
  - click
  - scrape
  - playwright
---

# Browser control (agent-browser + live takeover)

The sandbox runs a **real, headful Chromium** that the user **watches live** in
the cubebox browser panel and can **take over** at any time. You drive that same
browser with the `agent-browser` CLI over CDP. Because the user sees exactly what
you do, this is also how logins / OAuth / CAPTCHAs get solved: you navigate, and
when a step needs a human you ask the user to take over in the panel.

## Always attach first — never launch your own browser

```bash
agent-browser connect 9222
```

This attaches to the **user-visible** Chromium (CDP on `127.0.0.1:9222`). Do this
once at the start. **Never** start a fresh/headless browser — the user would see
nothing and the session wouldn't be theirs.

## Learn the commands from the CLI (don't guess)

```bash
agent-browser skills get core        # workflows + common patterns
agent-browser skills get core --full # full command reference
```

The CLI serves usage that matches the installed version. Common ones:

```bash
agent-browser goto https://example.com   # navigate the visible page
agent-browser snapshot                    # accessibility tree with @eN element refs
agent-browser click @e12                  # click an element by its ref
agent-browser type @e8 "search text"      # type into a field
agent-browser get text|url|title          # read page state
agent-browser screenshot out.png          # save a screenshot to /workspace
```

Target elements by the `@eN` refs from `snapshot` (reliable), not by guessing
selectors. Re-`snapshot` after the page changes.

## When a step needs the human (login / OAuth / 2FA / CAPTCHA)

1. Navigate as far as you can (e.g. to the login page).
2. Tell the user plainly: *"Please take over in the browser panel to log in / solve
   this, then tell me to continue."* The user clicks **Take over** and acts.
3. Wait for the user to confirm, then resume — the session (cookies, localStorage)
   persists because it's the same browser, so you stay logged in for later steps.

Do **not** try to type passwords or solve CAPTCHAs yourself; hand those to the user.

## Don't

- Don't `detach` or close the browser when finishing a task — leave it running for
  the user (the panel and the next task reuse it).
- Don't fall back to `curl`/HTTP fetch for pages that need JS or a login — use the
  browser.
