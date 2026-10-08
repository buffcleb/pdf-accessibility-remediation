# Design decisions

[Architecture](README.md) › Design decisions

| Decision | Why |
|---|---|
| Never change drawing operators in the visual output | The visual file is guaranteed identical to the original; every job and every edit checks it pixel by pixel |
| A separate "modified" output, least destructive first | Some documents can't comply without a visible change; people choose, and it's discarded if it would lose structure |
| Verification reads back with different libraries | A bug in the writer can't hide itself |
| Structure from layout rules; AI only where rules can't decide | Predictable, cheap and testable. AI writes alt text and interprets tables, hard reading orders and notation |
| Every AI result is checked or flagged | Notation readings must match the characters on the page; OCR corrections must look like the word they replace; everything AI wrote is in the review list |
| All AI through one gateway | Local, cloud and commercial models are interchangeable by configuration, spend is tracked per job, and a budget caps the total |
| A reviewer from a different model family | A model tends to approve its own mistakes |
| People choose an AI level, never a model | Models change; levels and kinds of document are stable names with stable costs |
| Services and AI levels share one quota model | Adobe, AI levels and the standard engine use the same opt-in, limits, ledger and audit, so a new service is configuration, not code |
| Local-only groups | Groups handling sensitive records can rule out every external provider in one switch |
| Every edit is a new version | Engine outputs stay as delivered; any change can be traced and compared |
| Adobe's file is kept as Adobe returned it | Adobe and the standard route can be compared fairly, and improvements are a separate copy that can be traced back |
| Figures on scans are redrawn from the same image, clipped | They can be tagged and described without changing a single pixel |
| Everything configured by environment variables | The same images run on a laptop, a server, Kubernetes or a cloud |
