-- A cut-down copy of the launcher's tables: only the columns Cold Steel reads,
-- plus a few it doesn't, as the real file has many more.
CREATE TABLE mods (
    id char(36) NOT NULL PRIMARY KEY, steamId varchar(255), name varchar(255),
    displayName varchar(255), version varchar(255), dirPath text, archivePath text,
    thumbnailPath text, status text NOT NULL, source text NOT NULL
);
CREATE TABLE playsets (
    id char(36) NOT NULL PRIMARY KEY, name varchar(255) NOT NULL, isActive boolean,
    loadOrder varchar(255), createdOn datetime NOT NULL, isRemoved boolean NOT NULL DEFAULT false
);
CREATE TABLE playsets_dlcs (
    playsetId char(36) NOT NULL, dlcId text NOT NULL, enabled boolean NOT NULL DEFAULT '0',
    PRIMARY KEY (playsetId, dlcId)
);
CREATE TABLE playsets_mods (
    playsetId char(36) NOT NULL, modId char(36) NOT NULL, enabled boolean DEFAULT '1',
    position integer
);

INSERT INTO mods VALUES
  ('m-alpha', '2000000001', 'Alpha Interface', NULL, '1.2',
   '@ROOT@/games/steamapps/workshop/content/281990/2000000001', NULL,
   NULL, 'ready_to_play', 'steam'),
  ('m-gamma', '2000000003', 'Gamma Soundtrack', NULL, '2.0',
   '@ROOT@/games/steamapps/workshop/content/281990/2000000003',
   '@ROOT@/games/steamapps/workshop/content/281990/2000000003/gamma.zip',
   NULL, 'ready_to_play', 'steam'),
  ('m-local', NULL, 'My Local Tweaks', NULL, '0.1.0',
   '@ROOT@/home/.local/share/Paradox Interactive/Stellaris/mod/my_local', NULL,
   NULL, 'ready_to_play', 'local'),
  ('m-gone', '2000000099', 'Unsubscribed Mod', NULL, '1.0',
   '@ROOT@/games/steamapps/workshop/content/281990/2000000099', NULL,
   NULL, 'unsubscribed', 'steam');

-- Created out of order on purpose: the launcher lists playsets oldest first.
INSERT INTO playsets VALUES
  ('p-second', 'Second Playset', 0, 'custom', 1700000002000, 0),
  ('p-main', 'Main Playset', 1, 'custom', 1700000001000, 0),
  ('p-removed', 'Deleted Playset', 0, 'custom', 1700000003000, 1);

-- Inserted out of order on purpose: position is the load order.
INSERT INTO playsets_mods VALUES
  ('p-main', 'm-gone', 1, 3),
  ('p-main', 'm-alpha', 1, 0),
  ('p-main', 'm-local', 1, 2),
  ('p-main', 'm-gamma', 0, 1),
  ('p-second', 'm-gamma', 1, 0),
  ('p-removed', 'm-alpha', 1, 0);

-- The launcher's DLC ids: some are the folder name without its number, and
-- dlc032 is named after the DLC's old title, not its folder.
INSERT INTO playsets_dlcs VALUES
  ('p-main', 'arachnoid', 0),
  ('p-main', 'dlc032_cybernetics', 0),
  ('p-second', 'arachnoid', 1),
  ('p-second', 'dlc032_cybernetics', 1);
