# Roadmap

Ideas for the dashboard, not yet scheduled. Effort: **S** about an hour or two, **M** half a day, **L** a day or more; **S–M** is somewhere in between. Anything that changes a page gets a short plan and a clickable mockup first.

## Know your real returns
| # | Idea | Effort | Notes |
|---|---|---|---|
| 1 | Transactions and sales | L | Buys are already recorded (average cost and earliest date). Add sales and a per-lot history for realized gains and a money-weighted return. Items 2 and 6 build on it |
| 2 | Contributions vs. growth | M | Automatic Investment: amount put in vs. market gain, monthly deposits chart. Needs dated deposits from #1 |
| 3 | Dividends and income | M | Keep Tiingo's per-day dividend amount (currently dropped) to show dividends received per year and yield on cost. Returns already include dividends (adjusted closes), so this is a separate income view and must not be added to gains. Upcoming payments need another source |

## Plan and stay on track
| # | Idea | Effort | Notes |
|---|---|---|---|
| 4 | Target allocation and drift | M | Target percentages per holding or asset class, a drift view, and an email when off by more than a set amount |
| 5 | Goals | M | "$X by year Y" per person or household, with progress and a simple projection |
| 6 | Tax view | M | Short- vs long-term gains and positions below cost. Needs per-lot dates from #1: holdings keep only the earliest purchase date. Describes data only, not tax advice |

## Stay informed
| # | Idea | Effort | Notes |
|---|---|---|---|
| 7 | Watchlist | S–M | Tickers you don't own, with signals, news and alerts |
| 8 | Earnings calendar | S | Upcoming earnings dates for holdings (Finnhub free plan). Ex-dividend dates need a paid source |
| 9 | Intraday alerts | S | Alert rules also checked against the 15-minute intraday prices (when `intradayQuotes` is on), not only after the close |
| 10 | Daily close email | S | Today's change and biggest movers after the evening run, as an option on the existing report email rather than a second message |

## Convenience
| # | Idea | Effort | Notes |
|---|---|---|---|
| 11 | CSV export and import | S–M | Download holdings and history; import a broker export. Also the simple route to broker sync |
| 12 | Install on your phone | Done | Home-screen icon, full screen (PR #30) |
| 13 | Ask about my portfolio | M | Questions answered by Claude from your own data. Small Bedrock cost per question |
| 14 | Push notifications | M | Alerts as phone notifications (home-screen app on iOS 16.4+) |
| 15 | Stay signed in on the phone | M | A few days, with Face ID on open. Security trade-off; design first |

## Understand what you own
| # | Idea | Effort | Notes |
|---|---|---|---|
| 16 | Fund overlap | M | Look through ETFs to real holdings, e.g. VTI and VOO overlap |
| 17 | Sector and region mix | M | From fund holdings; fits the planned pie chart |
| 18 | Fees in dollars | S | Expense ratios as dollars per year |

## Track progress
| # | Idea | Effort | Notes |
|---|---|---|---|
| 19 | Biggest movers | S | Top gainers and losers over a chosen period |
| 20 | Daily gains calendar | S | Heatmap of up and down days |
| 21 | Year-end summary | M | Growth, contributions, best and worst, dividends; optional PDF |
| 22 | Notes per holding | S | "Why I bought" notes and a decision journal |
| 23 | Net worth | M | Manually updated accounts (401k, savings, home) for one household total |
| 24 | Ticker detail page | M | Price chart with your buys, news and signals |
| 25 | Risk stats | S | Volatility, max drawdown and Sharpe ratio from stored price history |
| 26 | Concentration warning | S | Flag one holding above a set share (e.g. 25%), on the page and in the weekly email |
| 27 | Rebalance helper | M | "Where should my next $X go?" Builds on #4 |
| 28 | Since your last visit | S | Banner with changes, new alerts and news |

## Look and feel
| # | Idea | Effort | Notes |
|---|---|---|---|
| 29 | Faster phone open | M | Cache the last view so the app opens instantly, then refresh after sign-in |
| 30 | Light/dark switch | S | Manual override of the system theme |

## Also planned
- Overview: drop the SPY comparison (column, tile, dashed chart line), keep your own 1Y return, and stop syncing SPY prices.
- Overview: pie chart of holdings value and profit (design to agree first).
- Rule backtesting: how an alert rule would have fired historically.
- Finish the `cost_basis` → `avg_cost` rename: stop writing and accepting the old name, then remove it from stored holdings.
- Newer Claude model for news summaries once the account has access.
- Custom domain for the dashboard.
- Broker sync (for example through an aggregator), parked.

## Security and ops
- CloudTrail trail to S3 for a long-term audit log (free history is 90 days).
- Passwordless sign-in: drop the password factor, or require TOTP with it.
- Lockfile with hashes for the Lambda layer.
- Restore drill: point-in-time restore of the table to a test copy.

## CI ideas
- Workflow security lint (zizmor or actionlint).
- ESLint for `web/js`.
