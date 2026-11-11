# Auction Watch

A Python command-line prototype for watching **GovDeals** sellers, recording auction details in SQLite, estimating rough resale value, and emailing alerts for listings that are closing soon. An optional GSA Auctions adapter is included for federal surplus.

> **Maintenance status — November 11, 2026:** This is an inactive personal project, shared for reference and experimentation. No ongoing feature work, releases, or support response times are planned. Issues and pull requests may go unanswered.

**Read this first:** GovDeals detail extraction was not validated end to end in the original cloud environment because the site returned Access Denied. The GSA adapter's live response mapping is also unverified. Treat this as a starting point for your own testing, rather than a ready-to-run monitoring service.

## What it does

- Discovers GovDeals listing URLs from a public sitemap and filters by seller ID.
- Opens watched listings with Playwright and maps captured page JSON or rendered text into price, title, reserve status, and closing time.
- Optionally sends listing information to Anthropic for an estimated resale range.
- Sends SMTP email alerts and records each sent alert to avoid repeating the same closing alert.
- Stores listings locally in SQLite. It does not place bids, buy items, or continuously monitor by itself.

GovDeals detail requests come from the loaded page; the tool does not supply a private GovDeals API token. Website markup, access policies, and response fields can change. Check current source rules, keep the configured delay, and stop if access is denied; no connection type guarantees access.

## Install and configure

You need Python, a virtual environment, and Playwright's Chromium browser. Python 3.12 is a reasonable starting environment; this repository does not pin or provide a tested Python compatibility matrix.

```bash
git clone https://github.com/connorkeetch/auction-watch.git
cd auction-watch
python -m venv .venv
```

Activate the environment:

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Then install and create your private configuration:

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python -c "from pathlib import Path; Path('config.yaml').write_text(Path('config.example.yaml').read_text())"
```

Edit [config.yaml using the example](config.example.yaml) before running anything:

| Setting | What to choose |
|---|---|
| `sellers` | GovDeals seller IDs. In `/en/asset/<assetId>/<sellerId>`, the second number is the seller ID. The example's `7735` is a specific seller, not an all-site search. |
| `keywords` | Case-insensitive title substrings; any one match is enough. Use `[]` for all titles. |
| `crawl.delay_seconds` | The example uses six seconds between listing pages. Keep requests limited. |
| `valuation.backend` | Set `none` to avoid paid LLM calls. The example defaults to `llm`. |
| `valuation.model` | A model available to your own Anthropic account, when using `llm`. |
| `alert.hours_before_close` | Closing window, in hours; the example uses 24. |
| `alert.value_ratio` | Alert when price is at most this fraction of the low valuation estimate. |
| `alert.hard_price_cap` | An independent price trigger; set a number if you want alerts without LLM estimates. `null` disables it. |
| `alert.also_alert_if_reserve_not_met` | An additional reserve-status trigger; false by default. |
| `smtp` | Your SSL SMTP host, port, sender/login, and recipient. Implementation uses `SMTP_SSL`; the example uses port 465. |
| `gsa.enabled` | Leave false unless you are intentionally testing the experimental GSA source with your own key. |

For LLM valuation, set `ANTHROPIC_API_KEY`. For SMTP, `AUCTION_SMTP_PASSWORD` overrides `smtp.password`. Use your provider's appropriate app-specific credential rather than a normal account password where applicable.

```bash
# Bash: placeholders only
export ANTHROPIC_API_KEY='YOUR_OWN_KEY'
export AUCTION_SMTP_PASSWORD='YOUR_OWN_SMTP_CREDENTIAL'
```

```powershell
# PowerShell: placeholders only
$env:ANTHROPIC_API_KEY = 'YOUR_OWN_KEY'
$env:AUCTION_SMTP_PASSWORD = 'YOUR_OWN_SMTP_CREDENTIAL'
```

The application reads real environment variables; it does not automatically load a `.env` file. Never commit secrets. `config.yaml`, `*.db`, `dumps/`, and `*.log` are excluded by [.gitignore](.gitignore).

## First run

Start with inspection commands, before enabling email or scheduling:

```bash
python auction_watch.py discover
python auction_watch.py dump
python auction_watch.py list
```

`dump` is a detail refresh that also writes visible text to `dumps/<assetId>.txt` and captured response JSON to `dumps/<assetId>.json`. Compare recorded titles, prices, reserves, and UTC closing times with the original listings. If fields are missing or wrong, inspect `_from_json` and `parse_from_text` in [govdeals.py](govdeals.py). These mappings are heuristics, not a validated data contract.

The text fallback can select the largest dollar amount it sees when no recognized price label is found. Do not treat that value as a verified current bid.

## Commands

| Command | Effect |
|---|---|
| `discover` | Adds newly discovered GovDeals listings for configured sellers |
| `refresh` | Updates details for tracked, non-closed listings |
| `dump` | Refreshes details and saves diagnostic captures |
| `value` | Attempts estimates for titled rows that have not yet received an estimate attempt |
| `alert` | Sends qualifying emails; this has an external effect |
| `run` | Runs discovery, optional GSA collection, detail refresh, valuation, then email alerts |
| `list` | Prints stored rows without fetching listings |

Run a command as `python auction_watch.py COMMAND`. Paths default to `config.yaml` and `auctions.db` in the current working directory. For another location:

```bash
python auction_watch.py list --config /path/to/config.yaml --db /path/to/auctions.db
```

The closing alert requires a parseable, future closing time inside the configured window and no previous closing alert for that asset/source. It triggers on **any** enabled condition: price versus the low estimate, price versus the hard cap, or an unmet reserve. The reserve option can trigger without a readable price. Alerts are not repeated for later price cuts once a closing alert is recorded in that database.

## Scheduling

There is no built-in scheduler. After validating the source fields and an intentional email run, use cron or Windows Task Scheduler. Run from this repository directory, use the virtual environment's Python, and supply the same config, database, and environment variables each time.

Example cron entry, twice daily in the scheduler's own timezone:

```cron
0 7,19 * * * cd /path/to/auction-watch && /path/to/auction-watch/.venv/bin/python auction_watch.py run >> watch.log 2>&1
```

A scheduler generally does not inherit variables you exported in an interactive shell. Avoid overlapping runs against the same SQLite file. Twice-daily checks are not continuous coverage and can miss listings or late bid changes.

## Common questions and known limitations

| Question | Answer |
|---|---|
| Why are there no alerts with valuation disabled? | With `valuation.backend: none`, no estimate is supplied. Set a deliberate `hard_price_cap` or enable the reserve trigger; otherwise the default ratio alone cannot qualify an item. |
| Discovery works but refresh is blocked? | Sitemap access and browser page access are separate. The original environment hit a CDN block. Stop and investigate current access rules; do not assume a different IP or browser will solve it. |
| Why is a listing no longer refreshed after changing keywords? | Non-matching titles are marked closed. Changing the filter does not automatically reopen those rows. Keep a backup before deliberately starting with a new database. |
| Why does a failed or missing valuation not retry? | Even an unavailable/failed attempt can set confidence to `none`; `needs_estimate()` selects only rows whose confidence is null. Automatic retry is not implemented. |
| Is GSA fully supported? | No. [gsa.py](gsa.py) is an experimental mapping, disabled by default; validate its API response and refresh behavior yourself. It is a different source from GovDeals. |
| Is the estimate an appraisal or current market evidence? | No. It is an optional title-based triage estimate, without implemented sold-comparable lookup. `ebay_sold()` is an unfinished stub. Costs depend on your own API account and model. |
| Are tests and CI included? | No automated test suite or CI workflow is included in this snapshot. Live sources, email delivery, and scheduling were not exercised for this documentation update. |
| Can I rely on fixes or support? | No ongoing maintenance is planned. Search [issues](https://github.com/connorkeetch/auction-watch/issues), then provide reproduction steps, your environment, and redacted diagnostics. Replies or PR review are not guaranteed. |
| What license applies? | Project source code is available under the [MIT License](LICENSE). Third-party dependencies, assets, and services retain their own terms. |

## Code map

| File | Responsibility |
|---|---|
| [auction_watch.py](auction_watch.py) | CLI, configuration, command sequencing, and alert rules |
| [govdeals.py](govdeals.py) | Sitemap discovery and browser detail extraction |
| [gsa.py](gsa.py) | Experimental federal-surplus adapter |
| [valuation.py](valuation.py) | Optional Anthropic valuation and unimplemented sold-comparable hook |
| [store.py](store.py) | SQLite rows, estimate state, and sent-alert records |
| [alerts.py](alerts.py) | SSL SMTP delivery and email formatting |
| [config.example.yaml](config.example.yaml) | Complete example configuration |

Keep databases, SMTP credentials, API keys, diagnostic captures, and logs private. Contributions should target `main`, explain what was validated, and avoid including real credentials or private auction/account data.
