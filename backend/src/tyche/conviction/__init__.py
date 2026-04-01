"""Conviction engine — technical screening via 8/21 EMA strategy.

Three-layer architecture:
  features.py   — pure EMA/trend computation (data-derived)
  csp_policy.py — stateless CSP eligibility gate evaluation
  engine.py     — backward-compatible wrapper combining both
"""
