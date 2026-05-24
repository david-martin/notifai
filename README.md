# notifai

Get an email when something you care about happens.

Define a query in plain English — *"have Stray Kids announced European tour dates for this year?"*, *"is the home heating oil price back to pre-war levels?"* — and notifai checks daily and emails you when the answer is YES.

**Disclaimer! - This project makes use of genAI.**

---

## Self-hosted

Run it yourself. You'll need:

- An [Anthropic API key](https://console.anthropic.com)
- A [Resend API key](https://resend.com) (free tier is fine)
- Somewhere to run a daily cron (GitHub Actions works out of the box)

```bash
git clone https://github.com/david-martin/notifai
cd notifai
pip install -r requirements.txt

# Add your queries
cp queries.yaml my-queries.yaml  # edit to taste, or use assist.py

# Run
ANTHROPIC_API_KEY=... RESEND_API_KEY=... NOTIFY_EMAIL=you@example.com python check.py
```

To schedule daily runs, fork the repo, add the three keys as GitHub secrets (`ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `NOTIFY_EMAIL`), and the included workflow runs at 7am UTC automatically.

### Adding queries interactively

```bash
ANTHROPIC_API_KEY=... python assist.py
```

Prompts you for what to track, generates a well-formed query, and appends it to `queries.yaml`.

---

## How it works

- Loads all active queries
- For each query, asks Claude to search the web and answer: is this condition met today?
- If **YES** → sends a plain-text email with the reason and sources
- If **NO** → silent. No noise.

---

## What notifai is (and isn't) for

notifai uses Claude with live web search to evaluate a natural-language condition once a day. That makes it the right tool for a specific kind of problem.

**The sweet spot: conditions that require judgment**

notifai works best when the answer lives in unstructured text — news articles, press releases, announcements, social posts — and requires reading and interpreting rather than just fetching a number. There's no API for "have Stray Kids announced European tour dates?". The answer is scattered across fan sites, ticketing platforms, and social posts. Claude can find and synthesise it; a script can't.

Good fits:

- Concert, tour, or event announcements
- Product launches, especially from smaller or niche companies
- Regulatory or policy decisions ("has the EU passed X?")
- Soft comparisons — "is the price roughly back to pre-X levels?"
- Cultural events: renewals, sequels, casting announcements
- Visa or travel policy changes
- Any condition where the answer requires reading the web, not querying a number

**When to reach for something else**

If your condition has a clean, structured data source behind it, a dedicated tool will be faster, cheaper, and more reliable:

| Condition | Better tool |
|---|---|
| Price crosses a threshold | Broker alerts, exchange notifications, a price API with a webhook |
| Website goes down | Uptime Robot, Better Uptime, Freshping |
| New GitHub release | Watch the repo, GitHub release notifications |
| New video or post | YouTube notifications, RSS |
| Package delivered | Carrier app, a parcel tracker |
| Weather alert | Any weather app or service |
| Sports result | ESPN, your sports app |
| New post on a blog or site | RSS reader |

The rule of thumb: if the answer to your question is a number compared against a threshold, or a structured feed you could subscribe to, there's a better tool for it. notifai is for the questions where those don't exist.

---

## Hosted

Don't want to manage API keys or a scheduler? I run a hosted version at **[notifai.davecloud.dev](https://notifai.davecloud.dev)** — sign up and start tracking things without any setup.

### How query creation works on the hosted version

1. You describe what you want to be notified about in plain English
2. A guard model checks whether it's a monitorable event — specific, real-world, checkable via web search
3. If it needs reframing (e.g. you wrote a question instead of an event description), it suggests a corrected version — you confirm or edit it
4. If it can't be salvaged (too vague, not a real event), you get feedback and can try again — one retry allowed
5. Once valid, a second model generates the precise daily search query
6. You see a preview of exactly what will be checked each day and confirm
7. From the next scheduled run onwards, your query is live

Two AI calls per query creation, no exceptions.
