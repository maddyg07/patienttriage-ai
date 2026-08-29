"""
app/
====
Rendering only. Zero clinical logic.

`core/` deliberately imports nothing from here, and nothing in this package
computes a score, a band, a confidence figure or a question value. Everything
displayed is read off objects produced by `core/` and `simulation/`.

That separation is the reason the engine can be tested, reasoned about and
eventually served over an API without touching the UI -- and it is the reason a
bug in the dashboard cannot become a bug in a patient's acuity.
"""
