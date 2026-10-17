# Historical Toolchain Locks

Each file in this directory is an immutable copy of a former root
`toolchain.lock.json`, named by its SHA-256. Receipts continue to reference the
lock that existed when their experiment ran.

Do not alter or delete a historical lock while a receipt references it. A later
baseline receives a new root lock and new receipts.
