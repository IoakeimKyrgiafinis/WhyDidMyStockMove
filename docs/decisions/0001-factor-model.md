# 0001: Two-factor model with an orthogonalised sector factor

Decision: explain each stock's return with the market (IWV) and a "pure sector"
factor, which is the sector ETF's return after removing its market component.

Why: sector ETFs are highly correlated with the market, so using both raw gives
unstable, overlapping betas. Orthogonalising makes the contributions additive
and interpretable.

Out-of-sample: every day is explained with betas fitted on the prior 252 days
only, including the sector orthogonalisation. No look-ahead.

Uncertainty: Newey-West standard errors on the betas.

Known limits: a large stock is part of the market factor (NVDA is about 7% of
IWV), which shrinks its unexplained part. Daily contributions are arithmetic
and do not compound over long periods. Free-feed price noise lands in the
unexplained part.
