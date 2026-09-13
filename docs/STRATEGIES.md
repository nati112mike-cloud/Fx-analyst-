# Strategy Specifications

Auto-generated from each strategy's StrategySpec (fx_engine/strategies/*.py) -- 
the code and this document cannot drift apart because this IS the code, rendered.

### trend_pullback (v1.0)

**Market Conditions:** Established trend (EMA50 vs EMA200 aligned) with price pulling back to the EMA50 before continuation -- the classic 'buy the dip in an uptrend / sell the rip in a downtrend' setup, entered only after a reversal candle confirms the pullback is over.

**Pairs:**
- *

**Timeframes:**
- H4
- D1

**Indicators:**
- EMA50
- EMA200
- ATR14
- RSI14

**Entry Conditions:**
- EMA50 vs EMA200 defines trend direction (50>200 bull, 50<200 bear)
- Price within 0.6x ATR of EMA50 (the pullback zone)
- Most recent closed candle is a reversal candle in the trend direction (bullish candle for uptrend, bearish for downtrend)
- RSI14 turning back toward 50 from the pullback extreme (not already exhausted >70 / <30)

**Exit Conditions:**
- Stop-loss or take-profit hit
- Max holding bars elapsed
- Trend EMA cross reverses

**Stop Loss Method:** 1.2x ATR beyond the pullback low/high

**Take Profit Method:** Risk multiplied by MIN_RR_AFTER_COSTS (configurable, default >=1.5)

**Min Risk Reward:** 2.0

**Max Holding Bars:** 40

**Invalidation Conditions:**
- EMA50/EMA200 cross against the trade before entry triggers

**Excluded Regimes:**
- range
- high_volatility
- unclear

**Required Data:**
- OHLC H4 or D1

**Known Weaknesses:**
- Whipsaws in choppy/weak trends where EMA50 is repeatedly retested without real momentum
- Lagging: EMA-based trend definition confirms late relative to the actual turn

### momentum (v1.0)

**Market Conditions:** Building directional momentum: MACD histogram crosses zero in the direction of RSI confirmation, entered early in the move rather than at exhaustion.

**Pairs:**
- *

**Timeframes:**
- H1
- H4

**Indicators:**
- MACD(12,26,9)
- RSI14
- ATR14

**Entry Conditions:**
- MACD histogram crosses from negative to positive (bull) or positive to negative (bear) on the most recently closed bar
- RSI14 confirms direction: >50 and <70 for bull, <50 and >30 for bear (avoids buying/selling into an already-exhausted move)

**Exit Conditions:**
- Stop-loss or take-profit hit
- MACD histogram crosses back against the trade

**Stop Loss Method:** 1.5x ATR from entry

**Take Profit Method:** Risk multiplied by MIN_RR_AFTER_COSTS

**Min Risk Reward:** 1.8

**Max Holding Bars:** 25

**Invalidation Conditions:**
- MACD histogram re-crosses zero against the trade before target hit

**Excluded Regimes:**
- range

**Required Data:**
- OHLC H1 or H4

**Known Weaknesses:**
- MACD is a lagging derivative of price -- signals confirm after part of the move already happened
- Prone to false crosses in choppy weak-trend conditions

### breakout (v1.0)

**Market Conditions:** Price closes outside a 20-bar Donchian channel (a defined range) with volatility (ATR) expanding, consistent with a genuine breakout rather than a range-bound spike.

**Pairs:**
- *

**Timeframes:**
- H1
- H4

**Indicators:**
- Donchian(20)
- ATR14
- ATR percentile(100)

**Entry Conditions:**
- Close breaks above the prior 20-bar Donchian upper band (bull) or below the lower band (bear)
- ATR percentile has risen at least 15 percentile points over the last 5 bars (volatility expansion, not a low-volatility fake-out)

**Exit Conditions:**
- Stop-loss or take-profit hit
- Price closes back inside the pre-breakout channel

**Stop Loss Method:** Opposite side of the breakout bar's range, or 1x ATR, whichever is tighter

**Take Profit Method:** Measured move: channel height projected from the breakout point, floor of MIN_RR_AFTER_COSTS

**Min Risk Reward:** 1.8

**Max Holding Bars:** 30

**Invalidation Conditions:**
- Close back inside the channel within 3 bars of the breakout (failed auction)

**Excluded Regimes:**
- strong_bull_trend
- strong_bear_trend

**Required Data:**
- OHLC H1 or H4

**Known Weaknesses:**
- False breakouts ('fakeouts') are common, especially outside the New York session
- Whipsaw risk right at market open / thin liquidity windows

### mean_reversion (v1.0)

**Market Conditions:** Range-bound / low-volatility market where price has stretched to a statistical extreme (outside Bollinger Bands, RSI extreme) and is expected to revert toward the mean rather than continue -- explicitly gated OUT of trending regimes.

**Pairs:**
- *

**Timeframes:**
- H1
- H4

**Indicators:**
- BollingerBands(20,2)
- RSI14
- ATR14

**Entry Conditions:**
- Regime is range or low_volatility (never trend regimes)
- Close is outside the Bollinger Band (below lower for BUY, above upper for SELL)
- RSI14 is at an extreme: <30 for BUY, >70 for SELL
- Most recent bar shows a reversal wick back inside the band

**Exit Conditions:**
- Stop-loss or take-profit hit
- Price reaches the Bollinger mid-band (primary target)

**Stop Loss Method:** Beyond the extreme bar's high/low plus 0.3x ATR

**Take Profit Method:** Bollinger mid-band, floor of MIN_RR_AFTER_COSTS

**Min Risk Reward:** 1.5

**Max Holding Bars:** 20

**Invalidation Conditions:**
- Close beyond the stop before reversion confirms (regime was misread as trend)

**Excluded Regimes:**
- strong_bull_trend
- strong_bear_trend
- weak_trend
- breakout
- high_volatility

**Required Data:**
- OHLC H1 or H4

**Known Weaknesses:**
- Dangerous in a market that is actually trending (catching a falling knife) -- regime gate is the only real protection here
- Win rate can look high while a single trend day wipes out many small wins

### price_action (v1.0)

**Market Conditions:** Objective break-of-structure: price closes beyond the most recent CONFIRMED swing high/low (a fractal pivot, not a subjective 'obvious' level), in the direction of a rising/falling sequence of prior swings.

**Pairs:**
- *

**Timeframes:**
- H4
- D1

**Indicators:**
- Fractal swing points (lookback=5)
- ATR14

**Entry Conditions:**
- At least 2 confirmed swing lows are rising (bull structure) or 2 confirmed swing highs are falling (bear structure)
- Close breaks beyond the most recent confirmed swing high (bull) / swing low (bear)

**Exit Conditions:**
- Stop-loss or take-profit hit

**Stop Loss Method:** Beyond the swing point that defines the structure, plus 0.2x ATR buffer

**Take Profit Method:** Risk multiplied by MIN_RR_AFTER_COSTS

**Min Risk Reward:** 2.0

**Max Holding Bars:** 35

**Invalidation Conditions:**
- Price closes back below/above the broken swing point (failed break)

**Excluded Regimes:**
- high_volatility

**Required Data:**
- OHLC H4 or D1

**Known Weaknesses:**
- Swing points require `lookback` bars of confirmation AFTER the pivot, so entries are inherently a bit late relative to the actual turn
- Struggles in choppy markets that produce many small, contradictory swings

### volatility (v1.0)

**Market Conditions:** Volatility squeeze: Bollinger Band width sits at a multi-bar low (compression), then price breaks decisively out of the bands -- a direct, testable version of 'compression precedes expansion.'

**Pairs:**
- *

**Timeframes:**
- H1
- H4

**Indicators:**
- BollingerBands(20,2)
- ATR14
- ATR percentile(100)

**Entry Conditions:**
- Bollinger Band width was in its lowest 20th percentile over the last 100 bars within the last 5 bars (the squeeze)
- Close breaks outside the band in either direction on the current bar

**Exit Conditions:**
- Stop-loss or take-profit hit

**Stop Loss Method:** Opposite Bollinger Band, or 1x ATR, whichever is tighter

**Take Profit Method:** 2x the pre-breakout band width projected from the breakout point, floor of MIN_RR_AFTER_COSTS

**Min Risk Reward:** 1.6

**Max Holding Bars:** 25

**Invalidation Conditions:**
- Price closes back inside the bands within 2 bars

**Excluded Regimes:**
- strong_bull_trend
- strong_bear_trend

**Required Data:**
- OHLC H1 or H4

**Known Weaknesses:**
- Squeeze breakouts can be false starts (whipsaw back into the range) more often than genuine expansions -- this is the single riskiest module in the ensemble and is weighted accordingly

### multi_timeframe (v1.1)

**Market Conditions:** Any -- reports whether EMA trend direction agrees across three EMA-pair horizons computed on the base-timeframe series (proxy for base/mid/higher timeframe trend).

**Pairs:**
- *

**Timeframes:**
- any

**Indicators:**
- EMA20/50 (base)
- EMA60/150 (mid proxy)
- EMA180/450 (high proxy)

**Entry Conditions:**
- n/a -- confirmation overlay only, see StrategyEnsemble

**Exit Conditions:**
- n/a

**Stop Loss Method:** n/a

**Take Profit Method:** n/a

**Min Risk Reward:** 0.0

**Max Holding Bars:** 0

**Invalidation Conditions:**
- n/a

**Excluded Regimes:**

**Required Data:**
- OHLC at base timeframe with enough history for the EMA450 to warm up (>=~460 bars)

**Known Weaknesses:**
- Uses EMA-pair multipliers on the base-timeframe series as a fast proxy for higher-timeframe trend, not true independently-fetched H4/D1 candles -- correlated with, but not identical to, a real multi-timeframe read
- Needs a long warm-up (450+ bars) before the 'high' horizon is meaningful; returns NEUTRAL (not a fabricated opinion) until then
