# osopi. — Your ads, on autopilot.

Paste a landing URL and I'll:

1. Scrape it to extract brand identity and ad copy.
2. Design scroll-stopping ad creatives (1080×1080 PNG, hosted on Tigris).
3. Auto-discover your Meta ad account + page via pipeboard and publish
   the campaign — PAUSED by default.

Every step is mirrored live in the UI: the coordinator → subagent → tool
tree opens right here as the run progresses, and rendered creatives + brand
research are surfaced as styled cards. Full trace (token + cost per step)
ships to Langfuse when configured.

**Example prompts**

- `Build a Meta ad for https://acme.example.com`
- `Creative bake-off for https://acme.example.com, $25/day`
- `How is campaign abc123 doing so far?`
- `Go live on campaign abc123`
