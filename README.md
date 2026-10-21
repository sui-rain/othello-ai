# othello-ai

Rust製のオセロエンジン。AIが毎晩1件ずつ改善を試し、自動の判定（正しさ・答えの一致と速度・対戦の統計検定）に合格した変更だけが取り込まれる。

An Othello engine in Rust, improved one change per night by an AI agent. A change is merged only if automated gates pass: correctness (perft, search checks), identical answers with fewer instructions, or a statistically significant strength gain (SPRT).

- 要件：[docs/requirements.md](docs/requirements.md)
- 決定の理由：[docs/decisions.md](docs/decisions.md)
- 進捗：[docs/progress.md](docs/progress.md)

```
cargo run --release -- perft 11
cargo run --release -- random-games 10000
```
