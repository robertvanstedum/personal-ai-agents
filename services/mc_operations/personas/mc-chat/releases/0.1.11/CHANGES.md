# 0.1.11 — conditional native core parity, rebased onto capture

0.1.9's runtime binding (conditional native core parity) on top of 0.1.10's portable AGENTS, which carries the correction capture section. Nothing else changes. It exists so the parity candidate cannot drop capture when it is activated.

**Not activated.** Activation also needs 0.1.9's mc-chat tools change (`exec`/`process`/`session_status`). Same-user `exec` can read `~/minimoi-private/judgment/`, which conflicts with the judgment spec's rule that MC never reads the store (v0.2 §2). Before activation, add an OS-level boundary or get Robert's explicit acceptance of that trade-off. Codex's activation operator (core-native-parity/activation) is still unreviewed.
