# 0015: Peer grid, spillover, co-move and replay

Window: the last three years of daily closes. Peers are selected on the first two years and
everything shown in the grid is measured on the last year. Only stocks with complete prices
over all three years take part, so late listings are left out.

Ranking: every stock's residual (after its own market and pure-sector exposures, fitted on the
selection window) is correlated with the target's, and the top 99 form the grid. The
residualizer is fitted once on the selection window for the whole universe and applied
unchanged to the last year, so the grid uses no information from the window it describes.

Grid: 100 x 100, ordered by average-linkage clustering on selection-window residual
correlations, drawn on a canvas. Layers: residual correlation, raw correlation, and spillover
(the slope of one stock's residual on another's). The grid is computed in one step; it is not
animated to look as if it were computed cell by cell.

Cointegration: Engle-Granger on last-year log prices for the top 10, with the target as the
dependent series, and a Benjamini-Hochberg correction across those 10. The tests run one at a
time and are streamed to the page as each finishes (Server-Sent Events).

Spillover and co-move: for a chosen day, each top peer's unexplained move (from its own
out-of-sample daily model, as on its stock page) is compared with spillover beta x the
target's unexplained move, and the gap is scored by the peer's EWMA volatility.

Replay: rolling 63-day correlations for the target and its top 19 peers across the whole
history, every 5 trading days, for raw returns and for out-of-sample unexplained returns.
Frames are computed as they stream. Raw correlations spike together in a crash; residual ones
move much less, which is the point the replay makes.

Verified on synthetic data: planted cross-sector links rank first and hold up out of sample,
the best-looking noise stock regresses to zero, and only the planted cointegrated pair is
flagged after correction.

Limits: peers come only from the universe; correlations drift; spillover is a historical
average, not causation; cointegration describes price levels, not daily co-movement.
