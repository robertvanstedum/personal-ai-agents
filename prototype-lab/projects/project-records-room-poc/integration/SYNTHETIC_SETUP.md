# Isolated synthetic CoS test setup

Owner-operated test CLI, not a deployed portal integration. Review the exact
candidate before use. Robert must watch each inference turn. No private material.

This preserves the dirty mounted development checkout: the reviewed backend
source travels over Docker stdin and runs in memory, using the development
container's existing credentials. It changes no mounted source or container
configuration. The backend must target `http://cos-agent-a:18789/v1` with agent
`cos-agent-a`; any other configured route fails closed. Actual inference may
create runtime-side session/log records and incur provider charges.

From the PoC directory, using its declared Python requirements:

1. `python integration/synthetic_cos_setup.py init` creates a fresh private
   temporary root. Record the printed root and owner-key path; it never prints
   the key. Do not commit or upload this runtime directory.
2. Check port 18880 is free; do not terminate an unrelated listener. Run
   `python integration/synthetic_cos_setup.py serve --root <printed-root>`.
   It binds loopback only, without a reloader. Open http://127.0.0.1:18880 and
   use that root's owner key. Do not reuse the older preview's key or port.
3. Before sending, review every record in this session for non-private content.
   Confirm Robert is present and approves the call. The CLI flag below is an
   operator attestation, not remote authentication or a content classifier.
4. Generate a canonical UUID once and retain it for this attempt. Run
   `python integration/synthetic_cos_setup.py respond --root <printed-root> --request-id <uuid> --owner-watching`.
   Use a separately approved request with `--action brief` for a briefing.
5. Save the returned request ID, runtime ID, record ID and Records receipt ID.
   Verify the stored record and its source coverage, not just visible text.
   A synthetic reply is not a complete R1/H1 pass or portal-route proof.

Failure: stop and inspect the private attempt journal. The connector reserves
before inference and journals generated output before Records write. A transport
timeout may leave a remote request running even after the local Docker client
exits. Never create a new request ID to evade an uncertain attempt; reconcile
first. Same-ID replay follows the existing connector's fail-closed behavior.

The source digest captured at init must still match on invocation. It identifies
source, not review approval: independently verify the frozen candidate before
initialization. No automatic polling, agent loop or retries are introduced.

The runtime's existing tools remain available; this is not a tool-free sandbox.
Session allowlisting does not inspect content added later. Keep the test entirely
synthetic. This is an isolated connector test, bypassing the portal, and does not
establish that production CoS can access Records. Stop the owned server after
the test; preserve necessary test evidence before the OS purges temporary data.
