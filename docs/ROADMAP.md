# Roadmap

Ideas for the dashboard, not yet scheduled. Effort: **S** about an hour or two, **M** half a day, **L** a day or more; **S–M** is somewhere in between. Anything that changes a page gets a short plan and a clickable mockup first.

## Know your real returns
| # | Idea | Effort | Notes |
|---|---|---|---|
| 2 | Contributions vs. growth | M | Automatic Investment: amount put in vs. market gain, monthly deposits chart. Needs dated deposits from #1 |
| 3 | Dividends and income | M | Mockup agreed: Dividends page with annual income, yield, yield on cost, next-12-months by month, upcoming (estimated) and per holding with Both/Stocks/Auto. Store Tiingo's `divCash` and re-fetch ~2 years once. No "received" figures until purchase dates exist (#1). Not added to gains |

## Plan and stay on track
| # | Idea | Effort | Notes |
|---|---|---|---|
| 5 | Goals | M | "$X by year Y" per person or household, with progress and a simple projection |
| 6 | Tax view | M | Short- vs long-term gains and positions below cost. Needs per-lot dates from #1: holdings keep only the earliest purchase date. Describes data only, not tax advice |

## Stay informed
| # | Idea | Effort | Notes |
|---|---|---|---|
| 9 | Intraday alerts | S | Alert rules also checked against the 15-minute intraday prices (when `intradayQuotes` is on), not only after the close |
| 10 | Daily close email | S | Today's change and biggest movers after the evening run, as an option on the existing report email rather than a second message |

## Convenience
| # | Idea | Effort | Notes |
|---|---|---|---|
| 11 | CSV export and import | S–M | Download holdings and history; import a broker export. Also the simple route to broker sync |
| 13 | Ask about my portfolio | M | Questions answered by Claude from your own data. Small Bedrock cost per question |
| 15 | Stay signed in on the phone | M | A few days, with Face ID on open. Security trade-off; design first |

## Understand what you own
| # | Idea | Effort | Notes |
|---|---|---|---|
| 16 | Fund overlap | M | "Shared underneath your funds" on the Checkup, plus a single-company ceiling. Free SEC N-PORT holdings (quarterly; prototype worked; SPY has none, use VOO) |
| 17 | Sector and region mix | M | From fund holdings; fits the Allocation donut |
| 18 | Fees in dollars | S | Expense ratios as dollars per year |

## Track progress
| # | Idea | Effort | Notes |
|---|---|---|---|
| 20 | Daily gains calendar | S | Heatmap of up and down days |
| 21 | Year-end summary | M | Growth, contributions, best and worst, dividends; optional PDF |
| 22 | Notes per holding | S | "Why I bought" notes and a decision journal |
| 23 | Net worth | M | Manually updated accounts (401k, savings, home) for one household total |
| 24 | Ticker detail page | M | Price chart with your buys, news and signals |
| 25 | Risk stats | S | Volatility, max drawdown and Sharpe ratio from stored price history |
| 26 | Concentration warning | S | Flag one holding above a set share (e.g. 25%), on the page and in the weekly email |
| 27 | Rebalance helper | M | "Where should my next $X go?" Builds on #4 |

## Look and feel
| # | Idea | Effort | Notes |
|---|---|---|---|
| 30 | Light/dark switch | S | Manual override of the system theme |

## More ideas (Oct 14)
| # | Idea | Effort | Notes |
|---|---|---|---|
| 31 | Your money-weighted return | S | Now possible for Stock Holdings from the trade history (#1): your actual return, timing included |
| 32 | Tax-loss finder | S–M | Stock Holdings below cost, long vs. short term, and a 30-day wash-sale reminder. Facts only (pairs with #6) |
| 33 | "What if" trade preview | M | Try a sale or buy and see the new mix vs. target, risk and concentration before doing it at the broker |
| 35 | Earnings reaction history | S | How each stock moved the day after its last few reports (Finnhub + stored prices) |
| 37 | Weekly email upgrade | S | Add this week's earnings and biggest movers to the Sunday email |
| 38 | Data health page | S | When each job last ran, how fresh prices and news are, and month-to-date AWS spend (cached daily) |
| 41 | Backup download | S | One-click JSON/CSV of holdings, trades and targets (pairs with #11) |
| 42 | Wealthfront file import | M | Automatic Investment buys from a downloaded statement, so its history is exact too (#11) |


- Rule backtesting: how an alert rule would have fired historically.
- Newer Claude model for news summaries once the account has access.
- Custom domain for the dashboard.
- Broker sync (for example through an aggregator), parked.

## Bigger ideas
- Company filings digest: Claude summarizes each holding's new 10-Q, 10-K and 8-K (SEC EDGAR, free).
- Earnings call summaries: key numbers, guidance and tone (transcripts need a paid source).
- Company fundamentals per stock: valuation, growth, margins, estimates (paid data).
- Real-time quotes instead of 15-minute updates (paid data).
- Risk extras on the Checkup: correlation between holdings, stress tests (2008, 2020), Monte Carlo projection toward #5 goals.
- iPhone home-screen widget with today's change (pairs with #14).
- Separate logins per person, with shared household views.
- Checkup email to both people (second SES-verified address), or an HTML body.

## Security and ops
- CloudTrail trail to S3 for a long-term audit log (free history is 90 days).
- Passwordless sign-in: drop the password factor, or require TOTP with it.
- Restore drill: point-in-time restore of the table to a test copy.

## CI ideas
- Type checking for Python (mypy or pyright).
- Accessibility check of the dashboard pages (axe) on PRs.
- Yearly reminder to rotate the Tiingo and Finnhub API keys.

## Done
| # | Idea | PR |
|---|---|---|
| 1 | Transactions and sales (Stock Holdings: buys, FIFO sales, realized gains) | #47 |
| 4 | Target mix, Checkup page and monthly PDF email | #42, #44 |
| 7 | Watchlist | #34 |
| 12 | Install on your phone | #30 |
| 14 | Push notifications for rule alerts (installed app) | #55 |
| 19 | Today's movers | #38 |
| 29 | Faster phone open (last Overview shown instantly, then refreshed) | #56 |
| 34 | Price-level alerts (`price_below`, `price_above`) | #56 |
| 28 | Since your last visit (Overview strip) | #63 |
| 39 | Late-update warning on the Overview | #63 |
| 36 | Analysts & insiders (News & signals, Watchlist) | #61 |
| 40 | NYSE holidays and early closes, closed-market banner | #48, #54 |
| 8 | Earnings calendar | #50 |
| — | Overview redesign: performance by holding, donut, sortable Holdings; SPY removed | #39, #49 |
| — | CI: smoke test, weekly audit, dependency review, workflow lint, ESLint | #33, #51, #64 |
| — | Lambda layer lockfile with hashes (`make lock`) | #65 |
| — | `cost_basis` → `avg_cost` rename finished (old name removed from stored holdings) | #59, #60 |

Finished items move here with their PR; numbers don't change.
