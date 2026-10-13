Repair the Alembic history

The target URL migration is present, but the database history is broken before it reaches that migration.

Current repository graph:

14cf5447c245
  -> 1a821cd835fd
  -> 4a2f624ced4f
  -> 1c9c12f76c45
  -> 475aa45cde0c
  -> 33e7bfda0aab
  -> 4e73efadb26f
  -> 9c1f8f0e2a11

There is also an unrelated second head:

14cf5447c245
  -> add_timeline_screenshots

The database reports a missing revision named  fef1658bde83 . Do not create a replacement migration with the same ID unless its original contents are recovered. That could silently corrupt the database history.

Recommended fix:

git log --all --full-history -- backend/database/alembic/versions
git branch -a
git fetch --all --prune
git log --all --name-status -- fef1658bde83

Look for the original migration file in another branch or commit. Restore the exact file under:

backend/database/alembic/versions/

Then inspect the graph:

python -m alembic heads
python -m alembic history
python -m alembic current

Because the repository has two heads, the long term fix should add a merge migration rather than using  upgrade head  while the heads remain separate:

python -m alembic merge -m "merge project migration heads" 9c1f8f0e2a11 add_timeline_screenshots

After restoring the missing revision and creating the merge migration:

python -m alembic upgrade head
python -m alembic current

Then verify the index in PostgreSQL:

SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'projects'
  AND indexname = 'uq_projects_owner_target_url_active';

If the missing migration cannot be recovered, stop and inspect the production database before changing  alembic_version . A database administrator should create a documented repair migration after confirming which schema changes represented by  fef1658bde83  already exist.