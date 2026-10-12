# Roadmap

Ideas for the dashboard, not yet scheduled. Effort: **S** about an hour or two, **M** half a day, **L** a day or more. Anything that changes a page gets a short plan and a clickable mockup first.

## Know your real returns
| # | Idea | Effort | Notes |
|---|---|---|---|
| 1 | Transactions and sales | L | Dated buys and sells give realized gains and a money-weighted return. Today's performance ignores past buys and sells. Items 2 and 6 build on it |
| 2 | Contributions vs. growth | M | Automatic Investment: amount put in vs. market gain, monthly deposits chart |
| 3 | Dividends and income | M | Tiingo daily prices include dividend payments: dividends per year, next expected payment, yield on cost |

## Plan and stay on track
| # | Idea | Effort | Notes |
|---|---|---|---|
| 4 | Target allocation and drift | M | Target percentages per holding or asset class, a drift view, and an email when off by more than a set amount |
| 5 | Goals | M | "$X by year Y" per person or household, with progress and a simple projection |
| 6 | Tax view | M | Short- vs long-term gains from purchase dates; positions below cost. Describes data only, not tax advice. Needs #1 |

## Stay informed
| # | Idea | Effort | Notes |
|---|---|---|---|
| 7 | Watchlist | S–M | Tickers you don't own, with signals, news and alerts |
| 8 | Earnings and ex-dividend calendar | S | Upcoming dates for holdings (Finnhub free calendar) |
| 9 | Intraday alerts | S | Alert rules checked against the 15-minute intraday prices, not only after the close |
| 10 | Daily close email | S | Today's change, biggest movers and triggered alerts after the evening run |

## Convenience
| # | Idea | Effort | Notes |
|---|---|---|---|
| 11 | CSV export and import | S–M | Download holdings and history; import a broker export. Also the simple route to broker sync |
| 12 | Install on your phone | S | Installable web app (home-screen icon, full-screen) |
| 13 | Ask about my portfolio | M | Questions answered by Claude from your own data. Small Bedrock cost per question |

## Also planned
- Rule backtesting: how an alert rule would have fired historically.
- Factual-only news summaries.
- Rename the API field `cost_basis` to `avg_cost`.
- Newer Claude model for news summaries once the account has access.
- Custom domain for the dashboard.
- Broker sync (for example through an aggregator), parked.
