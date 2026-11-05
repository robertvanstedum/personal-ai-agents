-- Partnership v1.1 B4 seam (decision 6): the execution authority holds the task fence (C06) and the operation
-- ledger (E03). Additive: no existing table or column changes; a rollback leaves two empty tables.
-- Fences are issued only by the task service; admission never raises one.
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS guild.mc_task_fences (
    principal   text        NOT NULL,
    environment text        NOT NULL,
    task_id     text        NOT NULL,
    fence       bigint      NOT NULL CHECK (fence >= 0),
    updated_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (principal, environment, task_id)
);
-- One row per operation id. A lookup that finds nothing leaves a placeholder (task_id, digest and fence NULL,
-- applied false) whose dead_through makes every attempt at or below it refused, so a late send cannot contradict
-- an "absent" answer. An applied row's binding (task, digest, fence, card) is written once and never rewritten.
CREATE TABLE IF NOT EXISTS guild.mc_task_operations (
    principal     text        NOT NULL,
    environment   text        NOT NULL,
    operation_id  text        NOT NULL,
    kind          text        NOT NULL CHECK (kind IN ('admit_bundle', 'set_fence')),
    task_id       text,
    digest        text,
    fence         bigint      CHECK (fence IS NULL OR fence >= 0),
    card_id       text,
    dead_through  integer     NOT NULL DEFAULT 0 CHECK (dead_through >= 0),
    applied       boolean     NOT NULL DEFAULT false,
    recorded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (principal, environment, operation_id),
    CHECK (NOT applied OR (task_id IS NOT NULL AND digest IS NOT NULL AND fence IS NOT NULL)),
    CHECK (NOT applied OR kind <> 'admit_bundle' OR card_id IS NOT NULL)
);
-- B4 execution design v0.5 M2: an admission carries Robert's approval of exactly that card. The authority checks it
-- in its own transaction (digest equals the compiled card, recorded within 15 minutes by the database clock) and
-- keeps the approval event id here, unique: one approval admits one card, once.
ALTER TABLE guild.mc_task_operations ADD COLUMN IF NOT EXISTS approval_event text;
ALTER TABLE guild.mc_task_operations ADD COLUMN IF NOT EXISTS approved_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS mc_task_operations_approval_event
    ON guild.mc_task_operations (principal, environment, approval_event) WHERE approval_event IS NOT NULL;
