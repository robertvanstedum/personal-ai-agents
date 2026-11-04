-- Partnership v1.1 B4 seam (decision 6): the execution authority holds the task fence (C06) and the operation
-- ledger (E03). Additive: no existing table or column changes.
CREATE TABLE IF NOT EXISTS guild.mc_task_fences (
    principal   text        NOT NULL,
    environment text        NOT NULL,
    task_id     text        NOT NULL,
    fence       bigint      NOT NULL CHECK (fence >= 0),
    updated_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (principal, environment, task_id)
);
CREATE TABLE IF NOT EXISTS guild.mc_task_operations (
    principal     text        NOT NULL,
    environment   text        NOT NULL,
    operation_id  text        NOT NULL,
    kind          text        NOT NULL CHECK (kind IN ('admit_bundle', 'set_fence')),
    task_id       text        NOT NULL,
    digest        text        NOT NULL,
    fence         bigint      NOT NULL,
    card_id       text,
    dead_through  integer     NOT NULL DEFAULT 0 CHECK (dead_through >= 0),
    applied       boolean     NOT NULL DEFAULT false,
    recorded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (principal, environment, operation_id)
);
