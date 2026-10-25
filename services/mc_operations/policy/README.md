# MC tool policy (spec v1.2 §8.1–8.3)

`mc-policy.example.json` is an illustrative policy covering every code-owned handler at its floor tier. It is **not an
approval**: a policy enables nothing until the operator installs it together with an authority record for that exact
file, outside any agent's workspace, and points the portal at both:

    MINIMOI_MC_POLICY_FILE=/abs/path/mc-policy.json
    MINIMOI_MC_POLICY_AUTHORITY_FILE=/abs/path/mc-policy.authority.json

The authority record ties the policy's revision and digest to Robert's approval:

    {"schema": "mc-policy-authority/1", "revision": "<the policy's revision>", "digest": "<sha256>",
     "approved_by": "robert", "approved_at": "<ISO time>", "ref": "<change record or ticket>"}

Get the digest and check the pair before installing:

    python -m core.mc_tools.policy digest mc-policy.json
    python -m core.mc_tools.policy check mc-policy.json mc-policy.authority.json

`check` prints the policy version and the tools it enables: the plugin's operator `tools` list must name the same tools.
Installing a new version revokes every live turn's tool admission and closes offers made under the previous version.
Without a valid pair every tool is refused with a visible reason; there is no fallback.
