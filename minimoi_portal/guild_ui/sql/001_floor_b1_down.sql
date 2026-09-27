-- Removes the Shop floor B1 (c) tables, and with them every kept note, post-it
-- (including the bin) and Continue row. Robert's choice only: the tables are
-- additive, and leaving them unused is the normal rollback. Nothing runs this.

DROP TABLE IF EXISTS guild.floor_requests;
DROP TABLE IF EXISTS guild.floor_continue;
DROP TABLE IF EXISTS guild.floor_postits;
DROP TABLE IF EXISTS guild.floor_messages;
