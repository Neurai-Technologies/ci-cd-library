# Shared backup encryption

`deploy/backup/crypto.sh` is a source-only shell module. Importing it performs
no validation, file I/O, scheduling or network work. It does not read settings,
run PostgreSQL or require Docker. Call it from Bash or the backup image's ash:

```sh
. <library>/deploy/backup/crypto.sh
backup_encrypt /private/archive.tar.gz.enc < /private/archive.tar.gz
backup_verify /private/archive.tar.gz.enc
backup_decrypt /private/archive.tar.gz.enc > /private/restored.tar.gz
```

The caller supplies `BACKUP_PASSPHRASE` through its protected environment and
`BACKUP_HMAC_SALT`, exactly eight bytes, unchanged from its existing backups.
Neither belongs in an argument or a log. OpenSSL and standard shell file tools
must be installed. Each function exits non-zero on refusal or failure.

- `backup_encrypt OUTFILE` reads arbitrary bytes from stdin and creates
  `OUTFILE` and `OUTFILE.hmac`, mode 600. It refuses an existing output or
  sidecar, including symlinks. A private working directory is placed beside
  the output; publication uses same-filesystem links without overwriting.
  The sidecar appears first, ciphertext last as the completion marker.
- `backup_verify FILE` verifies a private snapshot of ciphertext and sidecar
  without decrypting or producing stdout.
- `backup_decrypt FILE` verifies that snapshot, decrypts it completely into
  a private temporary file, then writes plaintext to stdout. Failed integrity
  or decryption produces no plaintext. A source change after verification
  cannot change the snapshot being decrypted.

The helper sets `umask 077`: new ciphertext and sidecars are owner-only
(`0600`), and work directories are `0700`. Earlier wrappers running with
`umask 022` could produce `0644` files. This permissions change does not alter
existing files, but restore, mirror and upload readers must have access as the
owning user; verify the consumer's reader identities before adopting it.

Protect the output directory and temporary storage from other writers. The
functions use private mode-700 work directories and remove them on normal
exit and handled signals. SIGKILL or a power loss cannot run cleanup: the
operator must remove abandoned `.backup-crypto.*` or `backup-crypto.*`
directories before reusing storage. Decryption temporarily needs room for
both a ciphertext snapshot and the plaintext. Callers must protect any
plaintext they redirect or decompress themselves; this API does not erase
storage blocks or make environment variables unreadable to the same user.

A producer's failed pipeline is visible only to its caller. Use pipefail and
a transient output name, then check the complete pipeline before publishing
an application artifact. The database and log wrappers do this, and retain
their existing size checks, retention, locks, heartbeats and upload behaviour.
An unpaired sidecar after an uncatchable interruption is not a complete
artifact. Do not treat it as one.

## Compatibility

The format is unchanged: AES-256-CBC with a random OpenSSL salt and its
PBKDF2 defaults, then HMAC-SHA-256 over ciphertext. The MAC key is derived
separately from the same passphrase using the existing eight-byte label salt
and OpenSSL's AES-256-CBC PBKDF2 derivation. The lowercase hexadecimal MAC,
followed by a newline, is stored in the `.hmac` sidecar. Existing authenticated
dumps and log archives remain readable, and new output is readable by the
previous algorithm. This is code reuse, not a cryptographic migration.

The passphrase is passed to OpenSSL with `-pass env:BACKUP_PASSPHRASE`.
As in the old implementation, the derived MAC key does appear in an internal
OpenSSL `-macopt` argument. Never enable shell tracing around secret handling;
the public functions turn tracing off inside their subshells.

Compression, SQL headers/trailers and archive validation belong to callers.
Only `deploy/restore.sh` retains the historical explicit `RESTORE_UNVERIFIED=1`
opt-in for a dump without a sidecar. The shared API has no authentication bypass.

## Packaging

Backup and log archive services mount the complete `deploy/backup` directory
at `/scripts`, so their sibling `crypto.sh` is available on every run. A host
using `deploy/restore.sh` needs `deploy/backup/crypto.sh` at that same relative
location. A product image that copies only `restore.sh` must also copy the
helper to `backup/crypto.sh` beside it. Keep the versioned library intact;
never copy a second maintained encryption implementation into a product.

## Disposable container drill

From a normal terminal with Docker Compose, OpenSSL and Python available:

```sh
bash checks/backup-restore-drill.sh /tmp/backup-drill-report.log /srv/drill-shared
```

This builds the actual backup Dockerfile with the shared helper and wrappers,
then exercises PostgreSQL 16 and 17 against matching pinned database images.
For each major it restores the real dump through `deploy/restore.sh`, compares
table counts, ordered-row checksums and a sequence, refuses a tampered dump
before any SQL is applied, and decrypts a real log archive. Only synthetic
fixtures are used. Builds may download the pinned images and Alpine packages.
The second argument is an existing, user-owned host directory shared with the
Docker VM and not writable by other users. Each run creates its own private
child there, prints the bind folder, and proves bidirectional host/container
visibility and hard links before starting the databases. It writes backups
into both a named volume and that host bind, then restores the bind-mounted
backup. A missing share or unsupported hard link fails the drill. Only its
own scratch child is removed; the supplied parent is preserved.
It creates uniquely named containers, volumes, networks and a temporary image
on the currently selected Docker daemon; it exposes no host ports and removes
its resources on exit. Previously existing images are kept. It neither reads
an existing database nor starts or stops a VM. The report records the results;
a unit suite is not a substitute for this container drill.
