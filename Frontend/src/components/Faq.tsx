import type { ReactNode } from 'react'

/** Plain-English questions and answers. Every statement describes what the code actually does. */

type QA = { q: string; a: ReactNode }
type Group = { title: string; items: QA[] }

const GROUPS: Group[] = [
  {
    title: 'What Nivesh is and what it solves',
    items: [
      {
        q: 'What is Nivesh?',
        a: 'A research tool for Indian stocks listed on the NSE. It turns the exchange’s official end-of-day files into market dashboards, stock pages, scans, a strategy lab and a question-answering Copilot. It is educational analytics, not investment advice.',
      },
      {
        q: 'What problem does it solve?',
        a: 'Answering simple market questions ("how many stocks are above their 200-day average?", "which sectors are gaining strength?", "how did this rule perform since 2010?") usually means collecting files, fixing split and bonus price jumps, and writing code. Nivesh does that work once, every day, and shows the results with the source and date of every number.',
      },
      {
        q: 'How does it solve it?',
        a: (
          <>
            Every number is calculated by database queries and Python over the data, using rules written in version-controlled
            files. AI models never calculate a number and never decide a trade: in the Copilot they only pick the type of
            question, write one read-only query and put the result into words. Every number in that wording is checked
            against the query result. The <a href="#anatomy" className="text-accent hover:underline">Anatomy</a> above shows each step.
          </>
        ),
      },
      {
        q: 'Where does the data come from, and how fresh is it?',
        a: 'From the NSE’s public end-of-day archives: daily prices for every stock since 2010, index history, corporate actions, price bands and reference lists. A job runs every weekday at 18:45 IST. There is no live or intraday data: everything reflects the close of the last loaded session, shown as "Data through" in the top bar.',
      },
      {
        q: 'Why are prices adjusted?',
        a: 'When a company splits its shares or issues bonus shares, the price drops without anyone losing money. Without adjustment, charts and returns would show a fake crash. Nivesh adjusts past prices for splits, bonuses and demergers and records every adjustment it makes.',
      },
    ],
  },
  {
    title: 'How to use it',
    items: [
      {
        q: 'How do I get started?',
        a: 'Sign in with Google or Hugging Face. If you see "Access pending", your account has not been approved yet; send the shown message to the owner. Once approved, start on Market for the big picture, then open any stock with the search box at the top right.',
      },
      {
        q: 'What do the pages do?',
        a: (
          <ul className="list-disc space-y-1 pl-5">
            <li><b>Market</b>: the current market regime, nine engine scores, active warnings and similar past markets.</li>
            <li><b>Sectors</b>: which sectors are gaining or losing strength against the market.</li>
            <li><b>Stocks</b>: price chart, safety checklist, trend stage and gauges for one stock.</li>
            <li><b>Scans</b>: stocks that match a written rule today, ranked by score.</li>
            <li><b>News</b>: company announcements turned into events, and how prices moved after similar events.</li>
            <li><b>Strategies</b>: run a rule-based strategy over history (a backtest) and inspect every trade.</li>
            <li><b>Copilot</b>: ask a question in plain English and get the answer with the query behind it.</li>
            <li><b>Paper</b>: follow a strategy forward from a start date with pretend money.</li>
            <li><b>Alerts</b>: rules that put a message in your inbox when something happens.</li>
            <li><b>Watchlist</b>: your own list of stocks.</li>
          </ul>
        ),
      },
      {
        q: 'What is the "As of" date at the top?',
        a: 'Time travel. Pick a past date (or click a cell in the regime history) and every page shows what was known on that day, using only data up to that date. This lets you check how the tools looked at the time instead of with hindsight.',
      },
      {
        q: 'What are the regime and the nine engines?',
        a: 'The engines score parts of the market from 0 to 100, as a percentile against its own history: buying pressure, selling pressure, leadership, participation, structural breadth, momentum quality, liquidity and money flow, risk and exhaustion, and volatility. The regime is one of ten labels (from Capitulation to Momentum Bull) chosen by written rules from those readings. They describe the market today; they do not forecast it.',
      },
      {
        q: 'How do I use the Copilot?',
        a: 'First add your own Gemini key in "AI key & model" above (a free key is available from Google AI Studio, with usage limits set by Google). Then ask, for example, "Top 5 stocks by 21-day return on the latest date, liquid only". You get the answer, the SQL that produced it, the result table and the data date. You can pick which Gemini model it uses; the choice only affects your account.',
      },
      {
        q: 'How do backtests work, and how should I read them?',
        a: 'A strategy is a written set of entry, exit and position rules. The backtest replays it day by day: orders fill at the next day’s open, stops and price gaps are handled, and brokerage, taxes and slippage are deducted. Five checks flag over-fitted results: at least 30 trades, a test on data the rules were not tuned on, sensitivity to small parameter changes, higher costs, and results across different market regimes. A result that passes them is still only history.',
      },
    ],
  },
  {
    title: 'Your account, key and privacy',
    items: [
      {
        q: 'Why do I need my own API key?',
        a: 'The Copilot calls a Google Gemini model for each question. With your own key, you control how much it is used and what it costs. No one else’s key is used for your questions, and your key is never used for anyone else.',
      },
      {
        q: 'Is my key safe?',
        a: 'It is tested once, then stored encrypted (AES-256-GCM) and tied to your account, so a copy of the stored value cannot be used by another account. It is never shown again, not even to you: only the last 4 characters appear. It is removed from any error message before that message is logged or shown. You can delete it at any time.',
      },
      {
        q: 'Who can see my watchlist, alerts, backtests and questions?',
        a: 'Only you. Every saved item carries your account id and every read and write is limited to it. Asking for someone else’s item returns "not found". All approved accounts are equal; there is no account that can browse other users’ data in the app.',
      },
      {
        q: 'What do the AI models see?',
        a: 'Your question, the list of market tables with their descriptions, project documentation and the market data returned by the query. They never receive your watchlist, alerts, paper accounts, history or key.',
      },
      {
        q: 'Are there limits?',
        a: 'Yes, to keep the server fair for everyone: a daily number of Copilot questions and one at a time, a number of SQL queries per minute, a number of key tests per hour, one backtest at a time, and caps on watchlist size, alert rules and paper accounts.',
      },
    ],
  },
  {
    title: 'Facts, not myths',
    items: [
      {
        q: 'Myth: "the AI picks stocks for me."',
        a: 'Fact: no model picks, ranks or recommends a stock. Scans and strategies are written rules computed by the database. The Copilot only turns your question into a query and the result into words.',
      },
      {
        q: 'Myth: "a good backtest means the strategy will make money."',
        a: 'Fact: a backtest shows how fixed rules would have behaved on past prices, after costs. Markets change. The five checks reduce the chance of a lucky or over-tuned result; they do not remove it.',
      },
      {
        q: 'Myth: "the Copilot cannot be wrong."',
        a: 'Fact: it can misunderstand a question and query the wrong thing. What it cannot do is invent a number: numbers in the answer must match the query result, or you get the raw result instead. Always look at the SQL and the result table shown with the answer.',
      },
      {
        q: 'Myth: "the regime tells me where the market goes next."',
        a: 'Fact: the regime and engines describe the market as of the last close. "Similar past markets" show what happened after comparable readings in the past, which is history, not a forecast.',
      },
      {
        q: 'What does Nivesh not do?',
        a: 'It does not place orders, connect to a broker, use live or intraday prices, give personal financial advice, or guarantee any result. Paper accounts use pretend money only.',
      },
    ],
  },
]

export function Faq() {
  return (
    <div className="space-y-5 text-sm">
      {GROUPS.map((g) => (
        <section key={g.title} aria-label={g.title}>
          <h3 className="mb-2 font-semibold">{g.title}</h3>
          <div className="divide-y divide-line rounded border border-line">
            {g.items.map((item) => (
              <details key={item.q} className="group px-3 py-2">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-1 font-medium hover:text-accent">
                  {item.q}
                  <span aria-hidden="true" className="text-muted transition-transform group-open:rotate-45">+</span>
                </summary>
                <div className="pb-2 pt-1 leading-relaxed text-muted">{item.a}</div>
              </details>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
